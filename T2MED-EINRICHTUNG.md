# T2med: Steno per GDT einrichten

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
T2med "Messung starten" (Sprechzimmer 1) ──▶ T:\STNOT2MD.001   (6302, Steno holt ab + löscht)
Steno-Knopf startet die Aufnahme von selbst (rot, ■, Patientenname)
Klick auf ■ ──▶ Aufnahme geht an den Server: Transkription, Sprechertrennung, Dokumentation
            ──▶ T:\T2MDSTNO.001   (6310, T2med liest + löscht, etwa 10 s später)
```

- **Ohne „Messung starten“** startet ein Klick auf ▶ die Aufnahme „ohne Patient“. Die Dokumentation
  landet dann in T2med unter **„Ext. Aufträge“** zur manuellen Zuordnung (Patientennummer `0`).
- **Ein Auftrag gilt für genau eine Aufnahme.** Wer beim nächsten Patienten „Messung starten“ vergisst,
  landet ohne Patient und nicht beim vorigen. Ein Auftrag verfällt nach 60 Minuten (`GDT_AUFTRAG_GUELTIG`).
- **Auftrag während einer laufenden Aufnahme** (anderer Patient): Der Knopf fragt, ob er die laufende Aufnahme
  beenden und für den neuen Patienten neu starten soll. Er wechselt nie ungefragt.
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

Wie bei sono-gdt eine eigene Freigabe auf dem NAS, als Netzlaufwerk verbunden *(sono-gdt)*:

1. Auf dem NAS die Freigabe `\\192.168.2.70\steno-gdt` anlegen. **Nicht** `sono-gdt\_gdt` mitbenutzen: Ein
   eigener Ordner hält die beiden Dienste auseinander.
2. An **jedem** Arbeitsplatz die Freigabe als Netzlaufwerk `T:` verbinden (Lesen, Schreiben, Löschen).
   Ein Laufwerksbuchstabe statt UNC-Pfad wie `S:` bei sono-gdt: das ist dort erprobt und hält die Pfade in
   6305 kurz.
3. Auf dem Docker-Rechner in `docker-compose.yml` das Volume `gdt-share` einkommentieren
   (`device: //192.168.2.70/steno-gdt`), Zugangsdaten `GDT_SMB_USER` und `GDT_SMB_PASSWORD` in `.env`
   eintragen und den Mount auf `gdt-share:/gdt` umstellen.
