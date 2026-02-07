# 🏗️ Build-Anleitung: KI-Transcribe Pro Installer

Diese Anleitung zeigt, wie du einen benutzerfreundlichen Windows-Installer für KI-Transcribe Pro erstellst.

## 📋 Voraussetzungen

### 1. Python 3.8+ installieren
- Download: https://www.python.org/downloads/
- ⚠️ **Wichtig:** "Add Python to PATH" aktivieren!

### 2. Inno Setup installieren (für Installer)
- Download: https://jrsoftware.org/isdl.php
- Installiere die aktuelle Version (6.x)
- Optional: Unicode-Version für internationale Zeichen

### 3. Git (optional, für Versionskontrolle)
- Download: https://git-scm.com/downloads

---

## 🚀 Schnellstart: Automatischer Build

### Option 1: One-Click Build (empfohlen)

```cmd
cd diktat-service
build_installer.bat
```

Das war's! Der Installer wird automatisch erstellt.

**Output:**
- `dist\KI-Transcribe-Pro\KI-Transcribe-Pro.exe` - Standalone EXE
- `Output\KI-Transcribe-Installer.exe` - Windows-Installer

---

## 🔧 Manueller Build (Schritt für Schritt)

### Schritt 1: Dependencies installieren

```cmd
cd diktat-service

# PyInstaller installieren
pip install pyinstaller

# App-Dependencies installieren
pip install -r requirements-client.txt
```

### Schritt 2: EXE erstellen

```cmd
# Bereinige alte Builds
rmdir /s /q build dist

# Erstelle EXE
pyinstaller diktat_gui.spec --clean
```

**Output:** `dist\KI-Transcribe-Pro\KI-Transcribe-Pro.exe`

### Schritt 3: Installer erstellen

```cmd
# Mit Inno Setup kompilieren
iscc installer.iss
```

**Output:** `Output\KI-Transcribe-Installer.exe`

---

## 📦 Was wird erstellt?

### 1. Standalone EXE (`dist\KI-Transcribe-Pro\`)
- Komplette Anwendung (~70-100 MB)
- Enthält Python + alle Libraries
- Kann direkt ausgeführt werden
- Keine Installation nötig

### 2. Windows-Installer (`Output\KI-Transcribe-Installer.exe`)
- Professioneller Installer (~70-100 MB)
- Installation in `%LOCALAPPDATA%\KI-Transcribe-Pro`
- **Keine Admin-Rechte erforderlich!**
- Start-Menü + Desktop-Verknüpfung
- Uninstaller inklusive

---

## 🎯 Installer-Features

### ✅ Benutzerfreundlich
- ✨ One-Click Installation
- 🚀 Kein Python erforderlich
- 👤 Keine Admin-Rechte nötig
- 📋 Start-Menü Integration
- 🖥️ Desktop-Icon (optional)
- ❌ Saubere Deinstallation

### ⚙️ Config-Management
- Beim ersten Start: Dialog für Server-URL
- Config wird gespeichert in `%APPDATA%\KI-Transcribe-Pro\config.json`
- Über Menü "Einstellungen" → "Server-URL ändern..." jederzeit anpassbar

### 📁 Installation
```
%LOCALAPPDATA%\KI-Transcribe-Pro\
├── KI-Transcribe-Pro.exe       (Hauptanwendung)
├── _internal\                  (Python-Runtime + Libraries)
└── unins000.exe                (Uninstaller)

%APPDATA%\KI-Transcribe-Pro\
├── config.json                 (Konfiguration)
└── Transkripte_Raw\            (Gespeicherte Transkripte)
```

---

## 🔧 Anpassungen

### Icon hinzufügen (optional)

1. Erstelle/beschaffe ein Icon (`icon.ico`, 256x256 px)
2. Speichere es in `diktat-service\icon.ico`
3. Bearbeite `diktat_gui.spec`:
   ```python
   exe = EXE(
       ...
       icon='icon.ico',  # Pfad zum Icon
       ...
   )
   ```
4. Bearbeite `installer.iss`:
   ```ini
   SetupIconFile=icon.ico
   ```

### Versionsinformation hinzufügen (optional)

Erstelle `version_info.txt`:

```
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(2, 0, 0, 0),
    prodvers=(2, 0, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'040904B0',
          [
            StringStruct(u'CompanyName', u'KI-Transcribe Team'),
            StringStruct(u'FileDescription', u'Medizinisches Transkriptions-System'),
            StringStruct(u'FileVersion', u'2.0.0.0'),
            StringStruct(u'InternalName', u'KI-Transcribe-Pro'),
            StringStruct(u'LegalCopyright', u'© 2026'),
            StringStruct(u'OriginalFilename', u'KI-Transcribe-Pro.exe'),
            StringStruct(u'ProductName', u'KI-Transcribe Pro'),
            StringStruct(u'ProductVersion', u'2.0.0.0')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
```

Dann in `diktat_gui.spec`:
```python
exe = EXE(
    ...
    version_file='version_info.txt',
    ...
)
```

