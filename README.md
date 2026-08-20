# Fusion 360 → SVG

Exportiert eine **Ansicht** des aktiven Fusion-360-Designs (Standard:
Draufsicht) als massstabsgetreues SVG (mm, 1:1) — als gestapelte
Farbflaechen, wie eine Multi-Color-Druckvorschau.

## Idee

Das Modell wird entlang der Blickrichtung „durchgegangen": Von jedem
Koerper werden alle **zum Betrachter zeigenden Flaechen** als 2D-Kontur
extrahiert und im SVG von hinten nach vorn uebereinander gezeichnet
(Painter's Algorithm). Was naeher am Betrachter liegt, ueberdeckt
automatisch das, was dahinter liegt. Jede Flaeche wird mit dem echten
RGB-Wert der Appearance ihres Koerpers gefuellt.

## Voraussetzungen

- **Fusion 360 laeuft** und hat das gewuenschte Design **aktiv geoeffnet**
- **Fusion MCP Server aktiviert**:
  Voreinstellungen → Allgemein → „Fusion MCP Server" ankreuzen
  (Standard-Port 27182, also `http://127.0.0.1:27182/mcp`)
- **Python 3.10+** (64-bit empfohlen). Optionale Pakete, je Feature:

  | Paket | Wofuer | Installation |
  |---|---|---|
  | `pywebview` | GUI | `py -3 -m pip install pywebview` |
  | `Pillow` | Textur-Durchschnitt, Screenshot-Zuschnitt, JPG | `py -3 -m pip install pillow` |
  | `shapely` | Verdeckungs-Analyse | `py -3 -m pip install shapely` |
  | `vtracer` | Aufkleber vektorisieren | `py -3 -m pip install vtracer` |

  Ohne ein Paket faellt das jeweilige Feature mit Log-Warnung weg —
  der Export selbst laeuft mit reiner Standardbibliothek.

## GUI

`SVG-Export-GUI.bat` doppelklicken (oder `py -3 gui.py`) — ein Fenster
mit allem Drum und Dran. Der typische Ablauf:

1. **„Auslesen aus Fusion"** (links, blau): holt Viewport-Screenshot
   und Geometrie-/Farbdaten in den Cache. Waehrend der Extraktion
   laeuft ein Live-Fortschritt im Protokoll UND die SVG-Vorschau baut
   sich Koerper fuer Koerper auf. Danach ist die Farb-Palette gefuellt
   und die erste Vorschau erscheint automatisch.
2. **Live tunen:** Farben in der Palette, Deckkraft, Naht, 3D Fase,
   Verdeckungs-Analyse, Vektorisierung — jede Aenderung baut die
   Vorschau in ~0,05 s aus dem Cache neu. **Es wird dabei nichts
   gespeichert** („Vorschau generieren" erzeugt die Vorschau auch
   manuell).
3. **„Exportieren" (rechts, blau):** liest den aktuellen Stand frisch
   aus Fusion und schreibt die Datei(en). Der ▼-Pfeil daneben waehlt
   nur das Format: **SVG** (Vektor, Standard), **PNG** (300 dpi,
   transparent), **JPG** (300 dpi, weiss), **PDF** (Vektor), **AI**
   (Illustrator — technisch ein Vektor-PDF mit .ai-Endung). Bei
   PNG/JPG/PDF/AI wird das SVG immer zusaetzlich geschrieben
   (`svg_convert.py`, Edge headless).

Die Oberflaeche im Detail:

- **Seitenleisten mit auf-/zuklappbaren Gruppen:** links Fusion
  (Extraktion, Verbindung), rechts SVG (Grundeinstellungen, Aufkleber,
  3D Fase, Ausgabe). Auf/Zu-Zustand, letzte Einstellungen und
  Exportformat werden gemerkt.
- **Dokument-Profile:** Farb-Anpassungen UND alle Einstellungen werden
  pro Fusion-Dokument in `color_overrides.json` gespeichert und beim
  naechsten Auslesen derselben Zeichnung automatisch wiederhergestellt.
- **Gekoppelte Vorschauen:** Fusion-Ansicht und SVG-Ergebnis sind
  zoombar (Mausrad, zoomt zum Cursor, rendert vektorscharf nach) und
  verschiebbar (Ziehen) — synchron in beiden Ansichten. „Einpassen"
  setzt beide zurueck, „100 %" stellt das SVG auf Originalgroesse.
  Beim Live-Rebuild bleibt der Ausschnitt stehen. Der
  Fusion-Screenshot wird mit kurz eingepasster Kamera aufgenommen und
  per Alpha-Kanal auf das Modell zugeschnitten — beide Seiten zeigen
  das Modell gleich gross.
- **Farb-Palette (Mitte):** alle Fusion-Farben mit Koerpernamen —
  links das Original (Klick = zuruecksetzen), rechts der Picker fuer
  die neue Farbe. „Alle zuruecksetzen" loescht auch das gespeicherte
  Profil des Dokuments.
- **Rechtsklick auf eine Vorschau:** „Bild kopieren" (Zwischenablage)
  oder „Bild speichern" (PNG; SVG-Seite wird dafuer mit 300 dpi
  gerendert).
