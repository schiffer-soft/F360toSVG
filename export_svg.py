"""SVG-Export aus Fusion 360 — Draufsicht als gestapelte Farbflaechen.

Verbindet sich mit dem lokalen Fusion MCP Server, extrahiert aus dem
aktiven Design alle von oben (Z+) sichtbaren Flaechen und baut daraus
ein massstabsgetreues SVG (mm, 1:1): Flaechen werden von Z- nach Z+
uebereinander gezeichnet (Painter's Algorithm), Farben kommen aus den
Appearance-RGB-Werten der Koerper.

Verwendung:
    python export_svg.py                       # Ansicht aus Fusion-Kamera (auto)
    python export_svg.py --view top            # feste Draufsicht
    python export_svg.py --view front          # von Y- -> <Dokumentname>-front.svg
    python export_svg.py -o zeichnung.svg
    python export_svg.py --seam-mm 0           # ohne Naht-Stroke (masshaltig)
    python export_svg.py --dump-json faces.json  # Rohdaten zum Debuggen

Voraussetzungen: Fusion 360 laeuft, MCP Server aktiviert (Details: README.md).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import mimetypes
import re
import sys
from pathlib import Path

from fusion_mcp_client import DEFAULT_URL, FusionMcpClient, FusionMcpError
from svg_builder import DEFAULT_SEAM_STROKE_MM, build_svg

def app_dir() -> Path:
    """Arbeitsverzeichnis der Anwendung: neben der EXE (PyInstaller)
    bzw. der Skriptordner im normalen Python-Betrieb. Hier liegen
    veraenderliche Dateien wie color_overrides.json und Exporte."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(name: str) -> Path:
    """Eingebettete Ressource (gui.html, fusion_extract.py): PyInstaller
    entpackt sie nach sys._MEIPASS, sonst liegen sie im Skriptordner."""
    base = getattr(sys, "_MEIPASS", None)
    return (Path(base) if base else Path(__file__).resolve().parent) / name


SCRIPT_DIR = app_dir()
EXTRACT_SCRIPT = resource_path("fusion_extract.py")
INVALID_FILENAME_CHARS = r'<>:"/\|?*'
DEFAULT_TOL_MM = 0.01


class ExportError(RuntimeError):
    """Fachlicher Fehler im Exportablauf (fuer CLI und GUI)."""


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exportiert die Draufsicht des aktiven Fusion-360-Designs als SVG."
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Ziel-SVG (Standard: <Dokumentname>.svg im aktuellen Verzeichnis)",
    )
    parser.add_argument(
        "--view",
        choices=["auto", "top", "bottom", "front", "back", "left", "right"],
        default="auto",
        help="Blickrichtung: auto=aus der aktuellen Fusion-Kamera abgeleitet, "
             "top=von Z+, bottom=von Z-, front=von Y-, back=von Y+, "
             "right=von X+, left=von X- (Standard: auto)",
    )
    parser.add_argument(
        "--seam-mm", type=float, default=DEFAULT_SEAM_STROKE_MM,
        help=f"Breite des Naht-Strokes in mm, 0 = aus (Standard: {DEFAULT_SEAM_STROKE_MM})",
    )
    parser.add_argument(
        "--tol-mm", type=float, default=0.01,
        help="Kurven-Sampling-Toleranz in mm (Standard: 0.01)",
    )
    parser.add_argument(
        "--url", default=DEFAULT_URL,
        help=f"URL des Fusion MCP Servers (Standard: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--decal-opacity", type=float, default=None,
        help="Deckkraft aller Aufkleber überschreiben (0..1; "
             "Standard: Wert aus Fusion)",
    )
    parser.add_argument(
        "--trace-decals", action="store_true",
        help="Aufkleber-PNGs zu Vektorpfaden tracen (vtracer) statt als "
             "Rasterbild einzubetten — nur für Flachfarben-Grafiken sinnvoll",
    )
    parser.add_argument(
        "--dump-json", type=Path, default=None,
        help="Extrahierte Rohdaten zusätzlich als JSON speichern (Debug)",
    )
    parser.add_argument(
        "--texture-mode", choices=["color", "image", "vector"], default="color",
        help="Material-Texturen: color=Durchschnittsfarbe (Standard), "
             "image=als gekacheltes Bild einbetten, vector=Kachel tracen",
    )
    parser.add_argument(
        "--texture-colors", type=int, default=4,
        help="Farbstufen beim Textur-Tracen (2..16, Standard: 4)",
    )
    parser.add_argument(
        "--texture-recolor", choices=["original", "palette"], default="original",
        help="Texturfarben: original=aus der Bilddatei, "
             "palette=mit der (ggf. überschriebenen) Körperfarbe einfärben",
    )
    parser.add_argument(
        "--texture-scale", type=float, default=100.0,
        help="Kachelgröße in Prozent der Fusion-Größe (Standard: 100)",
    )
    parser.add_argument(
        "--texture-brightness", type=float, default=0.0,
        help="Textur-Helligkeit in Prozent, -100..+100 (Standard: 0)",
    )
    return parser.parse_args(argv)


