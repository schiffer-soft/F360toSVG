"""Prueft vor dem PyInstaller-Build, ob alle Pakete da sind.

Hintergrund: Fehlt beim Bauen z. B. vtracer, dann landet es stillschweigend
nicht in der EXE. Die laeuft trotzdem — nur "Vektorisiert" bleibt tot, und
das faellt erst beim Nutzer auf. Deshalb bricht der Build hier lieber ab.

Aufruf (macht build_exe.bat automatisch):

    py -3 check_build_env.py

Rueckgabewert 0 = alles da, 1 = mindestens ein Paket fehlt.
Abweichende Versionen sind nur ein Hinweis, kein Abbruch — sie machen den
Build unreproduzierbar, aber nicht kaputt.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Verteilungsname (so steht er in requirements.txt) -> Importname
IMPORT_NAMES = {
    "pywebview": "webview",
    "Pillow": "PIL",
    "pyinstaller": "PyInstaller",
}


def read_requirements(path: Path, seen: set[Path] | None = None) -> list[tuple[str, str]]:
    """Liefert [(Paket, erwartete Version)] und folgt "-r andere.txt"."""
    seen = seen if seen is not None else set()
    path = path.resolve()
    if path in seen or not path.is_file():
        return []
    seen.add(path)

    entries: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            entries += read_requirements(path.parent / line[3:].strip(), seen)
            continue
        match = re.match(r"^([A-Za-z0-9._-]+)==([^\s]+)$", line)
        if match:
            entries.append((match.group(1), match.group(2)))
        else:
            print(f"  ? Zeile nicht verstanden: {raw.strip()}")
    return entries


def main() -> int:
    entries = read_requirements(HERE / "requirements-dev.txt")
    if not entries:
        print("FEHLER: requirements-dev.txt fehlt oder ist leer.")
        return 1

    missing: list[str] = []
    mismatched: list[str] = []
    width = max(len(name) for name, _ in entries)

    for name, wanted in entries:
        module = IMPORT_NAMES.get(name, name)
        try:
            importlib.import_module(module)
        except Exception as exc:  # ImportError, DLL-Fehler, kaputte Installation
            missing.append(name)
            print(f"  FEHLT   {name:<{width}}  ({type(exc).__name__}: {exc})")
            continue
        try:
            found = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            found = "?"
        if found == wanted:
            print(f"  ok      {name:<{width}}  {found}")
        else:
            mismatched.append(f"{name} {found} statt {wanted}")
            print(f"  ABWEICH {name:<{width}}  {found}  (erwartet {wanted})")

    print()
    if missing:
        print("BUILD ABGEBROCHEN: " + ", ".join(missing) + " fehlt/fehlen.")
        print("  py -3 -m pip install -r requirements-dev.txt")
        return 1
    if mismatched:
        print("Hinweis: " + "; ".join(mismatched) + ".")
        print("Der Build laeuft, ist aber nicht der festgeschriebene Stand.")
    print(f"Alle {len(entries)} Pakete vorhanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
