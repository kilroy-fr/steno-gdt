@echo off
REM ============================================
REM Build-Skript für KI-Diktat Installer
REM Diktat-Only Version (ohne Ambient-Funktion)
REM ============================================

echo ============================================
echo KI-Diktat - Installer Build
echo Diktat-Only Version
echo ============================================
echo.

REM Prüfe Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [FEHLER] Python nicht gefunden!
    echo Bitte installiere Python 3.8 oder neuer.
    pause
    exit /b 1
)

echo [1/5] Installiere Build-Dependencies...
pip install pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [FEHLER] PyInstaller-Installation fehlgeschlagen!
    pause
    exit /b 1
)

echo [2/5] Installiere App-Dependencies...
pip install -r requirements-client.txt >nul 2>&1
if errorlevel 1 (
    echo [FEHLER] Dependency-Installation fehlgeschlagen!
    pause
    exit /b 1
)

echo [3/5] Bereinige alte Builds...
if exist "build" rmdir /s /q build
if exist "dist\KI-Diktat" rmdir /s /q "dist\KI-Diktat"

echo [4/5] Erstelle EXE mit PyInstaller...
pyinstaller diktat_gui_diktat.spec --clean
if errorlevel 1 (
    echo [FEHLER] PyInstaller-Build fehlgeschlagen!
    pause
    exit /b 1
)

echo [5/5] Prüfe Inno Setup...
where /q iscc
if errorlevel 1 (
    echo.
    echo [INFO] Inno Setup nicht gefunden!
    echo.
    echo EXE wurde erfolgreich erstellt in: dist\KI-Diktat\
    echo.
    echo Um den Installer zu erstellen:
    echo 1. Installiere Inno Setup von https://jrsoftware.org/isdl.php
    echo 2. Fuehre aus: iscc installer_diktat.iss
    echo.
    pause
    exit /b 0
)

echo [BONUS] Erstelle Installer mit Inno Setup...
iscc installer_diktat.iss
if errorlevel 1 (
    echo [WARNUNG] Installer-Erstellung fehlgeschlagen!
    echo EXE ist aber verfuegbar in: dist\KI-Diktat\
    pause
    exit /b 0
)

echo.
echo ============================================
echo BUILD ERFOLGREICH!
echo ============================================
echo.
echo Standalone EXE: dist\KI-Diktat\KI-Diktat.exe
echo Installer:      Output\KI-Diktat-Installer.exe
echo.
pause
