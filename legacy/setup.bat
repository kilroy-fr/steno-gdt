@echo off
REM ============================================
REM Diktat-System Client Setup (Windows 11)
REM ============================================

echo.
echo =========================================
echo  Diktat-System Client Setup
echo =========================================
echo.

REM Prüfe Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [FEHLER] Python nicht gefunden!
    echo Bitte installiere Python von: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] Python gefunden

REM Installiere Python-Dependencies
echo.
echo Installiere Python-Pakete...
pip install -r requirements.txt
if errorlevel 1 (
    echo [FEHLER] Pip-Installation fehlgeschlagen
    pause
    exit /b 1
)
echo [OK] Python-Pakete installiert

REM Prüfe SoX
echo.
echo Prüfe SoX...
where sox >nul 2>&1
if errorlevel 1 (
    echo [INFO] SoX nicht gefunden
    echo.
    echo SoX wird für Audio-Aufnahme benötigt.
    echo Download: https://sourceforge.net/projects/sox/files/sox/14.4.2/
    echo.
    echo Bitte installiere SoX und passe den Pfad in diktat_sox.ahk an!
    echo Alternativ: Nutze diktat.ahk mit PowerShell ^(langsamer^)
    echo.
) else (
    echo [OK] SoX gefunden
)

REM Prüfe AutoHotkey
echo.
echo Prüfe AutoHotkey v2...
if exist "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" (
    echo [OK] AutoHotkey v2 gefunden
) else (
    echo [INFO] AutoHotkey v2 nicht gefunden
    echo Download: https://www.autohotkey.com/download/ahk-v2.exe
)

REM Erstelle Config-Hinweis
echo.
echo =========================================
echo  WICHTIG: Konfiguration anpassen!
echo =========================================
echo.
echo 1. Öffne diktat_sox.ahk ^(oder diktat.ahk^)
echo 2. Passe SERVER_URL an ^(Zeile 8^)
echo    Beispiel: SERVER_URL := "http://192.168.1.100:8000"
echo.
echo 3. Wenn SoX installiert: Passe SOX_PATH an ^(Zeile 9^)
echo    Standard: "C:\Program Files\sox\sox.exe"
echo.
echo =========================================
echo.

pause
