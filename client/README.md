# Steno-Knopf

Ein runder Knopf, der immer vor allen Fenstern liegt, auch vor T2med. Er nimmt auf und lädt die Aufnahme
hoch. Alles Weitere, also Transkription, Sprechertrennung, Dokumentation und GDT-Übergabe, macht der Server.

Der Knopf hat drei Flächen und zwei Lampen:

| Fläche | Klick |
|---|---|
| Mitte ▶ / ■ | Aufnahme starten / beenden und losschicken |
| obere Ringhälfte, beschriftet | Art des Gesprächs weiterschalten: blau **P-Therapie** → rot **S-Stunde** → grün **Direkt**. Die Farbe des ganzen Knopfs zeigt die Art. |
| untere Ringhälfte (Zahnrad) | Menü, dasselbe wie Rechtsklick |
| Lampe oben rechts | grün = Server erreichbar, dunkel = nicht erreichbar |
| Lampe oben links (orange) | eine Aufnahme ist fehlgeschlagen |

Mehr ist nicht zu sehen: Um den Knopf herum ist das Fenster durchsichtig, ein Klick dort trifft T2med.
Steht die Maus auf dem Knopf, erscheint daneben, für wen aufgenommen wird; Meldungen wie
„✓ an T2med“ oder ein Mikrofon-Fehler erscheinen dort auch von selbst für einige Sekunden:

| Text | Bedeutung |
|---|---|
| `Steno` · bereit | wartet. Ein Klick auf ▶ startet eine Aufnahme ohne Patient. |
| `▶ Müller, Hans` | Auftrag aus T2med liegt vor, Klick auf ▶ startet für diesen Patienten |
| `Müller, Hans` · ● 12:34 (rot) | Aufnahme läuft. Ein Klick auf ■ beendet sie und schickt sie los. |
| `Diktat` · ● 0:42 | Diktat läuft (Direkt), der Text erscheint an der Schreibmarke |
| Diktat wartet: bitte in das Textfeld klicken | Steno selbst ist im Vordergrund (z. B. nach dem Menü). Ins Zielfeld klicken, dann wird eingegeben. |
| ✓ an T2med: Müller, Hans | Dokumentation liegt in T2med |
| 1 Fehler · Rechtsklick | Menü → „Verlauf öffnen“ zeigt den Grund, dann „erneut senden“ oder „verwerfen“ |
| Server nicht erreichbar | Aufnehmen geht trotzdem. Hochgeladen wird, sobald der Server wieder da ist. |

**Normalfall:** In T2med beim Patienten **„Messung starten“**. Der Knopf startet die Aufnahme nach 2–6 s von
selbst. Am Ende **■** klicken. Nach 1–3 Minuten steht die Dokumentation in T2med.

**Diktat (Direkt):** Ring oben auf grün **Direkt**, ins Zielfeld klicken (T2med, Word, Browser …), **▶**, sprechen.
Der Text erscheint an der Schreibmarke, jeweils ein bis zwei Sekunden nach einer Sprechpause (spätestens alle 20 s).
**■** beendet das Diktat; der letzte Satz kommt danach. Ein Diktat geht an keinen Patienten und nicht an T2med,
keine Zusammenfassung, keine Aufnahme auf der Platte. Direkt bleibt gewählt, bis man umschaltet. Ein „Messung
starten“ aus T2med nimmt trotzdem als Sprechstunde auf (danach steht der Knopf wieder auf Direkt); ein offener
Auftrag bleibt beim Diktieren offen.

**Mit FHIR** (falls auf dem Server eingerichtet, `T2MED-EINRICHTUNG.md` Abschnitt 4): statt „Messung starten“ in der
T2med-Menüleiste auf **Steno** klicken. Die Aufnahme startet sofort, auch direkt nach dem Start des Knopfs. Neben dem
Knopf steht kurz „T2med: Patient wird geladen…“, dann der Name.

- Mit gedrückter Maustaste (irgendwo auf dem Knopf) lässt sich das Fenster verschieben, die Position wird gemerkt.
- P-Therapie und S-Stunde gelten für die laufende bzw. nächste Aufnahme. Danach steht der Knopf wieder auf
  S-Stunde, bei einem Auftrag vom T2med-Gerät „Steno Psychotherapie“ auf P-Therapie. Direkt bleibt stehen.
  Zwischen Diktat und Gespräch lässt sich während einer Aufnahme nicht umschalten.
- **Menü** (Zahnrad oder Rechtsklick): Art des Gesprächs, Verlauf öffnen, Fehlgeschlagene erneut senden
  oder verwerfen, laufende Aufnahme verwerfen, Einstellungen, Beenden.
