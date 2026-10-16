# ============================================================
# SPRECHER-KONTEXT
# ============================================================
# Wird von server.py in ANALYZE_PROMPT und ABPRO_PROMPT als {speaker_context}
# eingesetzt – je nachdem, ob das Transkript diarisiert wurde oder nicht.
# Bewusst keine harte Vorgabe "genau zwei Personen": Begleitpersonen und MFA
# kommen im Sprechzimmer regelmaessig vor.

# Was Angehoerige berichten, ist Fremdanamnese und muss in der Kartei so
# stehen (aus asklaion: "Angehoerige oder Begleitpersonen als solche
# kennzeichnen"). Der Inhalt entscheidet, nicht der Marker: die
# Sprechertrennung allein soll niemanden zur Begleitperson machen.
COMPANION_RULE = """Was eine Begleitperson (Angehörige, Pflegekraft) berichtet, kennzeichne als Fremdanamnese, z. B. "lt. Ehefrau: …" – aber nur, wenn aus dem Inhalt eindeutig hervorgeht, dass eine andere Person spricht."""

SPEAKER_CONTEXT_PLAIN = """SPRECHER: Das Transkript ist ein Dialog ohne Sprecherkennzeichnung (Arzt, {patient_label}, ggf. Begleitperson). Ordne Aussagen anhand des Kontexts zu; wenn die Zuordnung unklar bleibt, formuliere neutral ("es wurde besprochen") statt zu raten. Laut überlegte Verdachtsdiagnosen des Arztes sind keine gesicherten Diagnosen; Rückfragen zu bestehenden Medikamenten sind keine neuen Verordnungen.
""" + COMPANION_RULE

SPEAKER_CONTEXT_DIARIZED = """SPRECHER: Die Sprecher wurden automatisch getrennt – jede Zeile beginnt mit einem anonymen Marker (SPRECHER A, SPRECHER B, ...). Welcher Marker der Arzt und welcher {patient_label} ist, steht NICHT fest: erschließe es aus dem Gesprächsverlauf – wer Anamnesefragen stellt, untersucht und Empfehlungen ausspricht, ist der Arzt; weitere Marker sind meist Begleitperson oder Praxispersonal.
Die Sprechertrennung ist fehleranfällig, vor allem an Sprecherwechseln und bei kurzen Einwürfen. Wenn Marker und Inhalt sich widersprechen, hat der Inhalt Vorrang; bleibt die Zuordnung unklar, formuliere neutral ("es wurde besprochen") statt zu raten. Laut überlegte Verdachtsdiagnosen des Arztes sind keine gesicherten Diagnosen; Rückfragen zu bestehenden Medikamenten sind keine neuen Verordnungen.
""" + COMPANION_RULE + """
Übernimm die Marker NICHT in deine Ausgabe."""

# ============================================================
# VOLLSTAENDIGKEIT
# ============================================================
# Steht in ANALYZE_PROMPT und ABPRO_PROMPT NACH dem Transkript: bei langen
# Gespraechen wiegen Anweisungen am Ende schwerer als solche vor zehntausend
# Zeichen Transkript. Anlass war die Klage, dass bei Vorsorgeuntersuchungen nur
# eines von mehreren Anliegen uebrig blieb. Die Hauptursache war zwar ein zu
# kleines Kontextfenster (siehe OLLAMA_NUM_CTX in server.py), aber auch der
# alte Prompt lud dazu ein: "extrem kompakt", "nur das Wesentliche".
# A/B/PRO bekommt eine eigene Check-up-Zeile: ohne Befund-Abschnitt und ohne
# Listen von Vorerkrankungen/Dauermedikation (siehe ABPRO_PROMPT).
_COMPLETENESS_RULES = """VOLLSTÄNDIGKEIT – die wichtigste Regel:
- Geh das Transkript von Anfang bis Ende durch. Gespräche springen zwischen Themen; was am Anfang besprochen wurde, ist genauso wichtig wie das Ende.
- Patienten kommen oft mit mehreren, voneinander unabhängigen Anliegen. Jede angesprochene Beschwerde und jedes Thema gehört in die Dokumentation – auch wenn es nur kurz oder nebenbei erwähnt wurde. Fasse unabhängige Beschwerden nicht zusammen und lass keine weg, weil eine andere wichtiger erscheint.
{checkup}
- Übernimm Zahlen wörtlich: Messwerte, Laborwerte, Dosierungen, Zeitangaben.
- Bewerte Befunde nur so, wie sie im Gespräch bewertet wurden – nichts als „unauffällig“ oder „gut“ zusammenfassen, was dort nicht so genannt wurde."""