- **Protokoll:** alle Meldungen mit Zeitstempel, farbcodiert
  (OK gruen, Warnungen gelb, Fehler rot); Status-Pille oben rechts
  mit Dreh-Animation, solange etwas laeuft.

### GUI erweitern

Optionen sind **schema-getrieben**: `OPTION_SCHEMA` in `gui.py`
beschreibt jedes Feld (`id`, `label`, `type`, `default`, `help`, …),
`OPTION_SECTIONS` die auf-/zuklappbaren Gruppen. Eine neue Option
braucht:

1. einen Eintrag in `OPTION_SCHEMA` — Typen: `choice`, `number`,
   `bool`, `text`, `optional_number`, `range` (Slider mit Einheit);
   `group` waehlt die Seitenleiste, `section` die Gruppe,
   `live: True` macht die Option sofort wirksam (Vorschau-Rebuild aus
   dem Cache),
2. einen gleichnamigen Parameter in `export_svg.finalize_svg()`
   (SVG-Bau) bzw. `extract_data()` (Extraktion).

Einsammeln, Speichern (localStorage + Dokument-Profil) und Uebergabe
laufen generisch ueber die `id`.

## Verwendung (CLI)

```bash
python export_svg.py                        # Ansicht aus der Fusion-Kamera (auto)
python export_svg.py --view top             # feste Draufsicht
python export_svg.py --view front           # von vorn -> <Dokumentname>-front.svg
python export_svg.py -o zeichnung.svg       # eigener Dateiname
python export_svg.py --seam-mm 0            # ohne Naht-Stroke (masshaltig)
python export_svg.py --tol-mm 0.001         # feineres Kurven-Sampling
python export_svg.py --dump-json faces.json # Rohdaten zum Debuggen
```

| Option | Standard | Bedeutung |
|---|---|---|
| `-o`, `--output` | `<Dokumentname>[-<view>].svg` | Ziel-SVG-Datei |
| `--view` | `auto` | Blickrichtung, siehe Tabelle unten |
| `--seam-mm` | `0.1` | Breite des Naht-Strokes in mm, `0` = aus |
| `--tol-mm` | `0.01` | Sampling-Toleranz fuer Kurven (Splines, Boegen) in mm |
| `--decal-opacity` | Wert aus Fusion | Deckkraft aller Aufkleber ueberschreiben (0..1) |
| `--trace-decals` | aus | Aufkleber zu Vektorpfaden tracen statt PNG einzubetten |
| `--url` | `http://127.0.0.1:27182/mcp` | URL des Fusion MCP Servers |
| `--dump-json` | – | extrahierte Rohdaten zusaetzlich als JSON speichern |

### Blickrichtungen (`--view`)

`auto` (Standard) liest die aktuelle Viewport-Kamera aus Fusion aus und
schnappt auf die naechstliegende der sechs Achsansichten (dominante
Komponente der Blickrichtung). Einfach in Fusion die gewuenschte Ansicht
am ViewCube einstellen und exportieren.

| Ansicht | Betrachter schaut ... | Bildebene |
|---|---|---|
| `top` | von Z+ nach unten (Draufsicht) | X→rechts, Y→oben |
| `bottom` | von Z− nach oben | X→rechts, Y→unten |
| `front` | von Y− nach Y+ (Vorderansicht) | X→rechts, Z→oben |
| `back` | von Y+ nach Y− | X→links, Z→oben |
| `right` | von X+ nach X− | Y→rechts, Z→oben |
| `left` | von X− nach X+ | Y→links, Z→oben |

## Dateien

| Datei | Rolle |
|---|---|
| `SVG-Export-GUI.bat` | **GUI starten** (Doppelklick) |
| `SVG-Export.bat` | CLI-Export per Doppelklick (Ansicht: auto) |
| `gui.py` | GUI-Backend (pywebview): JS-API, Options-Schema, Sektionen |
| `gui.html` | GUI-Frontend: Formular, gekoppelte Vorschauen, Palette, Log |
| `svg_convert.py` | SVG → PNG/JPG/PDF/AI (Edge headless) |
| `export_svg.py` | Export-Kern (`extract_data`/`finalize_svg`) + CLI |
| `fusion_mcp_client.py` | Minimaler MCP-Client (streamable HTTP, nur urllib) |
| `fusion_extract.py` | Laeuft **in Fusion**: sammelt sichtbare Flaechen als JSON |
| `svg_builder.py` | Stapelung, Naht, 3D-Fase — erzeugt das SVG-Dokument |
| `occlusion.py` | Verdeckungs-Analyse (shapely) |
| `color_overrides.json` | Dokument-Profile: Farben + Einstellungen (automatisch) |

