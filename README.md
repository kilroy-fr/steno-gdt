# KI-Transcribe Pro

Lokales Transkriptions- und Diktiersystem für medizinische Dokumentation. Bestehend aus einem Docker-basierten FastAPI-Server mit Browser-UI für Gesprächsanalyse sowie einem Windows-Desktop-Client für Direktdiktat.

## 🎯 Features

### 🌐 Web-UI: Gesprächsanalyse

- Audio-Upload oder direkte Aufnahme im Browser
- Vollständige Transkription via faster-whisper (GPU)
- **Deutsches Medizin-Modell** (`jimmymeister/whisper-large-v3-turbo-german-ct2`) für bessere Fachbegriff-Erkennung
- Sprachschalter im Frontend: „Gespräch nicht auf Deutsch" wechselt auf Standard-Modell mit Auto-Spracherkennung
- KI-gestützte Analyse des Arzt-Patienten-Gesprächs (ollama LLM)
- Strukturierte Ausgabe: Zusammenfassung, Anamnese, Diagnosen, Maßnahmen
- Modi: Vollanalyse, Kompaktdokumentation (A/B/PRO), Zusammenfassung, Bereinigung
- Geschlechterauswahl (Patient / Patientin / neutral) für LLM-Ausgabe
- Automatische Betreffzeile per KI
- Live-Streaming der LLM-Antwort (SSE)

### 🎙️ Windows-Client: Diktat

- Direktes Diktieren mit Live-Transkription
- Echtzeit-Vorschau des transkribierten Texts
- Perfekt für schnelle Notizen und Dokumentation

### ✨ Gemeinsame Features

- 🔄 Kontinuierliche Audio-Aufnahme in 30-Sekunden-Chunks
- 📝 Live-Transkription während der Aufnahme
- ✏️ Editierbares Ergebnisfenster
- 📎 Automatische Zwischenablage-Integration
- 💾 Speichert Roh-Transkripte als Backup
- ⏸️ Pause/Resume während der Aufnahme

---

## 📋 Voraussetzungen

### Server
- Docker & Docker Compose
- (Optional) NVIDIA GPU für schnellere Verarbeitung
- Mindestens 4 GB RAM
- Bestehender **ollama Container** mit Modell (z.B. llama3.1:8b)

### Client (Windows)
- Python 3.8+
- Windows 10/11
- Mikrofon

---

## 🚀 Installation

### 1️⃣ Server Setup

#### Mit bestehendem ollama Container

```bash
# Verbinde ollama mit Diktat-Netzwerk
docker network create diktat-network
docker network connect diktat-network <dein-ollama-container-name>

# Starte Server
docker-compose up -d
```

#### Test Server

```bash
# Status prüfen
curl http://localhost:8000/

# Health-Check (whisper + ollama + version)
curl http://localhost:8000/health
```

---

### 2️⃣ Client Setup (Windows)

**Option A: Installer (empfohlen)**

Lade `Output\KI-Transcribe-Installer.exe` herunter und führe ihn aus. Keine Admin-Rechte erforderlich.

**Option B: Python direkt**

```cmd
# Installiere Python-Dependencies
pip install -r requirements-client.txt

# GUI starten
python diktat_gui.py
```

#### Konfiguration

Beim ersten Start öffnet sich automatisch ein Dialog zur Eingabe der Server-URL. Die Konfiguration wird in `%APPDATA%\KI-Transcribe-Pro\config.json` gespeichert und kann jederzeit über Menü → Einstellungen → Server-URL ändern angepasst werden.

---

## 🎮 Nutzung

### Starten

1. **Server**: `docker-compose up -d` (einmalig)
2. **Web-UI**: Browser öffnen → URL des Servers (z.B. `http://transcribe.domain.local`)
3. **Diktat-Client**: `python diktat_gui.py` oder EXE starten

### Workflow

#### Web-UI: Gesprächsanalyse

1. Browser öffnen → Server-URL aufrufen
2. Audio-Datei hochladen oder direkt im Browser aufnehmen
3. Modus wählen: **Analysieren**, **Kompakt (A/B/PRO)**, **Zusammenfassung** oder **Bereinigen**
4. Geschlecht wählen (Patient / Patientin / neutral)
5. LLM-Modell wählen (falls mehrere verfügbar)
6. Verarbeitung startet → Transkript und Analyse erscheinen live
7. Text kopieren oder weiterverarbeiten

#### Diktat-Client (Windows-GUI)

1. GUI starten → Server-URL beim ersten Start eingeben
2. Klicke **Start** → Aufnahme läuft
3. Sprich dein Diktat
4. Sieh Live-Transkription im Textfeld
5. Klicke **Analyse** → Finalisierung und Speicherung
6. Editierbares Ergebnis-Fenster öffnet sich
7. Text wird automatisch in Zwischenablage kopiert

### Tastenkombinationen

Die GUI nutzt Maus-Bedienung. Für Hotkey-Support kann das optionale AHK-Script verwendet werden (siehe unten).