COMPLETENESS_RULES = _COMPLETENESS_RULES.replace("{checkup}", "- Bei Gesundheitsuntersuchung, Check-up oder Vorsorge ist der Anlass nicht das einzige Thema: dokumentiere zusätzlich jede dabei genannte Beschwerde, Vorerkrankung, Dauermedikation, Risikofaktoren (z. B. Rauchen, Gewicht, Blutdruck, familiäre Belastung), Impf- und Vorsorgestatus und jeden Befund – soweit im Gespräch vorkommend.")

ABPRO_COMPLETENESS_RULES = _COMPLETENESS_RULES.replace("{checkup}", "- Bei Gesundheitsuntersuchung, Check-up oder Vorsorge ist der Anlass nicht das einzige Thema: dokumentiere zusätzlich jede dabei genannte Beschwerde, Risikofaktoren (z. B. Rauchen, Gewicht, familiäre Belastung) und den Impf- und Vorsorgestatus – soweit im Gespräch vorkommend.")

ANALYZE_PROMPT = """Du bist ein KI-Assistent für medizinische Dokumentation in einer Hausarztpraxis.
{gender_instruction}
Analysiere das folgende Transkript eines Arzt-Patienten-Gesprächs und erstelle eine strukturierte Zusammenfassung für den Arzt. Halte dich ausschließlich an die Inhalte des Transkripts – keine Annahmen, keine Ergänzungen.
{speaker_context}
WICHTIG: Wenn ein Begriff im Transkript unklar, unverständlich oder phonetisch verzerrt wirkt, schreibe ihn exakt so ab oder markiere ihn als [unverständlich]. Erfinde unter keinen Umständen Wörter, Fachbegriffe, Produktnamen oder Medikamente. Nutze das folgende Glossar ausschließlich, um bereits im Transkript angedeutete Begriffe korrekt zu erkennen – niemals, um neue Inhalte zu ergänzen.

{medical_glossary}

<transkript>
{transcript}
</transkript>

""" + COMPLETENESS_RULES + """

Erstelle folgende strukturierte Übersicht:

## Zusammenfassung
(2–3 Sätze zum Kern des Gesprächs; bei mehreren Anliegen alle nennen)

## Vorstellungsgrund und Beschwerden
(Jedes Anliegen und jede Beschwerde als eigener Aufzählungspunkt – mit Dauer, Verlauf und Begleitumständen, soweit genannt)

## Anamnese
(Vorerkrankungen, Dauermedikation, Risikofaktoren, Sozial- und Familienanamnese – soweit im Gespräch erwähnt)

## Befunde
(Messwerte, Laborwerte, körperliche Untersuchung, Ergebnisse von Voruntersuchungen – mit Zahlen; sonst: Nicht besprochen)

## Diagnosen / Verdachtsdiagnosen
(Falls erwähnt – sonst: Nicht besprochen)

## Maßnahmen und Empfehlungen
(Je Anliegen: Untersuchungen, Medikamente mit Dosierung, Überweisungen, Impfungen, Beratung, Kontrolltermine – falls besprochen)

## Hinweise für den Arzt
(Auffälligkeiten, offene Punkte oder dringender Follow-up-Bedarf)

Antworte vollständig auf Deutsch."""