## Wie es funktioniert

1. **Verbindung:** `export_svg.py` spricht den lokalen Fusion MCP Server an
   und schickt `fusion_extract.py` als Skript an das Tool
   `fusion_mcp_execute` — es laeuft damit direkt in der Fusion-API.

2. **Sichtbarkeit:** Eine Flaeche gilt als sichtbar, wenn ihre Normale
   irgendwo eine Komponente **zum Betrachter** hat (`> 0.01`).
   Bei planaren Flaechen reicht eine Messung; bei gekruemmten
   (Zylinder-/Kegelbaender von Verrundungen, Senkungen, Bohrspitzen)
   wird auf einem 6×6-Parameter-Raster gemessen. Exakt parallel zur
   Blickrichtung stehende Waende fallen raus.

3. **Konturen:** Von jeder sichtbaren Flaeche werden alle Rand-Loops
   (aussen + Loecher) ueber die CoEdges abgelaufen. Die Orientierung
   jedes Kantensegments wird geometrisch bestimmt (welches Ende
   schliesst an die Kette an?), da die API-Flag `isOpposedToEdge`
   allein nicht immer stimmt. Kurven werden mit `--tol-mm` Toleranz
   (Standard 10 µm) zu Polylinien abgetastet und auf die Bildebene
   projiziert.

4. **Stapelung:** Alle Flaechen werden nach **(tiefe_max, tiefe_min)**
   aufsteigend sortiert (Tiefe = Richtung zum Betrachter): Erst was
   weiter hinten endet; bei gleicher Vorderkante zuerst, was weiter
   hinten beginnt. Dadurch liegt z. B. eine Fase korrekt **unter** ihrer
   Deckflaeche, aber **ueber** allem dahinter. Loecher werden per
   `fill-rule="evenodd"` ausgespart — tiefere Flaechen scheinen durch.

5. **SVG:** mm-Einheiten 1:1, Y-Achse gespiegelt (SVG-Y zeigt nach unten).
   Jeder Pfad traegt `data-body` (Koerpername) und `data-z-mm="von..bis"`,
   sodass nachvollziehbar bleibt, welche Flaeche woher stammt.

### Textur-Appearances (Grafik statt Farbe)

Hat der Farbkanal einer Appearance eine **Bild-Textur** statt einer
Farbe, liefert die Extraktion den lokalen Pfad der Texturdatei samt
Farb-Modifikatoren (Helligkeit/RGBAmount, R/G/B-Faktoren, Invertierung)
mit. Das CLI berechnet daraus per Pillow die **Durchschnittsfarbe** des
Bildes und verwendet sie als Flaechenfarbe — die Konsole meldet jede
Ersetzung (`Textur-Durchschnitt fuer '...': #1C1C1C (datei.jpg)`).
Ohne Pillow oder bei fehlender Datei gibt es eine Warnung und der
Koerper behaelt seine Fallback-Farbe.

### Aufkleber (Decals)

Sichtbare Aufkleber (Einfuegen → Aufkleber) werden als **echte Bilder**
ins SVG eingebettet (base64-data-URI, Original-PNG in voller
Aufloesung): Lage und Groesse kommen aus der
Decal-Transformationsmatrix, die Deckkraft wird uebernommen
(ueberschreibbar mit `--decal-opacity`), und das
Bild wird auf die Kontur seiner Traegerflaeche geclippt. Im Stapel
liegt ein Aufkleber hauchduenn ueber seiner Flaeche. Aufkleber, die vom
Betrachter wegzeigen, werden uebersprungen. Grenzen: nur Decals der
Stammkomponente; die Bilddatei muss lokal erreichbar sein (Fusion muss
mit dem Design geoeffnet sein — was fuer den Export ohnehin gilt).

Mit `--trace-decals` wird das PNG stattdessen per **vtracer**
(`py -3 -m pip install vtracer`) zu echten Vektorpfaden getract und an
derselben Stelle eingefuegt — das SVG bleibt dann komplett
vektorbasiert (skalierbar, in Inkscape/Illustrator editierbar).

