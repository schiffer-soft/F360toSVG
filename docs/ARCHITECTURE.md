# Architektur & Interna

Technische Dokumentation zu F360toSVG — für alle, die den Code erweitern
oder verstehen wollen, warum etwas so gebaut ist. Die Bedienung steht in
der [README](../README.md).

## Dateien

| Datei | Rolle |
|---|---|
| `gui.py` | GUI-Backend (pywebview): JS-API, Options-Schema, Sektionen, Version |
| `gui.html` | GUI-Frontend: Formular, gekoppelte Vorschauen, Palette, Log, i18n |
| `export_svg.py` | Export-Kern (`extract_data`/`finalize_svg`) + CLI |
| `i18n.py` | Alle Programm-Meldungen zweisprachig (`t()`, `retranslate()`) |
| `fusion_extract.py` | Läuft **in Fusion**: sammelt sichtbare Flächen als JSON |
| `svg_builder.py` | Stapelung, Naht, 3D-Fase, Textur-Muster — erzeugt das SVG |
| `occlusion.py` | Verdeckungs-Analyse (shapely) |
| `svg_convert.py` | SVG → PNG/JPG/PDF/AI (Edge headless) |
| `fusion_mcp_client.py` | Minimaler MCP-Client (streamable HTTP, nur urllib) |
| `build_exe.bat` | Baut die portable EXE (PyInstaller) |
| `check_build_env.py` | Prüft vor dem Build, ob alle Pakete da sind |
| `docs/TODO.md` | Was bewusst noch offen ist, samt Begründung |
| `requirements.txt` | Laufzeit-Pakete mit festen Versionen |
| `requirements-dev.txt` | Zusätzlich PyInstaller (nur zum Bauen) |
| `SVG-Export-GUI.bat` | GUI aus dem Quellcode starten |
| `SVG-Export.bat` | CLI-Export per Doppelklick |
| `%APPDATA%\F360toSVG\color_overrides.json` | Dokument-Profile: Farben + Einstellungen (automatisch angelegt) |
| `%APPDATA%\F360toSVG\app_settings.json` | Programmweite Kleinigkeiten, z. B. zuletzt benutzter Ausgabeordner |

## Ablauf

1. **Verbindung:** `export_svg.py` spricht den lokalen Fusion MCP Server an
   und schickt `fusion_extract.py` als Skript an das Tool
   `fusion_mcp_execute` — es läuft damit direkt in der Fusion-API.
   Konstanten (`VIEW`, `STROKE_TOL_CM`, `SESSION`, `MESSAGES`) werden
   vorher per Regex im Skripttext ersetzt.

2. **Sichtbarkeit:** Eine Fläche gilt als sichtbar, wenn ihre Normale
   irgendwo eine Komponente **zum Betrachter** hat (`> 0.01`).
   Bei planaren Flächen reicht eine Messung; bei gekrümmten
   (Zylinder-/Kegelbänder von Verrundungen, Senkungen, Bohrspitzen)
   wird auf einem Parameter-Raster gemessen. Exakt parallel zur
   Blickrichtung stehende Wände fallen raus.

3. **Konturen:** Von jeder sichtbaren Fläche werden alle Rand-Loops
   (außen + Löcher) über die CoEdges abgelaufen. Die Orientierung
   jedes Kantensegments wird **geometrisch** bestimmt (welches Ende
   schließt an die Kette an?), da die API-Flag `isOpposedToEdge`
   allein nicht immer stimmt — sonst entstehen selbstschneidende
   Polygone. Kurven werden mit `--tol-mm` Toleranz (Standard 10 µm) zu
   Polylinien abgetastet und auf die Bildebene projiziert.

4. **Stapelung:** Alle Flächen werden nach **(tiefe_max, tiefe_min)**
   aufsteigend sortiert (Tiefe = Richtung zum Betrachter): Erst was
   weiter hinten endet; bei gleicher Vorderkante zuerst, was weiter
   hinten beginnt. Dadurch liegt z. B. eine Fase korrekt **unter** ihrer
   Deckfläche, aber **über** allem dahinter. Löcher werden per
   `fill-rule="evenodd"` ausgespart — tiefere Flächen scheinen durch.

5. **SVG:** mm-Einheiten 1:1, Y-Achse gespiegelt (SVG-Y zeigt nach unten).
   Jeder Pfad trägt `data-body` (Körpername) und `data-z-mm="von..bis"`,
   sodass nachvollziehbar bleibt, welche Fläche woher stammt.

