#!/usr/bin/env python3
"""Laedt die pyannote-Pipeline beim Image-Build in den HF-Cache.

Laeuft nur zur Build-Zeit. Dadurch braucht der Container zur Laufzeit keinen
Netzzugang und keinen HF-Token (siehe HF_HUB_OFFLINE=1 im Dockerfile) — wichtig
fuer den Praxisbetrieb, in dem Audio das Haus nicht verlaesst.

Der Token kommt per BuildKit-Secret (nicht per ARG), damit er nicht in der
Image-History landet.
"""
import os
import sys

MODEL = os.getenv("PYANNOTE_MODEL", "pyannote/speaker-diarization-community-1")


def main() -> int:
    # Anfuehrungszeichen mit abstreifen: `echo "hf_..." > .hf_token` schreibt
    # sie in manchen Shells literal mit, was sonst still zu 401 fuehrt.
    token = (os.getenv("HF_TOKEN") or "").strip().strip('"').strip("'").strip()
    if not token:
        print(
            "FEHLER: Kein HF_TOKEN. Die pyannote-Modelle sind auf Hugging Face\n"
            "gated (kostenlos, nur Registrierung + Bedingungen akzeptieren).\n"
            "Siehe diarize/README.md.",
            file=sys.stderr,
        )
        return 1

    from pyannote.audio import Pipeline

    from torch_compat import allow_pyannote_checkpoint_globals

    allow_pyannote_checkpoint_globals()

    # pyannote 3.x nahm `use_auth_token`, 4.x nimmt `token`. Die Signatur zu
    # fragen haelt einen Rueckbau auf pyannote 3.x (requirements.txt) frei von
    # Aenderungen an dieser Datei.
    import inspect

    params = inspect.signature(Pipeline.from_pretrained).parameters
    kwarg = "token" if "token" in params else "use_auth_token"
    pipeline = Pipeline.from_pretrained(MODEL, **{kwarg: token})

    if pipeline is None:
        # from_pretrained gibt None zurueck (statt zu werfen), wenn die
        # Nutzungsbedingungen des Repos nicht akzeptiert wurden.
        print(
            f"FEHLER: Pipeline '{MODEL}' konnte nicht geladen werden.\n"
            "Meist sind die Nutzungsbedingungen noch nicht akzeptiert — auf\n"
            f"https://huggingface.co/{MODEL} bestaetigen (bei 3.1 zusaetzlich\n"
            "pyannote/segmentation-3.0 und pyannote/wespeaker-voxceleb-resnet34-LM).",
            file=sys.stderr,
        )
        return 1

    print(f"OK: '{MODEL}' im Cache ({os.getenv('HF_HOME')}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
