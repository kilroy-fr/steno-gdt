# T2med: KI-Transkription per GDT einrichten

KI-Transcribe übergibt die fertige Dokumentation als GDT-2.1-Satz 6310 an T2med. Klickt man vorher in
T2med beim Patienten auf **„Messung starten“**, kommt die Patientennummer aus diesem Auftrag (Satz 6302)
und T2med ordnet die Dokumentation automatisch zu. Das Muster stammt aus `sono-gdt` (Ultraschallbilder,
`../t2med-gdt`) und ist dort im Praxisbetrieb erprobt. Die Feldwerte unten folgen dessen
Einrichtung, angepasst an diesen Dienst.

## Ablauf

```
T2med "Messung starten" (Sprechzimmer 1) ──▶ <GDT-Ordner>/KITRT2MD.001   (6302, KI-Transcribe holt ab + löscht)
KI-Transcribe zeigt den Patienten an, die nächste Aufnahme gehört zu ihm
Analyse fertig, "An T2med senden"        ──▶ <GDT-Ordner>/T2MDKITR.001   (6310, T2med liest + löscht)
                                             <GDT-Ordner>/anhaenge/*.txt  (optional: Transkript als Anhang)
```

- **Ein Auftrag gilt für genau eine Aufnahme.** Beim Start der Aufnahme wird er ihr zugeordnet. Für den
  nächsten Patienten muss in T2med wieder „Messung starten“ geklickt werden. Wer das vergisst, landet
  **ohne** Patient (Patientennummer `0`) und nicht beim vorigen. Die Dokumentation erscheint dann in T2med unter
  **„Ext. Aufträge“** zur manuellen Zuordnung.
- Ein Auftrag verfällt nach 60 Minuten (`GDT_AUFTRAG_GUELTIG`). Ein neuerer Auftrag am selben
  Arbeitsplatz ersetzt den älteren.
- Kam der Auftrag erst während der Aufnahme an (zu spät geklickt), zeigt die Analyse den Knopf
  **„Zuordnen: <Name>“**. Ein Klick übernimmt ihn.
- Der Senden-Knopf nennt immer den Zielpatienten. Gesendet wird nur auf Klick, nie automatisch.
- Liegt `T2MDKITR.00x` noch im Ordner (T2med an diesem Arbeitsplatz nicht gestartet), meldet KI-Transcribe
  „noch nicht abgeholt“ und überschreibt nichts. Später erneut senden.

## Was in T2med ankommt

| Feld | Inhalt |
|---|---|
| 3000, 3101–3110 | Patientennummer, Name, Vorname, Geburtsdatum, Geschlecht aus dem Auftrag (ohne Auftrag: `3000=0`) |
| 6200/6201 | Beginn der Aufnahme |
| 8402 | `ALLG00` (`GDT_KENNFELD`) |
| 6220 (Befund) | die Dokumentation als Klartext (Markdown entfernt), eine Zeile je Feld, max. 60 Zeichen |
| 6227 (Kommentar) | „KI-generiert (Modus, Version, Modell). Vor Übernahme prüfen.“, ggf. die Schleifen-Warnung, Arbeitsplatz |
| 6302–6305 | nur mit `GDT_PVS_PFAD`: Textdatei mit Dokumentation (ungekürzt) und Transkript |

Name und Geburtsdatum gehen **nicht** an das Sprachmodell. Das Geschlecht aus dem Auftrag belegt nur die
Auswahl „Patient (m) / Patientin (w)“ vor.

## 1. Austauschordner

T2med (Windows) und der Container müssen denselben Ordner sehen. Zwei Möglichkeiten:

**a) Ordner auf dem Docker-Rechner** (Standard in `docker-compose.yml`): `./gdt` im Projektordner wird nach
`/gdt` gemountet. Den Ordner in Windows freigeben (z. B. als `ki-gdt`, Lesen/Schreiben/Löschen für die
T2med-Arbeitsplätze). In T2med heißt er dann `\\<docker-rechner>\ki-gdt`. Ein anderer Ordner auf dem
Docker-Rechner: `GDT_HOST_DIR=D:\gdt` in `.env`.

