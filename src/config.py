"""
Configuration: LLM provider + synthetic-dataset settings.
Secrets and overrides come from .env (see .env.example).
"""
import os

from dotenv import load_dotenv

load_dotenv()

# --- LLM model -------------------------------------------------------------
# Chosen via LLM_MODEL in .env. Default: gpt-5-nano
# (rationale: docs/adr/0001-extraction-model.md).
# gpt-5* models only accept the default sampling temperature, so `temperature`
# is None for them and omitted from the request.
_MODEL = os.getenv("LLM_MODEL", "gpt-5-nano")

# Approximate input price, USD per 1K tokens. Informational only — used for the
# call log / Langfuse cost display, never for control flow. Unknown models fall
# back to the gpt-5-nano rate.
# Reasoning budget for gpt-5 models, measured on the 60-document corpus:
#   default   ~22 min, and the reasoning tokens overflow the completion budget
#             -> unparseable JSON, the silent-failure class (P0-1)
#   minimal   ~5.6 min, 97.97% EM, 31/60 perfect — 0 reasoning tokens
#   low       ~8.3 min, 98.23% EM, 35/60 perfect — best measured
# "low" is the default: +0.26 EM and +4 perfect documents over "minimal" for
# ~2.7 min, still 2.6x faster than the model default, and it eliminated the
# straight-to-curly apostrophe error class outright.
# Override with LLM_REASONING_EFFORT ("minimal" | "low" | "medium" | "high").
# Never sent for models that do not accept the parameter.
_REASONING_EFFORT = (
    (os.getenv("LLM_REASONING_EFFORT") or "low")
    if _MODEL.startswith("gpt-5") else None
)

_COST_PER_1K: dict[str, float] = {
    "gpt-5-nano":   0.00005,
    "gpt-5-mini":   0.00025,
    "gpt-4.1-nano": 0.00010,
    "gpt-4.1-mini": 0.00040,
    "gpt-4o-mini":  0.00015,
}

LLM_PROVIDERS: dict = {
    "openai": {
        "api_key": os.getenv("OPENAI_API_KEY"),
        "model": _MODEL,
        "backend": "openai",
        "temperature": None if _MODEL.startswith("gpt-5") else 0.0,
        "reasoning_effort": _REASONING_EFFORT,
        "cost_per_1k_tokens": _COST_PER_1K.get(_MODEL, 0.00005),
    },
    # --- Optional: Groq / Llama-4 Scout vision -----------------------------
    # Disabled by default: customer documents should not transit a second
    # provider (see ADR-0001). To enable: `pip install groq`, set GROQ_API_KEY
    # (and optionally GROQ_API_KEY_2 for rate-limit rotation), uncomment, and
    # pass provider="groq" to the extractor/verifier.
    #
    # "groq": {
    #     "api_key":   os.getenv("GROQ_API_KEY"),
    #     "api_key_2": os.getenv("GROQ_API_KEY_2"),
    #     "model": "meta-llama/llama-4-scout-17b-16e-instruct",
    #     "backend": "groq",
    #     "cost_per_1k_tokens": 0.000018,
    # },
}

# --- Synthetic dataset (scripts/generate_dataset.py, scripts/split_dataset.py)
DATA_CONFIG: dict = {
    "languages": ["tr", "en"],
    "language_mix_probability": 0.30,   # 30% mixed-language documents
    "turkish_only_probability": 0.35,   # 35% Turkish-only; the rest English-only
    "document_types": ["invoice", "po", "receipt"],
}

DATASET_CONFIG: dict = {
    "total_documents": int(os.getenv("DATASET_TOTAL", "60")),
    "seed": int(os.getenv("DATASET_SEED", "42")),
    "splits": {"train": 0.60, "val": 0.20, "test": 0.20},
}

OUTPUT_DIRS: dict = {
    "invoices":        "data/raw/invoices",
    "purchase_orders": "data/raw/purchase_orders",
    "receipts":        "data/raw/receipts",
    "metadata":        "data/metadata",
}