### Zweistufiger Kern

`extract_data()` (teuer, spricht mit Fusion) und `finalize_svg(data, ...)`
(schnell, arbeitet auf einer JSON-Kopie) sind getrennt. Die GUI cacht die
Extraktionsdaten und baut daraus Live-Vorschauen in ~0,05 s neu, ohne
Fusion erneut zu befragen. Nur der Export schreibt Dateien
(`write_file=False` bei allen Vorschau-Aufbauten).

Auch der **Export** nimmt die Daten aus dem Cache, wenn welche da sind:
0,16 s statt 8,9 s, und das Ergebnis entspricht exakt der gezeigten
Vorschau. Frisch zu extrahieren waere sogar riskant — haette sich in
Fusion zwischenzeitlich etwas geaendert (schon eine gedrehte Kamera bei
Ansicht "auto"), bekaeme man etwas anderes als das gerade Freigegebene.
Frische Geometrie holt bewusst "Auslesen aus Fusion".

### Reihenfolge beim Auslesen

Beim Klick auf "Auslesen aus Fusion" laufen Screenshot und Extraktion
**nacheinander**, Screenshot zuerst. Das sieht nach verschenkter Zeit
aus, ist aber Absicht: Der MCP-Server arbeitet in Fusions Hauptthread,
zwei gleichzeitige Anfragen werden dort ohnehin serialisiert und
verzahnen sich. Gemessen am Testmodell:

| | Dauer |
|---|---|
| Screenshot allein | 0,7 s |
| Extraktion allein | 8,5 s |
| beide gleichzeitig gestartet | 7,3 s — **beide Ergebnisse erst am Ende** |
| nacheinander | 9,3 s |

Parallel spart also gut zwei Sekunden Gesamtzeit, aber die Fusion-Ansicht
erscheint dann erst nach 7 s zusammen mit den Daten. Sequenziell steht
sie nach 0,7 s, und die Extraktion laeuft danach mit ihrem Live-Aufbau
der Vorschau — die Wartezeit fuehlt sich deutlich kuerzer an.

### Ergebnis-Übertragung

Die print-Ausgabe des MCP-Servers wird bei ~1 MiB gekappt — große Designs
sprengen das locker. Das Fusion-Skript schreibt sein Ergebnis deshalb in
eine Temp-Datei und printet nur deren Pfad. Zusätzlich schreibt es
Fortschritt und Teilergebnisse pro Körper; die GUI liest beide per
Daemon-Thread mit und baut daraus die Live-Vorschau während der
Extraktion.

### Temp-Dateien pro Programmlauf

Alle Dateien im Temp-Ordner tragen eine **Sitzungskennung** aus PID und
zwei Zufallsbytes (`export_svg.SESSION`), vergeben beim Programmstart:

| Name | schreibt | liest |
|---|---|---|
| `fusion_svg_export_<Sitzung>.json` | Fusion | GUI/CLI |
| `fusion_svg_progress_<Sitzung>.txt` | Fusion | GUI |
| `fusion_svg_partial_<Sitzung>.jsonl` | Fusion | GUI |
| `fusion_svg_shot_<Sitzung>.png` | Fusion | GUI |
| `fusion_svg_clip_<Sitzung>.png` | GUI | PowerShell |

Vorher hatten alle feste Namen. Zwei gleichzeitig gestartete Instanzen
schrieben damit in dieselben Dateien — im schlimmsten Fall las die eine
das Extraktionsergebnis der anderen und exportierte deren Geometrie, ohne
dass es auffiel. Die Kennung enthält Zufall, weil Windows PIDs nach dem
Prozessende wiederverwendet.

Fusion läuft in einem eigenen Prozess und kennt unsere PID nicht — sie
wird mit `export_svg.with_session()` in den Skripttext eingesetzt
(`SESSION = "0"` → `SESSION = "<PID>-<hex>"`), genauso wie `VIEW` und
`MESSAGES`. Das gilt auch für das Screenshot-Skript in `gui.py`.

Aufgeräumt wird zweimal: `cleanup_session_temp_files()` löscht beim
Beenden die eigenen Dateien, `cleanup_stale_temp_files()` beim Start alle
`fusion_svg_*` älter als 24 Stunden. Die Altersgrenze ist wichtig — sonst
würde der Neustart der einen Instanz einer zweiten, parallel laufenden
die Dateien unter den Füßen wegziehen.

## Textur-Appearances