### Lizenz-Datei hinzufügen

1. Erstelle `LICENSE.txt`
2. Bearbeite `installer.iss`:
   ```ini
   LicenseFile=LICENSE.txt
   ```

---

## 🐛 Troubleshooting

### Problem: "PyInstaller nicht gefunden"

```cmd
pip install --upgrade pyinstaller
```

### Problem: "iscc ist kein gültiger Befehl"

Inno Setup nicht im PATH. Nutze vollständigen Pfad:

```cmd
"C:\Program Files (x86)\Inno Setup 6\iscc.exe" installer.iss
```

### Problem: "Import-Fehler in EXE"

Fehlende Hidden-Imports. Bearbeite `diktat_gui.spec`:

```python
hiddenimports=[
    'dein.fehlendes.modul',
    ...
]
```

### Problem: "EXE ist zu groß"

1. **UPX-Kompression aktivieren** (schon standard)
2. **Excludes hinzufügen** in `diktat_gui.spec`:
   ```python
   excludes=[
       'matplotlib',
       'pandas',
       'PIL',
       ...
   ]
   ```

### Problem: "DLL-Fehler beim Ausführen"

Teste auf einem sauberen System (ohne Python installiert):

```cmd
# In VM oder auf anderem PC
dist\KI-Transcribe-Pro\KI-Transcribe-Pro.exe
```

Wenn Fehler: Fehlende Runtime-Libraries. Installiere:
- [Visual C++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)

---

## 📤 Distribution

### Installer veröffentlichen

1. **GitHub Releases:**
   ```bash
   git tag v2.0
   git push origin v2.0
   ```
   Lade `Output\KI-Transcribe-Installer.exe` hoch

2. **Direkter Download:**
   - Hoste auf eigenem Server
   - Nutze File-Sharing (Dropbox, Google Drive, etc.)

3. **Update-Mechanismus (optional):**
   - Implementiere Auto-Update-Funktion
   - Prüfe auf neue Version via GitHub API
   - Download + Installation automatisieren

---

## 🔒 Code-Signing (optional, für professionelle Distribution)

### Warum Code-Signing?

- ✅ Kein Windows SmartScreen-Warnung
- ✅ Vertrauenswürdigkeit
- ✅ Integrität gesichert

### Schritte:

1. **Code-Signing-Zertifikat besorgen**
   - Kaufe bei DigiCert, Sectigo, etc. (~€100-300/Jahr)
   - Oder: Selbst-signiert (nur für Test)

2. **Signiere EXE:**
   ```cmd
   signtool sign /f cert.pfx /p password /t http://timestamp.digicert.com dist\KI-Transcribe-Pro\KI-Transcribe-Pro.exe
   ```

3. **Signiere Installer:**
   ```cmd
   signtool sign /f cert.pfx /p password /t http://timestamp.digicert.com Output\KI-Transcribe-Installer.exe
   ```

---

## 📊 Build-Statistiken

### Typische Größen:
- **Quellcode:** ~10 KB (diktat_gui.py)
- **Dependencies:** ~60 MB (numpy, scipy, sounddevice, etc.)
- **Python-Runtime:** ~20 MB
- **Finaler Installer:** ~70-100 MB (komprimiert)

### Build-Zeit:
- **PyInstaller:** ~2-5 Minuten
- **Inno Setup:** ~30 Sekunden
- **Gesamt:** ~3-6 Minuten

---

## 📝 Checkliste vor Release

- [ ] Alle Features getestet
- [ ] Server-URL-Config funktioniert
- [ ] Audio-Aufnahme funktioniert
- [ ] Transkription funktioniert
- [ ] App startet ohne Python-Installation
- [ ] Installer ohne Admin-Rechte installierbar
- [ ] Start-Menü-Eintrag erstellt
- [ ] Desktop-Icon erstellt (optional)
- [ ] Uninstaller funktioniert
- [ ] Config bleibt nach Update erhalten
- [ ] README.md aktualisiert
- [ ] Version-Nummer erhöht

---

## 🎉 Fertig!

Nach dem Build hast du:

✅ **Standalone EXE** - Kann direkt ausgeführt werden
✅ **Windows-Installer** - Professionelle Installation
✅ **Keine Admin-Rechte nötig** - User-freundlich
✅ **Config-Dialog** - Einfache Server-URL-Eingabe
✅ **Saubere Deinstallation** - Inklusive Uninstaller

Verteile `Output\KI-Transcribe-Installer.exe` an deine User!

---

## 📞 Support

Bei Problemen:
- GitHub Issues: https://github.com/yourusername/ki-transcribe/issues
- README.md: Hauptdokumentation
- QUICKSTART.md: Schnellstart-Anleitung

---

## 📜 Lizenz

Siehe [LICENSE](LICENSE) für Details.

Verwendete Tools:
- **PyInstaller:** GPL-2.0 License
- **Inno Setup:** Free for commercial use
- **Python:** PSF License
