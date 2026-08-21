"""GUI fuer den Fusion 360 -> SVG Export (pywebview).

Backend: stellt dem HTML-Frontend (gui.html) eine JS-API bereit.
Die Optionen sind schema-getrieben: OPTION_SCHEMA beschreibt jedes
Eingabefeld, das Frontend baut das Formular daraus automatisch.
Eine neue Option braucht nur einen Schema-Eintrag plus den passenden
Parameter in export_svg.run_export() — die IDs muessen uebereinstimmen.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

from export_svg import (
    DEFAULT_SEAM_STROKE_MM,
    DEFAULT_TOL_MM,
    ExportError,
    app_dir,
    default_output_path,
    extract_data,
    finalize_svg,
    resource_path,
)
import i18n
from fusion_mcp_client import DEFAULT_URL, FusionMcpClient, FusionMcpError
from i18n import t

# Veraenderliches (Profile, Exporte) liegt neben EXE/Skript,
# eingebettete Ressourcen (gui.html) kommen aus dem PyInstaller-Bundle
SCRIPT_DIR = app_dir()
GUI_HTML = resource_path("gui.html")
WINDOW_TITLE = "Fusion 360 → SVG"
APP_VERSION = "1.0.3"  # erscheint links in der Footerleiste
GITHUB_REPO = "schiffer-soft/F360toSVG"


def data_dir() -> Path:
    """Verzeichnis für Nutzerdaten: %APPDATA%\\F360toSVG.

    Bewusst NICHT neben der EXE — dort ist je nach Ablageort kein
    Schreibrecht (Programme, Netzlaufwerk), Cloud-Ordner synchronisieren
    jede Änderung mit, und beim Wechsel auf eine neue Programmversion
    wären die Profile weg. Fällt auf den Programmordner zurück, falls
    %APPDATA% fehlt.
    """
    base = os.environ.get("APPDATA")
    directory = Path(base) / "F360toSVG" if base else SCRIPT_DIR
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return SCRIPT_DIR
    return directory


DATA_DIR = data_dir()

# Auf-/zuklappbare Gruppen der Seitenleisten (Reihenfolge = Anzeige).
# label = Deutsch, label_en = Englisch — das Frontend waehlt je Sprache.
OPTION_SECTIONS = [
    {"id": "extraktion", "label": "Extraktion", "label_en": "Extraction", "group": "fusion", "open": True},
    {"id": "verbindung", "label": "Verbindung", "label_en": "Connection", "group": "fusion", "open": False},
    {"id": "svg_basis", "label": "Grundeinstellungen", "label_en": "Basics", "group": "svg", "open": True},
    {"id": "texturen", "label": "Texturen", "label_en": "Textures", "group": "svg", "open": True},
    {"id": "aufkleber", "label": "Aufkleber", "label_en": "Decals", "group": "svg", "open": True},
    {"id": "fase", "label": "3D Fase", "label_en": "3D Bevel", "group": "svg", "open": True},
]

# Beschreibt die Export-Optionen fuers Frontend. IDs = Parameter von
# run_export(). Typen: choice | number | bool | text | optional_number | range.
OPTION_SCHEMA = [
    {
        "id": "view", "label": "Ansicht", "label_en": "View", "type": "choice", "default": "auto", "group": "fusion", "section": "extraktion",
        "choices": [
            {"value": "auto", "label": "Auto (Fusion-Kamera)", "label_en": "Auto (Fusion camera)"},
            {"value": "top", "label": "Oben (Z+)", "label_en": "Top (Z+)"},
            {"value": "bottom", "label": "Unten (Z−)", "label_en": "Bottom (Z−)"},
            {"value": "front", "label": "Vorne (Y−)", "label_en": "Front (Y−)"},
            {"value": "back", "label": "Hinten (Y+)", "label_en": "Back (Y+)"},
            {"value": "right", "label": "Rechts (X+)", "label_en": "Right (X+)"},
            {"value": "left", "label": "Links (X−)", "label_en": "Left (X−)"},
        ],
        "help": "Blickrichtung der Projektion",
        "help_en": "Viewing direction of the projection",
    },
    {
        "id": "seam_mm", "label": "Naht-Stroke", "label_en": "Seam stroke",
        "type": "optional_number", "group": "svg", "section": "svg_basis",
        "default": DEFAULT_SEAM_STROKE_MM, "min": 0.02, "max": 0.5, "step": 0.02,
        "fallback": DEFAULT_SEAM_STROKE_MM, "factor": 1, "digits": 2, "unit": " mm",
        "live": True,
        "help": "Überdeckt feine Antialiasing-Nähte zwischen angrenzenden "
                "Flächen — normalerweise an lassen. Aus = maßhaltig "
                "(z. B. für Lasercut)",
        "help_en": "Covers thin antialiasing seams between adjacent faces — "
                   "usually keep it on. Off = dimensionally exact "
                   "(e.g. for laser cutting)",
    },
    {
        "id": "tol_mm", "label": "Kurven-Toleranz (mm)", "label_en": "Curve tolerance (mm)", "type": "number", "group": "fusion", "section": "extraktion",
        "default": DEFAULT_TOL_MM, "min": 0.001, "max": 1, "step": 0.005,
        "help": "Sampling-Genauigkeit für Splines und Bögen",
        "help_en": "Sampling accuracy for splines and arcs",
    },
    {
        "id": "decal_opacity", "label": "Deckkraft", "label_en": "Opacity", "type": "optional_number", "group": "svg", "section": "aufkleber",
        "default": 0.5, "min": 0, "max": 1, "step": 0.05, "fallback": 0.5,
        "factor": 100, "digits": 0, "unit": " %",
        "live": True,
        "help": "Standard 50 %; Aus = Wert aus Fusion übernehmen. Wirkt sofort auf das SVG",
        "help_en": "Default 50%; off = use the value from Fusion. Updates the SVG immediately",
    },
    {
        "id": "cull_hidden", "label": "Verdeckte Flächen entfernen", "label_en": "Remove hidden faces", "type": "bool",
        "group": "svg", "section": "svg_basis", "default": True, "live": True,
        "help": "Wirft Flächen raus, die komplett hinter anderen liegen",
        "help_en": "Drops faces that are completely covered by others",
    },
    {
        "id": "fase_3d", "label": "Aktivieren", "label_en": "Enable", "type": "bool",
        "group": "svg", "section": "fase", "default": False, "live": True,
        "help": "Schattiert Fasen je nach Lichtrichtung heller/dunkler",
        "help_en": "Shades bevels lighter/darker depending on the light direction",
    },
    {
        "id": "light_deg", "label": "Lichtrichtung", "label_en": "Light direction", "type": "range",
        "group": "svg", "section": "fase", "default": 180, "min": 0, "max": 360, "step": 5,
        "unit": "°", "live": True,
        "help": "0 = unten, 90 = rechts, 180 = oben, 270 = links",
        "help_en": "0 = bottom, 90 = right, 180 = top, 270 = left",
    },
    {
        "id": "fase_strength", "label": "Stärke", "label_en": "Strength", "type": "range",
        "group": "svg", "section": "fase", "default": 50, "min": 0, "max": 100, "step": 5,
        "unit": " %", "live": True,
        "help": "Wie stark die Fasen aufgehellt/abgedunkelt werden",
        "help_en": "How strongly bevels are lightened/darkened",
    },
    {
        "id": "texture_mode", "label": "Modus", "label_en": "Mode", "type": "choice",
        "group": "svg", "section": "texturen", "default": "color", "live": True,
        "choices": [
            {"value": "color", "label": "Durchschnittsfarbe", "label_en": "Average color"},
            {"value": "image", "label": "Bild (Muster)", "label_en": "Image (pattern)"},
            {"value": "vector", "label": "Vektorisiert", "label_en": "Vectorized"},
        ],
        "help": "Wie Material-Texturen ins SVG kommen: eingedampft zur "
                "Durchschnittsfarbe, als gekacheltes Bild oder als Vektor-Muster",
        "help_en": "How material textures end up in the SVG: reduced to an "
                   "average color, as a tiled image, or as a vector pattern",
    },
    {
        "id": "texture_colors", "label": "Farbstufen", "label_en": "Color levels", "type": "range",
        "group": "svg", "section": "texturen", "default": 4, "min": 2, "max": 8,
        "step": 1, "live": True,
        "help": "Auf wie viele Farben die Kachel vorm Vektorisieren "
                "reduziert wird (nur Modus 'Vektorisiert')",
        "help_en": "How many colors the tile is reduced to before tracing "
                   "(mode 'Vectorized' only)",
    },
    {
        "id": "texture_recolor", "label": "Texturfarben", "label_en": "Texture colors", "type": "choice",
        "group": "svg", "section": "texturen", "default": "original", "live": True,
        "choices": [
            {"value": "original", "label": "Original (aus Textur)", "label_en": "Original (from texture)"},
            {"value": "palette", "label": "Palette (einfärben)", "label_en": "Palette (tint)"},
        ],
        "help": "Palette: Kachel wird mit der Körperfarbe eingefärbt — "
                "Farb-Überschreibungen wirken dann auch auf die Textur",
        "help_en": "Palette: the tile is tinted with the body color — "
                   "color overrides then also affect the texture",
    },
    {
        "id": "texture_scale", "label": "Skalierung", "label_en": "Scale", "type": "range",
        "group": "svg", "section": "texturen", "default": 100, "min": 10,
        "max": 400, "step": 10, "unit": " %", "live": True,
        "help": "Kachelgröße relativ zu Fusion (100 % = Original)",
        "help_en": "Tile size relative to Fusion (100% = original)",
    },
    {
        "id": "texture_brightness", "label": "Helligkeit", "label_en": "Brightness", "type": "range",
        "group": "svg", "section": "texturen", "default": 0, "min": -100,
        "max": 100, "step": 5, "unit": " %", "live": True,
        "help": "Textur aufhellen/abdunkeln (wirkt in allen Textur-Modi)",
        "help_en": "Lighten/darken the texture (applies in all texture modes)",
    },
    {
        "id": "trace_decals", "label": "Vektorisieren", "label_en": "Vectorize", "type": "bool", "group": "svg", "section": "aufkleber",
        "default": False, "live": True,
        "help": "PNG zu Vektorpfaden tracen (nur Flachfarben-Grafiken; "
                "braucht beim Umschalten einen Moment)",
        "help_en": "Trace the PNG into vector paths (flat-color artwork only; "
                   "takes a moment when toggled)",
    },
    {
        "id": "url", "label": "MCP-Server-URL", "label_en": "MCP server URL", "type": "text", "group": "fusion",
        "section": "verbindung", "default": DEFAULT_URL,
        "help": "Adresse des Fusion MCP Servers",
        "help_en": "Address of the Fusion MCP server",
    },
]

OPTION_IDS = {entry["id"] for entry in OPTION_SCHEMA}

# Screenshot mit eingepasster Kamera: fitten -> transparent rendern ->
# Kamera sofort wiederherstellen (ohne Animation, minimaler Blip).
# So zeigt die Fusion-Vorschau immer das ganze Modell — unabhaengig
# davon, wie weit der Nutzer in Fusion gerade gezoomt hat.
FIT_SCREENSHOT_SCRIPT = '''
import os
import tempfile

import adsk.core


def run(_context: str):
    app = adsk.core.Application.get()
    viewport = app.activeViewport
    saved = viewport.camera
    fitted = viewport.camera
    fitted.isSmoothTransition = False
    fitted.isFitView = True
    viewport.camera = fitted
    adsk.doEvents()
    path = os.path.join(tempfile.gettempdir(), "fusion_svg_shot.png")
    try:
        options = adsk.core.SaveImageFileOptions.create(path)
        options.width = 1600
        options.height = 1600
        options.isBackgroundTransparent = True
        ok = viewport.saveAsImageFileWithOptions(options)
    finally:
        saved.isSmoothTransition = False
        viewport.camera = saved
        adsk.doEvents()
    print(path if ok else "")
'''


class _UiStream(io.TextIOBase):
    """Leitet print()-Zeilen als Log-Eintraege ans Frontend weiter."""

    def __init__(self, push, kind: str) -> None:
        self._push = push
        self._kind = kind
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._push(line, self._kind)
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            self._push(self._buffer, self._kind)
        self._buffer = ""


class Api:
    """JS-Bruecke: alle Methoden sind aus gui.html via pywebview.api.* erreichbar."""

    def __init__(self) -> None:
        self._window = None  # nicht 'window': oeffentliche Attribute landen in der JS-Bruecke
        self._export_lock = threading.Lock()
        self._cache: dict | None = None  # letzte Extraktion + Ausgabepfad
        self._last_shot: bytes | None = None  # letzter Fusion-Screenshot (PNG)
        self._last_svg: str | None = None  # zuletzt gebautes SVG (Vorschau)
        self._migrate_legacy_store()

    # --- intern -----------------------------------------------------------

    def _push_log(self, line: str, kind: str) -> None:
        if self._window is not None:
            self._window.evaluate_js(
                f"UI.appendLog({json.dumps(line)}, {json.dumps(kind)})"
            )

    PROGRESS_FILE = Path(tempfile.gettempdir()) / "fusion_svg_progress.txt"

    @contextlib.contextmanager
    def _progress_tail(self):
        """Liest die Fortschrittsdatei der Fusion-Extraktion live mit."""
        try:
            self.PROGRESS_FILE.unlink()
        except OSError:
            pass
        stop = threading.Event()

        def tail() -> None:
            position = 0
            buffer = b""
            while True:
                try:
                    if self.PROGRESS_FILE.is_file():
                        with open(self.PROGRESS_FILE, "rb") as handle:
                            handle.seek(position)
                            chunk = handle.read()
                        position += len(chunk)
                        buffer += chunk
                        # nur komplette Zeilen ausgeben — halbe Zeilen koennten
                        # mitten in einem UTF-8-Zeichen enden
                        while b"\n" in buffer:
                            raw, buffer = buffer.split(b"\n", 1)
                            text = raw.decode("utf-8", "replace").strip()
                            if text:
                                self._push_log("  » " + text, "out")
                except OSError:
                    pass
                if stop.is_set():
                    return  # letzter Durchlauf hat Reste eingesammelt
                stop.wait(0.3)

        watcher = threading.Thread(target=tail, daemon=True)
        watcher.start()
        try:
            yield
        finally:
            stop.set()
            watcher.join(timeout=2)

    # Farb-Anpassungen UND Einstellungen pro Fusion-Dokument (dauerhaft)
    OVERRIDES_FILE = DATA_DIR / "color_overrides.json"
    LEGACY_OVERRIDES_FILE = SCRIPT_DIR / "color_overrides.json"
    DOC_OPTION_KEYS = ("format", "output")  # zusaetzlich zu OPTION_IDS

    @classmethod
    def _migrate_legacy_store(cls) -> None:
        """Profile aus dem Programmordner einmalig nach %APPDATA% holen."""
        if cls.OVERRIDES_FILE.exists() or not cls.LEGACY_OVERRIDES_FILE.exists():
            return
        try:
            cls.OVERRIDES_FILE.write_bytes(cls.LEGACY_OVERRIDES_FILE.read_bytes())
            print(t("info.profiles_migrated",
                    source=cls.LEGACY_OVERRIDES_FILE, target=cls.OVERRIDES_FILE))
        except OSError as exc:
            print(t("warn.profiles_migrate_failed", error=exc), file=sys.stderr)

    def _read_store(self) -> dict:
        try:
            store = json.loads(self.OVERRIDES_FILE.read_text(encoding="utf-8"))
            return store if isinstance(store, dict) else {}
        except (OSError, ValueError):
            return {}

    def _load_doc_entry(self, document: str | None) -> dict:
        empty = {"overrides": {}, "options": {}}
        if not document:
            return empty
        entry = self._read_store().get(document)
        if not isinstance(entry, dict):
            return empty
        if "overrides" in entry or "options" in entry:
            return {
                "overrides": entry.get("overrides") or {},
                "options": entry.get("options") or {},
            }
        return {"overrides": entry, "options": {}}  # Alt-Format: nur Farben

    def _load_saved_overrides(self, document: str | None) -> dict:
        return self._load_doc_entry(document)["overrides"]

    def save_doc_settings(self, document: str, overrides: dict,
                          options: dict) -> dict:
        """Speichert Farben + Einstellungen eines Dokuments dauerhaft."""
        if not document:
            return {"ok": False, "error": t("err.no_document")}
        allowed = OPTION_IDS | set(self.DOC_OPTION_KEYS)
        clean_options = {
            key: value for key, value in (options or {}).items()
            if key in allowed and value is not None
        }
        entry = {}
        if overrides:
            entry["overrides"] = overrides
        if clean_options:
            entry["options"] = clean_options
        try:
            store = self._read_store()
            if entry:
                store[document] = entry
            else:
                store.pop(document, None)
            self.OVERRIDES_FILE.write_text(
                json.dumps(store, indent=1, ensure_ascii=False), encoding="utf-8"
            )
            return {"ok": True}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    PARTIAL_FILE = Path(tempfile.gettempdir()) / "fusion_svg_partial.jsonl"

    @contextlib.contextmanager
    def _live_preview(self):
        """Baut waehrend der Extraktion alle ~0.8s eine Zwischen-Vorschau.

        Das Fusion-Skript schreibt fertige Koerper als JSON-Zeilen; hier
        entsteht daraus ein Teil-SVG (ohne Culling/Decals — die kommen
        am Ende mit der richtigen Vorschau). Kostet ~40 ms pro Update.
        """
        if self._window is None:
            yield
            return
        try:
            self.PARTIAL_FILE.unlink()
        except OSError:
            pass
        stop = threading.Event()

        def worker() -> None:
            from svg_builder import build_svg

            position = 0
            buffer = b""
            meta, bodies, dirty = None, [], False
            while True:
                try:
                    if self.PARTIAL_FILE.is_file():
                        with open(self.PARTIAL_FILE, "rb") as handle:
                            handle.seek(position)
                            chunk = handle.read()
                        position += len(chunk)
                        buffer += chunk
                        while b"\n" in buffer:
                            raw, buffer = buffer.split(b"\n", 1)
                            try:
                                obj = json.loads(raw.decode("utf-8"))
                            except ValueError:
                                continue
                            if "meta" in obj:
                                meta = obj["meta"]
                            elif "body" in obj:
                                bodies.append(obj["body"])
                                dirty = True
                except OSError:
                    pass
                if dirty and meta and bodies:
                    try:
                        data = {**meta, "bodies": bodies, "decals": []}
                        result = build_svg(data, cull_hidden=False)
                        self._window.evaluate_js(
                            "UI.previewSvg("
                            + json.dumps(result["svg"])
                            + f", {len(bodies)})"
                        )
                    except Exception:
                        pass  # Teilstand kann unbaubar sein — naechster Tick
                    dirty = False
                if stop.is_set():
                    return
                stop.wait(0.8)

        watcher = threading.Thread(target=worker, daemon=True)
        watcher.start()
        try:
            yield
        finally:
            stop.set()
            watcher.join(timeout=3)

    def _clean_options(self, options: dict) -> dict:
        cleaned = {}
        for key, value in (options or {}).items():
            if key == "output":
                if value:
                    cleaned[key] = value
            elif key in OPTION_IDS and value is not None:
                cleaned[key] = value
        return cleaned

    # --- Status & Vorschau --------------------------------------------------

    def get_schema(self) -> dict:
        return {
            "sections": OPTION_SECTIONS,
            "options": OPTION_SCHEMA,
            "version": APP_VERSION,
        }

    def set_language(self, lang: str) -> dict:
        """Sprache der Protokoll-Meldungen setzen (Flaggen-Umschalter)."""
        return {"ok": True, "lang": i18n.set_lang(lang)}

    # Praefix der Fortschrittszeilen aus Fusion (siehe _progress_tail)
    PROGRESS_PREFIX = "  » "

    def retranslate_log(self, lines: list, lang: str) -> list:
        """Bereits ausgegebene Protokoll-Zeilen in die Zielsprache bringen.

        Liefert je Zeile den neuen Text oder None (dann bleibt sie stehen).
        """
        result = []
        for line in lines or []:
            text = str(line)
            prefix = ""
            if text.startswith(self.PROGRESS_PREFIX):
                prefix, text = self.PROGRESS_PREFIX, text[len(self.PROGRESS_PREFIX):]
            translated = i18n.retranslate(text, lang)
            result.append(prefix + translated if translated else None)
        return result

    @staticmethod
    def _version_tuple(text: str) -> tuple:
        """'v1.2.3' -> (1, 2, 3); unbekannte Teile werden zu 0."""
        parts = str(text).strip().lstrip("vV").split(".")
        numbers = []
        for part in parts[:4]:
            digits = "".join(ch for ch in part if ch.isdigit())
            numbers.append(int(digits) if digits else 0)
        return tuple(numbers)

    def check_update(self) -> dict:
        """Prüft still, ob auf GitHub ein neueres Release liegt.

        Bewusst fehlertolerant: kein Netz, GitHub down oder API-Limit
        liefern einfach {"available": False} — der Nutzer soll davon
        nichts merken.
        """
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        try:
            import urllib.request

            request = urllib.request.Request(
                url, headers={"Accept": "application/vnd.github+json",
                              "User-Agent": f"F360toSVG/{APP_VERSION}"},
            )
            with urllib.request.urlopen(request, timeout=6) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception:  # Netzfehler, Timeout, Rate-Limit, kaputtes JSON
            return {"available": False}

        latest = str(data.get("tag_name") or "").strip()
        if not latest:
            return {"available": False}
        if self._version_tuple(latest) <= self._version_tuple(APP_VERSION):
            return {"available": False}
        return {
            "available": True,
            "version": latest.lstrip("vV"),
            "url": data.get("html_url")
                   or f"https://github.com/{GITHUB_REPO}/releases/latest",
        }

    def open_url(self, url: str) -> dict:
        """Oeffnet einen Footer-Link im System-Browser (nie im GUI-Fenster)."""
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return {"ok": False, "error": f"Ungültige URL: {url!r}"}
        import webbrowser

        webbrowser.open(url)
        return {"ok": True}

    def get_status(self, url: str = DEFAULT_URL) -> dict:
        """Verbindung testen und aktives Dokument ermitteln."""
        try:
            client = FusionMcpClient(url or DEFAULT_URL)
            client.connect()
            content = client.call_tool(
                "fusion_mcp_read", {"queryType": "document", "operation": "open"}
            )
            info = json.loads(content[0]["text"])
            active = next(
                (r["name"] for r in info.get("results", []) if r.get("isActive")),
                None,
            )
            result = {"connected": True, "document": active}
            if active:
                result["path"] = self._document_path(client)
            return result
        except (FusionMcpError, ValueError, KeyError, IndexError) as exc:
            return {"connected": False, "error": str(exc)}

    # Ordner-Hierarchie des aktiven Dokuments (Cloud-Projekt).
    # Der document-Query liefert nur die unterste Ebene, die Kette
    # bekommt man nur ueber die API.
    FOLDER_SCRIPT = '''
import json


def run(_context: str):
    import adsk.core

    app = adsk.core.Application.get()
    chain = []
    try:
        data_file = app.activeDocument.dataFile
        folder = data_file.parentFolder if data_file else None
        guard = 0
        while folder is not None and guard < 12:
            chain.append(folder.name)
            try:
                folder = folder.parentFolder
            except Exception:
                folder = None
            guard += 1
    except Exception:
        pass
    print(json.dumps({"chain": list(reversed(chain))}, ensure_ascii=True))
'''

    def _document_path(self, client) -> str | None:
        """'Projekt / Ordner / Unterordner' oder None (z. B. lokale Datei)."""
        try:
            output = client.run_fusion_script(self.FOLDER_SCRIPT)
            chain = json.loads(output).get("chain") or []
        except (FusionMcpError, ValueError, KeyError, TypeError):
            return None
        return " / ".join(chain) if chain else None

    @staticmethod
    def _crop_to_content(png_b64: str) -> str:
        """Schneidet einen transparenten Screenshot auf den Modellinhalt zu.

        So zeigt die Fusion-Ansicht wie das SVG nur das Modell selbst —
        beide Vorschauen wirken beim Einpassen gleich gross.
        """
        try:
            import base64
            import io

            from PIL import Image
        except ImportError:
            return png_b64  # ohne Pillow: unbeschnitten anzeigen
        try:
            with Image.open(io.BytesIO(base64.b64decode(png_b64))) as image:
                image = image.convert("RGBA")
                bbox = image.split()[3].getbbox()  # Alpha-Kanal
                if not bbox:
                    return png_b64
                margin = 8
                bbox = (
                    max(0, bbox[0] - margin), max(0, bbox[1] - margin),
                    min(image.width, bbox[2] + margin),
                    min(image.height, bbox[3] + margin),
                )
                buffer = io.BytesIO()
                image.crop(bbox).save(buffer, "PNG")
                return base64.b64encode(buffer.getvalue()).decode("ascii")
        except OSError:
            return png_b64

    def fusion_screenshot(self, url: str = DEFAULT_URL) -> dict:
        """Fusion-Ansicht: Modell eingepasst, transparent, zugeschnitten."""
        import base64

        try:
            client = FusionMcpClient(url or DEFAULT_URL)
            client.connect()
            shot_path = client.run_fusion_script(FIT_SCREENSHOT_SCRIPT).strip()
            if not shot_path:
                return {"ok": False, "error": "Fusion hat kein Bild erzeugt."}
            raw = Path(shot_path).read_bytes()
            data = self._crop_to_content(base64.b64encode(raw).decode("ascii"))
            self._last_shot = base64.b64decode(data)  # fuer Kopieren/Speichern
            return {"ok": True, "dataUri": f"data:image/png;base64,{data}"}
        except (FusionMcpError, OSError) as exc:
            return {"ok": False, "error": str(exc)}

    def _copy_png_to_clipboard(self, png: bytes) -> dict:
        """PNG-Bytes in die Windows-Zwischenablage legen (auf Weiss)."""
        import subprocess

        try:
            # DIB in der Zwischenablage kennt kein Alpha -> auf Weiss legen
            import io

            from PIL import Image
            with Image.open(io.BytesIO(png)) as image:
                background = Image.new("RGBA", image.size, (255, 255, 255, 255))
                background.alpha_composite(image.convert("RGBA"))
                buffer = io.BytesIO()
                background.convert("RGB").save(buffer, "PNG")
                png = buffer.getvalue()
        except ImportError:
            pass
        clip_path = Path(tempfile.gettempdir()) / "fusion_svg_clip.png"
        try:
            clip_path.write_bytes(png)
            script = (
                "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
                f"$img = [System.Drawing.Image]::FromFile('{clip_path}'); "
                "[System.Windows.Forms.Clipboard]::SetImage($img); "
                "$img.Dispose()"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", script],
                capture_output=True, timeout=30,
            )
            if result.returncode != 0:
                return {"ok": False,
                        "error": result.stderr.decode(errors="replace")[:200]}
            return {"ok": True}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "error": str(exc)}

    def _save_png_dialog(self, png: bytes, default_name: str) -> dict:
        """Speichern-Dialog anzeigen und PNG-Bytes (mit Alpha) schreiben."""
        import subprocess

        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.SaveFileDialog; "
            "$d.Filter = 'PNG-Bild (*.png)|*.png'; "
            f"$d.InitialDirectory = '{SCRIPT_DIR}'; "
            f"$d.FileName = '{default_name}'; "
            "if ($d.ShowDialog() -eq 'OK') { Write-Output $d.FileName }"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", script],
                capture_output=True, text=True, timeout=300,
            )
            path = (result.stdout or "").strip()
            if not path:
                return {"ok": False, "error": t("err.cancelled")}
            Path(path).write_bytes(png)
            return {"ok": True, "path": path}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "error": str(exc)}

    def _render_last_svg_png(self) -> bytes | None:
        """Aktuelle SVG-Vorschau als PNG rendern (300 dpi, transparent)."""
        if not self._last_svg:
            return None
        try:
            from svg_convert import ConvertError, convert_svg_file
        except ImportError:
            return None
        try:
            with tempfile.TemporaryDirectory() as tmp_name:
                svg_path = Path(tmp_name) / "vorschau.svg"
                svg_path.write_text(self._last_svg, encoding="utf-8")
                return convert_svg_file(svg_path, "png").read_bytes()
        except (ConvertError, OSError):
            return None

    def copy_fusion_image(self) -> dict:
        """Legt den aktuellen Fusion-Screenshot in die Zwischenablage."""
        if not self._last_shot:
            return {"ok": False, "error": "Noch kein Fusion-Bild geladen."}
        return self._copy_png_to_clipboard(self._last_shot)

    def save_fusion_image(self) -> dict:
        """Speichert den aktuellen Fusion-Screenshot als PNG (mit Alpha)."""
        if not self._last_shot:
            return {"ok": False, "error": "Noch kein Fusion-Bild geladen."}
        return self._save_png_dialog(self._last_shot, "fusion-ansicht.png")

    def copy_svg_image(self) -> dict:
        """Rendert die SVG-Vorschau als PNG und kopiert sie."""
        png = self._render_last_svg_png()
        if not png:
            return {"ok": False, "error": "Noch keine SVG-Vorschau vorhanden."}
        return self._copy_png_to_clipboard(png)

    def save_svg_image(self) -> dict:
        """Rendert die SVG-Vorschau als PNG (300 dpi) und speichert sie."""
        png = self._render_last_svg_png()
        if not png:
            return {"ok": False, "error": "Noch keine SVG-Vorschau vorhanden."}
        return self._save_png_dialog(png, "svg-ansicht.png")

    # --- Export ---------------------------------------------------------------

    @staticmethod
    def _palette_from_data(data: dict) -> list:
        """Eindeutige Fusion-Farben mit den zugehoerigen Koerpernamen."""
        palette: dict[str, dict] = {}
        for body in data.get("bodies", []):
            if not body.get("faces"):
                continue
            color = body.get("color", "#808080")
            entry = palette.setdefault(color, {"color": color, "bodies": []})
            entry["bodies"].append(body.get("name", "?"))
        return list(palette.values())

    def read_fusion(self, options: dict) -> dict:
        """Nur auslesen: Fusion-Extraktion in den Cache, Palette fuellen."""
        if not self._export_lock.acquire(blocking=False):
            return {"ok": False, "error": t("err.busy_task")}
        try:
            cleaned = self._clean_options(options)
            out = _UiStream(self._push_log, "out")
            err = _UiStream(self._push_log, "err")
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err),                     self._progress_tail(), self._live_preview():
                data = extract_data(
                    view=cleaned.get("view", "auto"),
                    tol_mm=cleaned.get("tol_mm", DEFAULT_TOL_MM),
                    url=cleaned.get("url", DEFAULT_URL),
                )
            out.flush()
            err.flush()
            self._cache = {"data": data, "path": None}
            self._push_log(t("info.data_read"), "sys")
            saved = self._load_doc_entry(data.get("document"))
            return {
                "ok": True,
                "palette": self._palette_from_data(data),
                "document": data.get("document"),
                "view": data.get("view"),
                "savedOverrides": saved["overrides"],
                "savedOptions": saved["options"],
            }
        except (ExportError, FusionMcpError) as exc:
            self._push_log(t("err.prefix", error=exc), "err")
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            self._push_log(t("err.internal", error=repr(exc)), "err")
            return {"ok": False, "error": repr(exc)}
        finally:
            self._export_lock.release()

    def run_export(self, options: dict) -> dict:
        """Voller Export (Fusion-Extraktion + SVG); Logs streamen live."""
        if not self._export_lock.acquire(blocking=False):
            return {"ok": False, "error": t("err.busy_export")}
        try:
            cleaned = self._clean_options(options)
            out = _UiStream(self._push_log, "out")
            err = _UiStream(self._push_log, "err")
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err),                     self._progress_tail(), self._live_preview():
                # Liegen ausgelesene Daten im Cache, wird daraus exportiert:
                # das Ergebnis entspricht dann exakt der gezeigten Vorschau
                # (und ist sofort da). Frische Geometrie holt "Auslesen aus
                # Fusion". Ohne Cache wird hier selbst extrahiert.
                cached = self._cache["data"] if self._cache else None
                if cached is not None:
                    print(t("info.export_from_cache"))
                    data = cached
                else:
                    data = extract_data(
                        view=cleaned.get("view", "auto"),
                        tol_mm=cleaned.get("tol_mm", DEFAULT_TOL_MM),
                        url=cleaned.get("url", DEFAULT_URL),
                    )
                # gespeicherte Anpassungen des Dokuments unterlegen —
                # explizite Aenderungen aus der Sitzung gewinnen
                effective = {
                    **self._load_saved_overrides(data.get("document")),
                    **((options or {}).get("color_overrides") or {}),
                }
                options = {**(options or {}), "color_overrides": effective}
                result = self._finalize(data, options, cleaned.get("output"))
                self._convert_result(result, options)
            out.flush()
            err.flush()
            self._cache = {"data": data, "path": result["svgPath"]}
            return {
                "ok": True,
                "palette": self._palette_from_data(data),
                "appliedOverrides": effective,
                **result,
            }
        except (ExportError, FusionMcpError) as exc:
            self._push_log(t("err.prefix", error=exc), "err")
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # unerwartete Fehler sichtbar machen
            self._push_log(t("err.internal", error=repr(exc)), "err")
            return {"ok": False, "error": repr(exc)}
        finally:
            self._export_lock.release()

    def rebuild(self, options: dict) -> dict:
        """Vorschau aus dem Cache neu bauen — es wird NICHTS gespeichert."""
        if self._cache is None:
            return {
                "ok": False,
                "error": t("err.no_cache"),
            }
        if not self._export_lock.acquire(blocking=False):
            return {"ok": False, "error": t("err.busy_export")}
        try:
            out = _UiStream(self._push_log, "out")
            err = _UiStream(self._push_log, "err")
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                result = self._finalize(
                    self._cache["data"], options, self._cache.get("path"),
                    write_file=False,
                )
            out.flush()
            err.flush()
            return {"ok": True, **result}
        except (ExportError, FusionMcpError) as exc:
            self._push_log(t("err.prefix", error=exc), "err")
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            self._push_log(t("err.internal", error=repr(exc)), "err")
            return {"ok": False, "error": repr(exc)}
        finally:
            self._export_lock.release()

    def _finalize(self, data: dict, options: dict, output,
                  write_file: bool = True) -> dict:
        cleaned = self._clean_options(options)
        # Naht-Stroke ist abschaltbar: None (Schalter aus) = 0 mm
        seam = (options or {}).get("seam_mm")
        result = finalize_svg(
            data,
            seam_mm=float(seam) if seam is not None else 0.0,
            decal_opacity=(options or {}).get("decal_opacity"),
            trace_decals=cleaned.get("trace_decals", False),
            color_overrides=(options or {}).get("color_overrides"),
            output=output,
            write_file=write_file,
            cull_hidden=cleaned.get("cull_hidden", True),
            fase_3d=cleaned.get("fase_3d", False),
            light_deg=cleaned.get("light_deg", 180.0),
            fase_strength=cleaned.get("fase_strength", 50.0),
            texture_mode=cleaned.get("texture_mode", "color"),
            texture_colors=int(cleaned.get("texture_colors", 4)),
            texture_recolor=cleaned.get("texture_recolor", "original"),
            texture_scale=float(cleaned.get("texture_scale", 100.0)),
            texture_brightness=float(cleaned.get("texture_brightness", 0.0)),
        )
        result["svgPath"] = result["path"]  # SVG bleibt immer die Quelle
        self._last_svg = result["svg"]  # fuer Kontextmenue (kopieren/speichern)
        return result

    def _convert_result(self, result: dict, options: dict) -> None:
        """Konvertiert das frisch gebaute SVG ins gewuenschte Zielformat."""
        fmt = str((options or {}).get("format", "svg")).lower()
        if fmt in ("", "svg"):
            return
        try:
            from svg_convert import ConvertError, convert_svg_file
        except ImportError:
            print(t("warn.svg_convert_missing"), file=sys.stderr)
            return
        try:
            converted = convert_svg_file(Path(result["svgPath"]), fmt)
            print(t("info.converted", path=converted))
            result["path"] = str(converted)
            result["format"] = fmt
        except ConvertError as exc:
            print(t("warn.convert_failed", format=fmt.upper(), error=exc),
                  file=sys.stderr)

    # --- Dateisystem ------------------------------------------------------------

    # Zuletzt benutzter Ausgabeordner (ueber Sitzungen hinweg)
    APP_SETTINGS_FILE = DATA_DIR / "app_settings.json"

    def _app_settings(self) -> dict:
        try:
            data = json.loads(self.APP_SETTINGS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _remember_output_dir(self, path: str) -> None:
        try:
            settings = self._app_settings()
            settings["last_export_dir"] = str(Path(path).parent)
            self.APP_SETTINGS_FILE.write_text(
                json.dumps(settings, indent=1, ensure_ascii=False), encoding="utf-8"
            )
        except (OSError, ValueError):
            pass  # Merken ist Komfort, nie ein Abbruchgrund

    FORMAT_LABELS = {
        "svg": "SVG", "png": "PNG", "jpg": "JPG", "pdf": "PDF", "ai": "Illustrator",
    }

    def suggested_name(self, fmt: str = "svg") -> str:
        """Vorschlag fuers Speichern: <Dokument>[-Ansicht].<Format>."""
        fmt = (fmt or "svg").lower()
        data = (self._cache or {}).get("data") or {}
        document = data.get("document") or "fusion-export"
        view = data.get("view") or "top"
        stem = default_output_path(document, view).stem
        return f"{stem}.{fmt}"

    def choose_output(self, fmt: str = "svg", suggest: bool = True) -> str | None:
        """Speichern-Dialog; merkt sich den Ordner fuer das naechste Mal.

        suggest=False laesst das Namensfeld leer (Option "Automatischer
        Dateiname" ist aus) — dann tippt der Nutzer alles selbst.
        """
        fmt = (fmt or "svg").lower()
        name = self.suggested_name(fmt) if suggest else ""
        start_dir = self._app_settings().get("last_export_dir") or str(SCRIPT_DIR)
        try:
            path = self._choose_output_webview(fmt, name, start_dir)
        except Exception as exc:
            self._push_log(
                f"pywebview-Dialog fehlgeschlagen ({exc!r}) — "
                "nutze Windows-Dialog.", "err",
            )
            path = self._choose_output_windows(fmt, name, start_dir)
        if path:
            self._remember_output_dir(path)
        return path

    def _choose_output_webview(self, fmt: str, name: str,
                               start_dir: str) -> str | None:
        import webview

        dialog_type = getattr(webview, "SAVE_DIALOG", None)
        if dialog_type is None:  # pywebview >= 5: Enum statt Konstante
            dialog_type = webview.FileDialog.SAVE
        label = self.FORMAT_LABELS.get(fmt, fmt.upper())
        result = self._window.create_file_dialog(
            dialog_type,
            directory=start_dir,
            save_filename=name,
            file_types=(f"{label} (*.{fmt})", "Alle Dateien (*.*)"),
        )
        if isinstance(result, (list, tuple)):
            return result[0] if result else None
        return result

    def _choose_output_windows(self, fmt: str, name: str,
                               start_dir: str) -> str | None:
        """Nativer Speichern-Dialog als Fallback (eigener Prozess, robust)."""
        import subprocess

        label = self.FORMAT_LABELS.get(fmt, fmt.upper())

        def quote(text: str) -> str:  # einfache Anfuehrungszeichen verdoppeln
            return str(text).replace("'", "''")

        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d = New-Object System.Windows.Forms.SaveFileDialog; "
            f"$d.Filter = '{quote(label)} (*.{fmt})|*.{fmt}|Alle Dateien (*.*)|*.*'; "
            f"$d.InitialDirectory = '{quote(start_dir)}'; "
            f"$d.FileName = '{quote(name)}'; "
            f"$d.DefaultExt = '{fmt}'; $d.AddExtension = $true; "
            "if ($d.ShowDialog() -eq 'OK') { Write-Output $d.FileName }"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", script],
                capture_output=True, text=True, timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._push_log(t("err.dialog_failed", error=repr(exc)), "err")
            return None
        path = (result.stdout or "").strip()
        return path or None

    def open_file(self, path: str) -> None:
        target = Path(path)
        if target.is_file():
            os.startfile(str(target))  # noqa: S606 — bewusst: Datei im Standardprogramm

    def open_folder(self, path: str) -> None:
        folder = Path(path).parent
        if folder.is_dir():
            os.startfile(str(folder))  # noqa: S606


def main() -> int:
    try:
        import webview
    except ImportError:
        print(
            "pywebview fehlt — bitte installieren: py -3 -m pip install pywebview",
            file=sys.stderr,
        )
        return 1

    api = Api()
    api._window = webview.create_window(
        WINDOW_TITLE,
        str(GUI_HTML),
        js_api=api,
        width=1900,
        height=1080,
        min_size=(1150, 700),
        background_color="#0d1017",
    )
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
