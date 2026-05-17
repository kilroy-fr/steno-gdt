@echo off
REM ============================================
REM Build-Skript für KI-Transcribe Pro Installer
REM ============================================

echo ============================================
echo KI-Transcribe Pro - Installer Build
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
if exist "dist" rmdir /s /q dist

echo [4/5] Erstelle EXE mit PyInstaller...
pyinstaller diktat_gui.spec --clean
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
    echo EXE wurde erfolgreich erstellt in: dist\KI-Transcribe-Pro\
    echo.
    echo Um den Installer zu erstellen:
    echo 1. Installiere Inno Setup von https://jrsoftware.org/isdl.php
    echo 2. Fuehre aus: iscc installer.iss
    echo.
    pause
    exit /b 0
)

echo [BONUS] Erstelle Installer mit Inno Setup...
iscc installer.iss
if errorlevel 1 (
    echo [WARNUNG] Installer-Erstellung fehlgeschlagen!
    echo EXE ist aber verfuegbar in: dist\KI-Transcribe-Pro\
    pause
    exit /b 0
)

echo.
echo ============================================
echo BUILD ERFOLGREICH!
echo ============================================
echo.
echo Standalone EXE: dist\KI-Transcribe-Pro\KI-Transcribe-Pro.exe
echo Installer:      Output\KI-Transcribe-Installer.exe
echo.
pause