ABPRO_PROMPT = """Du bist ein KI-Assistent für medizinische Dokumentation in einer Hausarztpraxis.
{gender_instruction}
Erstelle aus dem folgenden Transkript eines Arzt-Patienten-Gesprächs eine Dokumentation nach dem A/B/PRO-Schema. Nur Transkript-Inhalte – keine Annahmen, keine Ergänzungen. Erfinde keine Wörter, Fachbegriffe oder Medikamente – unklare Stellen als [unverständlich] markieren.
{speaker_context}
Nutze das folgende Glossar ausschließlich, um bereits im Transkript angedeutete Begriffe korrekt zu erkennen – niemals, um neue Inhalte zu ergänzen.

{medical_glossary}

<transkript>
{transcript}
</transkript>

""" + ABPRO_COMPLETENESS_RULES + """

FORM:
- Telegrammstil: knappe Stichworte statt ganzer Sätze, keine Einleitung, keine Erläuterungen. Knapp heißt kurz formuliert – nicht weniger Themen.
- Mehrere Anliegen innerhalb eines Abschnitts durch Semikolon trennen, jedes mit eigenem Stichwort.
- Kein Befund-Abschnitt: körperliche Untersuchung und Befunde dokumentiert der Arzt selbst. Schreib keinen Abschnitt "B:" und keine Untersuchungsbefunde.
- Keine Unterpunkte oder Überschriften wie "Vorerkrankungen:", "Vormedikation:" oder "Dauermedikation:" – die stehen in der Kartei. Eine Vorerkrankung oder ein Medikament nur erwähnen, wenn es zu einem Anliegen gehört (z. B. als Auslöser, Nebenwirkung oder Anlass einer Änderung), und dann beim Anliegen selbst.
- Verwende exakt diese zwei Abschnitte, ohne die Klammertexte zu wiederholen:

A: (Anlass des Besuchs, dann jede Beschwerde bzw. jedes Anliegen einzeln mit Dauer und Verlauf)

PRO: (Für jedes Anliegen aus A die besprochenen Maßnahmen: Medikation mit Dosierung, Untersuchungen, Überweisungen, Impfungen, Beratung, Kontrolltermine)

Antwort auf Deutsch."""

# ============================================================
# PSYCHOTHERAPIE (verhaltenstherapeutische Sitzung, 25/50 min)
# ============================================================
# Anders als in der Sprechstunde geht es nicht um ein Schema, sondern um eine
# Zusammenfassung des Gesagten — mit wechselnden Themen und bewusst weniger
# Kuerzung: in einer Sitzung traegt das Detail (welche Situation, welcher
# Gedanke, wer hat was gesagt) die Information. Keine Diagnostik, keine
# Deutung. Der eigene Sprecher-Kontext ersetzt "Arzt"/"Anamnesefragen".
#
# Die Gliederung folgt dem, was fuer die stundenbezogene Verlaufsdokumentation
# empfohlen wird — BPtK-Dokumentationsempfehlungen (Abschnitt 9: Interventionen
# und Ergebnisse, Inhalt des Therapiegeschehens, Aenderungen u. a. Suizidalitaet,
# Ereignisse ausserhalb der Therapie, Umsetzungsprobleme, Fort-/Rueckschritte),
# Stundenprotokoll der Uni Wuerzburg (letzte Hausaufgabe, Themen, Ergebnisse,
# neue Hausaufgabe, besondere Absprachen) und der KVT-Sitzungsablauf nach
# J. Beck (Befinden, Hausaufgaben, Themen, Zusammenfassung, neue Aufgabe,
# Rueckmeldung). Bewusst weggelassen: Interaktionsverhalten und beobachteter
# Befund — das Transkript zeigt nur, was gesagt wurde; beides ergaenzt die
# Therapeutin selbst.
SPEAKER_CONTEXT_PSY_PLAIN = """SPRECHER: Das Transkript ist ein Dialog ohne Sprecherkennzeichnung zwischen Therapeut/in und {patient_label}. Ordne Aussagen anhand des Kontexts zu; wenn die Zuordnung unklar bleibt, formuliere neutral ("es wurde besprochen") statt zu raten. Was die Therapeutin/der Therapeut als Frage, Hypothese oder Beispiel einbringt, hat nicht {patient_label} gesagt."""