---

## ⚙️ Konfiguration

### Server-Einstellungen

Bearbeite `docker-compose.yml`:

```yaml
environment:
  # Whisper-Einstellungen
  - WHISPER_LANGUAGE=de

  # Ollama-Modell (Standard-Modell für die Web-UI)
  - OLLAMA_MODEL=qwen2.5:14b
  - OLLAMA_HOST=ollama
```

Das Whisper-Modell wird im `faster-whisper` Container konfiguriert:

```yaml
faster-whisper:
  environment:
    - WHISPER__MODEL=large-v3          # Standard-Modell (Fallback / Auto-Sprache)
    - WHISPER__ALLOW_DOWNLOADS=true    # Erlaubt Download zusätzlicher Modelle

gespraech-web:
  environment:
    - WHISPER_MEDICAL_MODEL=jimmymeister/whisper-large-v3-turbo-german-ct2  # Deutsches Medizin-Modell
```

Das Medizin-Modell wird beim ersten deutschen Aufruf automatisch von HuggingFace heruntergeladen (~3 GB) und danach im Volume gecacht. Im Frontend schaltet „Gespräch nicht auf Deutsch" auf das Standard-Modell mit automatischer Spracherkennung zurück.

### Whisper-Modelle

| Modell | Größe | Geschwindigkeit | Qualität | Empfehlung |
|--------|-------|----------------|----------|------------|
| `tiny` | 39 MB | ⚡⚡⚡⚡⚡ | ⭐⭐ | Nur Tests |
| `base` | 74 MB | ⚡⚡⚡⚡ | ⭐⭐⭐ | **Standard** |
| `small` | 244 MB | ⚡⚡⚡ | ⭐⭐⭐⭐ | Balance |
| `medium` | 769 MB | ⚡⚡ | ⭐⭐⭐⭐⭐ | Hohe Qualität |
| `large-v3` | 1.5 GB | ⚡ | ⭐⭐⭐⭐⭐ | Beste Qualität |

### Ollama-Modelle

```bash
# Im ollama Container
docker exec -it <ollama-container> bash

# Modelle herunterladen
ollama pull llama3.1:8b      # Empfohlen
ollama pull mistral:7b
ollama pull qwen2.5:7b

# Liste Modelle
ollama list
```

### Audio-Einstellungen

Bearbeite `diktat_gui.py`:

```python
SAMPLE_RATE = 16000        # Audio-Qualität (8000-48000 Hz)
CHUNK_DURATION = 30        # Sekunden pro Chunk (10-60)
```

---

## 🐛 Troubleshooting

### Problem: "Verbindung zum Server fehlgeschlagen"

```bash
# 1. Prüfe Server-Status
docker ps | grep diktat

# 2. Teste Server
curl http://localhost:8000/

# 3. Prüfe Logs
docker logs diktat-api

# 4. Firewall (falls Server im LAN)
sudo ufw allow 8000/tcp  # Linux
```

### Problem: "faster-whisper nicht erreichbar"

```bash
# Container-Logs
docker logs diktat-whisper

# Neu starten
docker restart diktat-whisper
```

### Problem: "ollama nicht gefunden"

```bash
# Prüfe ollama
docker ps | grep ollama

# Verbinde mit Netzwerk
docker network connect diktat-network <ollama-container>

# Test
curl http://localhost:11434/api/tags
```

### Problem: "Keine Audio-Aufnahme"

1. **Mikrofon-Berechtigungen** prüfen (Windows-Einstellungen)
2. **Standard-Mikrofon** setzen (Windows Sound-Einstellungen)
3. **Python-Test**:
   ```python
   import sounddevice as sd
   print(sd.query_devices())
   ```

### Problem: "Transkription ist schlecht"

1. **Besseres Modell**: `WHISPER_MODEL=small` statt `base`
2. **Näher am Mikrofon** sprechen
3. **Ruhige Umgebung** wählen
4. **Externes Mikrofon** verwenden

### Problem: "Text-Säuberung zu aggressiv/schwach"

Bearbeite in `prompts.py` die Konstante `CLEAN_PROMPT`:

```python
CLEAN_PROMPT = """Bereinige das folgende medizinische Transkript:
- Entferne Füllwörter (äh, ähm, hm, ...)
# Hier anpassen für mehr/weniger Säuberung
...
"""
```

Danach neu bauen:
```bash
docker-compose down
docker-compose up -d --build
```

---

## 🔧 Erweiterte Features

### GPU-Beschleunigung

**faster-whisper mit GPU:**

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

**ollama mit GPU:**

```yaml
ollama:
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
```

### Automatischer Start (Windows)

Erstelle `start_diktat.bat`:

```batch
@echo off
cd /d "%~dp0"
start /min python diktat_gui.py
```

Verknüpfung in Windows-Autostart-Ordner (`Win+R` → `shell:startup`) ablegen.

---

## 📁 Dateistruktur

