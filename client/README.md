# Steno-Knopf

Ein runder Knopf, der immer vor allen Fenstern liegt, auch vor T2med. Er nimmt auf und lädt die Aufnahme
hoch. Alles Weitere, also Transkription, Sprechertrennung, Dokumentation und GDT-Übergabe, macht der Server.

| Knopf | Bedeutung |
|---|---|
| 🔵 ▶ `Steno` · bereit | wartet. Ein Klick startet eine Aufnahme ohne Patient. |
| 🔵 ▶ `Müller, Hans` | Auftrag aus T2med liegt vor, Klick startet für diesen Patienten |
| 🔴 ■ `Müller, Hans` · ● 12:34 | Aufnahme läuft. Ein Klick beendet sie und schickt sie los. |
| Ψ am Knopf | Psychotherapie-Sitzung |
| ✓ an T2med: Müller, Hans | Dokumentation liegt in T2med |
| 🟠 Punkt · 1 Fehler | Rechtsklick → „Verlauf öffnen“ zeigt den Grund, dann „erneut senden“ oder „verwerfen“ |
| ⚪ ▶ Server nicht erreichbar | Aufnehmen geht trotzdem. Hochgeladen wird, sobald der Server wieder da ist. |

**Normalfall:** In T2med beim Patienten **„Messung starten“**. Der Knopf startet die Aufnahme nach 2–6 s von
selbst. Am Ende **■** klicken. Nach 1–3 Minuten steht die Dokumentation in T2med.

- Nur der runde Knopf schaltet. Am Text lässt sich das Fenster verschieben, die Position wird gemerkt.
- **Rechtsklick:** Art des Gesprächs (Automatisch / Sprechstunde / Psychotherapie, gilt für die laufende
  bzw. nächste Aufnahme), Verlauf öffnen, Fehlgeschlagene erneut senden oder verwerfen, laufende Aufnahme
  verwerfen, Einstellungen, Beenden.
- Aufnahmen unter 5 s gelten als Fehlklick und werden verworfen. Der Auftrag bleibt dann für die nächste.
- Eine Aufnahme endet von selbst nach 20 min Stille oder spätestens nach 120 min.
- Kommt während einer Aufnahme ein Auftrag für einen anderen Patienten, fragt der Knopf, bevor er wechselt.
- Lag beim Start des Knopfs schon ein Auftrag vor, startet er nicht von selbst. Der Name steht dann am Knopf,
  ein Klick nimmt für diesen Patienten auf.

Die Aufnahme liegt bis zur Bestätigung „an T2med gesendet“ in `%LOCALAPPDATA%\Steno\aufnahmen` und wird
danach gelöscht. Absturz oder Neustart verlieren nichts: Beim nächsten Start wird nachgeholt.

## Bauen

Auf einem Windows-Rechner mit Python 3.13:

```powershell
powershell -ExecutionPolicy Bypass -File client\build.ps1
```

Ergebnis: `client\dist\Steno.exe` (eine Datei, etwa 30 MB, ohne Python auf den Arbeitsplätzen).

## Installieren (je Sprechzimmer)

1. `Steno.exe` nach `C:\Program Files\Steno\` kopieren.
2. Einmal von Hand mit Server und Arbeitsplatz starten, die Werte werden gespeichert:
   ```
   "C:\Program Files\Steno\Steno.exe" --server https://steno.hausarzt-rothenburg.de --arbeitsplatz 001
   ```
   Ohne Parameter fragt der Knopf beim ersten Start danach.
3. Verknüpfung auf `Steno.exe` in den Autostart legen (`Win+R` → `shell:startup`).
4. Mikrofon: die EPOS Expand 40 per **USB** (nicht Bluetooth) und in Windows als Standard-Aufnahmegerät.
   Oder in den Einstellungen `mikrofon = EPOS` setzen.

| Sprechzimmer | `--arbeitsplatz` | T2med-Dateien |
|---|---|---|
| 1 | `001` | `T2MDSTNO.001` / `STNOT2MD.001` |
| 2 | `002` | `T2MDSTNO.002` / `STNOT2MD.002` |
| 3 | `003` | `T2MDSTNO.003` / `STNOT2MD.003` |

Die Arbeitsplatznummer muss zur Endung der Dateinamen passen, die T2med an diesem Rechner benutzt
(`T2MED-EINRICHTUNG.md`).

## Einstellungen

`%APPDATA%\Steno\steno.ini` (Rechtsklick → „Einstellungen bearbeiten“, danach Steno neu starten):

```ini
[steno]
server = https://steno.hausarzt-rothenburg.de
arbeitsplatz = 001
mikrofon =                 ; Teil des Gerätenamens, leer = Windows-Standard
stille_minuten = 20        ; so lange Stille beendet die Aufnahme
stille_schwelle_db = -50   ; darunter gilt der Pegel als Stille
max_minuten = 120
mindest_sekunden = 5       ; kürzere Aufnahmen gelten als Fehlklick
auftrag_abfrage_sekunden = 2
```

Protokoll: `%LOCALAPPDATA%\Steno\steno.log`. Es enthält Patientennummern, keine Namen.

## Voraussetzungen auf dem Server

- Der Knopf spricht den Server über HTTPS an. Dafür braucht es eine Caddy-Route auf `transcribe-gdt-web:8000`
  (siehe CLAUDE.md, „Erreichbarkeit“) und einen DNS-Eintrag.
- Die T2med-Anbindung muss eingerichtet sein (`GDT_DIR`), sonst verweigert der Knopf den Start.