Hat der Farbkanal einer Appearance eine **Bild-Textur** statt einer Farbe,
liefert die Extraktion den lokalen Pfad der Texturdatei samt
Farb-Modifikatoren (Helligkeit/RGBAmount, R/G/B-Faktoren, Invertierung).
Daraus wird per Pillow die **Durchschnittsfarbe** berechnet
(Modus `color`, Standard).

Für die Muster-Modi (`image`, `vector`) kommt die Platzierung aus Fusion:
Kachelgröße, Drehung und Versatz aus den Appearance-Eigenschaften
(`texture_RealWorldScale*`, Werte in **Zoll**) und der **Ankerpunkt** des
Kachelrasters aus der Projektions-Matrix von `body.textureMapControl`.
Daraus baut der Builder ein `<pattern patternUnits="userSpaceOnUse">`.
Gekrümmte Flächen erhalten dieselbe ebene Kachelung (Parallelprojektion,
keine Verzerrung wie beim 3D-Mapping).

Beim Vektor-Modus wird die Kachel auf wenige Farbstufen quantisiert und
per vtracer getract; unter den Pfaden liegt die Durchschnittsfarbe als
Grundfläche, damit Trace-Lücken an Kachelrändern unsichtbar bleiben.
Aufbereitete Kacheln werden pro Prozess gecacht (Schlüssel: Datei,
Faktoren, Modus, Farbstufen, Tint, Helligkeit) — Live-Rebuilds tracen
nicht erneut.

`texture_recolor=palette` normiert die Kachel auf Graustufen und färbt
sie mit der Körperfarbe ein (Mittelwert = Zielfarbe), sodass
Palette-Überschreibungen auch auf die Textur wirken.

## Aufkleber (Decals)

Sichtbare Aufkleber der Stammkomponente werden als base64-data-URI
eingebettet. Lage und Größe kommen aus der Decal-Transformationsmatrix
(4×4, zeilenweise — die Basisvektoren kodieren die volle Breite/Höhe),
das Bild wird über eine SVG-`matrix()` auf ein zentriertes Einheitsquadrat
abgebildet und auf die Loops seiner Trägerfläche geclippt. Im Stapel liegt
ein Aufkleber `DECAL_DEPTH_EPS_MM` über seiner Fläche. Aufkleber, die vom
Betrachter wegzeigen, werden übersprungen.

**Trace-Pipeline** (`--trace-decals`): Binarisieren → Tracen. Weiche
Alpha-Verläufe (Glühpunkte, Schein) würden vom Tracer in eckige
Farbstufen-Kleckse zerlegt; deshalb wird das Bild zuerst mit einem
Alpha-Schwellwert auf harte Konturen gebracht. Konstanten in
`export_svg.py`: `TRACE_ALPHA_THRESHOLD` und `TRACE_BLUR_RATIO`
(optionaler Weichzeichner, Standard 0/aus — ein SVG-Filter würde beim
Rendern gerastert und beim Zoomen unscharf). Schlägt das Tracen fehl,
fällt der Export automatisch auf die PNG-Einbettung zurück.

## Verdeckungs-Analyse

Der Painter's Algorithm lässt auch Flächen im SVG stehen, die komplett
von näher liegenden überdeckt sind. `occlusion.py` geht die Zeichenliste
**von vorn nach hinten** durch, sammelt die abgedeckte Region als
shapely-Union (Batch-Größe 25) und verwirft vollständig verdeckte Flächen.
Die Geometrie der behaltenen Flächen bleibt unverändert, das Ergebnis ist
pixelidentisch — es wird bewusst **nichts beschnitten**, nur ganz oder
gar nicht verworfen. Decals verdecken nie und werden nie verworfen.

## 3D Fase

Lambert-Schattierung mit fester Sonnenhöhe (`LIGHT_ELEVATION_DEG = 45`)
über der Bildebene; die Deckfläche ist die Referenzhelligkeit. Der
Lichtvektor ergibt sich aus der Lichtrichtung (0 = unten, 90 = rechts,
180 = oben, 270 = links).

- **Flachschattierung** für ebene Fasen und gerade Verrundungsbänder:
  eine Kipprichtung = ein Farbwert.
