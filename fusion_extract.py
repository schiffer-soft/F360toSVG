"""Fusion-seitiges Extraktionsskript — laeuft IN Fusion 360, nicht lokal.

Wird von export_svg.py per MCP an Fusion geschickt und dort ausgefuehrt.
Sammelt aus dem aktiven Design alle aus der Blickrichtung VIEW
sichtbaren Flaechen (Standard: "top" = Draufsicht von Z+):

- Jede Flaeche, deren Normale irgendwo zum Betrachter zeigt
  (planar: eine Messung reicht; gekruemmt: Stichproben auf einem
  Parameter-Raster), gilt als sichtbar.
- Ihre Randkonturen (aeussere Loops und Loch-Loops) werden mit
  STROKE_TOL_CM Toleranz abgetastet und auf die Bildebene projiziert.
- Pro Flaeche wird der Tiefenbereich (z_min/z_max, zum Betrachter)
  mitgeliefert; die Stapelreihenfolge (Painter's Algorithm) bestimmt
  spaeter der SVG-Builder.

Ausgabe: ein JSON-Objekt auf stdout (print), Koordinaten in mm.
"""
import json
import os
import tempfile

import adsk.core
import adsk.fusion

PROGRESS_FILE = os.path.join(tempfile.gettempdir(), "fusion_svg_progress.txt")
PARTIAL_FILE = os.path.join(tempfile.gettempdir(), "fusion_svg_partial.jsonl")

STROKE_TOL_CM = 0.001  # Kurven-Sampling-Toleranz (0.001 cm = 10 um)
NZ_EPS = 0.01          # minimale Zum-Betrachter-Komponente der Flaechennormale
GRID_N = 5             # Parameter-Raster fuer Normalen-Check gekruemmter Flaechen
COORD_DECIMALS = 4     # Nachkommastellen der mm-Koordinaten
VIEW = "auto"          # Blickrichtung; wird von export_svg.py ersetzt

# Fortschritts-Texte in der Sprache der Oberflaeche; werden von
# export_svg.py als reines ASCII (\uXXXX) eingesetzt. Wichtig: KEINE
# Umlaute direkt in dieses Skript schreiben — beim Transfer nach Fusion
# gehen Nicht-ASCII-Zeichen kaputt (aus "Koerper" wird "KÃ¶rper").
MESSAGES = {}


def msg(key, **kwargs):
    """Uebersetzten Fortschrittstext bauen; faellt auf den Schluessel zurueck."""
    text = MESSAGES.get(key, key)
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except Exception:
        return text

# Toleranzen der Kanten-Verkettung (Koordinaten hier in cm):
JOIN_DIST2_TOL_CM2 = 1e-12  # quadrierter Abstand fuer "Punkte identisch"

# Achsen-Abbildung je Ansicht: (u, v, d) als (Achsenindex, Vorzeichen).
# u = SVG-x (nach rechts), v = SVG-y (nach oben, Builder spiegelt),
# d = Tiefe zum Betrachter (grosses d = naeher dran = weiter oben im Stapel).
VIEWS = {
    "top":    ((0, 1), (1, 1), (2, 1)),
    "bottom": ((0, 1), (1, -1), (2, -1)),
    "front":  ((0, 1), (2, 1), (1, -1)),
    "back":   ((0, -1), (2, 1), (1, 1)),
    "right":  ((1, 1), (2, 1), (0, 1)),
    "left":   ((1, -1), (2, 1), (0, -1)),
}

# Kamerarichtung (dominante Achse, Vorzeichen) -> Ansicht.
# Beispiel: Blick nach -Z (von oben herab) = "top".
CAMERA_VIEW_MAP = {
    (2, False): "top", (2, True): "bottom",
    (1, True): "front", (1, False): "back",
    (0, False): "right", (0, True): "left",
}


def resolve_view(app):
    """Ansicht bestimmen; bei "auto" aus der aktuellen Viewport-Kamera."""
    if VIEW != "auto":
        if VIEW not in VIEWS:
            raise ValueError("Unbekannte Ansicht: " + VIEW)
        return VIEW
    camera = app.activeViewport.camera
    eye, target = camera.eye, camera.target
    direction = (target.x - eye.x, target.y - eye.y, target.z - eye.z)
    axis = max(range(3), key=lambda i: abs(direction[i]))
    return CAMERA_VIEW_MAP[(axis, direction[axis] > 0)]