- **Fortsetzen:** Bis 10 Minuten nach ■ steht ganz oben im Menü „Fortsetzen: Müller, Hans (10:12)“. Das nimmt
  für denselben Patienten weiter auf. Beide Teile gehen als **ein** Gespräch an den Server, die Dokumentation
  ersetzt die vorige. Ist die vorige noch nicht in T2med, kommt sie gar nicht erst an. Steht sie schon dort,
  sagt der Kommentar der neuen „ersetzt die dort schon übergebene Dokumentation“, dann die ältere in T2med
  löschen. Nach 10 Minuten oder sobald eine neue Aufnahme beginnt, verschwindet der Eintrag.

| Art | In T2med steht |
|---|---|
| P-Therapie | Zusammenfassung der Sitzung, nach 1–3 Minuten |
| S-Stunde | A/B/PRO-Dokumentation, nach 1–3 Minuten |
| Direkt | nichts — der Text steht an der Schreibmarke (siehe Diktat oben) |

- Aufnahmen unter 5 s gelten als Fehlklick und werden verworfen. Der Auftrag bleibt dann für die nächste.
  Ein Diktat nicht: der Text steht schon da, und ein kurzes Diktat ist eben kurz.
- Eine Aufnahme endet von selbst nach 20 min Stille oder spätestens nach 120 min.
- Kommt während einer Aufnahme ein Auftrag für einen anderen Patienten, fragt der Knopf, bevor er wechselt.
- Lag beim Start des Knopfs schon ein Auftrag vor, startet er nicht von selbst. Der Name steht dann im Hinweis
  neben dem Knopf, ein Klick nimmt für diesen Patienten auf.

Die Aufnahme liegt bis zur Bestätigung „an T2med gesendet“ in `%LOCALAPPDATA%\Steno\aufnahmen`. Danach bleibt
nur die jüngste noch so lange liegen, wie sie sich fortsetzen lässt (`fortsetzen_minuten`), und wird dann
gelöscht, spätestens beim Beenden. Absturz oder Neustart verlieren nichts: Beim nächsten Start wird nachgeholt.

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
   Oder in den Einstellungen (Rechtsklick → „Einstellungen…“) unter Mikrofon die EPOS auswählen.

| Raum | `--arbeitsplatz` | T2med-Dateien |
|---|---|---|
| 1 | `001` | `T2MDSTNO.001` / `STNOT2MD.001` |
| 2 | `002` | `T2MDSTNO.002` / `STNOT2MD.002` |
| 3 | `003` | `T2MDSTNO.003` / `STNOT2MD.003` |
| Home | `004` | `T2MDSTNO.004` / `STNOT2MD.004` |

Die Arbeitsplatznummer muss zur Endung der Dateinamen passen, die T2med an diesem Rechner benutzt
(`T2MED-EINRICHTUNG.md`).

## Einstellungen

Rechtsklick → **„Einstellungen…“** öffnet ein Fenster mit allen Werten: Server, Arbeitsplatz (Auswahl aus
den Arbeitsplätzen des Servers), Mikrofon (Auswahl aus den angeschlossenen Geräten oder „Windows-Standard“),
Stille, Höchstdauer, Fehlklick, Fortsetzen, Knopfgröße. Mikrofon und Aufnahme-Werte gelten ab der nächsten
Aufnahme. Server, Arbeitsplatz, Knopfgröße und Abfrageintervall liest der Knopf nur beim Start. Ändert sich
einer davon, startet Steno nach dem Speichern selbst neu, während einer Aufnahme erst nach ■.

Gespeichert wird in `%APPDATA%\Steno\steno.ini`. Die Datei lässt sich weiter von Hand bearbeiten
(Steno vorher beenden, sonst überschreibt er sie beim nächsten Speichern):

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
knopf_pixel = 132          ; Durchmesser des Knopfs bei 100 % Skalierung (mind. 96)
fortsetzen_minuten = 10    ; so lange lässt sich die letzte Aufnahme fortsetzen, 0 = nie
```

Protokoll: `%LOCALAPPDATA%\Steno\steno.log`. Es enthält Patientennummern, keine Namen.

## Voraussetzungen auf dem Server

- Der Knopf spricht den Server über HTTPS an: `https://steno.hausarzt-rothenburg.de` ist eine Caddy-Route auf
  `transcribe-gdt-web:8000` (siehe CLAUDE.md, „Erreichbarkeit“) und braucht einen DNS-Eintrag auf `192.168.5.20`.
- Die T2med-Anbindung muss eingerichtet sein (`GDT_DIR`), sonst verweigert der Knopf den Start.
