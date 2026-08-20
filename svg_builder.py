"""Baut aus den in Fusion extrahierten Flaechendaten ein SVG.

Kern-Logik (Painter's Algorithm von hinten nach vorn):
Alle sichtbaren Flaechen aller Koerper werden nach ihrem Tiefenbereich
(z_max, z_min — Tiefe zum Betrachter, je nach Ansicht eine andere
Modellachse) aufsteigend sortiert und in dieser Reihenfolge als
gefuellte Pfade gezeichnet. Eine Fase liegt so korrekt UNTER ihrer
Deckflaeche (endet gleich hoch, beginnt tiefer), aber UEBER allem,
was dahinter liegt. Loecher laufen ueber fill-rule="evenodd".

SVG-Einheit: mm, Massstab 1:1. Die v-Achse wird gespiegelt (SVG-Y
zeigt nach unten, die Bildebenen-v-Achse der Extraktion nach oben).
"""
from __future__ import annotations

import math

PRECISION = 3  # Nachkommastellen im SVG-Pfad (mm)

# 3D-Fase: Hoehe der gedachten Sonne ueber der Bildebene. 45 Grad heisst:
# die Deckflaeche ist Referenz, zur Sonne gekippte Fasen werden heller,
# von ihr weg gekippte dunkler.
LIGHT_ELEVATION_DEG = 45.0
FASE_MIN_TILT = 0.03    # minimale Bildebenen-Komponente der Normale
FASE_MAX_FACING = 0.995  # darunter gilt eine Ebene als geneigt (Fase)

# Naht-Killer: Stroke in Fuellfarbe ueberdeckt Antialiasing-Haarlinien
# zwischen exakt aneinanderstossenden Flaechen. Jede Kontur waechst
# dadurch optisch um die halbe Breite — fuer Farbflaechen-Export
# vernachlaessigbar, fuer masshaltige Weiterverarbeitung auf 0 setzen.
DEFAULT_SEAM_STROKE_MM = 0.1


def _fmt(value: float) -> str:
    text = f"{value:.{PRECISION}f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def shade_fase_color(hex_color: str, normal: list,
                     light_deg: float, strength: float) -> str:
    """Schattiert eine Fasenfarbe nach Lambert-Licht aus Richtung light_deg.

    Winkel: 0 = Sonne von unten, 90 = rechts, 180 = oben, 270 = links.
    strength: 0..1 — skaliert den maximalen Helligkeitsversatz.
    """
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
    except (ValueError, IndexError):
        return hex_color
    theta = math.radians(light_deg)
    elevation = math.radians(LIGHT_ELEVATION_DEG)
    light = (
        math.sin(theta) * math.cos(elevation),   # u: 90 Grad = von rechts
        -math.cos(theta) * math.cos(elevation),  # v: 180 Grad = von oben
        math.sin(elevation),                     # d: zum Betrachter
    )
    nu, nv, nd = normal
    length = math.sqrt(nu * nu + nv * nv + nd * nd) or 1.0
    nu, nv, nd = nu / length, nv / length, nd / length
    illumination = max(0.0, nu * light[0] + nv * light[1] + nd * light[2])
    base = light[2]  # Beleuchtung der flachen Deckflaeche als Referenz
    factor = max(-1.0, min(1.0, (illumination - base) / base * strength))
    if factor >= 0:
        rgb = (round(c + (255 - c) * factor) for c in (r, g, b))
    else:
        rgb = (round(c * (1.0 + factor)) for c in (r, g, b))
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _tilt_ok(normal: list | None) -> bool:
    if not normal:
        return False
    tilt = math.sqrt(normal[0] ** 2 + normal[1] ** 2)
    return tilt > FASE_MIN_TILT and normal[2] < FASE_MAX_FACING


RING_AXIS_MIN_D = 0.9  # Ringachse muss (fast) in Blickrichtung stehen