4. Optional für den Transkript-Anhang: `GDT_PVS_PFAD=T:\`.
5. `docker compose up -d` im Projektordner. Das fasst nur `transcribe-gdt-web` an. Whisper, Sprechertrennung
   und ollama laufen weiter. Prüfen: `curl localhost:8011/steno` muss `"gdt": true` zeigen.

## 2. Gerät „Steno“ in T2med anlegen

An jedem Arbeitsplatz unter **Hauptmenü → Geräte verwalten → Eigene → Gerät konfigurieren**. Einmal in
Sprechzimmer 1 einrichten, in den anderen über *Weitere → Übernehmen* kopieren, mit dem Bleistift bearbeiten
und **nur die beiden Dateinamen** auf `.002` bzw. `.003` ändern *(sono-gdt)*. **Nach jeder Änderung T2med an
diesem Arbeitsplatz neu starten.**

| Raum | T2med-Arbeitsplatz | Dateiname Import | Dateiname Export | Steno-Knopf `arbeitsplatz` |
|---|---|---|---|---|
| Sprechzimmer 1 | Sprechzimmer 1 | `T2MDSTNO.001` | `STNOT2MD.001` | `001` |
| Sprechzimmer 2 | Sprechzimmer 2 | `T2MDSTNO.002` | `STNOT2MD.002` | `002` |
| Sprechzimmer 3 | Sprechzimmer 3 | `T2MDSTNO.003` | `STNOT2MD.003` | `003` |

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
| Importverzeichnis | `T:\` | hier legt Steno die Dokumentation ab |
| Exportverzeichnis | `T:\` *(sono-gdt: gleich wie Import)* | hier legt T2med den Auftrag ab |
| Programm | `C:\Windows\System32\cmd.exe` *(sono-gdt)* | Platzhalter. Der Steno-Knopf läuft ohnehin und reagiert auf die Auftragsdatei. |
| Parameter | `/c exit` *(sono-gdt)* | beendet sich sofort |
| **Betriebsmodus** | **„Aufruf für jede einzelne Messung“** *(sono-gdt)* | T2med holt die Datei damit etwa 10 s nach dem Ablegen ab, ohne Neustart. |
| Einzelverarbeitung nötig | aus *(sono-gdt)* | |
| Serienmessung unterstützt | aus *(sono-gdt)* | |
| über Gerätegruppe suchen | aus *(sono-gdt)* | |
| Zeitpunktbezug Messergebnis | „Zeitpunkt der Erhebung“ *(sono-gdt)* | 6200/6201 = Beginn der Aufnahme |
| Kürzel für Austauschdateien | `STNO` | |
| **Im- und Exportdateien festlegen** | **an** *(sono-gdt)* | |
| Dateiname Import | `T2MDSTNO.001` (2: `.002`, 3: `.003`) | = `GDT_DATEINAME=T2MDSTNO.{ap}` |
| Dateiname Export | `STNOT2MD.001` (2: `.002`, 3: `.003`) | = `GDT_AUFTRAG_DATEINAME=STNOT2MD.{ap}` |
| Verzeichnis ist Netzwerkpfad | aus *(sono-gdt)* | Das Verzeichnis ist das Netzlaufwerk `T:`, kein UNC-Pfad. |

### Anzeige von Befunden

| Feld | Eintrag | Erklärung |
|---|---|---|
| Sichtplatz (nur Befundanzeige) | aus *(sono-gdt)* | |
| Abweichendes Anzeigeprogramm | leer | |
| Kürzel Gerätegruppe | `STNO` | |
| Ergebnisse anzeigen | „In T2med anzeigen“ *(sono-gdt)* | |
| Parameter für Anzeigeprogramm | leer | |
| nachträgliche Befundung | keine nachträgliche Befundung *(sono-gdt)* | |

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
| Inhalte der freien Kategorie | „Messwerte jeweils einzeilig“ *(sono-gdt)* | |
| **Kurzeintrag erstellen** | **an** *(sono-gdt)* | damit die Dokumentation (6220) in der Karteikarte steht |
| Anzahl der Zeilen | so hoch wie möglich | sono-gdt braucht 5 Zeilen für einen Kurzbefund, eine Dokumentation hat 10–40 Zeilen. Test, Schritt 6. |
| Messergebnis Felder | leer | |

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
| Dateiname Import | `T2MDSTNP.001` (2: `.002`, 3: `.003`) |
| Dateiname Export | `STNPT2MD.001` (2: `.002`, 3: `.003`) |

Danach in `docker-compose.yml` die drei Zeilen `GDT_PSY_*` einkommentieren und `docker compose up -d`.
**Erst, wenn das Gerät an allen drei Arbeitsplätzen eingerichtet ist.** Eine Sitzung ohne Auftrag geht an
`T2MDSTNP`, und an einem Arbeitsplatz ohne dieses Gerät holt T2med die Datei nie ab.

Das Ergebnis geht immer an das Gerät zurück, von dem der Auftrag kam, auch wenn die Art am Knopf umgestellt
wurde. Dort wartet T2med darauf.

## Erster Test (in Sprechzimmer 1)

1. Austauschordner einrichten (`T:` verbunden), Container neu starten, `curl localhost:8011/steno` prüfen.
2. Steno-Knopf installieren (`client/README.md`), Arbeitsplatz `001`.
3. T2med einrichten, speichern, **T2med neu starten**.
4. Testpatienten öffnen, **Messung starten**. Nach 2–6 s wird der Knopf rot und zeigt den Namen. Im Log
   (`docker compose logs -f transcribe-gdt-web`) steht `Messauftrag an Arbeitsplatz 001: …`.
   - Steht dort `STNOT2MD.001 ignoriert (… Felder …)`: T2med schreibt eine andere Satzart. Die Zeile an
     Claude/den Betreuer geben.
   - Steht dort nichts: Liegt `STNOT2MD.001` auf `T:`? Exportverzeichnis und Dateiname prüfen.
5. Eine Minute sprechen, **■** klicken. Nach 1–3 Minuten erscheint `T2MDSTNO.001` auf `T:` und verschwindet etwa
   10 s später, **ohne Neustart von T2med**. Der Knopf meldet „✓ an T2med“.
6. In T2med prüfen:
   - richtiger Patient, Datum/Uhrzeit = Aufnahmebeginn?
   - Umlaute korrekt? Sonst `GDT_ZEICHENSATZ=3` (und in T2med ISO 8859-1/CP1252) probieren.
   - **Zeilenumbrüche:** GDT 2.1 begrenzt eine Zeile auf 60 Zeichen, die Dokumentation wird deshalb umgebrochen.
     Steht ein A/B/PRO-Absatz zerstückelt auf mehreren Zeilen, `GDT_ZEILENBREITE=200` probieren. Wird dann
     abgeschnitten, zurück auf 60.
   - Steht die ganze Dokumentation im Kurzeintrag oder nur die ersten Zeilen („Anzahl der Zeilen“)?
7. Gegenprobe ohne „Messung starten“: ▶ klicken, sprechen, ■. Die Dokumentation muss unter **Ext. Aufträge**
   erscheinen und darf **nicht** automatisch beim zuletzt geöffneten Patienten landen.
8. Patientenwechsel: „Messung starten“ für Patient A, dann während der Aufnahme für Patient B. Der Knopf muss
   fragen. „Wechseln“ → A bekommt die erste, B die zweite Dokumentation.
9. Mit Psychotherapie-Gerät: „Messung starten“ bei „Steno Psychotherapie“, Ψ am Knopf, Ergebnis kommt als
   Sitzungszusammenfassung über `T2MDSTNP.001` an.