```
ki-transcribe/
├── server.py                  # FastAPI Server (Web-UI + API)
├── prompts.py                 # LLM-Prompts (ANALYZE, ABPRO, SUMMARY, SUBJECT, CLEAN)
├── diktat_gui.py              # Windows-GUI-Client (Diktat)
├── benchmark_summary.py       # Speed-Benchmark: Prompts gegen alle Whitelist-Modelle
├── Dockerfile                 # Docker-Build für server.py
├── docker-compose.yml         # Docker-Setup
├── requirements-client.txt    # Client-Python-Dependencies
├── requirements-server.txt    # Server-Python-Dependencies
├── README.md                  # Diese Datei
├── QUICKSTART.md              # Schnellstart-Anleitung
├── BUILD_README.md            # Build-Anleitung für Windows-Installer
│
├── templates/index.html       # Web-UI HTML-Template
├── static/                    # CSS, JS, Favicon
│
├── diktat_gui.spec            # PyInstaller-Konfiguration
├── installer.iss              # Inno Setup Script
├── build_installer.bat        # One-Click Build
├── Output/                    # Fertiger Windows-Installer
├── dist/                      # PyInstaller-Output (EXE)
│
└── legacy/                    # Alter AHK+SoX CLI-Client (nur Referenz)
    ├── diktat_client.py       # CLI-Client (veraltet)
    └── diktat_sox.ahk         # AutoHotkey-Script (veraltet)
```

---

## 🎯 Alternative: AHK-Hotkey-System (Optional)

Für Nutzer, die Hotkeys bevorzugen, ist das alte AHK-System weiterhin verfügbar:

### Setup

1. **AutoHotkey v2** installieren
2. **SoX** installieren (https://sourceforge.net/projects/sox/)
3. `diktat_sox.ahk` ausführen

### Hotkeys

| Hotkey | Aktion |
|--------|--------|
| `Ctrl+Shift+D` | Aufnahme Start/Stop |
| `Ctrl+Shift+S` | Abbrechen |

**Hinweis:** AHK-Methode unterstützt nur einfaches Diktat, keine Ambient-Funktion oder Live-Transkription.

---

## 🤝 Nützliche Kommandos

```bash
# Container-Status
docker-compose ps

# Logs anschauen
docker-compose logs -f

# Container neu starten
docker-compose restart

# Alles stoppen
docker-compose down

# Neu bauen
docker-compose up -d --build

# Ins Container-Terminal
docker exec -it diktat-api bash
```

---

## 📝 Changelog

### Version 0.6.1 (aktuell)
- **Deutsches Medizin-Whisper-Modell** (`jimmymeister/whisper-large-v3-turbo-german-ct2`) für bessere Erkennung medizinischer Fachbegriffe
- **Sprachschalter** im Frontend: „Gespräch nicht auf Deutsch" wechselt auf Standard-`large-v3` mit Auto-Spracherkennung
- Neuer Env-Var `WHISPER_MEDICAL_MODEL` für konfigurierbares Medizin-Modell
- `WHISPER__ALLOW_DOWNLOADS=true` im Whisper-Container für On-Demand-Modell-Download
- Statusmeldung zeigt aktives Modell: „Transkribiere Audio (DE-Medizin)..." / „(Sprache Auto)..."

### Version 0.5.x
- Neuer Modus **Kompakt (A/B/PRO)** (`abpro`) für Telegrammstil-Dokumentation
- Geschlechterauswahl (Patient / Patientin / neutral) für LLM-Output
- Automatische KI-Betreffzeile (`/subject`-Endpoint)
- Endpoint `/analyze-text`: LLM-Analyse ohne Whisper-Schritt (Direkteingabe)
- Unterdrückung bekannter Whisper-Halluzinationen (Stille-Artefakte)
- Alle LLM-Prompts in separater `prompts.py`
- `benchmark_summary.py` für Modell-Benchmarks

### Version 0.1.x
- Web-UI: Gesprächsanalyse mit LLM-Streaming (SSE)
- Web-UI: Modellauswahl im Browser
- Diktat-Client: Config-Dialog beim ersten Start
- Diktat-Client: Konfiguration über Menü änderbar
- FastAPI-Server mit Jinja2-Templates und Static Files
- faster-whisper Transkription (GPU)
- ollama LLM-Analyse (Streaming)
- Windows-GUI-Client (Diktat-Modus)
- Windows-Installer via PyInstaller + Inno Setup

### Version 1.0 (Legacy)
- Initiales Release mit AHK+SoX-System
- faster-whisper Integration
- ollama LLM-Säuberung
- Docker-Setup

---

## ✨ Credits

Basierend auf:
- [Asklaion](https://github.com/lollylan/asklaion) - Ambient Scribe Konzept
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) - Transkription
- [ollama](https://ollama.ai/) - LLM-Säuberung

---

## 📜 Lizenz

Dieses Projekt nutzt:
- **faster-whisper**: MIT License
- **ollama**: MIT License
- **AutoHotkey v2**: GPL-2.0
- **SoX**: GPL-2.0