def _is_fase(surface: str, normal: list | None) -> bool:
    # Flachschattierung: eine Kipprichtung = ein Farbwert. Auch der
    # Fallback fuer Ringe ohne brauchbares Zentrum/Achse.
    return (
        surface in ("Plane", "Cylinder", "Cone", "Torus")
        and _tilt_ok(normal)
    )


def _is_ring_fase(surface: str, normal: list | None,
                  center: list | None, axis_d: float | None) -> bool:
    # Kegel-/Torus-Ringe um eine Achse in Blickrichtung: die Normale
    # laeuft um den Ring -> Linearverlauf, verankert am Ringzentrum,
    # damit auch TEIL-Segmente den richtigen Ausschnitt zeigen
    return (
        surface in ("Cone", "Torus")
        and _tilt_ok(normal)
        and center is not None
        and axis_d is not None
        and abs(axis_d) >= RING_AXIS_MIN_D
    )


def _ring_gradient(gradient_id: str, color: str, normal: list,
                   light_deg: float, strength: float,
                   center_svg: tuple, radius: float) -> tuple[str, str]:
    """(defs-Eintrag, fill-Referenz) fuer einen Ring-Verlauf.

    Der Verlauf spannt sich in SVG-Koordinaten ueber den GANZEN Ring
    (Zentrum +/- Radius entlang der Lichtrichtung) — Teilsegmente
    greifen sich so automatisch den korrekten Ausschnitt, und die
    Uebergaenge zu flach schattierten Nachbarfasen passen.
    """
    theta = math.radians(light_deg)
    direction_u = math.sin(theta)          # 90 Grad = von rechts
    direction_v = -math.cos(theta)         # 180 Grad = von oben (v nach oben)
    tilt = math.sqrt(normal[0] ** 2 + normal[1] ** 2)
    facing = normal[2]
    bright = shade_fase_color(
        color, [tilt * direction_u, tilt * direction_v, facing],
        light_deg, strength,
    )
    dark = shade_fase_color(
        color, [-tilt * direction_u, -tilt * direction_v, facing],
        light_deg, strength,
    )
    # SVG-y zeigt nach unten -> v spiegeln
    svg_dx, svg_dy = direction_u, -direction_v
    cx, cy = center_svg
    x1, y1 = cx - svg_dx * radius, cy - svg_dy * radius
    x2, y2 = cx + svg_dx * radius, cy + svg_dy * radius
    definition = (
        f'    <linearGradient id="{gradient_id}" gradientUnits="userSpaceOnUse" '
        f'x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}">'
        f'<stop offset="0" stop-color="{dark}"/>'
        f'<stop offset="1" stop-color="{bright}"/></linearGradient>'
    )
    return definition, f"url(#{gradient_id})"


def _ring_radius(loops: list, center: list) -> float:
    """Aeusserer Ringradius = groesster Konturabstand zum Zentrum (mm)."""
    radius = 0.0
    for loop in loops:
        for x, y in loop.get("points", []):
            distance = math.hypot(x - center[0], y - center[1])
            if distance > radius:
                radius = distance
    return radius or 1.0


def _seam_attrs(color: str, seam_stroke_mm: float) -> str:
    if seam_stroke_mm <= 0:
        return ""
    return (
        f'stroke="{color}" stroke-width="{_fmt(seam_stroke_mm)}" '
        f'stroke-linejoin="round" '
    )


def _loop_to_path(points: list[list[float]], min_x: float, max_y: float) -> str:
    parts = []
    for i, (x, y) in enumerate(points):
        command = "M" if i == 0 else "L"
        parts.append(f"{command}{_fmt(x - min_x)} {_fmt(max_y - y)}")
    parts.append("Z")
    return "".join(parts)


# Aufkleber liegen minimal ueber ihrer Traegerflaeche im Stapel
DECAL_DEPTH_EPS_MM = 0.001


