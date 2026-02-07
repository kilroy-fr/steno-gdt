# KI-Transcribe Pro

Lokales Transkriptions- und Diktiersystem mit automatischer KI-gestützter Textsäuberung.

## 🎯 Features

### 🎙️ Zwei Arbeitsmodi

**Ambient-Modus**
- Zeichnet Gespräche kontinuierlich auf
- Ideal für Arzt-Patienten-Gespräche oder Meetings
- Transkribiert und analysiert das komplette Gespräch
- Standard-Befunde per Knopfdruck einfügen

**Diktat-Modus**
- Direktes Diktieren mit Live-Transkription
- Echtzeit-Vorschau des transkribierten Texts
- Perfekt für schnelle Notizen und Dokumentation

### ✨ Weitere Features

- 🔄 Kontinuierliche Audio-Aufnahme in 30-Sekunden-Chunks
- 📝 Live-Transkription während der Aufnahme
- 🤖 KI-gestützte Textsäuberung (entfernt Füllwörter, korrigiert Fehler)
- 📋 13 vordefinierte Standard-Befunde
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
cd diktat-service/

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

# Health-Checks
curl http://localhost:8000/health/whisper
curl http://localhost:8000/health/ollama
```

---

### 2️⃣ Client Setup (Windows)

```cmd
cd diktat-service/

# Installiere Python-Dependencies
pip install -r requirements-client.txt
```

#### Konfiguration

Öffne `diktat_gui.py` und passe die Server-URL an (Zeile 22):

```python
# Für lokalen Server:
SERVER_URL = "http://localhost:8000/transcribe"

# Für Server im LAN:
SERVER_URL = "http://192.168.1.100:8000/transcribe"
```

---

## 🎮 Nutzung

### Starten

1. **Server**: `docker-compose up -d` (einmalig)
2. **GUI**: `python diktat_gui.py`

### Workflow

#### Ambient-Modus (Gespräche)

1. Wechsle zum Tab "Gespräch (Ambient)"
2. Klicke **Start** → Aufnahme läuft
3. Während der Aufnahme:
   - Standard-Befunde per Klick einfügen
   - Manuelle Notizen hinzufügen
   - **Pause** für Unterbrechungen
4. Klicke **Analyse** → Verarbeitung startet
5. Editierbares Ergebnis-Fenster öffnet sich
6. Text wird automatisch in Zwischenablage kopiert

#### Diktat-Modus (Live-Transkription)

1. Wechsle zum Tab "Diktat"
2. Klicke **Start** → Aufnahme läuft
3. Sprich dein Diktat
4. Sieh Live-Transkription im Textfeld
5. Klicke **Analyse** → Finalisierung
6. Editiere und kopiere das Ergebnis

### Tastenkombinationen

Die GUI nutzt Maus-Bedienung. Für Hotkey-Support kann das optionale AHK-Script verwendet werden (siehe unten).

---

## ⚙️ Konfiguration

### Server-Einstellungen

Bearbeite `docker-compose.yml`:

```yaml
environment:
  # Whisper-Modell (tiny, base, small, medium, large-v3)
  - WHISPER_MODEL=base
  - WHISPER_LANGUAGE=de

  # Ollama-Modell
  - OLLAMA_MODEL=llama3.1:8b
  - OLLAMA_HOST=ollama
```

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

### Standard-Befunde anpassen

Bearbeite `diktat_gui.py`, Zeile 24-38:

```python
STANDARD_FINDINGS = {
    "Herz": "Cor: Herztöne rein, regelmäßig, rhythmisch.",
    "Eigener Befund": "Dein Text hier...",
    # Füge weitere hinzu oder entferne welche
}
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

Bearbeite `server.py`, Funktion `clean_text_with_llm()`, Zeile 84-99:

```python
# Prompt anpassen:
prompt = f"""Du bist ein Assistent für Transkriptions-Nachbearbeitung.

Aufgabe: Entferne nur Füllwörter (äh, ähm, hm).
Behalte ALLES andere bei!

# Hier anpassen für mehr/weniger Säuberung

Transkribierter Text:
{raw_text}

Gesäuberter Text:"""
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
└── diktat-service/
    ├── diktat_gui.py              # 🆕 GUI-Anwendung (Hauptmethode)
    ├── server.py                  # FastAPI Server
    ├── Dockerfile                 # Docker-Build
    ├── docker-compose.yml         # Docker-Setup
    ├── requirements-client.txt    # Client-Dependencies
    ├── requirements-server.txt    # Server-Dependencies
    ├── README.md                  # Diese Datei
    ├── QUICKSTART.md              # Schnellstart-Anleitung
    │
    ├── diktat_client.py           # (Optional) CLI-Client
    └── diktat_sox.ahk             # (Optional) AutoHotkey-Script
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

### Version 2.0 (GUI-Release)
- 🆕 Tkinter-basierte GUI-Anwendung
- 🆕 Ambient-Modus für Gesprächsaufzeichnung
- 🆕 Diktat-Modus mit Live-Transkription
- 🆕 Standard-Befunde (13 vordefinierte)
- 🆕 Editierbares Ergebnisfenster
- 🆕 Roh-Transkript-Speicherung

### Version 1.0
- Initiales Release mit AHK-System
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
