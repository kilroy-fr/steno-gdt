# KI-Diktat - Build & Installation

**Diktat-Only Version** (ohne Ambient-Funktion)

Diese Version enthält nur die Diktat-Funktion mit Live-Transkription, ohne die Ambient-Gesprächsanalyse-Funktion.

## 🎯 Übersicht

- **Name**: KI-Diktat
- **Version**: 2.0
- **Typ**: Diktat-Only (vereinfachte Version)
- **Plattform**: Windows 10/11 (x64)

## 📋 Voraussetzungen

### Zum Bauen benötigt
- Python 3.8 oder neuer
- pip (Python Package Manager)
- [Optional] Inno Setup 6 (für Installer-Erstellung)

### Zum Ausführen benötigt (als User)
- Windows 10/11 (64-Bit)
- Ki-transcribe Server (läuft separat)
- Mikrofon

## 🔧 Build-Prozess

### Variante 1: Automatischer Build (Empfohlen)

```batch
build_installer_diktat.bat
```

Dies führt automatisch aus:
1. Installation von PyInstaller
2. Installation der Dependencies
3. Bereinigung alter Builds
4. Erstellung der EXE mit PyInstaller
5. [Optional] Erstellung des Installers mit Inno Setup

### Variante 2: Manueller Build

#### Schritt 1: Dependencies installieren
```batch
pip install pyinstaller
pip install -r requirements-client.txt
```

#### Schritt 2: EXE erstellen
```batch
pyinstaller diktat_gui_diktat.spec --clean
```

Die EXE befindet sich dann in: `dist\KI-Diktat\KI-Diktat.exe`

#### Schritt 3: Installer erstellen (optional)
```batch
iscc installer_diktat.iss
```

Der Installer befindet sich dann in: `Output\KI-Diktat-Installer.exe`

## 📦 Output-Dateien

Nach erfolgreichem Build:

```
diktat-service/
├── dist/
│   └── KI-Diktat/
│       ├── KI-Diktat.exe          ← Standalone-Anwendung
│       └── [weitere DLLs/Dateien]
└── Output/
    └── KI-Diktat-Installer.exe    ← Windows-Installer
```

## 🚀 Installation (für End-User)

### Option 1: Mit Installer (Empfohlen)
1. Doppelklick auf `KI-Diktat-Installer.exe`
2. Installation folgen (KEINE Admin-Rechte erforderlich!)
3. Beim ersten Start Server-URL eingeben

### Option 2: Portable Version
1. Ordner `dist\KI-Diktat` kopieren
2. `KI-Diktat.exe` starten
3. Beim ersten Start Server-URL eingeben

## ⚙️ Konfiguration

### Erstkonfiguration
Beim ersten Start wird automatisch nach der Server-URL gefragt:
```
Beispiel: http://transcribe.domain.local/transcribe
```

### Konfiguration ändern
- Menü → Einstellungen → Server-URL ändern

### Konfigurationsdatei
```
%APPDATA%\KI-Transcribe-Pro\config.json
```

## 🔍 Unterschiede zur Vollversion

| Feature | Vollversion | Diktat-Only |
|---------|-------------|-------------|
| Diktat mit Live-Transkription | ✅ | ✅ |
| Ambient-Gesprächsanalyse | ✅ | ❌ |
| Standard-Befunde Buttons | ✅ | ❌ |
| Manuelle Notizen | ✅ | ❌ |
| Tab-Navigation | ✅ | ❌ |
| Fenstergröße | 500x920 | 500x700 |

## 🐛 Troubleshooting

### PyInstaller-Fehler
```batch
pip install --upgrade pyinstaller
```

### Import-Fehler
```batch
pip install -r requirements-client.txt --force-reinstall
```

### Inno Setup nicht gefunden
1. Download von https://jrsoftware.org/isdl.php
2. Installation durchführen
3. Pfad zu `iscc.exe` zur PATH-Variable hinzufügen

### EXE startet nicht
- Antivirus prüfen (False-Positive bei PyInstaller üblich)
- Windows Defender Ausnahme hinzufügen

## 📝 Notizen

- Die Diktat-Version verwendet denselben Server wie die Vollversion
- Standard-LLM auf dem Server: **llama3.2:3b** (oder angepasst: llama2.5:14b)
- Config-Ordner wird mit der Vollversion geteilt (`KI-Transcribe-Pro`)
- Beide Versionen können parallel installiert werden

## 🔐 Sicherheit

- Keine Admin-Rechte erforderlich
- Installation in User-AppData
- Keine Registry-Änderungen (außer Uninstaller)
- Config-Dateien bleiben bei Deinstallation erhalten (optional löschbar)

## 📄 Lizenz

Siehe Haupt-Projekt README.