ALBEDO_PROPERTY_ID = "opaque_albedo"  # der eigentliche Farbkanal der Appearance


def _albedo_property(props):
    prop = props.itemById(ALBEDO_PROPERTY_ID)
    if prop is not None and prop.objectType == adsk.core.ColorProperty.classType():
        return prop
    for i in range(props.count):
        candidate = props.item(i)
        if candidate.objectType == adsk.core.ColorProperty.classType():
            return candidate
    return None


def _texture_info(prop):
    """Textur-Bilddatei + Farb-Modifikatoren, falls der Farbkanal eine Textur hat."""
    try:
        if not prop.hasConnectedTexture:
            return None
        texture = prop.connectedTexture
    except Exception:
        return None
    if texture is None:
        return None
    tex_props = texture.properties

    def prop_value(prop_id, default):
        entry = tex_props.itemById(prop_id)
        try:
            return entry.value if entry is not None else default
        except Exception:
            return default

    filename = prop_value("unifiedbitmap_Bitmap", None)
    if not filename:
        return None
    rgb_amount = prop_value("unifiedbitmap_RGBAmount", 1.0)
    return {
        "file": filename,
        "factors": [
            rgb_amount * prop_value("unifiedbitmap_RedAmount", 1.0),
            rgb_amount * prop_value("unifiedbitmap_GreenAmount", 1.0),
            rgb_amount * prop_value("unifiedbitmap_BlueAmount", 1.0),
        ],
        "invert": bool(prop_value("unifiedbitmap_Invert", False)),
        # Kachel-Platzierung: RealWorldScale/-Offset sind in Zoll
        "scale_mm": [
            prop_value("texture_RealWorldScaleX", 1.0) * 25.4,
            prop_value("texture_RealWorldScaleY", 1.0) * 25.4,
        ],
        "offset_mm": [
            prop_value("texture_RealWorldOffsetX", 0.0) * 25.4,
            prop_value("texture_RealWorldOffsetY", 0.0) * 25.4,
        ],
        "angle_deg": prop_value("texture_WAngle", 0.0),
    }


def texture_anchor(body, axes):
    """Projizierter Ursprung der Textur-Projektion eines Koerpers (mm).

    body.textureMapControl liefert die Projektions-Matrix (Box-Mapping);
    ihre Translation ist der Ankerpunkt des Kachelrasters. None, wenn die
    API sie nicht hergibt — der Builder kachelt dann ab Ursprung.
    """
    u_axis, v_axis, _ = axes
    try:
        m = body.textureMapControl.transform.asArray()  # 4x4, zeilenweise, cm
    except Exception:
        return None
    origin3 = (m[3], m[7], m[11])
    return [
        round(u_axis[1] * origin3[u_axis[0]] * 10.0, COORD_DECIMALS),
        round(v_axis[1] * origin3[v_axis[0]] * 10.0, COORD_DECIMALS),
    ]


def partial_write(obj, truncate=False):
    """Teilergebnis als JSON-Zeile anhaengen — die GUI baut daraus live
    eine Zwischen-Vorschau, waehrend die Extraktion noch laeuft."""
    try:
        with open(PARTIAL_FILE, "w" if truncate else "a", encoding="utf-8") as handle:
            handle.write(json.dumps(obj, separators=(",", ":")) + "\n")
    except OSError:
        pass