- **Spline-Fasen** (NurbsSurface, Fasen entlang gebogener Kanten)
  bekommen einen **Bahn-Verlauf**: Die Extraktion liefert Punkt +
  Normale an beiden Bahn-Enden und in der Mitte (`band`), der Builder
  spannt daraus einen `linearGradient` von Anfang zu Ende. Die
  Stopp-Farben kommen aus den dort gemessenen Normalen — an den Nähten
  zu flach schattierten Nachbarfasen stimmt der Ton dadurch exakt
  (ein einzelner Ton für die ganze Bahn erzeugte dort Farbsprünge).
- **Ringfasen** (Kegel-/Torusmantel um eine Achse in Blickrichtung)
  bekommen einen `linearGradient` mit `gradientUnits="userSpaceOnUse"`,
  der über den **ganzen** Ring spannt (Zentrum ± Radius entlang der
  Lichtrichtung). Teilsegmente greifen sich so automatisch den richtigen
  Ausschnitt. Zentrum und Achse kommen als `ringCenter`/`ringAxisD` aus
  der Fusion-Geometrie; steht die Achse zu schräg
  (`|axis_d| < RING_AXIS_MIN_D`), fällt der Builder auf Flachschattierung
  zurück.

Zwei Details, die viel Debugging gekostet haben:

- **Konkave Ringe** (Innenfase am Loch) brauchen den **gespiegelten**
  Verlauf — die Extraktion liefert dafür `ringConcave` (Skalarprodukt aus
  Radialvektor und Normale).
- Der Verlauf hat einen **dritten Stopp in der Mitte** mit der Farbe einer
  quer zum Licht stehenden Fase. Ohne ihn interpoliert der Browser linear
  im RGB-Raum, was bei dunklen Farben deutlich zu hell ist — am Bogenende
  entsteht dann ein harter Schnitt zur flach schattierten Nachbarfase.

Bei texturierten Körpern bleiben schattierte Fasen **musterfrei** (die
Kachelung würde die Plastizität zerstören); nur Deckflächen tragen das
Muster.

## Naht-Stroke

Wo zwei Pfade exakt aneinanderstoßen (z. B. Fasenband ↔ Deckfläche),
entsteht beim Rendern durch Antialiasing eine haardünne Naht, durch die
der Hintergrund schimmert. Deshalb bekommt jeder Pfad einen dünnen Stroke
**in seiner eigenen Füllfarbe** — die später gezeichnete Fläche deckt die
Naht damit zu. Jede Kontur wächst dadurch optisch um die halbe
Stroke-Breite (bei 0,1 mm also 0,05 mm). Für maßhaltige Weiterverarbeitung
(z. B. Lasercut): `--seam-mm 0`.

## GUI erweitern

Optionen sind **schema-getrieben**: `OPTION_SCHEMA` in `gui.py` beschreibt
jedes Feld (`id`, `label`, `type`, `default`, `help`, …),
`OPTION_SECTIONS` die auf-/zuklappbaren Gruppen. Eine neue Option braucht:

1. einen Eintrag in `OPTION_SCHEMA` — Typen: `choice`, `number`, `bool`,
   `text`, `optional_number`, `range` (Slider mit Einheit); `group` wählt
   die Seitenleiste, `section` die Gruppe, `live: True` macht die Option
   sofort wirksam (Vorschau-Rebuild aus dem Cache),
2. einen gleichnamigen Parameter in `export_svg.finalize_svg()` (SVG-Bau)
   bzw. `extract_data()` (Extraktion).

Einsammeln, Speichern (localStorage + Dokument-Profil) und Übergabe laufen
generisch über die `id`.

### Mehrsprachigkeit

Die Oberfläche ist deutsch/englisch — inklusive aller Protokoll-Meldungen.

**Frontend:** Schema-Einträge tragen `label_en` und `help_en` (auch je
`choice`), die festen UI-Texte stehen im `I18N`-Wörterbuch in `gui.html`
und werden über `t(key)` geholt; `pickL(entry, feld)` wählt die
Sprachvariante eines Schema-Feldes. Der Sprachwechsel baut die Formulare
neu auf und stellt die Werte wieder her. Die Auswahl liegt in
`localStorage` (`svgExportLang`).

**Backend:** `i18n.py` hält alle Meldungen als `{schlüssel: (de, en)}`;
die Module rufen `t("key", param=…)`. Beim Sprachwechsel ruft das
Frontend `Api.set_language()`, ab dann erscheinen neue Meldungen in der
gewählten Sprache (bereits ausgegebene Zeilen bleiben stehen). Die
Log-Farben im Frontend erkennen beide Präfixe (`Warnung`/`Warning`).