SPEAKER_CONTEXT_PSY_DIARIZED = """SPRECHER: Die Sprecher wurden automatisch getrennt – jede Zeile beginnt mit einem anonymen Marker (SPRECHER A, SPRECHER B, ...). Welcher Marker Therapeut/in und welcher {patient_label} ist, steht NICHT fest: erschließe es aus dem Gesprächsverlauf – wer fragt, strukturiert, zusammenfasst und Übungen vorschlägt, ist die Therapeutin/der Therapeut.
Die Sprechertrennung ist fehleranfällig, vor allem an Sprecherwechseln und bei kurzen Einwürfen. Wenn Marker und Inhalt sich widersprechen, hat der Inhalt Vorrang. Was die Therapeutin/der Therapeut als Frage, Hypothese oder Beispiel einbringt, hat nicht {patient_label} gesagt.
Übernimm die Marker NICHT in deine Ausgabe."""

PSYCHO_PROMPT = """Du bist ein KI-Assistent für die Dokumentation verhaltenstherapeutischer Sitzungen.
{gender_instruction}
Fasse das folgende Transkript einer Psychotherapiesitzung (Verhaltenstherapie) für die Therapeutin/den Therapeuten zusammen. Ziel ist eine inhaltlich vollständige Zusammenfassung des Gesagten – keine Diagnostik, keine Deutung, keine eigene Bewertung.
{speaker_context}

<transkript>
{transcript}
</transkript>

REGELN:
- Du fasst zusammen, was gesagt wurde – nicht mehr. Die Therapeutin/der Therapeut ergänzt selbst, was nur zu beobachten war (Stimmung, Affekt, Kontaktverhalten); dazu schreibst du nichts, auch nicht vermutend.
- Geh die Sitzung von Anfang bis Ende durch. Die Themen wechseln; jedes Thema gehört in die Zusammenfassung, auch wenn es nur kurz angesprochen wurde. Weglassen darfst du nur Begrüßung, Smalltalk und Organisatorisches ohne Bezug zur Behandlung.
- Kürze nicht zu stark. Gib wieder, was {patient_label} berichtet: konkrete Situationen und Ereignisse, Gedanken, Gefühle, Körperempfindungen, Verhalten und dessen Folgen, Beziehungen – mit den Einzelheiten, die genannt wurden (wer, wann, was). Andere Personen mit der Beziehung bezeichnen, die im Gespräch genannt wurde (z. B. "der Ehemann", "eine Kollegin").
- Zahlen wörtlich übernehmen: Skalenwerte (z. B. Stimmung 4 von 10, Anspannung 80 %), Häufigkeiten, Dauer, Daten.
- Was {patient_label} prägnant formuliert – vor allem Gedanken und Bewertungen in eigenen Worten –, als kurzes wörtliches Zitat in Anführungszeichen übernehmen.
- Was die Therapeutin/der Therapeut tut, so beschreiben, wie es im Gespräch geschieht: was gefragt, erklärt, geübt oder vorgeschlagen wurde und wie {patient_label} darauf reagierte. Einen Fachbegriff (z. B. Situationsanalyse, kognitive Umstrukturierung, Exposition, Psychoedukation, Rollenspiel, Aktivitätenplan) nur, wenn er im Gespräch fällt oder das Vorgehen eindeutig ist – sonst beschreiben, was getan wurde. Erklärungen der Therapeutin/des Therapeuten knapp inhaltlich wiedergeben.
- Ergebnisse, Erkenntnisse, Fort- oder Rückschritte nur, wenn sie im Gespräch ausgesprochen wurden – wenn {patient_label} sie benennt oder die Therapeutin/der Therapeut sie zusammenfasst. Nicht selbst bewerten, ob etwas gelungen ist.
- Keine Diagnosen, keine Deutungen, keine Empfehlungen, die nicht im Gespräch ausgesprochen wurden.
- Suizidgedanken, Selbstverletzung oder Gefährdung anderer nur aufführen, wenn sie im Gespräch vorkamen – dann vollständig und möglichst wörtlich, einschließlich dazu getroffener Absprachen. Nichts erschließen.
- Unklare oder phonetisch verzerrte Stellen exakt abschreiben oder als [unverständlich] markieren; nichts erfinden.
- Wurde ein Abschnitt in der Sitzung nicht angesprochen, schreib den dort genannten Ersatztext – fülle ihn nicht aus anderen Teilen auf.

Gliedere so:

## Befinden und Ereignisse seit der letzten Sitzung
(Wie es {patient_label} nach eigener Aussage geht; was sich seither ereignet oder verändert hat – Arbeit, Familie, Partnerschaft, Gesundheit, Behandlung anderswo (Facharzt, Klinik, Medikation) – sonst: Nicht besprochen)

## Hausaufgaben aus der letzten Sitzung
(Was vereinbart war, ob und wie es umgesetzt wurde, was dabei herauskam, was schwierig war – sonst: Nicht besprochen)

## Themen
(Stichwortliste aller Themen in der Reihenfolge des Gesprächs)

## Verlauf der Sitzung
(Je Thema eine Zwischenüberschrift "### <Thema>" und darunter ein bis drei Absätze in ganzen Sätzen: was berichtet wurde, wie daran gearbeitet wurde und wie {patient_label} reagierte, was dabei herauskam. Was {patient_label} zur Sitzung oder zur Therapie zurückmeldet, beim passenden Thema oder am Ende)

## Vereinbarungen
(Neue Übungen und Hausaufgaben so konkret wie besprochen – was, wann, wie oft; weitere Absprachen, z. B. Facharzttermin, Klinik, Kontakt mit Angehörigen; was in der nächsten Sitzung drankommen soll; nächster Termin – sonst: Keine besprochen)

## Gefährdung
(Nur was dazu gesagt wurde, mit Absprachen dazu – sonst: Nicht thematisiert)

Antworte auf Deutsch, in der dritten Person."""

