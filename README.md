# KI-Transcribe Pro

Lokales Web-System für medizinische Gesprächsdokumentation. Docker-basierter FastAPI-Server mit Browser-UI: Audio aufnehmen oder hochladen, per faster-whisper transkribieren und per ollama-LLM strukturiert dokumentieren.

## 🎯 Features

### 🌐 Web-UI: Gesprächsanalyse

- Audio-Upload oder direkte Aufnahme im Browser
- Vollständige Transkription via faster-whisper (GPU)
- **Deutsches Medizin-Modell** (`jimmymeister/whisper-large-v3-turbo-german-ct2`) für bessere Fachbegriff-Erkennung
- Sprachschalter im Frontend: „Gespräch nicht auf Deutsch" wechselt auf Standard-Modell mit Auto-Spracherkennung
- **Medizinisches Fachvokabular** (`medical_vocab.py`): kuratiertes Glossar aus Diagnosen, Wirkstoffen, Impfstoffen und Untersuchungen, das dem LLM mitgegeben wird, damit phonetisch verzerrte Begriffe erkannt statt geraten werden
- KI-gestützte Analyse des Arzt-Patienten-Gesprächs (ollama LLM)
- Sprecherzuordnung: die Prompts weisen das LLM auf den Dialogcharakter ohne Sprecherkennzeichnung hin, damit Arzt-Überlegungen nicht als gesicherte Diagnosen und Patienten-Rückfragen nicht als Verordnungen dokumentiert werden
- Modi: Vollanalyse, Kompaktdokumentation (A/B/PRO, Standard), Zusammenfassung, Bereinigung
- A/B/PRO-Modus zeigt zusätzlich drei separat kopierbare Karten (Anamnese / Befund / Procedere)
- Geschlechterauswahl (Patient / Patientin / neutral) für LLM-Ausgabe, auch nachträglich per Rerun auf der Analyse-Karte
- „Nächster Patient"-Button: beendet Aufnahme, startet Analyse und sofort eine neue Aufnahme
- Verlauf mehrerer Analysen innerhalb einer Sitzung
- Automatische Betreffzeile per KI
- Live-Streaming der LLM-Antwort (SSE)
- Einstellungen-Modal (Zahnrad-Icon): KI-Modell-Auswahl, Mikrofon-Auswahl, Logging-Modus
- Optionaler Logging-Modus: speichert Transkript + Analyse als JSON auf dem Server (`LOG_DIR`)
- Live-Health-Anzeige für Whisper und Ollama im Header

### ✨ Bedienung

- 🎙️ Direktaufnahme im Browser oder Datei-Upload (Drag & Drop)
- 📝 Editierbares Ergebnisfenster mit Markdown-Rendering
- 📎 Kopieren einzelner Abschnitte per Klick
- ⏸️ Aufnahme pausierbar

---

## 📋 Voraussetzungen

- Docker & Docker Compose
- NVIDIA GPU für faster-whisper (Standard-Image nutzt CUDA; ohne GPU auf `latest-cpu` umstellen)
- Mindestens 4 GB RAM
- Bestehender **ollama-Container** mit Modell (z. B. `gemma4:12b`), erreichbar über das externe Netzwerk `ollama-net`
- (Optional) Caddy oder anderer Reverse-Proxy im externen Netzwerk `caddy-proxy-network`

---

## 🚀 Installation

### Mit bestehendem ollama-Container

```bash
# Netzwerke, falls noch nicht vorhanden
docker network create diktat-network
docker network connect diktat-network <dein-ollama-container-name>

# Server starten
docker-compose up -d
```

### Test

```bash
# UI erreichbar?
curl http://localhost:8010/

# Health-Check (whisper + ollama + version)
curl http://localhost:8010/health
```

`docker-compose.yml` bindet Port `8010` auf `localhost` — Zugriff von außen läuft über den Reverse-Proxy im `caddy-proxy-network`.

---

