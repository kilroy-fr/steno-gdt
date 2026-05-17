# 🚀 Quickstart - KI-Transcribe Pro

Schnellstart-Anleitung für die GUI-Anwendung.

---

## ⚡ 5-Minuten-Setup

### Server (einmalig)

```bash
# Netzwerk erstellen
docker network create diktat-network

# Ollama verbinden (falls vorhanden)
docker network connect diktat-network <ollama-container>

# Server starten
docker-compose up -d

# Warten bis alles läuft (ca. 1-2 Minuten)
docker-compose logs -f
```

### Client (Windows)

```cmd
# Dependencies installieren
pip install -r requirements-client.txt

# GUI starten
python diktat_gui.py
```

---

## 🎯 Erste Schritte

### 1. Server-URL konfigurieren

Beim ersten Start der GUI erscheint automatisch ein Dialog. Dort die Server-URL eingeben:

```
http://localhost:8000/transcribe-only        # Lokaler Server
http://transcribe.domain.local/transcribe-only  # Server im LAN (via Caddy)
```

Die Konfiguration wird in `%APPDATA%\KI-Transcribe-Pro\config.json` gespeichert und kann jederzeit über Menü → Einstellungen → Server-URL ändern angepasst werden.

### 2. GUI starten

```cmd
python diktat_gui.py
```

### 3. Erstes Diktat

1. Klicke **"Start"**
3. Sprich einen kurzen Satz
4. Klicke **"Analyse"**
5. Warte auf das Ergebnis-Fenster
6. Text ist automatisch in Zwischenablage!

---

## 🔧 Häufige Probleme

### Server nicht erreichbar

```bash
# Test
curl http://localhost:8000/

# Logs prüfen
docker logs diktat-api
```

### Mikrofon funktioniert nicht

```python
# Test in Python:
import sounddevice as sd
print(sd.query_devices())
```

### Transkription schlecht

Besseres Modell in `docker-compose.yml`:

```yaml
- WHISPER_MODEL=small  # statt base
```

Dann neu starten:
```bash
docker-compose down
docker-compose up -d --build
```

---

## 📖 Vollständige Dokumentation

Siehe [README.md](README.md) für:
- Detaillierte Konfiguration
- Erweiterte Features
- GPU-Support
- Troubleshooting

---

## 🎓 Weitere Ressourcen

- **Asklaion** (Original): https://github.com/lollylan/asklaion
- **faster-whisper**: https://github.com/SYSTRAN/faster-whisper
- **ollama**: https://ollama.ai/

---

**Viel Erfolg! 🚀**
