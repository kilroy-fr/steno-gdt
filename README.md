# Steno

Schreibt Arzt-Patienten-Gespräche und Psychotherapie-Sitzungen mit, fasst sie zusammen und übergibt die Dokumentation per GDT an T2med. Lokal: faster-whisper für die Transkription, pyannote für die Sprechertrennung, ollama für die Zusammenfassung.

Abzweig von [`ki-transcribe`](../ki-transcribe). Dieser Ordner bringt den Server mit (`transcribe-gdt-web`, Port 8011) und den Steno-Knopf für die Arbeitsplätze ([`client/`](client/README.md)). Whisper, Sprechertrennung und ollama werden mitbenutzt: `diktat-whisper` und `diktat-diarize` aus `ki-transcribe`, `ollama` aus seinem eigenen Container.

## 🔴 Steno-Knopf

Ein runder Knopf, der immer vor T2med liegt:

1. In T2med beim Patienten **„Messung starten“**. Der Knopf wird rot (■) und nimmt auf.
2. Am Ende **■** klicken. 1–3 Minuten später steht die Dokumentation in der Karteikarte, als KI-generiert gekennzeichnet.

Ohne „Messung starten“ nimmt ein Klick auf ▶ „ohne Patient“ auf, und die Dokumentation landet unter „Ext. Aufträge“. Für verhaltenstherapeutische Sitzungen gibt es eine eigene, ausführlichere Zusammenfassung. Sie wird über ein zweites T2med-Gerät, per Rechtsklick am Knopf oder bei langen Aufnahmen automatisch gewählt. Aufgenommen wird nur auf Anforderung, nie dauerhaft.

Einrichtung: [`client/README.md`](client/README.md) (Knopf) und [`T2MED-EINRICHTUNG.md`](T2MED-EINRICHTUNG.md) (T2med, Austauschordner). Nachschau im Browser: `/verlauf`.

## 🎯 Features

### 🌐 Web-UI: Gesprächsanalyse

- Audio-Upload oder direkte Aufnahme im Browser
- Vollständige Transkription via faster-whisper (GPU)
- **Deutsches Medizin-Modell** (`jimmymeister/whisper-large-v3-turbo-german-ct2`) für bessere Fachbegriff-Erkennung
- Sprachschalter im Frontend: „Gespräch nicht auf Deutsch" wechselt auf Standard-Modell mit Auto-Spracherkennung
- **Medizinisches Fachvokabular** (`medical_vocab.py`): kuratiertes Glossar aus Diagnosen, Wirkstoffen, Impfstoffen und Untersuchungen, das dem LLM mitgegeben wird, damit phonetisch verzerrte Begriffe erkannt statt geraten werden
- KI-gestützte Analyse des Arzt-Patienten-Gesprächs (ollama LLM)
- **Sprechertrennung (Diarisation)** via pyannote.audio: trennt Arzt, Patient und weitere Anwesende im Transkript (`SPRECHER A:` / `SPRECHER B:` …). Läuft auf der GPU in einem eigenen Container, parallel zur Transkription. Jeder Satz wird geschlossen einem Sprecher zugeordnet, damit Wechsel nicht mitten im Satz liegen
- Sprecherzuordnung: das LLM erschließt aus dem Gesprächsverlauf, welcher Marker der Arzt ist – so werden Arzt-Überlegungen nicht als gesicherte Diagnosen und Patienten-Rückfragen nicht als Verordnungen dokumentiert. Ohne Diarisation greift weiterhin die reine Kontext-Heuristik
- **Vollständige Dokumentation auch langer Gespräche:** das Kontextfenster des LLM wird an die Transkriptlänge angepasst (`OLLAMA_NUM_CTX`), und die Prompts verlangen jedes Anliegen einzeln – gerade bei Vorsorgeuntersuchungen mit mehreren unabhängigen Beschwerden
- **Schutz vor Whisper-Wiederholschleifen:** gepatchter Whisper-Router (`whisper-patch/` in `ki-transcribe`), VAD, automatisches Kürzen verbliebener Schleifen und eine Warnung, wenn dem Transkript dadurch Gespräch fehlt
- Modi: Vollanalyse, Kompaktdokumentation (A/B/PRO, Standard), Psychotherapie-Sitzung, Zusammenfassung, Bereinigung
- A/B/PRO-Modus zeigt zusätzlich drei separat kopierbare Karten (Anamnese / Befund / Procedere)
- Geschlechterauswahl (Patient / Patientin / neutral) für LLM-Ausgabe, auch nachträglich per Rerun auf der Analyse-Karte
- „Nächster Patient"-Button: beendet Aufnahme, startet Analyse und sofort eine neue Aufnahme
- Verlauf mehrerer Analysen innerhalb einer Sitzung
- Automatische Betreffzeile per KI
- Live-Streaming der LLM-Antwort (SSE)
- Einstellungen-Modal (Zahnrad-Icon): KI-Modell-Auswahl, Mikrofon-Auswahl, Logging-Modus
- Optionaler Logging-Modus: speichert Transkript + Analyse als JSON auf dem Server (`LOG_DIR`)
- **Übergabe an T2med per GDT:** „Messung starten“ in T2med legt den Patienten fest, „An T2med senden“ schreibt die Dokumentation als Befund in seine Akte (Einrichtung: [`T2MED-EINRICHTUNG.md`](T2MED-EINRICHTUNG.md))
- Live-Health-Anzeige für Whisper, Ollama und Sprechertrennung im Header

