# CLAUDE.md — KI-Transcribe Pro

## Projektübersicht

Medizinisches Transkriptions- und Diktiersystem, bestehend aus zwei unabhängigen Komponenten:

1. **Web-App / FastAPI-Server** (`server.py`) — läuft als Docker-Container, bietet eine Browser-UI für Arzt-Patienten-Gesprächsanalyse sowie eine REST-API für Transkription und LLM-Analyse.
2. **Windows-Desktop-Client** (`diktat_gui.py`) — tkinter-GUI für Live-Diktat, verteilt als standalone EXE per Windows-Installer.

---

## Architektur

```
Browser ──► Caddy (Reverse Proxy) ──► gespraech-web (FastAPI, Port 8000)
                                           ├── faster-whisper (Transkription, GPU)
                                           └── ollama (LLM, externer Container)

Windows-PC ──► diktat_gui.exe ──► http://transcribe.domain.local/transcribe-only
```

**Netzwerke (Docker):**
- `diktat-network` — intern: gespraech-web ↔ faster-whisper
- `ollama-net` — extern, vordefiniert: gespraech-web ↔ ollama
- `caddy-proxy-network` — extern, vordefiniert: Caddy ↔ gespraech-web

---

## Dateistruktur

```
ki-transcribe/
├── server.py                  # FastAPI-Server (Web-UI + API)
├── diktat_gui.py              # Windows-GUI-Client (Diktat)
├── Dockerfile                 # Docker-Image für server.py
├── docker-compose.yml         # Docker-Setup (gespraech-web + faster-whisper)
├── requirements-server.txt    # Server-Python-Dependencies
├── requirements-client.txt    # Client-Python-Dependencies (für EXE-Build)
│
├── templates/index.html       # Jinja2-Template für Web-UI
├── static/                    # CSS, JS, SVG für Web-UI
│
├── diktat_gui.spec            # PyInstaller-Config für EXE-Build
├── installer.iss              # Inno Setup Script für Windows-Installer
├── build_installer.bat        # One-Click Build: EXE + Installer
├── Output/                    # Build-Output: KI-Transcribe-Installer.exe
├── dist/                      # PyInstaller-Output: standalone EXE
│
├── legacy/                    # Alter AHK+SoX CLI-Client (nur Referenz)
│
├── README.md                  # Hauptdokumentation
├── QUICKSTART.md              # Schnellstart
└── BUILD_README.md            # Build-Anleitung für Windows-Installer
```

---

## Server-Endpoints (`server.py`)

| Methode | Pfad | Beschreibung |
|---------|------|--------------|
| GET | `/` | Web-UI (Jinja2-Template) |
| GET | `/health` | Status whisper + ollama |
| GET | `/models` | Liste verfügbarer ollama-Modelle |
| POST | `/transcribe-only` | Nur Transkription via faster-whisper |
| POST | `/analyze` | Transkription + LLM (SSE-Stream) |

**`/analyze` SSE-Event-Typen:** `status`, `transcript`, `token`, `error`, `done`

---

## Konfiguration (Umgebungsvariablen)

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `WHISPER_HOST` | `faster-whisper` | Hostname des Whisper-Containers |
| `WHISPER_PORT` | `8000` | Port |
| `OLLAMA_HOST` | `ollama` | Hostname des ollama-Containers |
| `OLLAMA_PORT` | `11434` | Port |
| `OLLAMA_MODEL` | `qwen3:8b` | Standard-Modell |
| `WHISPER_LANGUAGE` | `de` | Sprache für Transkription |

---

## Server entwickeln / lokal testen

```bash
# Python-Abhängigkeiten installieren
pip install -r requirements-server.txt

# Server starten (erwartet Whisper + ollama via Env-Vars)
uvicorn server:app --reload --port 8000
```

Mit Docker:
```bash
docker-compose up -d
docker-compose logs -f gespraech-web
```

---

## Windows-Client bauen (EXE + Installer)

Voraussetzungen: Python 3.8+, Inno Setup 6.x

```cmd
# One-Click Build (aus dem Repo-Root)
build_installer.bat
```

Manuell:
```cmd
pip install pyinstaller
pip install -r requirements-client.txt
pyinstaller diktat_gui.spec --clean
iscc installer.iss
```

**Output:**
- `dist\KI-Transcribe-Pro\KI-Transcribe-Pro.exe` — standalone EXE
- `Output\KI-Transcribe-Installer.exe` — Windows-Installer (keine Admin-Rechte nötig)

**Client-Konfiguration** wird beim ersten Start via Dialog abgefragt und in `%APPDATA%\KI-Transcribe-Pro\config.json` gespeichert. Änderbar über Menü → Einstellungen → Server-URL.

---

## Wichtige Hinweise

- **Diktat-Client vs. Web-UI sind unabhängig:** Der Windows-Client (`diktat_gui.py`) sendet Audio direkt an den Endpunkt `/transcribe-only` des Servers. Die Web-UI (`/`) ist ein separates Interface für ausführlichere Gesprächsanalyse mit LLM.
- **Ollama ist external:** Der ollama-Container wird nicht in dieser `docker-compose.yml` definiert — er muss bereits laufen und mit `ollama-net` verbunden sein.
- **GPU erforderlich für faster-whisper:** Der Container nutzt `runtime: nvidia`. Ohne GPU muss das Image auf `latest-cpu` geändert werden.
- **Diktat-Variante:** Es existiert eine zweite Variante der Build-Dateien (`*_diktat.*`) — diese sind identisch zur Hauptvariante und dienen als separate Distribution unter dem Namen "Diktat".
