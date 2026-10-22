"""Settings. Every option is an env-var name (server default) that a request can override with the same name:
    get("TABULA_READER", settings)  ->  settings["TABULA_READER"] if given, else os.environ["TABULA_READER"]
so .env, the CLI, the HTTP API and the viewer all speak one vocabulary (see .env.example).
API keys passed per request are used for that request only; nothing here writes them anywhere.
"""
import os

# Readers that look at the page image. "eval" = measured on the 85-page eval set (REPORT.md §3).
READERS = {
    "typellm": {"label": "TypeLLM (typed extraction API)", "needs": ["TYPELLM_API_KEY"],
                "eval": "0.996 cells alone · 1.000 with the text layer · ≈ $0.0001/page"},
    "scaledown": {"label": "ScaleDown Extract", "needs": ["SCALEDOWN_API_KEY"],
                  "eval": "0.720 cells: drops rows and columns on dense statements"},
    "vlm": {"label": "VLM, any OpenAI-compatible endpoint (vLLM, OpenAI, Gemini, OpenRouter…)", "needs": ["TABULA_VLM_URL"],
            "eval": "Qwen3-VL-8B zero-shot: 0.995 cells alone · 0.999 with the text layer (other models: not measured)"},
    "none": {"label": "None: text layer only", "needs": [], "eval": "exact on born-digital pages; can't read scans; no cross-check"},
}


def get(name, settings=None, default=None):
    v = (settings or {}).get(name)
    return v if v not in (None, "") else os.environ.get(name, default)


def available(reader, settings=None):
    """Can this reader answer right now? Demo replay counts (saved replies for the eval pages)."""
    if reader == "none":
        return True
    if get("TABULA_DEMO", settings) == "1":
        return True
    return all(get(k, settings) for k in READERS[reader]["needs"])
