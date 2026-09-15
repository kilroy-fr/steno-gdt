#!/usr/bin/env python3
"""Macht die pyannote-Checkpoints unter PyTorch >= 2.6 ladbar.

PyTorch 2.6 hat den Default von `torch.load(weights_only=...)` auf True gedreht.
Die pyannote-Checkpoints enthalten neben den Gewichten Metadaten-Objekte, die
der restriktive Unpickler ablehnt — der Ladevorgang bricht mit
`UnpicklingError: Weights only load failed` ab.

Statt `weights_only=False` zu erzwingen (das erlaubt beliebige Code-Ausfuehrung
beim Laden) werden hier nur die konkret benoetigten Klassen freigegeben. Die
Checkpoints stammen aus den beim Build festgelegten pyannote-Repos und liegen
im Image, sind also keine fremde Eingabe.
"""
import logging

logger = logging.getLogger(__name__)


def allow_pyannote_checkpoint_globals() -> None:
    import torch

    add_safe_globals = getattr(torch.serialization, "add_safe_globals", None)
    if add_safe_globals is None:
        return  # torch < 2.6: weights_only ist noch False, nichts zu tun

    # Die Checkpoints speichern neben den Gewichten ihre Task-Beschreibung.
    # Specifications ist ein NamedTuple mit den beiden Enums Problem und
    # Resolution - alle drei muessen freigegeben sein, sonst bricht der
    # Unpickler nacheinander an jeder einzelnen ab.
    candidates = [
        ("torch.torch_version", "TorchVersion"),
        ("pyannote.audio.core.task", "Specifications"),
        ("pyannote.audio.core.task", "Problem"),
        ("pyannote.audio.core.task", "Resolution"),
    ]

    allowed = []
    for module_name, class_name in candidates:
        try:
            module = __import__(module_name, fromlist=[class_name])
            allowed.append(getattr(module, class_name))
        except Exception as e:
            logger.debug(f"{module_name}.{class_name} nicht verfuegbar: {e}")

    if allowed:
        add_safe_globals(allowed)
        logger.info(f"torch.load: {len(allowed)} Klasse(n) freigegeben")