def progress(message):
    """Fortschritt in eine Temp-Datei schreiben — die GUI liest live mit.

    print() kaeme erst am Skriptende gesammelt an; die Datei erlaubt
    echte Zwischenmeldungen waehrend der Extraktion.
    """
    try:
        with open(PROGRESS_FILE, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except OSError:
        pass  # Fortschritt ist nice-to-have, nie ein Abbruchgrund


def body_color(body):
    """(Hex-Farbe | None, Textur-Info | None) der Appearance eines Koerpers."""
    appearance = body.appearance
    if not appearance:
        return None, None
    prop = _albedo_property(appearance.appearanceProperties)
    if prop is None:
        return None, None
    texture = _texture_info(prop)
    try:
        values = prop.values
    except Exception:
        values = None
    color_hex = None
    if values:
        c = values[0]
        color_hex = "#{:02X}{:02X}{:02X}".format(c.red, c.green, c.blue)
    return color_hex, texture


def normal_toward_viewer(normal, d_axis):
    axis, sign = d_axis
    return sign * (normal.x, normal.y, normal.z)[axis]


def max_facing_normal(face, d_axis):
    """Maximale Zum-Betrachter-Komponente der Flaechennormale (Stichproben)."""
    evaluator = face.evaluator
    ok, normal = evaluator.getNormalAtPoint(face.pointOnFace)
    best = normal_toward_viewer(normal, d_axis) if ok else -1.0
    if face.geometry.objectType == adsk.core.Plane.classType():
        return best  # planar: eine Messung genuegt
    param_range = evaluator.parametricRange()
    if not param_range:
        return best
    du = param_range.maxPoint.x - param_range.minPoint.x
    dv = param_range.maxPoint.y - param_range.minPoint.y
    for i in range(GRID_N + 1):
        for j in range(GRID_N + 1):
            p2 = adsk.core.Point2D.create(
                param_range.minPoint.x + du * i / GRID_N,
                param_range.minPoint.y + dv * j / GRID_N,
            )
            if not evaluator.isParameterOnFace(p2):
                continue
            ok, normal = evaluator.getNormalAtParameter(p2)
            if ok:
                best = max(best, normal_toward_viewer(normal, d_axis))
    return best


def project(point3d, u_axis, v_axis):
    coords = (point3d.x, point3d.y, point3d.z)
    return (u_axis[1] * coords[u_axis[0]], v_axis[1] * coords[v_axis[0]])


def sample_edge(edge, u_axis, v_axis):
    """Kante als projizierte Punktfolge (u/v, in cm) mit STROKE_TOL_CM abtasten."""
    evaluator = edge.evaluator
    if evaluator is None:  # degenerierte Kante (z.B. Kegelspitze)
        return [
            project(vertex.geometry, u_axis, v_axis)
            for vertex in (edge.startVertex, edge.endVertex)
            if vertex is not None
        ]
    ok, p0, p1 = evaluator.getParameterExtents()
    if not ok:
        return []
    ok, strokes = evaluator.getStrokes(p0, p1, STROKE_TOL_CM)
    if not ok:
        return []
    return [project(pt, u_axis, v_axis) for pt in strokes]


def _dist2(a, b):
    dx, dy = a[0] - b[0], a[1] - b[1]
    return dx * dx + dy * dy


def loop_points(loop, u_axis, v_axis):
    """Kanten eines Loops via CoEdges in Laufrichtung zu einem Polygon verbinden.

    Die Orientierung jedes Segments wird geometrisch bestimmt (welches Ende
    schliesst an das Kettenende an?) — isOpposedToEdge dient nur als
    Startorientierung, da die Flag bei manchen Kanten nicht zur
    tatsaechlichen Anschlussrichtung passt (fuehrt sonst zu
    selbstschneidenden Polygonen).
    """
    points = []
    for co_edge in loop.coEdges:
        segment = sample_edge(co_edge.edge, u_axis, v_axis)
        if not segment:
            continue
        if co_edge.isOpposedToEdge:
            segment = list(reversed(segment))
        if points and _dist2(points[-1], segment[0]) > _dist2(points[-1], segment[-1]):
            segment = list(reversed(segment))  # Anschluss-Korrektur
        if points and _dist2(points[-1], segment[0]) < JOIN_DIST2_TOL_CM2:
            segment = segment[1:]  # doppelten Stosspunkt vermeiden
        points.extend(segment)
    if len(points) > 1 and _dist2(points[0], points[-1]) < JOIN_DIST2_TOL_CM2:
        points = points[:-1]  # SVG 'Z' schliesst den Pfad selbst
    return [
        [round(x * 10.0, COORD_DECIMALS), round(y * 10.0, COORD_DECIMALS)]
        for x, y in points  # cm -> mm
    ]


def depth_range(bounding_box, d_axis):
    """Tiefenbereich (zum Betrachter) einer Bounding Box in mm."""
    axis, sign = d_axis
    lo = (bounding_box.minPoint.x, bounding_box.minPoint.y, bounding_box.minPoint.z)[axis]
    hi = (bounding_box.maxPoint.x, bounding_box.maxPoint.y, bounding_box.maxPoint.z)[axis]
    d1, d2 = sign * lo, sign * hi
    return min(d1, d2) * 10.0, max(d1, d2) * 10.0


def visible_faces(body, axes):
    """Alle zum Betrachter zeigenden Flaechen mit projizierten Konturen."""
    u_axis, v_axis, d_axis = axes
    faces = []
    for face in body.faces:
        if max_facing_normal(face, d_axis) < NZ_EPS:
            continue
        loops = []
        for loop in face.loops:
            points = loop_points(loop, u_axis, v_axis)
            if len(points) >= 3:
                loops.append({"isOuter": loop.isOuter, "points": points})
        if not loops:
            continue
        d_min, d_max = depth_range(face.boundingBox, d_axis)
        entry = {
            "z_min_mm": round(d_min, COORD_DECIMALS),
            "z_max_mm": round(d_max, COORD_DECIMALS),
            "surface": face.geometry.objectType.split("::")[-1],
            "loops": loops,
        }
        # Normale im Bildraum (u, v, d) — Basis fuer die 3D-Fasen-Schattierung
        ok, normal3 = face.evaluator.getNormalAtPoint(face.pointOnFace)
        if ok:
            coords = (normal3.x, normal3.y, normal3.z)
            entry["normal"] = [
                round(u_axis[1] * coords[u_axis[0]], 4),
                round(v_axis[1] * coords[v_axis[0]], 4),
                round(d_axis[1] * coords[d_axis[0]], 4),
            ]
        # Ringfasen (Kegel/Torus): Zentrum + Achsrichtung, damit auch
        # TEIL-Ringe den richtigen Ausschnitt des Lichtverlaufs bekommen
        if entry["surface"] in ("Cone", "Torus"):
            try:
                geometry = face.geometry
                origin = geometry.origin
                axis = geometry.axis
                center_uv = project(origin, u_axis, v_axis)
                entry["ringCenter"] = [
                    round(center_uv[0] * 10.0, COORD_DECIMALS),
                    round(center_uv[1] * 10.0, COORD_DECIMALS),
                ]
                axis_coords = (axis.x, axis.y, axis.z)
                entry["ringAxisD"] = round(
                    d_axis[1] * axis_coords[d_axis[0]], 4
                )
                # Konkav (Innenfase am Loch): Normale zeigt ZUR Achse ->
                # der Lichtverlauf muss gespiegelt werden
                if ok:
                    point = face.pointOnFace
                    along = ((point.x - origin.x) * axis.x
                             + (point.y - origin.y) * axis.y
                             + (point.z - origin.z) * axis.z)
                    radial = (point.x - origin.x - along * axis.x,
                              point.y - origin.y - along * axis.y,
                              point.z - origin.z - along * axis.z)
                    dot = (radial[0] * normal3.x + radial[1] * normal3.y
                           + radial[2] * normal3.z)
                    entry["ringConcave"] = dot < 0
            except Exception:
                pass  # ohne Zentrum faellt der Builder auf Flachschattierung zurueck
        faces.append(entry)
    return faces


def collect_decals(root, axes):
    """Sichtbare Aufkleber (Decals) mit Bilddatei, Lage und Clip-Kontur."""
    u_axis, v_axis, d_axis = axes
    result = []
    if not hasattr(root, "decals"):
        return result  # aeltere Fusion-Version ohne Decal-API
    if getattr(root, "isDecalFolderLightBulbOn", True) is False:
        return result

    def project_mm(p3):
        return [
            round(u_axis[1] * p3[u_axis[0]] * 10.0, COORD_DECIMALS),
            round(v_axis[1] * p3[v_axis[0]] * 10.0, COORD_DECIMALS),
        ]

    decals = root.decals
    for i in range(decals.count):
        decal = decals.item(i)
        if not decal.isVisible or not decal.isLightBulbOn:
            continue
        m = decal.transform.asArray()  # 4x4, zeilenweise
        x_axis3 = (m[0], m[4], m[8])    # lokale X-Basis = volle Breite
        y_axis3 = (m[1], m[5], m[9])    # lokale Y-Basis = volle Hoehe
        normal3 = (m[2], m[6], m[10])   # Flaechennormale des Aufklebers
        origin3 = (m[3], m[7], m[11])
        length = (normal3[0] ** 2 + normal3[1] ** 2 + normal3[2] ** 2) ** 0.5
        if length == 0:
            continue
        axis_idx, axis_sign = d_axis
        facing = axis_sign * normal3[axis_idx] / length
        if facing < NZ_EPS:
            continue  # zeigt vom Betrachter weg

        clip_loops = []
        faces = decal.faces  # BRepFaceVector (std::vector-Wrapper)
        try:
            face_count = faces.size()
        except Exception:
            face_count = 0
        for j in range(face_count):
            try:
                face = faces[j]
            except Exception:
                break
            for loop in face.loops:
                points = loop_points(loop, u_axis, v_axis)
                if len(points) >= 3:
                    clip_loops.append({"isOuter": loop.isOuter, "points": points})

        result.append({
            "name": decal.name,
            "file": decal.imageFilename,
            "opacity": decal.opacity,
            "origin": project_mm(origin3),
            "uAxis": project_mm(x_axis3),
            "vAxis": project_mm(y_axis3),
            "depth_mm": round(axis_sign * origin3[axis_idx] * 10.0, COORD_DECIMALS),
            "clip": clip_loops,
        })
    return result


def run(_context: str):
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        print(json.dumps({"error": msg("fusion.no_design")}))
        return

    root = design.rootComponent
    view = resolve_view(app)
    axes = VIEWS[view]
    out = {
        "document": app.activeDocument.name,
        "units": "mm",
        "view": view,
        "viewSource": "camera" if VIEW == "auto" else "cli",
        "bodies": [],
    }
    progress(msg("fusion.document", document=app.activeDocument.name, view=view))
    partial_write(
        {"meta": {"document": out["document"], "units": "mm", "view": view}},
        truncate=True,
    )

    all_bodies = list(root.bRepBodies)
    for occurrence in root.allOccurrences:
        # Occurrence-Bodies sind Proxies -> Geometrie kommt in Weltkoordinaten
        all_bodies.extend(occurrence.bRepBodies)
    visible_bodies = [b for b in all_bodies if b.isVisible]
    progress(msg("fusion.bodies_found", count=len(visible_bodies)))

    total_faces = 0
    # nicht jeden Koerper melden — bei grossen Designs ~20 Sammelmeldungen
    report_step = max(1, len(visible_bodies) // 20)
    for index, body in enumerate(visible_bodies, start=1):
        color_hex, texture = body_color(body)
        faces = visible_faces(body, axes)
        total_faces += len(faces)
        entry = {
            "name": body.name,
            "color": color_hex or "#808080",
            "faces": faces,
        }
        if texture:
            texture["anchor"] = texture_anchor(body, axes)
            entry["texture"] = texture
        out["bodies"].append(entry)
        if entry["faces"]:
            partial_write({"body": entry})
        if index % report_step == 0 or index == len(visible_bodies):
            progress(msg(
                "fusion.body_progress", index=index, total=len(visible_bodies),
                faces=total_faces, name=body.name,
            ))

    progress(msg("fusion.decals_search"))
    out["decals"] = collect_decals(root, axes)
    if out["decals"]:
        progress(msg("fusion.decals_found", count=len(out['decals'])))
    progress(msg(
        "fusion.done", bodies=len(visible_bodies), faces=total_faces,
    ))

    # Ergebnis in eine Temp-Datei schreiben statt zu printen:
    # der MCP-Server kappt print-Ausgaben bei 1 MiB, grosse Designs
    # sprengen das locker. Fusion laeuft lokal, das CLI liest die Datei.
    payload = json.dumps(out, separators=(",", ":"))
    result_path = os.path.join(tempfile.gettempdir(), "fusion_svg_export.json")
    with open(result_path, "w", encoding="utf-8") as handle:
        handle.write(payload)
    print(json.dumps({"resultFile": result_path, "bytes": len(payload)}))