### ✨ Bedienung

- 🎙️ Direktaufnahme im Browser oder Datei-Upload (Drag & Drop)
- 📝 Editierbares Ergebnisfenster mit Markdown-Rendering
- 📎 Kopieren einzelner Abschnitte per Klick
- ⏸️ Aufnahme pausierbar

---

## 📋 Voraussetzungen

- Docker & Docker Compose
- Laufendes **`ki-transcribe`** mit `diktat-whisper` und `diktat-diarize` im Netzwerk `diktat-network` (GPU, Hugging-Face-Token usw. siehe dort)
- Laufender **ollama-Container** mit Modell (z. B. `gemma4:12b`) im Netzwerk `ollama-net`
- Netzwerk `caddy-proxy-network` (Caddy-Reverse-Proxy)

---

## 🚀 Installation

```bash
# Nur die Web-App bauen und starten. Whisper, Sprechertrennung und ollama
# werden dabei weder gestartet noch gestoppt (kein depends_on).
docker compose up -d --build
```

### Test

```bash
# UI erreichbar? ("enabled": true = GDT-Ordner gefunden)
curl http://localhost:8011/

# Health-Check: whisper, ollama und diarize müssen "ok" melden
curl http://localhost:8011/health
```

`docker-compose.yml` bindet Port `8011` auf `localhost` (8010 gehört `ki-transcribe`). Für die Arbeitsplätze braucht es eine Route im Caddy-Reverse-Proxy (`caddy-proxy-network`), denn das Mikrofon funktioniert im Browser nur über HTTPS oder auf `localhost`.

---

## 🎮 Nutzung

1. **Server starten**: `docker compose up -d` (einmalig)
2. **Web-UI öffnen**: Browser → Server-URL (auf dem Docker-Rechner `http://localhost:8011`)
3. Audio-Datei hochladen oder direkt im Browser aufnehmen
4. Modus wählen: **Vollanalyse**, **A/B/PRO** (Standard), **Zusammenfassung** oder **Bereinigen**
5. Geschlecht wählen (Patient / Patientin / neutral)
6. Bei Bedarf über das Zahnrad-Icon KI-Modell oder Mikrofon wechseln
7. Verarbeitung startet → Transkript und Analyse erscheinen live (SSE-Stream)
8. Text abschnittsweise kopieren oder mit „Nächster Patient" direkt weiter aufnehmen

**Mit T2med:** vor der Aufnahme in T2med beim Patienten „Messung starten“ klicken. Der Patient erscheint links
unter „Patient aus T2med“ und gehört zur nächsten Aufnahme. Nach der Analyse über **An T2med senden** übergeben;
der Knopf nennt den Zielpatienten. Ohne „Messung starten“ landet die Dokumentation in T2med unter
„Ext. Aufträge“ zur manuellen Zuordnung. Ein Auftrag gilt für genau eine Aufnahme.

---

## ⚙️ Konfiguration

### Server-Einstellungen

Bearbeite `docker-compose.yml` (Service `transcribe-gdt-web`):