## 🎮 Nutzung

1. **Server starten**: `docker-compose up -d` (einmalig)
2. **Web-UI öffnen**: Browser → Server-URL (z. B. `http://transcribe.domain.local`)
3. Audio-Datei hochladen oder direkt im Browser aufnehmen
4. Modus wählen: **Vollanalyse**, **A/B/PRO** (Standard), **Zusammenfassung** oder **Bereinigen**
5. Geschlecht wählen (Patient / Patientin / neutral)
6. Bei Bedarf über das Zahnrad-Icon KI-Modell oder Mikrofon wechseln
7. Verarbeitung startet → Transkript und Analyse erscheinen live (SSE-Stream)
8. Text abschnittsweise kopieren oder mit „Nächster Patient" direkt weiter aufnehmen

---

## ⚙️ Konfiguration

### Server-Einstellungen

Bearbeite `docker-compose.yml` (Service `gespraech-web`):

```yaml
environment:
  - WHISPER_HOST=faster-whisper
  - WHISPER_PORT=8000
  - OLLAMA_HOST=ollama
  - OLLAMA_PORT=11434
  - OLLAMA_MODEL=gemma4:12b        # Standard-Modell für die Web-UI
  - WHISPER_LANGUAGE=de
  - WHISPER_MEDICAL_MODEL=jimmymeister/whisper-large-v3-turbo-german-ct2
  - LOG_DIR=/logs                  # Ziel für optionalen Logging-Modus
```

Das Whisper-Modell wird im `faster-whisper`-Container konfiguriert:

```yaml
faster-whisper:
  environment:
    - WHISPER__MODEL=large-v3          # Standard-Modell (Fallback / Auto-Sprache)
    - WHISPER__ALLOW_DOWNLOADS=true    # Erlaubt Download zusätzlicher Modelle
```

Das Medizin-Modell wird beim ersten deutschen Aufruf automatisch von HuggingFace heruntergeladen (~3 GB) und danach im Volume `whisper-models` gecacht. Im Frontend schaltet „Gespräch nicht auf Deutsch" auf das Standard-Modell mit automatischer Spracherkennung zurück.

> **Hinweis:** Der `faster-whisper`-Container (`diktat-whisper`) wird mit dem Projekt `ki-atteste` geteilt. Änderungen an Modell-Env-Vars wirken sich auf beide Projekte aus.

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

Die Prompts für alle Modi liegen zentral in `prompts.py` (`ANALYZE_PROMPT`, `ABPRO_PROMPT`, `SUMMARY_PROMPT`, `SUBJECT_PROMPT`, `CLEAN_PROMPT`). Nach Änderungen:

```bash
docker-compose restart gespraech-web
```

(`server.py`, `prompts.py`, `medical_vocab.py`, `templates/` und `static/` sind read-only in den Container gemounted — es ist kein Rebuild nötig, außer bei Änderungen an `requirements-server.txt` oder dem `Dockerfile`.)

### Fachvokabular erweitern

`medical_vocab.py` enthält eine kuratierte Liste deutscher Hausarzt-Begriffe. `MEDICAL_GLOSSARY` wird in `ANALYZE_PROMPT`, `ABPRO_PROMPT` und `CLEAN_PROMPT` eingebettet, damit das LLM verzerrte ASR-Ausgaben gegen bekannte Fachbegriffe abgleichen kann. Neue Begriffe einfach in die passende Liste eintragen und den Container neu starten.

> **Hinweis:** Das Glossar wird bewusst **nicht** als faster-whisper `hotwords`-Parameter verwendet — eine Liste dieser Größe sprengt dessen 448-Token-Decoder-Budget und lässt Whisper mit `ValueError: The maximum decoding length must be > 0` abstürzen.

---

## 🐛 Troubleshooting

### Problem: „Verbindung zum Server fehlgeschlagen"

