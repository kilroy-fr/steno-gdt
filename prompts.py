ANALYZE_PROMPT = """Du bist ein KI-Assistent für medizinische Dokumentation in einer Arztpraxis.

Analysiere das folgende Transkript eines Arzt-Patienten-Gesprächs und erstelle eine strukturierte Zusammenfassung für den Arzt. Halte dich ausschließlich an die Inhalte des Transkripts – keine Annahmen, keine Ergänzungen.

**Transkript:**
{transcript}

---

Erstelle folgende strukturierte Übersicht:

## Zusammenfassung
(2–3 Sätze zum Kern des Gesprächs)

## Vorstellungsgrund / Hauptbeschwerden
(Was hat den Patienten in die Praxis geführt?)

## Anamnese
(Relevante Informationen zur Krankengeschichte, die im Gespräch erwähnt wurden)

## Diagnosen / Verdachtsdiagnosen
(Falls erwähnt – sonst: Nicht besprochen)

## Maßnahmen und Empfehlungen
(Untersuchungen, Medikamente, Überweisungen, Folgeberatung – falls besprochen)

## Hinweise für den Arzt
(Auffälligkeiten, Besonderheiten oder dringender Follow-up-Bedarf)

Antworte vollständig auf Deutsch."""

SUMMARY_PROMPT = """Erstelle eine kurze, allgemeinverständliche Zusammenfassung des folgenden Gesprächs.
- 5–8 Sätze, keine Fachbegriffe, kein Schema
- Trenne unterschiedliche Themen durch einen Zeilenumbruch voneinander
- Nur die wichtigsten Punkte, die im Gespräch besprochen wurden
- Gib NUR die Zusammenfassung zurück, ohne Überschriften oder Meta-Kommentare

Transkript:
{transcript}

Zusammenfassung:"""

CLEAN_PROMPT = """Bereinige das folgende medizinische Transkript:
- Entferne Füllwörter (äh, ähm, hm, sozusagen, halt, ne, also, irgendwie)
- Korrigiere offensichtliche Transkriptionsfehler
- Behalte den Inhalt und die Formulierungen exakt bei
- Keine Umformulierungen, keine Zusammenfassungen
- Gib NUR den bereinigten Text zurück, ohne Meta-Kommentare

Transkript:
{transcript}

Bereinigter Text:"""
