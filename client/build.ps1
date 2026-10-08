# Steno-Knopf als einzelne Steno.exe bauen (Windows, Python 3.13).
#   powershell -ExecutionPolicy Bypass -File client\build.ps1
# Ergebnis: client\dist\Steno.exe — Verteilung und Autostart: client\README.md
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) { python -m venv .venv }
& .venv\Scripts\python -m pip install --quiet --upgrade pip
& .venv\Scripts\python -m pip install --quiet -r requirements.txt pyinstaller

# Eine laufende Steno.exe sperrt Windows - PyInstaller scheitert sonst erst
# nach dem ganzen Build mit "Zugriff verweigert". Nicht selbst beenden: eine
# laufende Aufnahme soll ueber Menue -> Beenden sauber uebergeben werden.
$exe = Join-Path $PSScriptRoot "dist\Steno.exe"
if (Get-Process Steno -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }) {
    throw "Steno.exe laeuft noch ($exe). Erst den Knopf beenden (Rechtsklick -> Beenden), dann erneut bauen."
}

# --noconsole: kein schwarzes Fenster neben dem Knopf; Meldungen stehen in
# %LOCALAPPDATA%\Steno\steno.log
& .venv\Scripts\pyinstaller --noconfirm --clean --onefile --noconsole --name Steno steno.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller fehlgeschlagen" }
Write-Host "Fertig: $PSScriptRoot\dist\Steno.exe"