```bash
docker ps | grep gespraech-web
curl http://localhost:8010/
docker logs gespraech-web
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

### Problem: „Transkription ist schlecht"

1. Näher am Mikrofon sprechen, ruhige Umgebung wählen
2. Bei nicht-deutschen Gesprächen den Toggle „Gespräch nicht auf Deutsch" aktivieren
3. `WHISPER__MODEL` im `faster-whisper`-Container auf ein größeres Modell stellen (z. B. `large-v3`, bereits Standard)
4. Häufig falsch erkannte Fachbegriffe in `medical_vocab.py` ergänzen — das LLM gleicht verzerrte Stellen dann gegen das Glossar ab

### Problem: „container … is not connected to the network ki-transcribe_diktat-network"

Tritt beim Recreate auf, wenn neben `diktat-network` noch ein leeres, projekt-präfigiertes Netzwerk aus früheren Compose-Versionen existiert. Beide tragen dasselbe Compose-Label, Compose greift auf das falsche zu:

```bash
# Prüfen, dass das Alt-Netzwerk leer ist (Containers: 0)
docker network inspect ki-transcribe_diktat-network --format 'Containers: {{len .Containers}}'

# Dann entfernen
docker network rm ki-transcribe_diktat-network
docker compose up -d
```

### Problem: „Text-Säuberung / Analyse zu aggressiv/schwach"

Bearbeite die passende Konstante in `prompts.py` (z. B. `CLEAN_PROMPT`, `ANALYZE_PROMPT`) und starte den Container neu:

```bash
docker-compose restart gespraech-web
```

---

## 🔧 Erweiterte Features

### GPU-Beschleunigung

`faster-whisper` läuft standardmäßig mit `fedirz/faster-whisper-server:latest-cuda` und reserviert eine NVIDIA-GPU:

```yaml
faster-whisper:
  image: fedirz/faster-whisper-server:latest-cuda
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
```

Ohne GPU: Image auf `fedirz/faster-whisper-server:latest-cpu` ändern und den `deploy`-Block entfernen.

### Logging-Modus

Über das Einstellungen-Modal aktivierbar. Speichert bei jeder Analyse Transkript + Ergebnis als JSON in `LOG_DIR` (Server-seitig, Dateiname `YYYYMMDD_HHMMSS_<mode>.json`). Standardmäßig deaktiviert.

---

## 📁 Dateistruktur

```
ki-transcribe/
├── server.py                  # FastAPI-Server (Web-UI + API)
├── prompts.py                 # LLM-Prompts (ANALYZE, ABPRO, SUMMARY, SUBJECT, CLEAN)
├── medical_vocab.py           # Kuratiertes Fachvokabular (MEDICAL_GLOSSARY)
├── Dockerfile                 # Docker-Build für server.py
├── docker-compose.yml         # Docker-Setup (gespraech-web + faster-whisper)
├── requirements-server.txt    # Server-Python-Dependencies
├── VERSION                    # Aktuelle Versionsnummer
├── README.md                  # Diese Datei
├── QUICKSTART.md              # Schnellstart-Anleitung
├── CLAUDE.md                  # Technische Referenz für KI-gestützte Entwicklung
│
├── templates/index.html       # Web-UI HTML-Template (Jinja2)
├── static/                    # CSS, JS, Favicon, Logo
└── logs/                      # Ziel für optionalen Logging-Modus (LOG_DIR)
```

---

## 🤝 Nützliche Kommandos

```bash
# Container-Status
docker-compose ps

# Logs anschauen
docker-compose logs -f

# Container neu starten (z. B. nach Prompt-Änderung)
docker-compose restart gespraech-web

# Neu bauen (nach Dependency- oder Dockerfile-Änderung)
docker-compose up -d --build

# Nur die Web-App neu bauen, ohne den geteilten Whisper-Container zu stoppen
docker compose up -d --build --no-deps gespraech-web

# Alles stoppen
docker-compose down

# Ins Container-Terminal
docker exec -it gespraech-web bash
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
