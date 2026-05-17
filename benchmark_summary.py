#!/usr/bin/env python3
"""
Speed-Benchmark für SUMMARY_PROMPT gegen alle Whitelist-Modelle.

Ablauf:
  1. Audio einmalig transkribieren (via /transcribe-only)
  2. Für jedes Modell: SUMMARY_PROMPT streamen, TTFT + tok/s messen
  3. Ergebnis als Tabelle ausgeben
"""

import sys
import time
import json
import requests

SERVER   = "http://localhost:8010"
OLLAMA   = "http://localhost:11434"
AUDIO    = "example/9CE89D4F_2.mp3"

# ── SUMMARY_PROMPT (identisch mit prompts.py) ────────────────────────────────
SUMMARY_PROMPT = """Erstelle eine kurze, allgemeinverständliche Zusammenfassung des folgenden Gesprächs.
- 5–8 Sätze, keine Fachbegriffe, kein Schema
- Trenne unterschiedliche Themen durch einen Zeilenumbruch voneinander
- Nur die wichtigsten Punkte, die im Gespräch besprochen wurden
- Gib NUR die Zusammenfassung zurück, ohne Überschriften oder Meta-Kommentare

Transkript:
{transcript}

Zusammenfassung:"""

# ─────────────────────────────────────────────────────────────────────────────

def transcribe(audio_path: str) -> str:
    print(f"Transkribiere {audio_path} …", flush=True)
    with open(audio_path, "rb") as f:
        r = requests.post(
            f"{SERVER}/transcribe-only",
            files={"audio": (audio_path, f, "audio/mpeg")},
            timeout=300,
        )
    r.raise_for_status()
    text = r.json()["transcript"]
    print(f"  -> {len(text)} Zeichen transkribiert\n", flush=True)
    return text


def benchmark_model(model: str, prompt: str) -> dict:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "think": False,
        "options": {"temperature": 0.3, "top_p": 0.9, "num_predict": 1024},
    }
    t_start   = time.perf_counter()
    t_first   = None
    n_tokens  = 0
    output    = []

    try:
        with requests.post(
            f"{OLLAMA}/api/generate", json=payload, stream=True, timeout=300
        ) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines():
                if not raw:
                    continue
                data = json.loads(raw)
                token = data.get("response", "")
                if token:
                    if t_first is None:
                        t_first = time.perf_counter()
                    n_tokens += 1
                    output.append(token)
                if data.get("done"):
                    break
    except Exception as e:
        return {"model": model, "error": str(e)}

    t_end    = time.perf_counter()
    elapsed  = t_end - t_start
    ttft     = (t_first - t_start) if t_first else elapsed
    tok_s    = n_tokens / elapsed if elapsed > 0 else 0

    return {
        "model":    model,
        "ttft_s":   round(ttft, 2),
        "total_s":  round(elapsed, 2),
        "tokens":   n_tokens,
        "tok_s":    round(tok_s, 1),
        "output":   "".join(output).strip(),
        "error":    None,
    }


def main():
    # Verfügbare Whitelist-Modelle vom Server holen
    models = requests.get(f"{SERVER}/models", timeout=10).json().get("models", [])
    if not models:
        print("Keine Modelle gefunden – Server läuft?")
        sys.exit(1)

    print(f"Modelle ({len(models)}): {', '.join(models)}\n")

    transcript = transcribe(AUDIO)
    prompt     = SUMMARY_PROMPT.format(transcript=transcript)

    results = []
    for i, model in enumerate(models, 1):
        print(f"[{i}/{len(models)}] {model} …", end=" ", flush=True)
        res = benchmark_model(model, prompt)
        results.append(res)
        if res.get("error"):
            print(f"FEHLER: {res['error']}")
        else:
            print(f"{res['total_s']}s  |  TTFT {res['ttft_s']}s  |  {res['tok_s']} tok/s  |  {res['tokens']} Tokens")

    # ── Tabelle ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print(f"{'Modell':<22} {'TTFT':>7} {'Gesamt':>8} {'Tokens':>7} {'tok/s':>7}")
    print("-" * 72)
    ok = [r for r in results if not r.get("error")]
    ok.sort(key=lambda r: r["total_s"])
    for r in ok:
        print(f"{r['model']:<22} {r['ttft_s']:>6.1f}s {r['total_s']:>7.1f}s {r['tokens']:>7} {r['tok_s']:>7.1f}")
    for r in results:
        if r.get("error"):
            print(f"{r['model']:<22}  FEHLER: {r['error']}")
    print("=" * 72)

    # ── Ausgaben der Modelle ─────────────────────────────────────────────────
    print("\n\n-- Zusammenfassungen --\n")
    for r in ok:
        print(f"[{r['model']}]")
        for line in r["output"].splitlines():
            print(f"  {line}")
        print()


if __name__ == "__main__":
    main()
