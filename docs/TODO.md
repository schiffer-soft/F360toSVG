# Offene Punkte

Was bewusst noch nicht gebaut ist — mit dem Grund, warum es warten kann.
Erledigtes wird hier gelöscht, nicht abgehakt; die Historie steht in git.

## Aus dem Code-Review (August 2026)

### Dateiprotokoll (Review-Punkt 6b)

Das Programm schreibt bisher **kein** Protokoll auf die Platte. Was im
Fenster steht, ist nach dem Schließen weg. Meldet jemand „die Vorschau
geht manchmal nicht", gibt es nichts zum Nachsehen.

Geplant, wenn tatsächlich Rückfragen kommen:

- Rotierendes Protokoll unter `%APPDATA%\F360toSVG\logs\F360toSVG.log`
  (bewusst dort und nicht in `%LOCALAPPDATA%` — daneben liegen bereits
  `color_overrides.json` und `app_settings.json`)
- Alles hineinschreiben, was auch im Fenster steht, zusätzlich die
  Tracebacks der bewusst geschluckten Ausnahmen
- „Protokoll öffnen" in der Fußzeile

Erledigt ist bereits Stufe a: Die Live-Vorschau meldet einen Fehlschlag
einmal pro Durchlauf ins Fenster-Protokoll, statt still zu bleiben.

### Geometrie-Sonderfälle prüfen

Der Review empfiehlt als nächsten Schritt gezielte Tests der Geometrie
statt weiterer Struktur-Arbeit. Noch nicht systematisch geprüft:

- mehrere überlappende Körper, Körper exakt auf gleicher Höhe
- verschachtelte, gespiegelte und transformierte Komponenten
- sehr kleine Kanten, tangentiale Übergänge
- Freiformflächen und Kugeln (aktuell ohne echte Silhouette)

## Ältere Ideen

- Verlaufs-Schattierung **quer** über gerade Fillet-Bänder
  (längs ist über `band` gelöst)
- Ringe mit schräger Achse — aktuell Rückfall auf flache Schattierung
- Echte Silhouetten für Kugeln und Freiformflächen
- Teilweise verdeckte Flächen beschneiden statt ganz oder gar nicht
  (Pathfinder-artig)

## Offene Frage

Gibt es den Fusion MCP Server auch in der **Personal-Lizenz**? Ließ sich
bisher nicht belegen.