```yaml
environment:
  - WHISPER_HOST=diktat-whisper      # geteilter Container aus ki-transcribe
  - WHISPER_PORT=8000
  - OLLAMA_HOST=ollama
  - OLLAMA_PORT=11434
  - OLLAMA_MODEL=gemma4:12b        # Standard-Modell für die Web-UI
  - OLLAMA_NUM_CTX=16384           # Kontextfenster (Untergrenze, wächst bei langen Gesprächen)
  - WHISPER_LANGUAGE=de
  - WHISPER_MEDICAL_MODEL=jimmymeister/whisper-large-v3-turbo-german-ct2
  - DIARIZE_HOST=diktat-diarize      # geteilter Container aus ki-transcribe
  - DIARIZE_PORT=8000
  - DIARIZE_MIN_SPEAKERS=0         # 0 = pyannote schätzt die Sprecherzahl selbst
  - DIARIZE_MAX_SPEAKERS=0         # 0 = ohne Obergrenze (Begleitperson, MFA)
  - AUDIO_TIMEOUT=1800             # Wartezeit (s) auf Whisper und Diarisation
  - LOOP_WARN_SHARE=0.15           # ab diesem Schleifenanteil warnt die Oberfläche
  - LOG_DIR=/logs                  # Ziel für optionalen Logging-Modus
  - TZ=Europe/Berlin               # Ortszeit für T2med und Log-Dateinamen
  - GDT_DIR=/gdt                   # GDT-Austauschordner mit T2med (leer = Anbindung aus)
  - GDT_ARBEITSPLAETZE=001=Sprechzimmer 1;002=Sprechzimmer 2;003=Sprechzimmer 3
```

Alle `GDT_*`-Variablen und die Einrichtung in T2med: [`T2MED-EINRICHTUNG.md`](T2MED-EINRICHTUNG.md).

### Whisper und Sprechertrennung (geteilt)

`diktat-whisper` und `diktat-diarize` sind in `../ki-transcribe/docker-compose.yml` definiert und
werden dort gebaut, konfiguriert und neu gestartet. Sie dienen zugleich `ki-transcribe` und
`ki-atteste`. Einrichtung (Hugging-Face-Token, GPU, Whisper-Patch) siehe README von `ki-transcribe`.

Im Frontend schaltet der Toggle „Sprechertrennung" die Funktion pro Analyse ab. Fällt der
Diarisations-Container aus, läuft die Analyse ohne Sprecherkennzeichnung weiter. Die Transkription
bricht **nicht** ab.

### Kontextfenster des LLM (`OLLAMA_NUM_CTX`)

Ohne Vorgabe arbeitet ollama auf einer 16-GB-Karte mit nur **4096 Tokens** Kontext
und schneidet längere Prompts **vorne** ab – stillschweigend, nur mit
`truncating input prompt` im ollama-Log. Das Modell sieht dann nur das
Gesprächsende; Beschwerden vom Anfang fehlen in der Dokumentation. Bis
September 2026 betraf das zwei Drittel aller Analysen.

`server.py` setzt `num_ctx` deshalb bei jedem Aufruf: mindestens
`OLLAMA_NUM_CTX`, bei langen Gesprächen in 8k-Schritten mehr. Bei `gemma4:12b`
kosten 16k Kontext nur ~260 MiB VRAM; bei Modellen ohne Gleitfenster-Aufmerksamkeit
(qwen3, llama) ein Vielfaches. Prüfen, ob noch etwas abgeschnitten wird:

```bash
docker logs ollama 2>&1 | grep "truncating input prompt"
docker logs transcribe-gdt-web 2>&1 | grep "Prompt-Tokens"
```

### Erlaubte Ollama-Modelle (Frontend-Dropdown)

Nur Modelle mit passendem Namens-Präfix erscheinen in der Modellauswahl (`MODEL_WHITELIST` in `server.py`):

`qwen3:`, `qwen2.5:`, `phi4`, `mistral-nemo`, `deepseek-r1:14b`, `gemma4:`, `llama3.1:`, `meditron`

```bash
# Im ollama-Container
docker exec -it <ollama-container> bash

ollama pull gemma4:12b      # Standard
ollama pull qwen2.5:14b
ollama list
```

### Analyse-Prompts anpassen

