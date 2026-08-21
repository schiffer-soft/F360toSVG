"""Konvertiert das exportierte SVG in andere Formate.

PNG/JPG: Rasterung ueber Edge headless (Screenshot in Zielaufloesung).
PDF/AI:  Vektor-PDF ueber Edge headless (--print-to-pdf) — Pfade bleiben
         Vektoren, eingebettete Bilder bleiben Raster. Eine .ai-Datei
         ist ein PDF-kompatibles Format: das erzeugte Vektor-PDF mit
         .ai-Endung oeffnet Adobe Illustrator direkt als editierbare
         Vektordatei.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

EXPORT_DPI = 300
JPG_QUALITY = 92
EDGE_TIMEOUT_S = 120
FORMATS = ("png", "jpg", "pdf", "ai")

EDGE_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]


class ConvertError(RuntimeError):
    """Konvertierung fehlgeschlagen (Edge fehlt, SVG unlesbar, ...)."""


def _find_edge() -> str:
    for candidate in EDGE_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    raise ConvertError(
        "Microsoft Edge nicht gefunden — wird für PNG/JPG/PDF/AI-Export benötigt."
    )


def _svg_size_mm(svg_text: str) -> tuple[float, float]:
    match = re.search(
        r'<svg[^>]*\bwidth="([0-9.]+)mm"[^>]*\bheight="([0-9.]+)mm"', svg_text
    )
    if not match:
        raise ConvertError("SVG-Größe (mm) nicht lesbar.")
    return float(match.group(1)), float(match.group(2))


def _run_edge(args: list[str]) -> None:
    result = subprocess.run(
        [_find_edge(), "--headless", "--disable-gpu", *args],
        capture_output=True, timeout=EDGE_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise ConvertError(
            f"Edge-Aufruf fehlgeschlagen (Code {result.returncode}): "
            f"{result.stderr.decode(errors='replace')[:300]}"
        )


def _raster(svg_text: str, width_mm: float, height_mm: float,
            fmt: str, out_path: Path, dpi: int) -> None:
    px_w = max(1, round(width_mm / 25.4 * dpi))
    px_h = max(1, round(height_mm / 25.4 * dpi))
    background = "00000000" if fmt == "png" else "FFFFFFFF"  # PNG transparent
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        page = tmp / "page.html"
        page.write_text(
            '<!doctype html><meta charset="utf-8"><style>'
            "html,body{margin:0;padding:0;overflow:hidden}"
            "svg{display:block;width:100vw;height:100vh}"
            f"</style>{svg_text}",
            encoding="utf-8",
        )
        shot = tmp / "shot.png"
        _run_edge([
            f"--screenshot={shot}",
            f"--window-size={px_w},{px_h}",
            f"--default-background-color={background}",
            "--hide-scrollbars",
            page.as_uri(),
        ])
        if not shot.is_file():
            raise ConvertError("Edge hat kein Bild erzeugt.")
        if fmt == "png":
            shutil.copyfile(shot, out_path)
        else:
            try:
                from PIL import Image
            except ImportError as exc:
                raise ConvertError(
                    "Pillow fehlt für JPG-Export (pip install pillow)."
                ) from exc
            with Image.open(shot) as image:
                image.convert("RGB").save(out_path, quality=JPG_QUALITY)


def _vector_pdf(svg_text: str, width_mm: float, height_mm: float,
                out_path: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        page = tmp / "page.html"
        page.write_text(
            '<!doctype html><meta charset="utf-8"><style>'
            f"@page{{size:{width_mm}mm {height_mm}mm;margin:0}}"
            "html,body{margin:0;padding:0}"
            f"svg{{display:block;width:{width_mm}mm;height:{height_mm}mm}}"
            f"</style>{svg_text}",
            encoding="utf-8",
        )
        pdf = tmp / "out.pdf"
        _run_edge([f"--print-to-pdf={pdf}", "--no-pdf-header-footer", page.as_uri()])
        if not pdf.is_file():
            raise ConvertError("Edge hat kein PDF erzeugt.")
        shutil.copyfile(pdf, out_path)


def convert_svg_file(svg_path: Path, fmt: str, dpi: int = EXPORT_DPI) -> Path:
    """Konvertiert eine SVG-Datei; Zieldatei = gleicher Name, neue Endung."""
    fmt = fmt.lower().lstrip(".")
    if fmt not in FORMATS:
        raise ConvertError(f"Unbekanntes Format: {fmt} (möglich: {', '.join(FORMATS)})")
    svg_text = svg_path.read_text(encoding="utf-8")
    width_mm, height_mm = _svg_size_mm(svg_text)
    out_path = svg_path.with_suffix("." + fmt)
    if fmt in ("png", "jpg"):
        _raster(svg_text, width_mm, height_mm, fmt, out_path, dpi)
    else:
        _vector_pdf(svg_text, width_mm, height_mm, out_path)
    return out_path
