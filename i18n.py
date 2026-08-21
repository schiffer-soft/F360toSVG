"""Übersetzung aller Programm-Meldungen (Protokoll, CLI, Fehlertexte).

Die GUI setzt die Sprache über `set_lang()`, sobald der Nutzer die Flagge
umschaltet — ab dann erscheinen neue Meldungen in der gewählten Sprache.
Das CLI bleibt bei Deutsch, solange nichts anderes gesetzt wird.

Aufruf: ``t("texture.average", name=..., color=...)``. Fehlt ein
Schlüssel, wird er selbst zurückgegeben — nie eine Exception, Meldungen
dürfen den Export niemals abbrechen.
"""
from __future__ import annotations

_LANG = "de"

# key: (deutsch, englisch) — Platzhalter mit str.format
MESSAGES: dict[str, tuple[str, str]] = {
    # --- Warnungen: fehlende Pakete ---------------------------------------
    "warn.pillow.average": (
        "Warnung: Pillow fehlt — Textur-Durchschnitt nicht möglich "
        "(pip install pillow).",
        "Warning: Pillow is missing — cannot average textures "
        "(pip install pillow).",
    ),
    "warn.pillow.tiles": (
        "Warnung: Pillow fehlt — Textur-Kacheln nicht möglich "
        "(pip install pillow).",
        "Warning: Pillow is missing — cannot build texture tiles "
        "(pip install pillow).",
    ),
    "warn.pillow.binarize": (
        "Warnung: Pillow fehlt — Tracing ohne Binarisierung "
        "(weiche Kanten werden fleckig).",
        "Warning: Pillow is missing — tracing without binarization "
        "(soft edges will look blotchy).",
    ),
    "warn.vtracer.texture": (
        "Warnung: vtracer fehlt — Textur wird als Bild eingebettet "
        "(pip install vtracer).",
        "Warning: vtracer is missing — texture is embedded as an image "
        "(pip install vtracer).",
    ),
    "warn.vtracer.decal": (
        "Warnung: vtracer fehlt — Aufkleber wird als PNG eingebettet "
        "(pip install vtracer).",
        "Warning: vtracer is missing — decal is embedded as PNG "
        "(pip install vtracer).",
    ),
    "warn.shapely": (
        "Warnung: shapely fehlt — verdeckte Flächen bleiben im SVG "
        "(py -3 -m pip install shapely).",
        "Warning: shapely is missing — hidden faces stay in the SVG "
        "(py -3 -m pip install shapely).",
    ),
    "warn.svg_convert_missing": (
        "Warnung: svg_convert.py fehlt — es bleibt beim SVG.",
        "Warning: svg_convert.py is missing — keeping the SVG.",
    ),

    # --- Warnungen: Dateien und Verarbeitung ------------------------------
    "warn.texture.not_found": (
        "Warnung: Texturdatei nicht gefunden: {path}",
        "Warning: texture file not found: {path}",
    ),
    "warn.texture.unreadable": (
        "Warnung: Textur nicht lesbar ({name}): {error}",
        "Warning: texture not readable ({name}): {error}",
    ),
    "warn.texture.trace_failed": (
        "Warnung: Textur-Tracing fehlgeschlagen: {error}",
        "Warning: texture tracing failed: {error}",
    ),
    "warn.texture.trace_unexpected": (
        "Warnung: Unerwartetes vtracer-Ergebnis für Textur-Kachel",
        "Warning: unexpected vtracer result for the texture tile",
    ),
    "warn.decal.not_found": (
        "Warnung: Aufkleber-Bild nicht gefunden: {path}",
        "Warning: decal image not found: {path}",
    ),
    "warn.decal.unreadable": (
        "Warnung: Aufkleber-Bild nicht lesbar ({name}): {error}",
        "Warning: decal image not readable ({name}): {error}",
    ),
    "warn.decal.trace_failed": (
        "Warnung: Tracing fehlgeschlagen ({name}): {error}",
        "Warning: tracing failed ({name}): {error}",
    ),
    "warn.decal.trace_unexpected": (
        "Warnung: Unerwartetes vtracer-Ergebnis für {name}",
        "Warning: unexpected vtracer result for {name}",
    ),
    "warn.binarize_failed": (
        "Warnung: Binarisierung fehlgeschlagen ({name}): {error}",
        "Warning: binarization failed ({name}): {error}",
    ),
    "warn.convert_failed": (
        "Warnung: {format}-Konvertierung fehlgeschlagen ({error}) — "
        "das SVG liegt trotzdem vor.",
        "Warning: {format} conversion failed ({error}) — "
        "the SVG is still there.",
    ),
    "warn.profiles_migrate_failed": (
        "Warnung: Profile konnten nicht übernommen werden: {error}",
        "Warning: could not carry over the profiles: {error}",
    ),

    # --- Fortschritt und Ergebnisse ---------------------------------------
    "info.texture.average": (
        "Textur-Durchschnitt für '{body}': {color} ({file})",
        "Texture average for '{body}': {color} ({file})",
    ),
    "info.texture.tile": (
        "Textur für '{body}' {kind} (Kachel {w:.1f} x {h:.1f} mm{detail})",
        "Texture for '{body}' {kind} (tile {w:.1f} x {h:.1f} mm{detail})",
    ),
    "info.texture.kind_vector": ("vektorisiert", "vectorized"),
    "info.texture.kind_image": ("als Bild eingebettet", "embedded as an image"),
    "info.texture.paths": (", {count} Pfade", ", {count} paths"),
    "info.decal.traced": (
        "Aufkleber '{name}' vektorisiert ({file}, {count} Pfade, {opacity})",
        "Decal '{name}' vectorized ({file}, {count} paths, {opacity})",
    ),
    "info.decal.embedded": (
        "Aufkleber '{name}' eingebettet ({file}, {size} KB, {opacity})",
        "Decal '{name}' embedded ({file}, {size} KB, {opacity})",
    ),
    "info.decal.opacity": ("Deckkraft {value:.0%}", "opacity {value:.0%}"),
    "info.connected": (
        "Verbunden mit Fusion MCP Server ({url}), "
        "extrahiere Flächen (Ansicht: {view}) ...",
        "Connected to the Fusion MCP server ({url}), "
        "extracting faces (view: {view}) ...",
    ),
    "info.view_from_camera": (
        "Ansicht aus Fusion-Kamera abgeleitet: {view}",
        "View derived from the Fusion camera: {view}",
    ),
    "info.raw_saved": (
        "Rohdaten gespeichert: {path}",
        "Raw data saved: {path}",
    ),
    "info.converted": ("Konvertiert: {path}", "Converted: {path}"),
    "info.occlusion": (
        "Verdeckungs-Analyse: {removed} von {total} Flächen unsichtbar — entfernt",
        "Occlusion analysis: {removed} of {total} faces invisible — removed",
    ),
    "info.bevel": (
        "3D-Fase: {count} Fasen schattiert "
        "(Licht {light:.0f} Grad, Stärke {strength:.0f} %)",
        "3D bevel: {count} bevels shaded "
        "(light {light:.0f} degrees, strength {strength:.0f} %)",
    ),
    "info.bevel_no_normals": (
        "Hinweis: Daten ohne Normalen — für 3D-Fase bitte einmal "
        "neu 'Auslesen aus Fusion'.",
        "Note: data without normals — please run 'Read from Fusion' "
        "once more for the 3D bevel.",
    ),
    "info.export_ok": (
        "OK: {path} — {faces} Flächen, {w:.1f} x {h:.1f} mm, Ansicht: {view}",
        "OK: {path} — {faces} faces, {w:.1f} x {h:.1f} mm, view: {view}",
    ),
    "info.preview_ok": (
        "Vorschau: {faces} Flächen, {w:.1f} x {h:.1f} mm, "
        "Ansicht: {view} (nichts gespeichert)",
        "Preview: {faces} faces, {w:.1f} x {h:.1f} mm, "
        "view: {view} (nothing saved)",
    ),
    "info.data_read": (
        "Daten ausgelesen — Farben geladen. Änderungen an Farben/"
        "Deckkraft bauen das SVG direkt aus dem Cache.",
        "Data extracted — colors loaded. Changing colors or opacity "
        "rebuilds the SVG straight from the cache.",
    ),
    "info.profiles_migrated": (
        "Dokument-Profile übernommen: {source} → {target}",
        "Document profiles carried over: {source} → {target}",
    ),

    # --- Fehler ------------------------------------------------------------
    "err.prefix": ("Fehler: {error}", "Error: {error}"),
    "err.internal": ("Interner Fehler: {error}", "Internal error: {error}"),
    "err.busy_export": (
        "Ein Export läuft bereits.", "An export is already running."),
    "err.busy_task": (
        "Ein Vorgang läuft bereits.", "A task is already running."),
    "err.no_cache": (
        "Noch nichts ausgelesen — erst 'Auslesen aus Fusion' "
        "oder einen Export starten.",
        "Nothing extracted yet — run 'Read from Fusion' or an export first.",
    ),
    "err.no_document": ("Kein Dokumentname.", "No document name."),
    "err.decal_opacity": (
        "Aufkleber-Deckkraft muss zwischen 0 und 1 liegen.",
        "Decal opacity must be between 0 and 1.",
    ),
    "err.texture_mode": (
        "Unbekannter Textur-Modus: {value}",
        "Unknown texture mode: {value}",
    ),
    "err.texture_recolor": (
        "Unbekannter Texturfarben-Modus: {value}",
        "Unknown texture color mode: {value}",
    ),
    "err.no_faces": (
        "Keine von oben sichtbaren Flächen gefunden — "
        "ist ein Design mit sichtbaren Körpern aktiv?",
        "No faces visible from this direction — "
        "is a design with visible bodies active?",
    ),
    "err.data_incomplete": (
        "Extraktionsdaten unvollständig ({error})",
        "Extraction data incomplete ({error})",
    ),
    "err.script_output": (
        "Unerwartete Skriptausgabe:\n{output}",
        "Unexpected script output:\n{output}",
    ),
    "err.result_unreadable": (
        "Ergebnisdatei {path} nicht lesbar: {error}",
        "Result file {path} not readable: {error}",
    ),
    "err.write_failed": (
        "Fehler beim Schreiben: {error}",
        "Error while writing: {error}",
    ),
    "err.placeholder": (
        "Platzhalter '{pattern}' nicht in fusion_extract.py gefunden — "
        "wurde die Vorlage umformatiert?",
        "Placeholder '{pattern}' not found in fusion_extract.py — "
        "was the template reformatted?",
    ),
    "err.tol": (
        "--tol-mm muss eine endliche Zahl größer 0 sein.",
        "--tol-mm must be a finite number greater than 0.",
    ),
    "err.dialog_failed": (
        "Windows-Dialog fehlgeschlagen: {error}",
        "Windows dialog failed: {error}",
    ),
    "err.cancelled": ("Abgebrochen.", "Cancelled."),

    # --- Verbindung zu Fusion ---------------------------------------------
    "mcp.unreachable": (
        "Fusion MCP Server nicht erreichbar ({url}).\n"
        "Läuft Fusion 360 und ist der MCP Server in den Voreinstellungen aktiviert?",
        "Fusion MCP server not reachable ({url}).\n"
        "Is Fusion 360 running and the MCP server enabled in the preferences?",
    ),
    "mcp.timeout": (
        "Zeitüberschreitung nach {seconds}s — dauert die Extraktion zu lange?",
        "Timed out after {seconds}s — is the extraction taking too long?",
    ),
    "mcp.script_error": (
        "Skriptfehler in Fusion:\n{error}",
        "Script error inside Fusion:\n{error}",
    ),
    "convert.no_edge": (
        "Microsoft Edge nicht gefunden — wird für PNG/JPG/PDF/AI-Export benötigt.",
        "Microsoft Edge not found — required for PNG/JPG/PDF/AI export.",
    ),

    # --- Fusion-Extraktion (Fortschritt aus Fusion) ------------------------
    "fusion.no_design": (
        "Kein aktives Design in Fusion geöffnet.",
        "No active design open in Fusion.",
    ),
    "fusion.document": (
        "Dokument '{document}', Ansicht: {view}",
        "Document '{document}', view: {view}",
    ),
    "fusion.bodies_found": (
        "{count} sichtbare Körper gefunden",
        "{count} visible bodies found",
    ),
    "fusion.body_progress": (
        "Körper {index}/{total} verarbeitet, {faces} Flächen (zuletzt: '{name}')",
        "Body {index}/{total} processed, {faces} faces (last: '{name}')",
    ),
    "fusion.decals_search": (
        "Suche Aufkleber (Decals) ...", "Looking for decals ..."),
    "fusion.decals_found": (
        "{count} Aufkleber erfasst", "{count} decals captured"),
    "fusion.done": (
        "Extraktion fertig: {bodies} Körper, {faces} Flächen — "
        "übertrage Ergebnis ...",
        "Extraction finished: {bodies} bodies, {faces} faces — "
        "transferring the result ...",
    ),
}


def set_lang(lang: str) -> str:
    """Sprache setzen ('de' oder 'en'); liefert die gesetzte Sprache."""
    global _LANG
    _LANG = "en" if str(lang).lower().startswith("en") else "de"
    return _LANG


def get_lang() -> str:
    return _LANG


def t(key: str, **kwargs) -> str:
    """Übersetzten Text holen. Unbekannte Schlüssel liefern den Schlüssel."""
    entry = MESSAGES.get(key)
    if entry is None:
        return key
    text = entry[1] if _LANG == "en" else entry[0]
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return text