Die Prompts für alle Modi liegen zentral in `prompts.py` (`ANALYZE_PROMPT`, `ABPRO_PROMPT`, `SUMMARY_PROMPT`, `SUBJECT_PROMPT`, `CLEAN_PROMPT`). Die Vollständigkeitsregeln (`COMPLETENESS_RULES`) stehen in Vollanalyse und A/B/PRO bewusst **nach** dem Transkript – bei langen Gesprächen wirken Anweisungen am Ende stärker. Nach Änderungen:

```bash
docker compose restart transcribe-gdt-web
```

(`server.py`, `prompts.py`, `medical_vocab.py`, `gdt.py`, `templates/` und `static/` sind read-only in den Container gemounted — es ist kein Rebuild nötig, außer bei Änderungen an `requirements-server.txt` oder dem `Dockerfile`.)

### Fachvokabular erweitern

`medical_vocab.py` enthält eine kuratierte Liste deutscher Hausarzt-Begriffe. `MEDICAL_GLOSSARY` wird in `ANALYZE_PROMPT`, `ABPRO_PROMPT` und `CLEAN_PROMPT` eingebettet, damit das LLM verzerrte ASR-Ausgaben gegen bekannte Fachbegriffe abgleichen kann. Neue Begriffe einfach in die passende Liste eintragen und den Container neu starten.

> **Hinweis:** Das Glossar wird bewusst **nicht** als faster-whisper `hotwords`-Parameter verwendet — eine Liste dieser Größe sprengt dessen 448-Token-Decoder-Budget und lässt Whisper mit `ValueError: The maximum decoding length must be > 0` abstürzen.

---

## 🐛 Troubleshooting

### Problem: „Verbindung zum Server fehlgeschlagen"

```bash
docker ps | grep transcribe-gdt-web
curl http://localhost:8011/health
docker logs transcribe-gdt-web
```

### Problem: „faster-whisper nicht erreichbar"

```bash
docker logs diktat-whisper
docker restart diktat-whisper
```

### Problem: „ollama nicht gefunden"

```bash
docker ps | grep ollama
docker network connect ollama-net <ollama-container>
curl http://localhost:11434/api/tags
```

### Problem: „Keine Audio-Aufnahme"

1. Mikrofon-Berechtigung im Browser erteilen (wird beim ersten Aufnahme-Klick angefragt)
2. Richtiges Mikrofon im Einstellungen-Modal (Zahnrad-Icon) auswählen
3. Betriebssystem-Mikrofonrechte für den Browser prüfen

### Mikrofon: EPOS Expand 40

Die Web-App schaltet Rauschunterdrückung, automatische Pegelregelung und
Echounterdrückung des Browsers ab (`MIC_PROCESSING` in `static/app.js`) – die
Konferenzspinne erledigt das selbst, die Browser-Verarbeitung schneidet leise,
entfernte Stimmen mit weg und hebt in Pausen das Rauschen an, aus dem Whisper
halluziniert.

1. Per **USB** anschließen, nicht per Bluetooth-Direktverbindung: das
   Bluetooth-Headset-Profil überträgt das Mikrofon nur schmalbandig
2. Mittig zwischen Arzt und Patient stellen, weg von PC-Lüfter und Tastatur
3. Da der Browser den Pegel nicht mehr nachregelt: Windows-Aufnahmepegel des
   Geräts einmal prüfen (Probeaufnahme, beide Stimmen gut verständlich)

### Problem: „In der Dokumentation fehlen Beschwerden"

1. ollama-Log auf `truncating input prompt` prüfen (siehe *Kontextfenster des
   LLM*) – falls vorhanden, `OLLAMA_NUM_CTX` erhöhen
2. Transkript prüfen: fehlt der Inhalt schon dort, liegt es an der Aufnahme
   (Pegel, Abstand) oder an einer Wiederholschleife (gelbe Warnung)
3. Anderes Modell über das Zahnrad-Icon testen; die Regeln dazu stehen in
   `COMPLETENESS_RULES` (`prompts.py`)

### Problem: „Transkription ist schlecht"

1. Näher am Mikrofon sprechen, ruhige Umgebung wählen
2. Bei nicht-deutschen Gesprächen den Toggle „Gespräch nicht auf Deutsch" aktivieren
3. `WHISPER__MODEL` im `faster-whisper`-Container auf ein größeres Modell stellen (z. B. `large-v3`, bereits Standard)
4. Häufig falsch erkannte Fachbegriffe in `medical_vocab.py` ergänzen — das LLM gleicht verzerrte Stellen dann gegen das Glossar ab