def _collect_shapes(data: dict) -> list[tuple]:
    """Flaechen und Aufkleber einsammeln, nach (z_max, z_min) sortieren.

    Eintraege: ((z_max, z_min), kind, payload) mit kind "face" | "decal".
    """
    shapes = []
    try:
        for body in data.get("bodies", []):
            for face in body.get("faces", []):
                shapes.append((
                    (face["z_max_mm"], face["z_min_mm"]),
                    "face",
                    (
                        body["name"], body["color"], face["loops"],
                        face.get("surface", ""), face.get("normal"),
                        face.get("ringCenter"), face.get("ringAxisD"),
                    ),
                ))
        for decal in data.get("decals", []):
            if not decal.get("dataUri") and not decal.get("vector"):
                continue  # Bild konnte nicht eingebettet werden
            depth = decal["depth_mm"] + DECAL_DEPTH_EPS_MM
            shapes.append(((depth, depth), "decal", decal))
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Extraktionsdaten unvollstaendig ({exc})") from exc
    return sorted(shapes, key=lambda shape: shape[0])


def _decal_markup(decal: dict, index: int, min_x: float, max_y: float) -> tuple[str, str]:
    """(defs-Eintrag, Element) fuer einen Aufkleber.

    Das Bild wird als Einheitsquadrat um den lokalen Ursprung gelegt
    (x/y in [-0.5, 0.5], Bild-y zeigt nach unten) und per Matrix auf die
    projizierten Basisvektoren des Decals abgebildet.
    """
    ou, ov = decal["origin"]
    ux, uy = decal["uAxis"]   # projizierte X-Basis (volle Breite, mm)
    vx, vy = decal["vAxis"]   # projizierte Y-Basis (volle Hoehe, mm)
    matrix = (ux, -uy, -vx, vy, ou - min_x, max_y - ov)
    matrix_text = " ".join(_fmt(value) for value in matrix)

    clip_def, clip_attr = "", ""
    if decal.get("clip"):
        clip_d = " ".join(
            _loop_to_path(lp["points"], min_x, max_y) for lp in decal["clip"]
        )
        clip_id = f"decal-clip-{index}"
        clip_def = (
            f'    <clipPath id="{clip_id}">'
            f'<path d="{clip_d}" clip-rule="evenodd"/></clipPath>'
        )
        clip_attr = f' clip-path="url(#{clip_id})"'

    vector = decal.get("vector")
    if vector:
        # Getracte Pfade liegen im Pixelraum [0..W]x[0..H] (y nach unten):
        # erst auf das Einheitsquadrat um den Ursprung bringen, dann Matrix.
        scale_x = 1.0 / vector["width"]
        scale_y = 1.0 / vector["height"]
        inner = (
            f'<g transform="matrix({matrix_text}) translate(-0.5 -0.5) '
            f'scale({scale_x:.10f} {scale_y:.10f})">{vector["content"]}</g>'
        )
    else:
        inner = (
            f'<image x="-0.5" y="-0.5" width="1" height="1" '
            f'preserveAspectRatio="none" transform="matrix({matrix_text})" '
            f'href="{decal["dataUri"]}"/>'
        )
    element = (
        f'  <g{clip_attr} opacity="{_fmt(decal.get("opacity", 1.0))}" '
        f'data-decal="{_xml_escape(str(decal.get("name", "")))}">'
        + inner
        + "</g>"
    )
    return clip_def, element


