"""Kuratiertes deutsches Fachvokabular für Hausarzt-/Allgemeinmedizin-Gespräche.

Dient als Glossar-Kontext im LLM-Prompt, damit unklare/phonetisch verzerrte Wörter im
Transkript gegen bekannte Fachbegriffe abgeglichen statt frei geraten werden.

Hinweis: Wird NICHT als faster-whisper `hotwords`-Parameter verwendet – die ~120 Begriffe
sprengen dessen 448-Token-Decoder-Budget und lassen Whisper mit
"ValueError: The maximum decoding length must be > 0" abstürzen.

Dies ist ein kuratierter Startpunkt, keine vollständige Nomenklatur. Begriffe stammen aus
gängiger Hausarztpraxis-Terminologie. Für eine umfassendere/aktuellere Basis eignen sich als
Ergänzung die frei verfügbaren offiziellen Quellen:
- ICD-10-GM (Diagnosen) – BfArM, https://www.bfarm.de (kostenloser Download, Nutzungsvertrag)
- ATC-Index mit DDD (Wirkstoffe) – WIdO/GKV-Arzneimittelindex, https://www.wido.de
- STIKO-Impfempfehlungen – RKI

Liste einfach um weitere Begriffe ergänzen, keine Code-Änderung an anderer Stelle nötig.
"""

DIAGNOSEN = [
    "Gürtelrose", "Herpes zoster", "Hypertonie", "Diabetes mellitus Typ 2",
    "Diabetes mellitus Typ 1", "Osteoporose", "Hypothyreose", "Hyperthyreose",
    "Vorhofflimmern", "koronare Herzkrankheit", "Herzinsuffizienz", "COPD",
    "Divertikulitis", "Gastritis", "Refluxkrankheit", "Migräne", "Ischialgie",
    "Lumbago", "Zystitis", "Bronchitis", "Pneumonie", "Niereninsuffizienz",
    "Hyperlipidämie", "Adipositas", "Depression", "Angststörung", "Schlaganfall",
    "transitorische ischämische Attacke", "Thrombose", "Lungenembolie", "Sinusitis",
    "Otitis media", "Konjunktivitis", "Ekzem", "Psoriasis", "Gicht", "Arthrose",
    "rheumatoide Arthritis", "Karpaltunnelsyndrom", "Struma", "Anämie",
    "Harnwegsinfekt", "Gastroenteritis", "Tinnitus", "Schwindel", "Fibromyalgie",
]

WIRKSTOFFE = [
    "Metformin", "Ramipril", "Bisoprolol", "Amlodipin", "Simvastatin", "Atorvastatin",
    "L-Thyroxin", "Pantoprazol", "Omeprazol", "Ibuprofen", "Diclofenac", "Paracetamol",
    "Metamizol", "Amoxicillin", "Apixaban", "Rivaroxaban", "Phenprocoumon",
    "Acetylsalicylsäure", "Alendronsäure", "Prednisolon", "Insulin", "Sertralin",
    "Citalopram", "Mirtazapin", "Tamsulosin", "Furosemid", "Torasemid", "Allopurinol",
    "Colchicin", "Candesartan", "Losartan", "Metoprolol", "Pregabalin", "Escitalopram",
    "Levothyroxin", "Clopidogrel", "Spironolacton", "Budesonid", "Salbutamol",
]

IMPFSTOFFE = [
    "Shingrix", "Gürtelrose-Impfung", "Grippeimpfung", "Influenza-Impfung", "FSME-Impfung",
    "Tetanus-Impfung", "Diphtherie-Impfung", "Pertussis-Impfung", "Pneumokokken-Impfung",
    "Prevenar", "COVID-19-Impfung", "Comirnaty", "Spikevax", "Hepatitis-A-Impfung",
    "Hepatitis-B-Impfung", "HPV-Impfung", "Gardasil", "Meningokokken-Impfung",
]

UNTERSUCHUNGEN = [
    "Sonografie", "Koloskopie", "Gastroskopie", "Elektrokardiogramm", "Langzeit-EKG",
    "Belastungs-EKG", "Röntgen", "Magnetresonanztomografie", "Computertomografie",
    "Blutbild", "HbA1c", "CRP", "TSH-Wert", "Kreatinin", "glomeruläre Filtrationsrate",
    "Lipidstatus",
]

ALL_TERMS = DIAGNOSEN + WIRKSTOFFE + IMPFSTOFFE + UNTERSUCHUNGEN

# Glossar-Block zum Einbetten in LLM-Prompts.
MEDICAL_GLOSSARY = (
    "Bekannte Fachbegriffe zur Orientierung (nur verwenden, wenn sie tatsächlich zum "
    "Gesagten passen – nichts erzwingen, keine Begriffe einfügen, die im Transkript nicht "
    "andeutungsweise vorkommen):\n" + ", ".join(ALL_TERMS)
)