### Problem: „Sprechertrennung nicht verfügbar"

Die Analyse läuft dann bewusst ohne Sprecherkennzeichnung weiter. Ursache prüfen:

```bash
curl http://127.0.0.1:8002/health      # {"status": "ok"} erwartet
docker logs --tail 50 diktat-diarize
```

Behoben wird es in `ki-transcribe` (siehe dort, „Sprechertrennung nicht verfügbar“).
Der Health-Punkt „Sprecher" im Header bleibt rot, solange der Container startet.

### Problem: „Sprecher werden falsch zugeordnet"

Ein Raummikrofon liefert einen Mono-Kanal — die Trennung ist gut, aber nicht
perfekt; Fehler sitzen vor allem an Sprecherwechseln und bei kurzen Einwürfen.

1. Mikrofon mittig zwischen Arzt und Patient platzieren (Freisprecheinrichtungen
   wie das EPOS Expand 40 sind deutlich besser als Laptop-Mikrofone, siehe oben)
2. Bei mehr als zwei Anwesenden ist das erwartbar — die Prompts sind darauf
   ausgelegt, bei Widersprüchen dem Inhalt statt dem Marker zu folgen
3. `DIARIZE_MIN_SPEAKERS`/`DIARIZE_MAX_SPEAKERS` nur setzen, wenn die
   Personenzahl wirklich feststeht — eine harte Vorgabe von 2 ordnet Aussagen
   von Begleitpersonen falsch zu

### Problem: „network diktat-network declared as external, but could not be found"

`ki-transcribe` legt das Netzwerk an. Dort einmal `docker compose up -d` laufen lassen (oder
`docker network create diktat-network`), danach hier erneut starten. Dasselbe gilt für
`ollama-net` (ollama) und `caddy-proxy-network` (Caddy).

### Problem: „Text-Säuberung / Analyse zu aggressiv/schwach"

Bearbeite die passende Konstante in `prompts.py` (z. B. `CLEAN_PROMPT`, `ANALYZE_PROMPT`) und starte den Container neu:

```bash
docker compose restart transcribe-gdt-web
```

---

## 🔧 Erweiterte Features

### Logging-Modus

Über das Einstellungen-Modal aktivierbar. Speichert bei jeder Analyse Transkript + Ergebnis als JSON in `LOG_DIR` (Server-seitig, Dateiname `YYYYMMDD_HHMMSS_<mode>.json`). Standardmäßig deaktiviert.

---

## 📁 Dateistruktur

```
transcribe-gdt/
├── server.py                  # FastAPI-Server (Web-UI + API)
├── prompts.py                 # LLM-Prompts (ANALYZE, ABPRO, PSYCHO, SUMMARY, SUBJECT, CLEAN)
├── medical_vocab.py           # Kuratiertes Fachvokabular (MEDICAL_GLOSSARY)
├── gdt.py                     # GDT 2.1: Messauftrag aus T2med lesen, Dokumentation zurückgeben
├── client/                    # Steno-Knopf für die Arbeitsplätze (Windows-exe, siehe client/README.md)
├── T2MED-EINRICHTUNG.md       # T2med-Geräteeinrichtung, Austauschordner, erster Test
├── Dockerfile                 # Docker-Build für server.py
├── docker-compose.yml         # nur transcribe-gdt-web; Whisper/Diarisation/ollama extern
├── requirements-server.txt    # Server-Python-Dependencies
├── VERSION                    # Aktuelle Versionsnummer
├── README.md                  # Diese Datei
├── CLAUDE.md                  # Technische Referenz für KI-gestützte Entwicklung
│
├── templates/index.html       # Web-UI HTML-Template (Jinja2)
├── templates/verlauf.html     # Nachschau der Knopf-Aufträge (/verlauf)
├── static/                    # CSS, JS, Favicon, Logo
├── logs/                      # Ziel für optionalen Logging-Modus (LOG_DIR)
└── gdt/                       # GDT-Austauschordner mit T2med (Standard-Mount, gitignored)
```

---

## 🤝 Nützliche Kommandos