**b) NAS-Freigabe**, wie bei sono-gdt `\\192.168.2.70\sono-gdt`: in `docker-compose.yml` das Volume
`gdt-share` einkommentieren, Freigabe und Zugangsdaten (`GDT_SMB_USER`, `GDT_SMB_PASSWORD` in `.env`) eintragen
und den Mount auf `gdt-share:/gdt` umstellen. **Nicht** `_gdt` von sono-gdt mitbenutzen. Ein eigener Ordner
hält die beiden Dienste auseinander.

Danach in `docker-compose.yml` prüfen:

```
GDT_DIR=/gdt
GDT_ARBEITSPLAETZE=001=Sprechzimmer 1;002=Sprechzimmer 2;003=Sprechzimmer 3
GDT_PVS_PFAD=\\192.168.2.70\ki-transcribe-gdt     # optional, für den Transkript-Anhang
```

und im Ordner `transcribe-gdt` neu starten: `docker compose up -d`. Das fasst nur den eigenen Container
`transcribe-gdt-web` an, Whisper, Sprechertrennung und ollama laufen unberührt weiter.
`curl localhost:8011/` muss `"enabled": true` enthalten. Sonst existiert `GDT_DIR` im Container nicht.

## 2. Arbeitsplatz im Browser

In KI-Transcribe auf jedem Rechner über das **Zahnrad → T2med-Arbeitsplatz** den eigenen Arbeitsplatz wählen.
Die Wahl gilt für diesen Browser. Die Nummer muss zur Endung der Dateinamen passen, die T2med an diesem
Arbeitsplatz benutzt (unten).

## 3. Gerät in T2med anlegen

An jedem Arbeitsplatz unter **Hauptmenü → Geräte verwalten → Eigene → Gerät konfigurieren**. Einmal in
Sprechzimmer 1 einrichten, in den anderen über *Weitere → Übernehmen* kopieren und **nur die beiden
Dateinamen** auf `.002` bzw. `.003` ändern. **Nach jeder Änderung T2med an diesem Arbeitsplatz neu starten.**

### Gerät und Messung

| Feld | Eintrag | Erklärung |
|---|---|---|
| Anzeigename | `KI-Transkription` | frei wählbar |
| Geräte- und verfahrensspezifisches Kennfeld (8402) | `ALLG00` | = `GDT_KENNFELD` |
| Test-Ident (8410) | leer | keine Einzelmesswerte |
| Satzart | „Neue Untersuchung anfordern“ | Das ist der Auftrag (6302). |
| Kostenträger-IK / SSW / E-Mail / Telefon übertragen | aus | werden nicht gebraucht |
| GDT-ID Gerät | `KI_TRANS` | = `GDT_ID_GERAET` (8316) |
| GDT-ID T2med | `T2MED` (Vorgabe `T2MED_PX` überschreiben) | = `GDT_ID_PVS` (8315) |
| Zeichensatz | IBM CP437 (Standard) | = `GDT_ZEICHENSATZ=2` |
| Langzeitmessung | aus | |
| **Annahme ohne Messauftrag** | **an** | sonst geht eine Dokumentation ohne „Messung starten“ verloren |
| Messgerät anzeigen | an | |

### Geräteanbindung

| Feld | Eintrag | Erklärung |
|---|---|---|
| Import- und Exportverzeichnis | der Austauschordner aus Schritt 1, z. B. `\\192.168.2.70\ki-transcribe-gdt` | beide gleich |
| Programm / Parameter | `C:\Windows\System32\cmd.exe` / `/c exit` | Platzhalter, KI-Transcribe läuft als Dienst |
| **Betriebsmodus** | **„Programm läuft eigenständig“** | sonst liest T2med erst beim Neustart ein (Erfahrung aus sono-gdt) |
| Einzelverarbeitung / Serienmessung | aus | |
| Zeitpunktbezug Messergebnis | „Zeitpunkt der Erhebung“ | 6200/6201 = Beginn der Aufnahme |
| Kürzel für Austauschdateien | `KITR` | |
| **Im- und Exportdateien festlegen** | **an** | |
| Dateiname Import | `T2MDKITR.001` (Sprechzimmer 2: `.002`, 3: `.003`) | = `GDT_DATEINAME=T2MDKITR.{ap}` |
| Dateiname Export | `KITRT2MD.001` (Sprechzimmer 2: `.002`, 3: `.003`) | = `GDT_AUFTRAG_DATEINAME=KITRT2MD.{ap}` |
| Verzeichnis ist Netzwerkpfad | an | |

