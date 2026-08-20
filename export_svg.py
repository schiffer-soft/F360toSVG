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
import json
import math
import mimetypes
import re
import sys
from pathlib import Path

from fusion_mcp_client import DEFAULT_URL, FusionMcpClient, FusionMcpError
from svg_builder import DEFAULT_SEAM_STROKE_MM, build_svg

SCRIPT_DIR = Path(__file__).resolve().parent
EXTRACT_SCRIPT = SCRIPT_DIR / "fusion_extract.py"
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
        help="Deckkraft aller Aufkleber ueberschreiben (0..1; "
             "Standard: Wert aus Fusion)",
    )
    parser.add_argument(
        "--trace-decals", action="store_true",
        help="Aufkleber-PNGs zu Vektorpfaden tracen (vtracer) statt als "
             "Rasterbild einzubetten — nur fuer Flachfarben-Grafiken sinnvoll",
    )
    parser.add_argument(
        "--dump-json", type=Path, default=None,
        help="Extrahierte Rohdaten zusaetzlich als JSON speichern (Debug)",
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
        raise ValueError("--tol-mm muss eine endliche Zahl groesser 0 sein.")
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
            "Warnung: Pillow fehlt — Textur-Durchschnitt nicht moeglich "
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
            print(f"Textur-Durchschnitt fuer '{body.get('name')}': {average} ({source})")
            body["color"] = average


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
    match = re.search(
        r'<svg[^>]*\bwidth="(\d+)"[^>]*\bheight="(\d+)"[^>]*>(.*)</svg>',
        svg_text, re.DOTALL,
    )
    if not match:
        print(f"Warnung: Unerwartetes vtracer-Ergebnis fuer {path.name}",
              file=sys.stderr)
        return None
    width = int(match.group(1))
    blur_px = width * TRACE_BLUR_RATIO
    content = match.group(3).strip()
    if blur_px > 0:  # optional — rastert beim Rendern, macht Zoom unscharf
        filter_id = f"decal-soft-{index}"
        content = (
            f'<filter id="{filter_id}">'
            f'<feGaussianBlur stdDeviation="{blur_px:.2f}"/></filter>'
            f'<g filter="url(#{filter_id})">{content}</g>'
        )
    return {
        "width": width,
        "height": int(match.group(2)),
        "content": content,
        "pathCount": content.count("<path"),
    }


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
        f"extrahiere Flaechen (Ansicht: {view}) ..."
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
) -> dict:
    """Stufe 2: SVG aus (ggf. gecachten) Daten bauen und schreiben.

    Arbeitet auf einer Kopie — dieselben Daten koennen beliebig oft
    mit anderen Optionen (Farben, Deckkraft, Naht) neu gebaut werden.
    """
    if decal_opacity is not None and not (0.0 <= decal_opacity <= 1.0):
        raise ExportError("Aufkleber-Deckkraft muss zwischen 0 und 1 liegen.")

    work = json.loads(json.dumps(data))  # resolve_* mutiert -> frische Kopie
    if color_overrides:
        for body in work.get("bodies", []):
            new_color = color_overrides.get(body.get("color"))
            if new_color:
                body["color"] = new_color

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
            f"OK: {output_path} — {len(result['shapes'])} Flaechen, "
            f"{result['width_mm']:.1f} x {result['height_mm']:.1f} mm, "
            f"Ansicht: {resolved_view}"
        )
        for z_max, z_min, name, color in result["shapes"]:
            print(f"  z={z_min:7.2f}..{z_max:7.2f}  {color}  {name}")
    else:
        print(
            f"Vorschau: {len(result['shapes'])} Flaechen, "
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
        )
    except (ExportError, FusionMcpError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