Alle Befehle betreffen nur `transcribe-gdt-web`. Whisper, Sprechertrennung und ollama bleiben unberührt.

```bash
# Container-Status
docker compose ps

# Logs anschauen
docker compose logs -f

# Container neu starten (z. B. nach Prompt-Änderung)
docker compose restart transcribe-gdt-web

# Neu bauen (nach Dependency- oder Dockerfile-Änderung)
docker compose up -d --build

# Stoppen
docker compose down

# Ins Container-Terminal
docker exec -it transcribe-gdt-web bash
```

---

## 📝 Changelog

### Version 0.6.9 (aktuell)
- **Medizinisches Fachvokabular** (`medical_vocab.py`): kuratiertes Glossar aus Diagnosen, Wirkstoffen, Impfstoffen und Untersuchungen, eingebettet in `ANALYZE_PROMPT`, `ABPRO_PROMPT` und `CLEAN_PROMPT`
- **Sprecherzuordnung** in `ANALYZE_PROMPT` und `ABPRO_PROMPT`: expliziter Hinweis auf den Dialog ohne Sprecherkennzeichnung, damit Verdachtsdiagnosen des Arztes nicht als gesicherte Diagnosen und Patienten-Rückfragen nicht als Verordnungen dokumentiert werden
- `prompts.py` und `medical_vocab.py` sind jetzt ebenfalls live in den Container gemounted — Prompt-Änderungen brauchen nur noch einen `restart` statt eines Rebuilds

### Version 0.6.7
- Finale Transkription läuft über die komplette Aufnahme statt über zusammengesetzte Chunks — durchgehender Kontext, weniger Halluzinationen an Segmentgrenzen

### Version 0.6.5
- Windows-Diktat-Client entfernt — reine Web-Lösung
- Standard-Ollama-Modell auf `gemma4:12b` gewechselt
- Mikrofon-Auswahl im Einstellungen-Modal behält die zuvor gewählte Permission-Liste bei
- faster-whisper teilt sich Netzwerk `caddy-proxy-network` (gemeinsam mit `ki-atteste`)

### Version 0.6.1
- **Deutsches Medizin-Whisper-Modell** (`jimmymeister/whisper-large-v3-turbo-german-ct2`) für bessere Erkennung medizinischer Fachbegriffe
- **Sprachschalter** im Frontend: „Gespräch nicht auf Deutsch" wechselt auf Standard-`large-v3` mit Auto-Spracherkennung
- Neuer Env-Var `WHISPER_MEDICAL_MODEL` für konfigurierbares Medizin-Modell
- `WHISPER__ALLOW_DOWNLOADS=true` im Whisper-Container für On-Demand-Modell-Download

### Version 0.5.x
- Logging-Modus (`/logging-mode`-Endpoint) zum optionalen Speichern von Transkript + Analyse
- Neuer Modus **A/B/PRO** (`abpro`) für Telegrammstil-Dokumentation, inkl. separat kopierbarer Karten
- „Nächster Patient"-Flow und Verlaufsansicht mehrerer Analysen
- Geschlechterauswahl (Patient / Patientin / neutral) für LLM-Output, inkl. nachträglichem Rerun
- Automatische KI-Betreffzeile (`/subject`-Endpoint)
- Endpoint `/analyze-text`: LLM-Analyse ohne Whisper-Schritt (Direkteingabe)
- Unterdrückung bekannter Whisper-Halluzinationen (Stille-Artefakte)
- Alle LLM-Prompts in separater `prompts.py`
- Modellauswahl hinter Zahnrad-Einstellungsmodal versteckt

### Version 0.1.x
- Web-UI: Gesprächsanalyse mit LLM-Streaming (SSE)
- Web-UI: Modellauswahl im Browser
- FastAPI-Server mit Jinja2-Templates und Static Files
- faster-whisper Transkription (GPU)
- ollama LLM-Analyse (Streaming)

---

## ✨ Credits

Basierend auf:
- [Asklaion](https://github.com/lollylan/asklaion) - Ambient Scribe Konzept
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) - Transkription
- [ollama](https://ollama.ai/) - LLM-Analyse

---

## 📜 Lizenz

Dieses Projekt nutzt:
- **faster-whisper**: MIT License
- **ollama**: MIT License
