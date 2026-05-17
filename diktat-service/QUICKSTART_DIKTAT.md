# KI-Diktat - Schnellstart

**Diktat-Only Version** - Vereinfachte Version ohne Ambient-Funktion

## 🚀 Quick Setup (5 Minuten)

### 1. Installer bauen
```batch
build_installer_diktat.bat
```

### 2. Installer ausführen
```batch
Output\KI-Diktat-Installer.exe
```

### 3. Anwendung starten
- Desktop-Icon doppelklicken
- ODER: Startmenü → KI-Diktat

### 4. Server-URL eingeben
```
http://transcribe.domain.local/transcribe
```

### 5. Diktat starten!
1. **Start** drücken
2. Sprechen
3. Live-Transkription erscheint
4. **Analyse** drücken zum Beenden
5. Text wird in Zwischenablage kopiert

## 🎯 Verwendung

### Diktat-Workflow
```
Start → Diktieren → Pause (optional) → Analyse → Text kopieren
```

### Tastenkombinationen
- **Enter**: Server-URL speichern (im Config-Dialog)

### Features
✅ Live-Transkription
✅ Automatische Textsäuberung
✅ Pause/Fortsetzung
✅ Clipboard-Integration
✅ Transkript-Speicherung

### Entfernte Features (vs. Vollversion)
❌ Ambient-Gesprächsanalyse
❌ Standard-Befunde-Buttons
❌ Manuelle Notizen
❌ Tab-Navigation

## 📁 Dateien

### Transkripte
```
Transkripte_Raw\
├── raw_2025-02-07_14-30-15.txt
├── raw_2025-02-07_15-45-22.txt
└── ...
```

### Konfiguration
```
%APPDATA%\KI-Transcribe-Pro\config.json
```

## ⚙️ Server-Konfiguration

### Standard-LLM ändern (auf Server-Seite)

Für **llama2.5:14b** als Standard:

1. Server-Konfiguration öffnen
2. LLM-Modell ändern auf: `llama2.5:14b`
3. Server neu starten

## 🆘 Häufige Probleme

**App startet nicht?**
→ Antivirus prüfen, Windows Defender Ausnahme hinzufügen

**Server nicht erreichbar?**
→ Server-URL prüfen (Menü → Einstellungen → Server-URL ändern)

**Keine Audio-Aufnahme?**
→ Mikrofon-Berechtigungen in Windows prüfen

**Schlechte Transkription?**
→ Mikrofon näher an den Mund, deutlicher sprechen

## 📞 Support

- Siehe Haupt-Projekt README
- Issues: https://github.com/yourusername/ki-transcribe/issues

## 💡 Tipps

1. **Deutlich sprechen** - Bessere Transkriptionsqualität
2. **Pausen nutzen** - Bei langen Diktaten Pause-Funktion verwenden
3. **Chunk-Größe** - Server verarbeitet alle 30 Sekunden automatisch
4. **Nachbearbeitung** - Text kann im Ergebnis-Fenster noch editiert werden

## ⚡ Schnellvergleich

| | Vollversion | Diktat-Only |
|---|---|---|
| **Hauptfunktion** | Ambient + Diktat | Nur Diktat |
| **Komplexität** | Hoch | Niedrig |
| **Fenstergröße** | 500x920 | 500x700 |
| **Lernkurve** | Mittel | Gering |
| **Empfohlen für** | Arztpraxis mit Gesprächsdoku | Reines Diktieren |

---

**Viel Erfolg mit KI-Diktat! 🎙️**
