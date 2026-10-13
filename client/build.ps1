# Steno-Knopf als einzelne Steno.exe bauen (Windows, Python 3.13).
#   powershell -ExecutionPolicy Bypass -File client\build.ps1
#   powershell -ExecutionPolicy Bypass -File client\build.ps1 -Veroeffentlichen
# Ergebnis: client\dist\Steno.exe — Verteilung und Updates: client\README.md
# -Veroeffentlichen legt sie zusaetzlich nach ..\knopf (STENO_KNOPF_DIR im
# Container): jeder installierte Knopf bietet sie beim naechsten Start an.
param([switch]$Veroeffentlichen)
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
Write-Host "Fertig: $exe"

if ($Veroeffentlichen) {
    $match = Select-String -Path steno.py -Pattern '^VERSION = "([\d.]+)"' | Select-Object -First 1
    if (-not $match) { throw "VERSION in steno.py nicht gefunden" }
    $version = $match.Matches[0].Groups[1].Value
    $ziel = Join-Path (Split-Path $PSScriptRoot) "knopf"
    New-Item -ItemType Directory -Force $ziel | Out-Null
    $json = Join-Path $ziel "Steno.json"
    if (Test-Path $json) {
        $alt = (Get-Content $json -Raw | ConvertFrom-Json).version
        if ($alt -eq $version) {
            Write-Warning "Version $version liegt schon bereit. Ohne neue VERSION in steno.py bekommen die Knoepfe kein Update angeboten."
        }
    }
    # Erst die exe (per Umbenennen, nie halb geschrieben), dann Steno.json:
    # wer dazwischen laedt, scheitert an der Pruefsumme statt eine falsche
    # Version fuer die neue zu halten.
    Copy-Item $exe (Join-Path $ziel "Steno.exe.neu") -Force
    Move-Item (Join-Path $ziel "Steno.exe.neu") (Join-Path $ziel "Steno.exe") -Force
    $sha256 = (Get-FileHash (Join-Path $ziel "Steno.exe") -Algorithm SHA256).Hash.ToLower()
    $inhalt = @{ version = $version; sha256 = $sha256 } | ConvertTo-Json
    [IO.File]::WriteAllText("$json.neu", $inhalt, (New-Object Text.UTF8Encoding $false))
    Move-Item "$json.neu" $json -Force
    Write-Host "Veroeffentlicht: Steno $version -> $ziel"
}