**Fusion-Skript:** `fusion_extract.py` läuft in Fusion und kann `i18n`
nicht importieren. Es hat deshalb ein leeres `MESSAGES = {}`, das
`load_extract_script()` mit den übersetzten Texten füllt — als
`json.dumps(..., ensure_ascii=True)`. Das ist **Pflicht**: Der Skripttext
geht als JSON durch den MCP-Server nach Fusion und Umlaute überleben den
Transfer nicht (aus `Körper` wurde `KÃ¶rper`); als `\uXXXX`-Escape kommt
jeder Buchstabe heil an. Deshalb gilt für dieses Skript: **keine
Nicht-ASCII-Zeichen in Texten, die ausgegeben werden.**

Merke: `_substitute_constant()` übergibt den Ersatztext als Funktion an
`re.subn`, damit Backslash-Folgen (`\uXXXX`, Windows-Pfade) wörtlich
eingesetzt und nicht als Escape-Sequenzen gedeutet werden.

### Speichern-Dialog

`Api.choose_output(fmt, suggest)` oeffnet den Windows-Speichern-Dialog:
Filter und Endung richten sich nach dem Format, Startordner ist der
zuletzt benutzte (`app_settings.json`), und `suggest=True` traegt
`<Dokument>[-Ansicht].<Format>` als Namen ein (aus dem Cache, sonst
`fusion-export`). Zuerst wird der pywebview-Dialog versucht, bei
Problemen ein PowerShell-`SaveFileDialog` in eigenem Prozess — dort
werden einfache Anfuehrungszeichen in Pfaden verdoppelt, sonst bricht
das Skript ab. Bricht der Nutzer ab, wird nichts geschrieben.

### Frozen-Pfade (EXE)

`export_svg.app_dir()` liefert das Verzeichnis **neben der EXE** (bzw. den
Skriptordner); `resource_path(name)` liefert eingebettete Ressourcen aus
dem PyInstaller-Bundle (`sys._MEIPASS`) — `gui.html` und
`fusion_extract.py`.

Nutzerdaten liegen dagegen in `gui.data_dir()` =
`%APPDATA%\F360toSVG`: neben der EXE ist je nach Ablageort kein
Schreibrecht (Programme, Netzlaufwerk), Cloud-Ordner synchronisieren jede
Änderung mit, und beim Wechsel auf eine neue Programmversion wären die
Profile weg. Eine alte `color_overrides.json` aus dem Programmordner wird
beim ersten Start einmalig übernommen (`_migrate_legacy_store`).

### Update-Hinweis

`Api.check_update()` fragt rund 30 Sekunden nach dem Start (Timer im
Frontend, damit der Start nicht aufs Netz wartet) das neueste Release der
GitHub-API ab und vergleicht `tag_name` mit `APP_VERSION`. Der Vergleich
läuft über Zahlen-Tupel, damit `1.10.0` korrekt größer als `1.9.9` ist.
Jeder Fehler — kein Netz, Timeout, API-Limit — liefert still
`{"available": False}`; erscheint ein Hinweis, dann nur als kleine Pille
in der Fußzeile, die auf die Releases-Seite verlinkt.

## Stilregeln

- **Sichtbare deutsche Texte** (Labels, Hilfen, Log- und Fehlermeldungen,
  CLI-Hilfen, Doku) immer mit echten Umlauten schreiben: ä ö ü ß. Alle
  Dateien sind UTF-8. Keine ae/oe/ue-Transliteration — nachträgliches
  Ersetzen ist fehlerträchtig („neue", „dauert", „Steuerung" …).
- Neue sichtbare Texte immer zweisprachig anlegen (siehe oben).

## Bekannte Fallstricke

- **pywebview:** Das js_api-Objekt darf **kein öffentliches Attribut
  `window`** haben — die Brücke serialisiert es rekursiv und registriert
  dann keine Methoden mehr. Deshalb `self._window`.
- **`pywebviewready`** kann feuern, bevor das Frontend seinen Listener
  registriert hat; `App.init()` pollt deshalb, bis die API-Methoden da sind.
- **Zoom** wird über die echte Elementgröße gemacht, nicht per CSS
  `transform: scale()` — sonst skaliert der Browser nur die gerasterte
  Ebene und das SVG wird unscharf.
- **Fusion-API:** `BRepFaceVector` hat kein `.count`/`.item()`, sondern
  `.size()` und `[i]`. `ColorProperty` kennt kein
  `isConnectedTextureMap`, sondern `hasConnectedTexture`/`connectedTexture`.