def _substitute_constant(script: str, pattern: str, replacement: str) -> str:
    """Konstante im Extraktionsskript ersetzen; schlaegt hart fehl statt still."""
    result, count = re.subn(pattern, replacement, script, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(
            f"Platzhalter '{pattern}' nicht in fusion_extract.py gefunden — "
            "wurde die Vorlage umformatiert?"
        )
    return result


def load_extract_script(tolerance_mm: float, view: str) -> str:
    """Extraktionsskript laden, Sampling-Toleranz und Ansicht einsetzen."""
    if not EXTRACT_SCRIPT.is_file():
        raise FileNotFoundError(f"Extraktionsskript fehlt: {EXTRACT_SCRIPT}")
    script = EXTRACT_SCRIPT.read_text(encoding="utf-8")
    if not math.isfinite(tolerance_mm) or tolerance_mm <= 0:
        raise ValueError("--tol-mm muss eine endliche Zahl größer 0 sein.")
    script = _substitute_constant(
        script,
        r"^STROKE_TOL_CM = [0-9.]+",
        f"STROKE_TOL_CM = {tolerance_mm / 10.0}",
    )
    return _substitute_constant(
        script,
        r'^VIEW = "[a-z]+"',
        f'VIEW = "{view}"',
    )


TEXTURE_SAMPLE_SIZE = 256  # Kantenlaenge, auf die Texturen vorm Mitteln verkleinert werden


def average_texture_color(texture: dict) -> str | None:
    """Durchschnittsfarbe einer Textur-Bilddatei, inkl. Appearance-Faktoren."""
    try:
        from PIL import Image, ImageStat
    except ImportError:
        print(
            "Warnung: Pillow fehlt — Textur-Durchschnitt nicht möglich "
            "(pip install pillow).",
            file=sys.stderr,
        )
        return None
    path = Path(str(texture.get("file", "")))
    if not path.is_file():
        print(f"Warnung: Texturdatei nicht gefunden: {path}", file=sys.stderr)
        return None
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((TEXTURE_SAMPLE_SIZE, TEXTURE_SAMPLE_SIZE))
            means = ImageStat.Stat(image).mean  # [R, G, B]
    except OSError as exc:
        print(f"Warnung: Textur nicht lesbar ({path.name}): {exc}", file=sys.stderr)
        return None
    if texture.get("invert"):
        means = [255.0 - value for value in means]
    factors = texture.get("factors") or [1.0, 1.0, 1.0]
    rgb = [
        max(0, min(255, round(mean * factor)))
        for mean, factor in zip(means, factors)
    ]
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def resolve_texture_colors(data: dict) -> None:
    """Ersetzt Koerperfarben durch den Durchschnittswert ihrer Textur."""
    for body in data.get("bodies", []):
        texture = body.get("texture")
        if not texture:
            continue
        average = average_texture_color(texture)
        if average:
            source = Path(str(texture.get("file", "?"))).name
            print(f"Textur-Durchschnitt für '{body.get('name')}': {average} ({source})")
            body["color"] = average


# --- Textur-Kacheln (Modi "Bild" / "Vektorisiert") ---------------------------

TEXTURE_MODES = ("color", "image", "vector")
TEXTURE_RECOLOR_MODES = ("original", "palette")
TEXTURE_TILE_MAX_PX = 512   # Kachel-Bitmap wird vorm Einbetten/Tracen begrenzt
TEXTURE_MIN_COLORS = 2
TEXTURE_MAX_COLORS = 16

# Aufbereitete Kacheln sind teuer (v. a. Tracen) und aendern sich zwischen
# Live-Rebuilds nicht — Cache ueber finalize-Aufrufe hinweg (pro Prozess).
_TEXTURE_TILE_CACHE: dict[tuple, dict | None] = {}


def _prepare_tile_image(texture: dict, tint: str | None,
                        brightness_pct: float = 0.0):
    """Kachel-Bitmap laden und aufbereiten; None bei Fehlern.

    Vertikal gespiegelt (Fusion-V zeigt nach oben, SVG-Muster-y nach
    unten). Ohne Tint werden Invert + Helligkeits-Faktoren der Appearance
    angewendet; mit Tint wird die Kachel grau-normiert und mit der
    Koerperfarbe eingefaerbt (Mittelwert = Tint — die Faktoren stecken
    schon in der Durchschnittsfarbe und kuerzen sich dabei heraus).
    brightness_pct (-100..+100) hellt zum Schluss multiplikativ auf/ab.
    """
    try:
        from PIL import Image, ImageOps, ImageStat
    except ImportError:
        print(
            "Warnung: Pillow fehlt — Textur-Kacheln nicht möglich "
            "(pip install pillow).",
            file=sys.stderr,
        )
        return None
    path = Path(str(texture.get("file", "")))
    if not path.is_file():
        print(f"Warnung: Texturdatei nicht gefunden: {path}", file=sys.stderr)
        return None
    try:
        with Image.open(path) as source:
            image = source.convert("RGB")
    except OSError as exc:
        print(f"Warnung: Textur nicht lesbar ({path.name}): {exc}", file=sys.stderr)
        return None
    image.thumbnail((TEXTURE_TILE_MAX_PX, TEXTURE_TILE_MAX_PX))
    image = image.transpose(Image.FLIP_TOP_BOTTOM)
    if texture.get("invert"):
        image = ImageOps.invert(image)
    if tint:
        try:
            tint_rgb = [int(tint[i:i + 2], 16) for i in (1, 3, 5)]
        except ValueError:
            tint_rgb = [128, 128, 128]
        gray = image.convert("L")
        mean = ImageStat.Stat(gray).mean[0] or 1.0
        channels = [
            gray.point(lambda v, c=c: max(0, min(255, round(v / mean * c))))
            for c in tint_rgb
        ]
        image = Image.merge("RGB", channels)
    else:
        factors = texture.get("factors") or [1.0, 1.0, 1.0]
        channels = [
            band.point(lambda v, f=f: max(0, min(255, round(v * f))))
            for band, f in zip(image.split(), factors)
        ]
        image = Image.merge("RGB", channels)
    if brightness_pct:
        factor = max(0.0, 1.0 + brightness_pct / 100.0)
        image = image.point(lambda v: max(0, min(255, round(v * factor))))
    return image


def _parse_traced_svg(svg_text: str) -> dict | None:
    """Zerlegt ein vtracer-SVG in width/height/Inhalt."""
    match = re.search(
        r'<svg[^>]*\bwidth="(\d+)"[^>]*\bheight="(\d+)"[^>]*>(.*)</svg>',
        svg_text, re.DOTALL,
    )
    if not match:
        return None
    content = match.group(3).strip()
    return {
        "width": int(match.group(1)),
        "height": int(match.group(2)),
        "content": content,
        "pathCount": content.count("<path"),
    }


def _trace_tile_vector(image, colors: int) -> dict | None:
    """Kachel auf wenige Farbstufen reduzieren und farbig tracen."""
    try:
        import vtracer
    except ImportError:
        print(
            "Warnung: vtracer fehlt — Textur wird als Bild eingebettet "
            "(pip install vtracer).",
            file=sys.stderr,
        )
        return None
    import tempfile

    levels = max(TEXTURE_MIN_COLORS, min(TEXTURE_MAX_COLORS, colors))
    posterized = image.quantize(colors=levels).convert("RGB")
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        in_path = tmp_dir / "tile.png"
        out_path = tmp_dir / "tile.svg"
        posterized.save(in_path)
        try:
            vtracer.convert_image_to_svg_py(
                str(in_path), str(out_path),
                colormode="color", mode="spline",
                filter_speckle=2, color_precision=8, layer_difference=8,
                corner_threshold=30, length_threshold=3.5,
                splice_threshold=30, path_precision=TRACE_PATH_PRECISION,
            )
            svg_text = out_path.read_text(encoding="utf-8")
        except Exception as exc:  # vtracer wirft generische Fehler
            print(f"Warnung: Textur-Tracing fehlgeschlagen: {exc}", file=sys.stderr)
            return None
    vector = _parse_traced_svg(svg_text)
    if vector is None:
        print("Warnung: Unerwartetes vtracer-Ergebnis für Textur-Kachel",
              file=sys.stderr)
    return vector


def _build_texture_tile(texture: dict, tint: str | None, mode: str,
                        colors: int, brightness_pct: float) -> dict | None:
    image = _prepare_tile_image(texture, tint, brightness_pct)
    if image is None:
        return None
    tile: dict = {"px": list(image.size)}
    if mode == "vector":
        vector = _trace_tile_vector(image, colors)
        if vector:
            tile["vector"] = vector
            return tile
        # Tracen fehlgeschlagen -> wenigstens als Bild einbetten
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    tile["dataUri"] = f"data:image/png;base64,{encoded}"
    return tile


def resolve_texture_tiles(data: dict, mode: str, colors: int,
                          recolor: str, scale_pct: float = 100.0,
                          brightness_pct: float = 0.0) -> None:
    """Haengt Koerpern mit Textur eine Fuellmuster-Kachel an ("textureTile").

    Die Kachel enthaelt den Inhalt (dataUri oder Vektorpfade) plus
    Platzierung in mm (Kachelgroesse, Anker, Versatz, Drehung) — der
    SVG-Builder macht daraus ein <pattern>. Modus "color" laesst alles
    bei der Durchschnittsfarbe. scale_pct skaliert nur die Kachelgroesse
    (100 = wie in Fusion) und braucht daher keinen neuen Kachel-Inhalt;
    brightness_pct steckt im Inhalt und damit im Cache-Schluessel.
    """
    if mode == "color":
        return
    for body in data.get("bodies", []):
        texture = body.get("texture")
        if not texture:
            continue
        tint = str(body.get("color")) if recolor == "palette" else None
        key = (
            str(texture.get("file")), tuple(texture.get("factors") or ()),
            bool(texture.get("invert")), mode,
            colors if mode == "vector" else None, tint, brightness_pct,
        )
        if key not in _TEXTURE_TILE_CACHE:
            _TEXTURE_TILE_CACHE[key] = _build_texture_tile(
                texture, tint, mode, colors, brightness_pct
            )
        tile = _TEXTURE_TILE_CACHE[key]
        if tile is None:
            continue  # Fallback: Durchschnittsfarbe bleibt stehen
        factor = max(0.01, scale_pct / 100.0)
        base_scale = texture.get("scale_mm") or [0.0, 0.0]
        scale = [base_scale[0] * factor, base_scale[1] * factor]
        body["textureTile"] = {
            **tile,
            "tile_mm": scale,
            "anchor": texture.get("anchor"),
            "offset_mm": texture.get("offset_mm") or [0.0, 0.0],
            "angle_deg": float(texture.get("angle_deg") or 0.0),
        }
        kind = "vektorisiert" if "vector" in tile else "als Bild eingebettet"
        detail = (
            f", {tile['vector']['pathCount']} Pfade" if "vector" in tile else ""
        )
        print(
            f"Textur für '{body.get('name')}' {kind} "
            f"(Kachel {scale[0]:.1f} x {scale[1]:.1f} mm{detail})"
        )


TRACE_PATH_PRECISION = 2   # Nachkommastellen der Pfadkoordinaten (Pixelraum)
TRACE_ALPHA_THRESHOLD = 48  # Alpha-Schwellwert der Binarisierung (0..255)
# Weichzeichnung = Bildbreite * Ratio (in px). 0 = aus (Standard):
# ein feGaussianBlur wuerde vom Browser GERASTERT — beim Zoomen wird
# der eigentlich vektorisierte Aufkleber dann wieder unscharf.
TRACE_BLUR_RATIO = 0.0


def _binarize_alpha(path: Path, tmp_dir: Path) -> Path:
    """Weiche Alpha-Kanten hart machen — Tracer-Futter ohne Verlaufsstufen.

    Weiche Verlaeufe wuerden vom Tracer in eckige Farbstufen-Kleckse
    zerlegt; nach der Binarisierung entstehen saubere Konturen, die
    Weichheit bringt spaeter ein SVG-Blur-Filter zurueck.
    """
    try:
        from PIL import Image
    except ImportError:
        print(
            "Warnung: Pillow fehlt — Tracing ohne Binarisierung "
            "(weiche Kanten werden fleckig).",
            file=sys.stderr,
        )
        return path
    try:
        with Image.open(path) as image:
            image = image.convert("RGBA")
            r, g, b, alpha = image.split()
            hard = alpha.point(
                lambda v: 255 if v >= TRACE_ALPHA_THRESHOLD else 0
            )
            out_path = tmp_dir / "binarized.png"
            Image.merge("RGBA", (r, g, b, hard)).save(out_path)
            return out_path
    except OSError as exc:
        print(f"Warnung: Binarisierung fehlgeschlagen ({path.name}): {exc}",
              file=sys.stderr)
        return path


def trace_decal_vector(path: Path, index: int) -> dict | None:
    """PNG per vtracer zu Vektorpfaden tracen; None bei Fehlschlag.

    Pipeline: Alpha binarisieren -> tracen -> Blur-Filter fuer weiche
    Kanten. Liefert Pfade im Pixelraum des Originalbilds.
    """
    try:
        import vtracer
    except ImportError:
        print(
            "Warnung: vtracer fehlt — Aufkleber wird als PNG eingebettet "
            "(pip install vtracer).",
            file=sys.stderr,
        )
        return None
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        trace_input = _binarize_alpha(path, tmp_dir)
        out_path = tmp_dir / "traced.svg"
        try:
            vtracer.convert_image_to_svg_py(
                str(trace_input), str(out_path),
                colormode="color", mode="spline",
                filter_speckle=2, color_precision=8, layer_difference=8,
                corner_threshold=30, length_threshold=3.5,
                splice_threshold=30, path_precision=TRACE_PATH_PRECISION,
            )
            svg_text = out_path.read_text(encoding="utf-8")
        except Exception as exc:  # vtracer wirft generische Fehler
            print(f"Warnung: Tracing fehlgeschlagen ({path.name}): {exc}",
                  file=sys.stderr)
            return None
    vector = _parse_traced_svg(svg_text)
    if vector is None:
        print(f"Warnung: Unerwartetes vtracer-Ergebnis für {path.name}",
              file=sys.stderr)
        return None
    blur_px = vector["width"] * TRACE_BLUR_RATIO
    if blur_px > 0:  # optional — rastert beim Rendern, macht Zoom unscharf
        filter_id = f"decal-soft-{index}"
        vector["content"] = (
            f'<filter id="{filter_id}">'
            f'<feGaussianBlur stdDeviation="{blur_px:.2f}"/></filter>'
            f'<g filter="url(#{filter_id})">{vector["content"]}</g>'
        )
    return vector


def resolve_decal_images(
    data: dict,
    opacity_override: float | None = None,
    trace: bool = False,
) -> None:
    """Bettet Decal-Bilder ein — als Vektorpfade (trace) oder data-URI."""
    kept = []
    for index, decal in enumerate(data.get("decals", [])):
        if opacity_override is not None:
            decal["opacity"] = opacity_override
        path = Path(str(decal.get("file", "")))
        if not path.is_file():
            print(f"Warnung: Aufkleber-Bild nicht gefunden: {path}", file=sys.stderr)
            continue
        opacity_note = f"Deckkraft {decal.get('opacity', 1.0):.0%}"
        if trace:
            vector = trace_decal_vector(path, index)
            if vector:
                decal["vector"] = vector
                kept.append(decal)
                print(
                    f"Aufkleber '{decal.get('name')}' vektorisiert "
                    f"({path.name}, {vector['pathCount']} Pfade, {opacity_note})"
                )
                continue  # sonst Fallback auf PNG-Einbettung
        try:
            raw = path.read_bytes()
        except OSError as exc:
            print(f"Warnung: Aufkleber-Bild nicht lesbar ({path.name}): {exc}",
                  file=sys.stderr)
            continue
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        decal["dataUri"] = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        kept.append(decal)
        print(
            f"Aufkleber '{decal.get('name')}' eingebettet "
            f"({path.name}, {len(raw) // 1024} KB, {opacity_note})"
        )
    data["decals"] = kept


def default_output_path(document_name: str, view: str) -> Path:
    safe_name = "".join(
        "_" if ch in INVALID_FILENAME_CHARS else ch for ch in document_name
    ).strip() or "fusion-export"
    suffix = "" if view == "top" else f"-{view}"
    return Path.cwd() / f"{safe_name}{suffix}.svg"


def extract_data(
    *,
    view: str = "auto",
    tol_mm: float = DEFAULT_TOL_MM,
    url: str = DEFAULT_URL,
) -> dict:
    """Stufe 1: Flaechendaten aus Fusion holen (der teure Teil).

    Das Ergebnis ist options-unabhaengig (inkl. Textur-Durchschnitten)
    und kann fuer schnelle Neuaufbauten gecacht werden.
    """
    try:
        script = load_extract_script(tol_mm, view)
    except (FileNotFoundError, ValueError) as exc:
        raise ExportError(str(exc)) from exc

    client = FusionMcpClient(url)
    client.connect()
    print(
        f"Verbunden mit Fusion MCP Server ({url}), "
        f"extrahiere Flächen (Ansicht: {view}) ..."
    )
    output_text = client.run_fusion_script(script)

    try:
        data = json.loads(output_text)
    except ValueError as exc:
        raise ExportError(
            f"Unerwartete Skriptausgabe:\n{output_text[:500]}"
        ) from exc
    if "error" in data:
        raise ExportError(str(data["error"]))

    # Grosse Ergebnisse kommen als Temp-Datei (siehe fusion_extract.py).
    if "resultFile" in data:
        result_file = Path(data["resultFile"])
        try:
            data = json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ExportError(
                f"Ergebnisdatei {result_file} nicht lesbar: {exc}"
            ) from exc
        try:
            result_file.unlink()
        except OSError:
            pass  # Aufraeumen ist optional, naechster Export ueberschreibt

    if view == "auto":
        print(f"Ansicht aus Fusion-Kamera abgeleitet: {data.get('view')}")

    resolve_texture_colors(data)
    return data


def finalize_svg(
    data: dict,
    *,
    seam_mm: float = DEFAULT_SEAM_STROKE_MM,
    decal_opacity: float | None = None,
    trace_decals: bool = False,
    color_overrides: dict | None = None,
    output: str | Path | None = None,
    dump_json: str | Path | None = None,
    write_file: bool = True,
    cull_hidden: bool = True,
    fase_3d: bool = False,
    light_deg: float = 180.0,
    fase_strength: float = 50.0,
    texture_mode: str = "color",
    texture_colors: int = 4,
    texture_recolor: str = "original",
    texture_scale: float = 100.0,
    texture_brightness: float = 0.0,
) -> dict:
    """Stufe 2: SVG aus (ggf. gecachten) Daten bauen und schreiben.

    Arbeitet auf einer Kopie — dieselben Daten koennen beliebig oft
    mit anderen Optionen (Farben, Deckkraft, Naht) neu gebaut werden.
    """
    if decal_opacity is not None and not (0.0 <= decal_opacity <= 1.0):
        raise ExportError("Aufkleber-Deckkraft muss zwischen 0 und 1 liegen.")
    if texture_mode not in TEXTURE_MODES:
        raise ExportError(f"Unbekannter Textur-Modus: {texture_mode}")
    if texture_recolor not in TEXTURE_RECOLOR_MODES:
        raise ExportError(f"Unbekannter Texturfarben-Modus: {texture_recolor}")

    work = json.loads(json.dumps(data))  # resolve_* mutiert -> frische Kopie
    if color_overrides:
        for body in work.get("bodies", []):
            new_color = color_overrides.get(body.get("color"))
            if new_color:
                body["color"] = new_color

    # Nach den Farb-Overrides: der Palette-Tint nutzt die finale Farbe
    resolve_texture_tiles(
        work, mode=texture_mode, colors=int(texture_colors),
        recolor=texture_recolor, scale_pct=float(texture_scale),
        brightness_pct=float(texture_brightness),
    )
    resolve_decal_images(
        work, opacity_override=decal_opacity, trace=trace_decals
    )

    resolved_view = work.get("view", "top")
    try:
        if dump_json:
            Path(dump_json).write_text(
                json.dumps(work, indent=1, ensure_ascii=False), encoding="utf-8"
            )
            print(f"Rohdaten gespeichert: {dump_json}")

        result = build_svg(
            work, seam_stroke_mm=seam_mm, cull_hidden=cull_hidden,
            fase_3d=fase_3d, light_deg=light_deg, fase_strength=fase_strength,
        )
        output_path = Path(output) if output else default_output_path(
            work.get("document", "fusion-export"), resolved_view
        )
        if write_file:
            output_path.write_text(result["svg"], encoding="utf-8")
    except ValueError as exc:
        raise ExportError(str(exc)) from exc
    except OSError as exc:
        raise ExportError(f"Fehler beim Schreiben: {exc}") from exc

    if write_file:
        print(
            f"OK: {output_path} — {len(result['shapes'])} Flächen, "
            f"{result['width_mm']:.1f} x {result['height_mm']:.1f} mm, "
            f"Ansicht: {resolved_view}"
        )
        for z_max, z_min, name, color in result["shapes"]:
            print(f"  z={z_min:7.2f}..{z_max:7.2f}  {color}  {name}")
    else:
        print(
            f"Vorschau: {len(result['shapes'])} Flächen, "
            f"{result['width_mm']:.1f} x {result['height_mm']:.1f} mm, "
            f"Ansicht: {resolved_view} (nichts gespeichert)"
        )

    return {
        "path": str(output_path) if write_file else None,
        "view": resolved_view,
        "document": work.get("document"),
        "width_mm": result["width_mm"],
        "height_mm": result["height_mm"],
        "shapeCount": len(result["shapes"]),
        "svg": result["svg"],
    }


def run_export(
    *,
    view: str = "auto",
    seam_mm: float = DEFAULT_SEAM_STROKE_MM,
    tol_mm: float = DEFAULT_TOL_MM,
    url: str = DEFAULT_URL,
    output: str | Path | None = None,
    decal_opacity: float | None = None,
    trace_decals: bool = False,
    dump_json: str | Path | None = None,
    texture_mode: str = "color",
    texture_colors: int = 4,
    texture_recolor: str = "original",
    texture_scale: float = 100.0,
    texture_brightness: float = 0.0,
) -> dict:
    """Kompletter Exportablauf — gemeinsamer Kern fuer CLI und GUI."""
    data = extract_data(view=view, tol_mm=tol_mm, url=url)
    return finalize_svg(
        data,
        seam_mm=seam_mm,
        decal_opacity=decal_opacity,
        trace_decals=trace_decals,
        output=output,
        dump_json=dump_json,
        texture_mode=texture_mode,
        texture_colors=texture_colors,
        texture_recolor=texture_recolor,
        texture_scale=texture_scale,
        texture_brightness=texture_brightness,
    )


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        run_export(
            view=args.view,
            seam_mm=args.seam_mm,
            tol_mm=args.tol_mm,
            url=args.url,
            output=args.output,
            decal_opacity=args.decal_opacity,
            trace_decals=args.trace_decals,
            dump_json=args.dump_json,
            texture_mode=args.texture_mode,
            texture_colors=args.texture_colors,
            texture_recolor=args.texture_recolor,
            texture_scale=args.texture_scale,
            texture_brightness=args.texture_brightness,
        )
    except (ExportError, FusionMcpError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
