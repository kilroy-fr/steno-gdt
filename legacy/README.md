# Legacy Files (Veraltet)

Dieser Ordner enthält die **veralteten** AutoHotkey-basierten Dateien, die in Version 1.0 verwendet wurden.

## ⚠️ Hinweis

Diese Dateien sind **veraltet** und werden nicht mehr aktiv gewartet. Verwende stattdessen die neue **GUI-Anwendung** ([diktat_gui.py](../diktat_gui.py)).

## 📦 Enthaltene Dateien

### diktat_sox.ahk
- AutoHotkey v2 Script für Hotkey-basierte Audio-Aufnahme
- Nutzt SoX für Audio-Aufnahme
- Hotkeys: Ctrl+Shift+D (Start/Stop), Ctrl+Shift+S (Abbrechen)

### diktat_client.py
- Python-Client für CLI-basierte Nutzung
- Sendet einzelne Audio-Datei zum Server
- Wird vom AHK-Script aufgerufen

### setup.bat
- Windows-Batch-Script zum Installieren der Python-Dependencies
- Für die alte AHK-Version

## 🆕 Neue Methode (Empfohlen)

Verwende stattdessen die **GUI-Anwendung**:

```cmd
cd ..
pip install -r requirements-client.txt
python diktat_gui.py
```

### Vorteile der neuen GUI

- ✅ Zwei Modi: Ambient (Gespräche) + Diktat (Live)
- ✅ Live-Transkription während der Aufnahme
- ✅ Standard-Befunde per Knopfdruck
- ✅ Editierbares Ergebnisfenster
- ✅ Keine externe Software nötig (kein SoX, kein AHK)
- ✅ Roh-Transkript-Speicherung
- ✅ Kontinuierliche Chunk-Verarbeitung

## 📖 Dokumentation

Siehe [../README.md](../README.md) für vollständige Dokumentation der neuen GUI-Anwendung.

## 🗑️ Archivierung

Diese Dateien werden aus folgenden Gründen archiviert:

1. **Begrenzte Funktionalität**: Nur einfaches Diktat, keine Ambient-Funktion
2. **Externe Abhängigkeiten**: Erfordert SoX + AutoHotkey Installation
3. **Keine Live-Transkription**: Nur finale Ausgabe
4. **Weniger Features**: Keine Standard-Befunde, keine Editierung

Die Dateien bleiben für Referenzzwecke erhalten, sollten aber nicht mehr verwendet werden.

---

**Letzte Aktualisierung**: 2026-02-07
**Status**: Archiviert / Veraltet