Die Trace-Pipeline: **Binarisieren → Tracen.** Weiche Alpha-Verlaeufe
(Gluehpunkte, Schein) wuerden vom Tracer in eckige Farbstufen-Kleckse
zerlegt; deshalb wird das Bild zuerst mit einem Alpha-Schwellwert auf
harte Konturen gebracht und dann sauber getract — die Pfade bleiben
dadurch bei jedem Zoom scharf. Konstanten in `export_svg.py`:
`TRACE_ALPHA_THRESHOLD` (Schwellwert) und `TRACE_BLUR_RATIO`
(optionaler Weichzeichner, Standard 0/aus — ein SVG-Filter wuerde beim
Rendern gerastert und beim Zoomen unscharf).
Fuer Fotos und echte Farbverlaeufe bleibt die PNG-Einbettung
(Standard) die bessere Wahl. Schlaegt das Tracen fehl, faellt der
Export automatisch auf die PNG-Einbettung zurueck.

### Verdeckte Flaechen entfernen

Der Painter's Algorithm laesst auch Flaechen im SVG stehen, die
komplett von naeher liegenden ueberdeckt sind (z. B. die Vorderseite
einer Rueckplatte). Die **Verdeckungs-Analyse** (GUI-Option, Standard
an; benoetigt `shapely`) geht die Zeichenliste von vorn nach hinten
durch, sammelt die abgedeckte Region als Polygon-Union und verwirft
vollstaendig verdeckte Flaechen — die Geometrie der behaltenen
Flaechen bleibt unveraendert, das Ergebnis ist pixelidentisch.
Beim Test-Badge: 629 → 308 Flaechen, Datei ~27 % kleiner.
Rueckseitige Flaechen (Normale zeigt vom Betrachter weg) werden
schon bei der Extraktion uebersprungen.

### 3D Fase (GUI-Option, Standard aus)

Schattiert **Fasen** (geneigte ebene Flaechen) nach einem
Lambert-Lichtmodell, sodass Kanten plastisch wirken: Fasen zur Sonne
werden heller als die Deckflaeche, quer liegende etwas dunkler,
abgewandte am dunkelsten. **Lichtrichtung** 0-360 Grad (0 = unten,
90 = rechts, 180 = oben/Standard, 270 = links) und **Fasen-Staerke**
0-100 % sind Live-Regler. Die Schattierung wirkt auf die finale Farbe
(inkl. Palette-Overrides). Benoetigt Flaechennormalen in den Daten —
nach dem Update einmal neu "Auslesen aus Fusion".

Flaechentypen: ebene Fasen und **gerade Verrundungsbaender** werden
flach schattiert (eine Kipprichtung = ein Farbwert). **Ringfasen an
Zylinderkanten** (Kegel-/Torusmantel) bekommen einen
**Linearverlauf** entlang der Lichtrichtung — die Sonnenseite des
Rings ist hell, die abgewandte dunkel, wie bei einem gerenderten
Drehknopf.

### Naht-Stroke (`--seam-mm`)

Wo zwei Pfade exakt aneinanderstossen (z. B. Fasenband ↔ Deckflaeche),
entsteht beim Rendern durch Antialiasing eine haarduenne Naht, durch die
der Hintergrund schimmert. Deshalb bekommt jeder Pfad einen duennen
Stroke **in seiner eigenen Fuellfarbe** — die spaeter gezeichnete Flaeche
deckt die Naht damit zu. Jede Kontur waechst dadurch optisch um die
halbe Stroke-Breite (bei 0,1 mm also 0,05 mm). Fuer masshaltige
Weiterverarbeitung (z. B. Lasercut): `--seam-mm 0`.

## Grenzen

- **Kugeln, Tori, Freiformflaechen:** Es wird die projizierte
  *Randkontur* der Flaeche gezeichnet, nicht die echte *Silhouette*.
  Bei einer Halbkugel stimmt das zufaellig (Aequator), bei einem
  liegenden Zylinder nicht. Fuer solche Flaechen braeuchte es
  Silhouetten-Berechnung oder Tessellation.
- **Gleichfarbige Details verschwinden:** Ein Sackloch im gruenen
  Koerper ist nur sichtbar, wo ein andersfarbiger Koerper durchscheint —
  reine Flachfarben-Logik ohne Schattierung.
- **Ueberlappende Koerper im selben Z-Bereich:** Die Reihenfolge ist
  dann nicht eindeutig definiert (betrifft nur sich durchdringende
  Koerper).
- Es zaehlt die **Sichtbarkeit in Fusion**: ausgeblendete Koerper
  (Gluehbirne aus) werden uebersprungen.

## Fehlerbilder

| Meldung | Ursache / Loesung |
|---|---|
| `... nicht erreichbar` | Fusion gestartet? MCP Server in den Voreinstellungen aktiviert? Port richtig? |
| `Kein aktives Design` | In Fusion ein Design-Dokument oeffnen/aktivieren |
| `Keine ... Flaechen gefunden` | Alle Koerper ausgeblendet oder leeres Design |
| `Skriptfehler in Fusion: ...` | Traceback lesen — Fehler stammt aus `fusion_extract.py` in der Fusion-API |
