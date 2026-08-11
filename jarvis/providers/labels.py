"""One provider/model label vocabulary, shared by every surface.

Why this module exists (2026-07-31). Provider labels were duplicated and had
drifted apart:

  * ``jarvis/cli.py`` carried a ``_PROVIDER_LABELS`` dict *inside* a function
    body, keyed on the model-menu vocabulary (``local``/``vertex``/``aistudio``/
    ``groq``).
  * ``jarvis/providers/__init__.py``'s ``_Tier`` stamps the RUNTIME vocabulary
    onto every call: ``ollama``/``vertex``/``aistudio``. Note ``ollama`` — not
    ``local``. The two vocabularies overlap but are not the same set, and
    nothing kept them in sync.
  * The Electron HUD had no dictionary at all: it hardcoded
    ``state === 'thinking' ? 'Gemini 2.5 Pro' : 'Gemini 2.5 Flash'``, i.e. it
    derived the *provider label* from an animation state and never read the
    real one. Running locally on qwen3:8b, the HUD said "Gemini".

The owner caught the CLI half of this live while testing. The rule that came
out of it, and which this module exists to make cheap to follow:

    A readout is bound to a real value or it says it has none. It never
    hardcodes a plausible-looking string.

``label_for_provider`` accepts BOTH vocabularies so a caller never has to know
which one its value came from.
"""
from __future__ import annotations

_PROVIDER_LABELS: dict[str, str] = {
    # runtime vocabulary — jarvis/providers/__init__.py's _Tier(...)
    "ollama": "Ollama",
    "vertex": "Vertex",
    "aistudio": "AI Studio",
    "nvidia": "NVIDIA NIM",
    # model-menu vocabulary — jarvis/agent.py's AVAILABLE_MODELS
    "local": "Ollama",
    "groq": "Groq",
    "gemini": "Gemini",
}

# What a surface shows when it genuinely has no value yet. Deliberately not "0",
# "unknown" or a plausible default: those read as measurements.
NO_VALUE = "—"


def label_for_provider(provider: str | None) -> str:
    """Human label for a provider id, from either vocabulary.

    An unrecognised, non-empty provider is title-cased rather than hidden — a
    new backend should show up as itself, not silently become NO_VALUE.
    """
    if not provider:
        return NO_VALUE
    return _PROVIDER_LABELS.get(provider, provider.title())


def describe_model(provider: str | None, model: str | None) -> str:
    """One-line "model · provider" readout, e.g. ``qwen3:8b · Ollama``.

    Returns NO_VALUE when the model is unknown: no turn has run yet, so there
    is nothing true to say. Callers must render that as-is rather than
    substituting a default.
    """
    if not model:
        return NO_VALUE
    label = label_for_provider(provider)
    return model if label == NO_VALUE else f"{model} · {label}"
