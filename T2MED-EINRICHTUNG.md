# T2med: Steno einrichten (GDT)

Steno schreibt Gespräche mit und übergibt die fertige Dokumentation als GDT-2.1-Satz 6310 an T2med.
Ein Klick in T2med auf **„Messung starten“** beim Patienten startet die Aufnahme am Steno-Knopf. Die
Patientennummer kommt aus diesem Auftrag (Satz 6302), und T2med ordnet die Dokumentation automatisch zu.

Muster und Feldwerte stammen aus `sono-gdt` (`../t2med-gdt`, Ultraschallbilder an T2med). Dessen
Einrichtung läuft seit dem 05.10.2026 in Sprechzimmer 2 zuverlässig. Werte, die von dort übernommen sind,
sind mit *(sono-gdt)* markiert. Die T2med-Anleitungen von sonoGDT, auf die sich sono-gdt stützt:
[T2MED.pdf](https://www.sonogdt.de/wp-content/uploads/T2MED.pdf) (Netzwerkpfad) und
[T2MED_lokal.pdf](https://www.sonogdt.de/wp-content/uploads/T2MED_lokal.pdf).

## Ablauf

```
T2med "Messung starten" (Sprechzimmer 1) ──▶ S:\_steno\STNOT2MD.001   (6302, Steno holt ab + löscht)
Steno-Knopf startet die Aufnahme von selbst (rot, ■, Patientenname)
Klick auf ■ ──▶ Aufnahme geht an den Server: Transkription, Sprechertrennung, Dokumentation
            ──▶ S:\_steno\T2MDSTNO.001   (6310, T2med liest + löscht, etwa 10 s später)
```

- **Ohne Patient** (ohne „Messung starten“) fragt ein Klick auf ▶ erst nach („Kein Patient
  ausgewählt …“), dann startet die Aufnahme „ohne Patient“. Die Dokumentation geht per GDT nach
  **„Ext. Aufträge“** zur manuellen Zuordnung (Patientennummer `0`). Holt T2med die Datei nicht binnen
  5 Minuten ab (`STENO_ABHOLUNG_MINUTEN`, GDT-Gerät am Arbeitsplatz fehlt oder T2med ist zu), zieht Steno sie
  zurück und der Knopf meldet einen Fehler. Der Text steht dann im Verlauf, nach der Korrektur geht
  „Fehlgeschlagene erneut senden“.
- **Ein Auftrag gilt für genau eine Aufnahme.** Wer beim nächsten Patienten „Messung starten“ vergisst,
  landet ohne Patient und nicht beim vorigen. Ein Auftrag verfällt nach 60 Minuten (`GDT_AUFTRAG_GUELTIG`).
- **Auftrag während einer laufenden Aufnahme** (anderer Patient, per „Messung starten“): Der Knopf
  beendet die laufende Aufnahme — sie geht wie nach ■ an ihren Patienten — und nimmt sofort für den neuen auf.
  Ein laufendes Diktat endet dabei ohne seinen noch nicht eingegebenen Rest. Derselbe Patient noch einmal
  ändert nichts.
- **Gesendet wird automatisch**, sobald die Dokumentation fertig ist (1–3 Minuten nach dem Stopp). Geprüft wird
  in T2med, der Kommentar (6227) sagt „KI-generiert … Vor Übernahme prüfen“.
- Liegt `T2MDSTNO.00x` noch im Ordner (T2med an diesem Arbeitsplatz nicht gestartet), wartet die nächste
  Dokumentation bis zu 60 Minuten (`STENO_SENDEN_WARTEN`) und überschreibt nichts. Es geht nichts verloren.

## Was in T2med ankommt

| Feld | Inhalt |
|---|---|
| 3000, 3101–3110 | Patientennummer, Name, Vorname, Geburtsdatum, Geschlecht aus dem Auftrag (ohne Auftrag: `3000=0`) |
| 6200/6201 | Beginn der Aufnahme |
| 8316 | `STENO` (Psychotherapie-Gerät: `STENO_PT`) |
| 8402 | `ALLG00` (`GDT_KENNFELD`) |
| 6220 (Befund) | die Dokumentation als Klartext (Markdown entfernt), eine Zeile je Feld, max. 60 Zeichen |
| 6227 (Kommentar) | „KI-generiert (Art, Version, Modell). Vor Übernahme prüfen.“, ggf. „Automatisch als Psychotherapie-Sitzung erkannt“, Schleifen-Warnung, Arbeitsplatz |
| 6302–6305 | nur mit `GDT_PVS_PFAD` und nie bei Psychotherapie: Textdatei mit Dokumentation und Transkript |

Name und Geburtsdatum gehen **nicht** an das Sprachmodell. Das Geschlecht aus dem Auftrag steuert nur
„der Patient / die Patientin“ im Text.

## 1. Austauschordner

Steno nutzt die Freigabe von sono-gdt mit. Sie ist in jedem Sprechzimmer schon als Netzlaufwerk
`S:` = `\\nas.praxis.example\sono-gdt` verbunden *(sono-gdt)*. Dort bekommt Steno einen eigenen Unterordner
**`S:\_steno`**. Ein zusätzliches Laufwerk muss also nicht verbunden werden.

- **Eigener Ordner statt `S:\_gdt`:** Die beiden Dienste bleiben getrennt, und der Container sieht nur
  `_steno`, also weder die Ultraschallbilder noch die Aufträge von sono-gdt.
- **Der Unterstrich ist nötig.** sono-gdt hält jeden anderen Ordner in der Freigabe für einen Patientenordner.
  Ordner, deren Name mit `_`, `.` oder `$` beginnt, überspringt es (wie `_gdt` und `_archiv`).

1. In `\\nas.praxis.example\sono-gdt` den Ordner `_steno` anlegen (z. B. im Explorer als `S:\_steno`).
2. Prüfen, ob `S:` an **jedem** Arbeitsplatz verbunden ist, auch an „Home“. Rechte: Lesen, Schreiben, Löschen
   (für sono-gdt ohnehin nötig).
3. Auf dem Docker-Rechner die Zugangsdaten in `.env` eintragen (dieselben wie für sono-gdt sind möglich).
   `docker-compose.yml` mountet `//nas.praxis.example/sono-gdt/_steno` als Volume `gdt-share` nach `/gdt`.
   ```
   GDT_SMB_DOMAIN=PRAXIS
   GDT_SMB_USER=Netadmin
   GDT_SMB_PASSWORD=...
   ```
   Domäne und Benutzer **getrennt**: Docker mountet über den Kernel, nicht über `mount.cifs`, und der versteht
   `PRAXIS\Netadmin` nicht (Fehler „permission denied“). Ein leeres `domain=` lehnt er ebenfalls ab („invalid
   argument“), Compose bricht deshalb ohne `GDT_SMB_DOMAIN` ab. Nach einer Änderung der Zugangsdaten das Volume
   neu anlegen, sonst behält Docker die alten Optionen: `docker compose down`,
   `docker volume rm transcribe-gdt_gdt-share`, `docker compose up -d`.
4. Optional für den Transkript-Anhang: `GDT_PVS_PFAD=S:\_steno`.
5. `docker compose up -d` im Projektordner. Das fasst nur `transcribe-gdt-web` an. Whisper, Sprechertrennung
   und ollama laufen weiter. Prüfen: `curl localhost:8011/steno` muss `"gdt": true` zeigen.

## 2. Gerät „Steno“ in T2med anlegen

Feld für Feld durch **Hauptmenü → Geräte verwalten → Eigene → Gerät konfigurieren** (auch erreichbar über
**Ext. Aufträge → Geräteverwaltung** unten rechts). Die Werte in `Code` müssen zu `docker-compose.yml` passen,
die Variable steht jeweils dabei.

Die Gerätekonfiguration gilt **je Arbeitsplatz** (Reiter *Eigene* = dieser Arbeitsplatz, *Weitere* = die
anderen). Einmal einrichten, an den anderen Arbeitsplätzen über *Weitere → Übernehmen* kopieren, mit dem
Bleistift bearbeiten und **nur die beiden Dateinamen** auf die eigene Endung ändern *(sono-gdt)*. **Nach jeder
Änderung T2med an diesem Arbeitsplatz neu starten.**

Werte ohne die Backticks aus dieser Datei und ohne Leerzeichen eintragen: Die Dateinamen haben genau 12 Zeichen,
das Kennfeld genau 6, mehr lässt T2med nicht zu („Der Dateiname darf höchstens 12 Zeichen lang sein“). In Feldern
ohne Längenprüfung (Verzeichnisse, GDT-IDs) nimmt T2med ein überzähliges Zeichen ohne Meldung an, das Gerät
funktioniert dann aber nicht.

| Raum | T2med-Arbeitsplatz | Dateiname Import | Dateiname Export | Steno-Knopf `arbeitsplatz` |
|---|---|---|---|---|
| Sprechzimmer 1 | Sprechzimmer 1 | `T2MDSTNO.001` | `STNOT2MD.001` | `001` |
| Sprechzimmer 2 | Sprechzimmer 2 | `T2MDSTNO.002` | `STNOT2MD.002` | `002` |
| Sprechzimmer 3 | Sprechzimmer 3 | `T2MDSTNO.003` | `STNOT2MD.003` | `003` |
| Home | Home | `T2MDSTNO.004` | `STNOT2MD.004` | `004` |

Auf dem Server passend dazu (`docker-compose.yml`):

```
GDT_ARBEITSPLAETZE=001=Sprechzimmer 1;002=Sprechzimmer 2;003=Sprechzimmer 3;004=Home
```

Ein weiterer Arbeitsplatz braucht dort einen Eintrag mit neuer Nummer und `docker compose up -d`. Eine Endung,
die der Server nicht kennt, lehnt er mit „Unbekannter Arbeitsplatz“ ab.

### Gerät und Messung

| Feld | Eintrag | Erklärung |
|---|---|---|
| Anzeigename | `Steno` | frei wählbar |
| Geräte- und verfahrensspezifisches Kennfeld (8402) | `ALLG00` | = `GDT_KENNFELD` |
| Test-Ident (8410) | leer *(sono-gdt)* | keine Einzelmesswerte |
| Satzart | „Neue Untersuchung anfordern“ *(sono-gdt)* | Das ist der Auftrag (6302), der die Aufnahme startet. |
| Kostenträger-IK / SSW / E-Mail / Telefon übertragen | aus *(sono-gdt)* | werden nicht gebraucht |
| GDT-ID Gerät | `STENO` | = `GDT_ID_GERAET` (8316) |
| GDT-ID T2med | `T2MED` (Vorgabe `T2MED_PX` überschreiben) *(sono-gdt)* | = `GDT_ID_PVS` (8315) |
| Zeichensatz | IBM CP437 (Standard) *(sono-gdt)* | = `GDT_ZEICHENSATZ=2` |
| Dezimalzeichen | Komma *(sono-gdt)* | |
| Langzeitmessung | aus *(sono-gdt)* | |
| **Annahme ohne Messauftrag** | **an** *(sono-gdt)* | sonst geht eine Aufnahme ohne „Messung starten“ verloren |
| Messgerät anzeigen | an | |

### Geräteanbindung

| Feld | Eintrag | Erklärung |
|---|---|---|
| Importverzeichnis | `S:\_steno` | hier legt Steno die Dokumentation ab. **Nicht** `S:\_gdt` (das ist sono-gdt) |
| Exportverzeichnis | `S:\_steno` *(sono-gdt: gleich wie Import)* | hier legt T2med den Auftrag ab |
| Programm | `C:\Windows\System32\cmd.exe` *(sono-gdt)* | Platzhalter. Der Steno-Knopf läuft ohnehin und reagiert auf die Auftragsdatei. |
| Parameter | `/c exit` *(sono-gdt)* | beendet sich sofort |
| **Betriebsmodus** | **„Aufruf für jede einzelne Messung“** *(sono-gdt)* | T2med holt die Datei damit etwa 10 s nach dem Ablegen ab, ohne Neustart. |
| Einzelverarbeitung nötig | aus *(sono-gdt)* | |
| Serienmessung unterstützt | aus *(sono-gdt)* | |
| über Gerätegruppe suchen | aus *(sono-gdt)* | |
| Zeitpunktbezug Messergebnis | „Zeitpunkt der Erhebung“ *(sono-gdt)* | 6200/6201 = Beginn der Aufnahme |
| Kürzel für Austauschdateien | `STNO` | |
| **Im- und Exportdateien festlegen** | **an** *(sono-gdt)* | |
| Dateiname Import | `T2MDSTNO.001` (2: `.002`, 3: `.003`, Home: `.004`) | = `GDT_DATEINAME=T2MDSTNO.{ap}` |
| Dateiname Export | `STNOT2MD.001` (2: `.002`, 3: `.003`, Home: `.004`) | = `GDT_AUFTRAG_DATEINAME=STNOT2MD.{ap}` |
| Verzeichnis ist Netzwerkpfad | aus *(sono-gdt)* | Das Verzeichnis liegt auf dem Netzlaufwerk `S:`, kein UNC-Pfad. |

### Anzeige von Befunden

| Feld | Eintrag | Erklärung |
|---|---|---|
| Sichtplatz (nur Befundanzeige) | aus *(sono-gdt)* | |
| Abweichendes Anzeigeprogramm | leer | |
| Kürzel Gerätegruppe | `STNO` | |
| Ergebnisse anzeigen | „In T2med bearbeiten“ | erprobt am 12.10.2026 (Home) |
| Parameter für Anzeigeprogramm | leer | |
| nachträgliche Befundung | „als neuen Befund“ | erprobt am 12.10.2026 (Home) |

### Übernahme von Befunden

| Feld | Eintrag | Erklärung |
|---|---|---|
| Aufbereitung durch | Dienst für allg. Messergebnisse (so lassen) *(sono-gdt)* | |
| Informationstyp | `Befund` | |
| Gewicht/Größe strukturiert übernehmen | aus | |
| **Uhrzeit und Datum übernehmen** | **an** *(sono-gdt)* | Eintrag bekommt den Aufnahmebeginn |
| Anhänge übernehmen | an, falls `GDT_PVS_PFAD` gesetzt ist | Transkript als Textdatei |
| Inhaltsbeschreibung übernehmen | an *(sono-gdt)* | „Transkript 05.10.2026 10:15“ |
| Einleseverzögerung | `10` ms *(sono-gdt)* | Steno legt die Datei fertig (atomar) ab |
| Automatische Zuordnung | „Letzter eAkteneintrag“ *(sono-gdt)* | Gegenprobe im Test, Schritt 7 |
| **Inhalte der freien Kategorie** | **„Messwerte jeweils mehrzeilig“** | „einzeilig“ (sono-gdt) zeigt nur „Befund Steno“, der Text erst per Doppelklick |
| **Kurzeintrag erstellen** | **an** *(sono-gdt)* | damit die Dokumentation (6220) in der Karteikarte steht |
| Anzahl der Zeilen | so hoch wie möglich (erprobt: `8`) | Begrenzt den Kurzeintrag. Mit `GDT_ZEILENBREITE=990` ist ein Absatz eine Zeile. Test, Schritt 6. |
| **Messergebnis Felder** | **alle** (DIAGNOSE, BEFUND, FREMDBEFUND, ERGEBNISSE, KOMMENTAR, FREIE_KATEGORIE, …) | BEFUND = Dokumentation (6220), KOMMENTAR = Prüfhinweis (6227) |

## 3. Zweites Gerät „Steno Psychotherapie“ (optional)

Für verhaltenstherapeutische Sitzungen. Wer dort „Messung starten“ klickt, bekommt die ausführliche
Sitzungszusammenfassung statt der Sprechstunden-Dokumentation. Alternativ lässt sich die Art am Knopf
umstellen (Rechtsklick → „Art des Gesprächs“), und ohne Vorgabe erkennt der Server lange Sitzungen
(ab 20 min) selbst.

Einrichtung: das Gerät „Steno“ über *Übernehmen* kopieren und nur diese Felder ändern:

| Feld | Eintrag |
|---|---|
| Anzeigename | `Steno Psychotherapie` |
| GDT-ID Gerät | `STENO_PT` (= `GDT_PSY_ID_GERAET`) |
| Kürzel für Austauschdateien / Gerätegruppe | `STNP` |
| Dateiname Import | `T2MDSTNP.001` (2: `.002`, 3: `.003`, Home: `.004`) |
| Dateiname Export | `STNPT2MD.001` (2: `.002`, 3: `.003`, Home: `.004`) |

Danach in `docker-compose.yml` die drei Zeilen `GDT_PSY_*` einkommentieren und `docker compose up -d`.
**Erst, wenn das Gerät an allen Arbeitsplätzen eingerichtet ist** (auch Home). Eine Sitzung ohne Auftrag geht an
`T2MDSTNP`, und an einem Arbeitsplatz ohne dieses Gerät holt T2med die Datei nie ab.

Das Ergebnis geht immer an das Gerät zurück, von dem der Auftrag kam, auch wenn die Art am Knopf umgestellt
wurde. Dort wartet T2med darauf.

## Debug: Was ging an T2med?

T2med löscht `T2MDSTNO.00x` nach dem Einlesen. Damit sich trotzdem nachsehen lässt, was ankam, legt der Server
bei gesetztem `GDT_DEBUG_DIR` je Übergabe eine Kopie ab. Auf dem Docker-Rechner liegt sie unter
`transcribe-gdt\logs\gdt-debug\`:

- `20261007_101733_004_abpro.txt` zum Lesen, in drei Teilen:
  1. die Antwort des Sprachmodells ungekürzt (das A/B/PRO, wie es aus dem Modell kam, mit Markdown),
  2. der Klartext für die Karteikarte, vor dem Umbruch auf 60 Zeichen,
  3. die GDT-Datei, wie T2med sie liest. Jede Zeile ist ein Feld, `…6220…` sind die Befundzeilen, `…6227…`
     der Kommentar.
- `20261007_101733_004_abpro.gdt`: dieselbe Datei Byte für Byte (CP437).

Steht etwas in T2med anders als in Teil 3, liegt es an der Geräteeinstellung in T2med („Anzahl der Zeilen“,
Zeichensatz). Steht es schon in Teil 1 falsch, liegt es am Sprachmodell.

Psychotherapie-Sitzungen werden nie kopiert, wie beim Logging. Die Dateien enthalten Patientennamen und
Dokumentation. Nach dem Einrichten deshalb `GDT_DEBUG_DIR=` leer setzen, `docker compose up -d` ausführen und
den Ordner leeren.

## Test zu Hause (Arbeitsplatz 004, Sprechstunde)

Voraussetzungen am Rechner zu Hause:

- Der T2med-Client läuft als Arbeitsplatz „Home“. Das Gerät „Steno“ ist wie oben eingerichtet, Dateinamen `.004`.
- `S:` ist mit `\\nas.praxis.example\sono-gdt` verbunden (über das VPN), und `S:\_steno` ist erreichbar.
- `https://steno.praxis.example/steno` öffnet sich im Browser. Die Route nimmt nur private Adressen
  an, von zu Hause aus also nur über das VPN.
- Der Steno-Knopf hat `arbeitsplatz = 004` in `%APPDATA%\Steno\steno.ini`.

Ablauf:

1. Testpatienten öffnen, **Messung starten**. Der Knopf zeigt nach 2–6 s den Namen und nimmt auf.
2. Den Ring auf **S-Stunde** (rot) lassen. Ohne Wahl ist das ohnehin die Vorgabe.
3. Ein bis zwei Minuten Sprechstunde spielen, dann **■**. Nach 1–3 Minuten meldet der Knopf „✓ an T2med“.
4. In T2med prüfen wie in Schritt 6 unten und die Debug-Kopie `logs\gdt-debug\…_004_abpro.txt` daneben legen.

## Erster Test (in Sprechzimmer 1)

1. Austauschordner einrichten (`S:\_steno`), Container neu starten, `curl localhost:8011/steno` prüfen.
2. Steno-Knopf installieren (`client/README.md`), Arbeitsplatz `001`.
3. T2med einrichten, speichern, **T2med neu starten**.
4. Testpatienten öffnen, **Messung starten**. Nach 2–6 s wird der Knopf rot und zeigt den Namen. Im Log
   (`docker compose logs -f transcribe-gdt-web`) steht `Messauftrag an Arbeitsplatz 001: …`.
   - Steht dort `STNOT2MD.001 ignoriert (… Felder …)`: T2med schreibt eine andere Satzart. Die Zeile an
     Claude/den Betreuer geben.
   - Steht dort nichts: Liegt `STNOT2MD.001` in `S:\_steno`? Exportverzeichnis und Dateiname prüfen.
5. Eine Minute sprechen, **■** klicken. Nach 1–3 Minuten erscheint `T2MDSTNO.001` in `S:\_steno` und verschwindet etwa
   10 s später, **ohne Neustart von T2med**. Der Knopf meldet „✓ an T2med“.
6. In T2med prüfen:
   - richtiger Patient, Datum/Uhrzeit = Aufnahmebeginn?
   - Umlaute korrekt? Sonst `GDT_ZEICHENSATZ=3` (und in T2med ISO 8859-1/CP1252) probieren.
   - **Zeilenumbrüche:** T2med zeigt jedes GDT-Feld als eigene Zeile. Mit der GDT-2.1-Empfehlung von 60 Zeichen
     stand ein A/B/PRO-Absatz zerstückelt auf mehreren Zeilen (12.10.2026), deshalb `GDT_ZEILENBREITE=990`
     (Formatgrenze). Mit einem langen Absatz prüfen, ob er vollständig ankommt; wird er abgeschnitten, die
     Breite senken.
   - Steht die ganze Dokumentation im Kurzeintrag oder nur die ersten Zeilen („Anzahl der Zeilen“)?
7. Gegenprobe ohne „Messung starten“: ▶ klicken, sprechen, ■. Die Dokumentation muss unter **Ext. Aufträge**
   erscheinen und darf **nicht** automatisch beim zuletzt geöffneten Patienten landen.
8. Patientenwechsel: „Messung starten“ für Patient A, dann während der Aufnahme für Patient B. Der Knopf
   wechselt ohne Rückfrage („… beendet · jetzt …“) → A bekommt die erste, B die zweite Dokumentation.
9. Mit Psychotherapie-Gerät: „Messung starten“ bei „Steno Psychotherapie“, Ψ am Knopf, Ergebnis kommt als
   Sitzungszusammenfassung über `T2MDSTNP.001` an.