# Rueckfall, wenn am Knopf nichts vorgegeben war und kein Auftrag vom
# Psychotherapie-Geraet kam: nur bei langen Aufnahmen gefragt (STENO_PSY_AB_MINUTEN).
PSY_CLASSIFY_PROMPT = """Ist das folgende Gespräch eine psychotherapeutische Sitzung (z. B. Verhaltenstherapie) oder eine ärztliche Sprechstunde (Beschwerden, Untersuchung, Medikamente, Vorsorge)?
Antworte mit genau einem Wort: PSYCHOTHERAPIE oder SPRECHSTUNDE.

Gesprächsausschnitt:
{transcript}

Antwort:"""

SUMMARY_PROMPT = """Erstelle eine kurze, allgemeinverständliche Zusammenfassung des folgenden Gesprächs.
- Jedes besprochene Thema bekommt 1–2 Sätze – auch Nebenthemen, keines auslassen
- Keine Fachbegriffe, kein Schema
- Trenne jedes Thema durch eine Leerzeile (doppelter Zeilenumbruch) vom nächsten
- Gib NUR die Zusammenfassung zurück, ohne Überschriften oder Meta-Kommentare

Transkript:
{transcript}

Zusammenfassung:"""

SUBJECT_PROMPT = """Erstelle eine einzeilige Betreffzeile (maximal 100 Zeichen) für das folgende Gespräch.
Gib NUR die Betreffzeile zurück, ohne Anführungszeichen, ohne abschließenden Punkt und ohne Meta-Kommentare.

Transkript:
{transcript}

Betreff:"""

CLEAN_PROMPT = """Bereinige das folgende medizinische Transkript:
- Entferne Füllwörter (äh, ähm, hm, sozusagen, halt, ne, also, irgendwie)
- Korrigiere offensichtliche Transkriptionsfehler
- Nutze das folgende Glossar, um phonetisch verzerrte Fachbegriffe zu erkennen und korrekt zu schreiben – nur wenn ein Begriff erkennbar gemeint ist, nichts erzwingen oder ergänzen
- Behalte den Inhalt und die Formulierungen exakt bei
- Falls Zeilen mit Sprecher-Markern beginnen (z. B. "SPRECHER A:"), behalte diese unverändert bei
- Keine Umformulierungen, keine Zusammenfassungen
- Gib NUR den bereinigten Text zurück, ohne Meta-Kommentare

{medical_glossary}

Transkript:
{transcript}

Bereinigter Text:"""