def build_svg(
    data: dict,
    seam_stroke_mm: float = DEFAULT_SEAM_STROKE_MM,
    cull_hidden: bool = True,
    fase_3d: bool = False,
    light_deg: float = 180.0,
    fase_strength: float = 50.0,
) -> dict:
    """Erzeugt das SVG-Dokument.

    Args:
        data: Ausgabe von fusion_extract.py (Koordinaten in mm).
        seam_stroke_mm: Breite des Naht-Strokes in mm (0 = aus).

    Returns:
        dict mit "svg" (Text), "width_mm", "height_mm" und "shapes"
        (Liste (z_max, z_min, body, color) in Zeichenreihenfolge).

    Raises:
        ValueError: wenn keine sichtbaren Flaechen vorhanden sind.
    """
    shapes = _collect_shapes(data)
    if cull_hidden and shapes:
        from occlusion import cull_hidden_shapes

        total = len(shapes)
        shapes, removed = cull_hidden_shapes(shapes)
        if removed:
            print(
                f"Verdeckungs-Analyse: {removed} von {total} Flaechen "
                "unsichtbar — entfernt"
            )
    all_points = [
        point
        for _, kind, payload in shapes
        if kind == "face"
        for loop in payload[2]
        for point in loop["points"]
    ]
    if not all_points:
        raise ValueError(
            "Keine von oben sichtbaren Flaechen gefunden — "
            "ist ein Design mit sichtbaren Koerpern aktiv?"
        )

    min_x = min(p[0] for p in all_points)
    max_x = max(p[0] for p in all_points)
    min_y = min(p[1] for p in all_points)
    max_y = max(p[1] for p in all_points)
    width, height = max_x - min_x, max_y - min_y

    defs, paths = [], []
    shaded_count = 0
    normals_present = False
    for index, ((z_max, z_min), kind, payload) in enumerate(shapes):
        if kind == "decal":
            clip_def, element = _decal_markup(payload, index, min_x, max_y)
            if clip_def:
                defs.append(clip_def)
            paths.append(element)
            continue
        body_name, color, loops, surface, normal, ring_center, ring_axis_d = payload
        if normal:
            normals_present = True
        gradient_paint = None
        if fase_3d and normal:
            if _is_ring_fase(surface, normal, ring_center, ring_axis_d):
                center_svg = (ring_center[0] - min_x, max_y - ring_center[1])
                definition, gradient_paint = _ring_gradient(
                    f"fase-grad-{index}", str(color), normal,
                    light_deg, fase_strength / 100.0,
                    center_svg, _ring_radius(loops, ring_center),
                )
                defs.append(definition)
                shaded_count += 1
            elif _is_fase(surface, normal):
                color = shade_fase_color(
                    str(color), normal, light_deg, fase_strength / 100.0
                )
                shaded_count += 1
        paint = gradient_paint or _xml_escape(str(color))
        path_d = " ".join(_loop_to_path(lp["points"], min_x, max_y) for lp in loops)
        paths.append(
            f'  <path d="{path_d}" fill="{paint}" fill-rule="evenodd" '
            + _seam_attrs(paint, seam_stroke_mm)
            + f'data-body="{_xml_escape(str(body_name))}" '
            + f'data-z-mm="{_fmt(z_min)}..{_fmt(z_max)}"/>'
        )

    if fase_3d:
        if shaded_count:
            print(f"3D-Fase: {shaded_count} Fasen schattiert "
                  f"(Licht {light_deg:.0f} Grad, Staerke {fase_strength:.0f} %)")
        elif not normals_present:
            print("Hinweis: Daten ohne Normalen — fuer 3D-Fase bitte einmal "
                  "neu 'Auslesen aus Fusion'.")

    document_name = _xml_escape(
        str(data.get("document", "Fusion Design")).replace("--", "- -")
    )
    view = _xml_escape(str(data.get("view", "top")))
    defs_block = "  <defs>\n" + "\n".join(defs) + "\n  </defs>\n" if defs else ""
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{_fmt(width)}mm" height="{_fmt(height)}mm" '
        f'viewBox="0 0 {_fmt(width)} {_fmt(height)}">\n'
        f"  <!-- {document_name} — Ansicht: {view}, "
        f"Flaechen von hinten nach vorn gestapelt -->\n"
        + defs_block
        + "\n".join(paths)
        + "\n</svg>\n"
    )

    def shape_label(kind, payload):
        if kind == "decal":
            return (f"Aufkleber: {payload.get('name')}", "-")
        return (payload[0], payload[1])

    return {
        "svg": svg,
        "width_mm": width,
        "height_mm": height,
        "shapes": [
            (z_max, z_min) + shape_label(kind, payload)
            for (z_max, z_min), kind, payload in shapes
        ],
    }
