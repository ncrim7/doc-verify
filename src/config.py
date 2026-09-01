"""
Configuration module for multi-API LLM providers and dataset settings.
Loads sensitive values from .env file.
"""
import os
from dotenv import load_dotenv

load_dotenv()

LLM_PROVIDERS: dict = {
    "openai": {
        "api_key": os.getenv("OPENAI_API_KEY"),
        "model": "gpt-4.1-nano",
        "backend": "openai",
        "temperature": 0.0,             # gpt-4.x supports temperature
        "cost_per_1k_tokens": 0.0001,   # $0.10/1M input
        "priority": 1,
    },
    "openai-gpt5-nano": {
        "api_key": os.getenv("OPENAI_API_KEY"),
        "model": "gpt-5-nano",
        "backend": "openai",
        "temperature": None,            # gpt-5 only supports default temperature
        "cost_per_1k_tokens": 0.00005,  # $0.05/1M input
        "priority": 2,
    },
    "openai-gpt5-mini": {
        "api_key": os.getenv("OPENAI_API_KEY"),
        "model": "gpt-5-mini",
        "backend": "openai",
        "temperature": None,            # gpt-5 only supports default temperature
        "cost_per_1k_tokens": 0.00025,  # $0.25/1M input
        "priority": 3,
    },
    "openai-4o-mini": {
        "api_key": os.getenv("OPENAI_API_KEY"),
        "model": "gpt-4o-mini",
        "backend": "openai",
        "temperature": 0.0,
        "cost_per_1k_tokens": 0.00015,  # $0.15/1M input
        "priority": 3,
    },
    "groq": {
        "api_key": os.getenv("GROQ_API_KEY"),
        "api_key_2": os.getenv("GROQ_API_KEY_2"),
        "model": "meta-llama/llama-4-scout-17b-16e-instruct",
        "backend": "groq",
        "cost_per_1k_tokens": 0.000018,
        "priority": 4,
    },
}

DATA_CONFIG: dict = {
    "languages": ["tr", "en"],
    "language_mix_probability": 0.30,   # 30% mixed docs
    "turkish_only_probability": 0.35,   # 35% Turkish-only
    # remaining 35% → English-only
    "document_types": ["invoice", "po", "receipt"],
}

DATASET_CONFIG: dict = {
    "total_documents": int(os.getenv("DATASET_TOTAL", 120)),
    "per_type": 40,
    "seed": int(os.getenv("DATASET_SEED", 42)),
    "splits": {
        "train": 0.60,
        "val":   0.20,
        "test":  0.20,
    },
}

OUTPUT_DIRS: dict = {
    "invoices":        "data/raw/invoices",
    "purchase_orders": "data/raw/purchase_orders",
    "receipts":        "data/raw/receipts",
    "metadata":        "data/metadata",
}

LANGFUSE_CONFIG: dict = {
    "public_key": os.getenv("LANGFUSE_PUBLIC_KEY"),
    "secret_key": os.getenv("LANGFUSE_SECRET_KEY"),
    "host": os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
    "enabled": bool(
        os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
    ),
}

TELEGRAM_CONFIG: dict = {
    "bot_token": os.getenv("TELEGRAM_BOT_TOKEN"),
    "enabled": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
}