### Anzeige und Übernahme von Befunden

| Feld | Eintrag | Erklärung |
|---|---|---|
| Ergebnisse anzeigen | interne Anzeige | |
| Informationstyp | `Befund` | |
| **Uhrzeit und Datum übernehmen** | **an** | |
| Anhänge übernehmen | an, falls `GDT_PVS_PFAD` gesetzt ist | Transkript als Textdatei |
| Inhaltsbeschreibung übernehmen | an | „Transkript 03.10.2026 10:15“ |
| Einleseverzögerung | leer | die Datei wird fertig (atomar) abgelegt |
| Automatische Zuordnung | über die Patientennummer (beim ersten Test „Keine automatische Zuordnung“) | |
| **Kurzeintrag erstellen** | **an** | damit der Befundtext (6220) in der Karteikarte steht |

## Erster Test (in Sprechzimmer 1)

1. Austauschordner einrichten, Container neu starten, `"enabled": true` prüfen.
2. In KI-Transcribe den Arbeitsplatz „Sprechzimmer 1“ wählen.
3. T2med einrichten, speichern, **T2med neu starten**.
4. Testpatienten öffnen, **Messung starten**. Nach wenigen Sekunden steht er in KI-Transcribe unter
   „Patient aus T2med“, im Log (`docker compose logs -f transcribe-gdt-web`) steht `Messauftrag an Arbeitsplatz 001: …`.
   - Steht dort `KITRT2MD.001 ignoriert (… Felder …)`: T2med schreibt eine andere Satzart, die Zeile an
     Claude/den Betreuer geben.
   - Steht dort nichts: Liegt `KITRT2MD.001` im Austauschordner? Exportverzeichnis und Dateiname prüfen.
5. Kurze Aufnahme machen, analysieren, **An T2med senden**. `T2MDKITR.001` muss im Ordner erscheinen und
   wenige Sekunden später verschwinden, **ohne Neustart von T2med**.
6. In T2med prüfen:
   - richtiger Patient, Datum/Uhrzeit = Aufnahmebeginn?
   - Umlaute korrekt? Sonst `GDT_ZEICHENSATZ=3` (und in T2med ISO 8859-1/CP1252) probieren.
   - **Zeilenumbrüche:** GDT 2.1 begrenzt eine Zeile auf 60 Zeichen, die Dokumentation wird deshalb umgebrochen.
     Fügt T2med die 6220-Zeilen mit harten Umbrüchen zusammen, steht ein A/B/PRO-Absatz auf mehreren Zeilen.
     Dann `GDT_ZEILENBREITE=200` probieren (T2med akzeptiert längere Zeilen meist). Wird abgeschnitten, zurück auf 60.
   - Steht der Text in der Karteikarte (Kurzeintrag) oder nur im Befund?
   - Mit `GDT_PVS_PFAD`: Ist der Anhang da und lässt er sich öffnen? GDT 2.1 erlaubt in 6305 eigentlich nur
     60 Zeichen. Bei abgeschnittenen Pfaden ein Netzlaufwerk verwenden (`GDT_PVS_PFAD=K:\`).
7. Gegenprobe ohne „Messung starten“: Die Dokumentation muss unter **Ext. Aufträge** zur Zuordnung erscheinen.
8. Zwei Patienten nacheinander, beim zweiten **kein** „Messung starten“: Die zweite Dokumentation darf **nicht**
   beim ersten Patienten landen (Anzeige „Ohne Patient“).
