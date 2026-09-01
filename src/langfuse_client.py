"""
Optional Langfuse client singleton.
Silently disabled when LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are not set
or when the langfuse package is not installed.
"""
import os
import logging

logger = logging.getLogger(__name__)

_client = None
_initialized = False


def get_langfuse():
    """Return Langfuse client if configured, else None."""
    global _client, _initialized
    if _initialized:
        return _client

    _initialized = True
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")

    if not public_key or not secret_key:
        logger.debug("Langfuse disabled — LANGFUSE_PUBLIC_KEY/SECRET_KEY not set")
        return None

    try:
        from langfuse import Langfuse
        # Support both LANGFUSE_HOST and LANGFUSE_BASE_URL (langfuse 4.x uses BASE_URL)
        host = (
            os.getenv("LANGFUSE_BASE_URL")
            or os.getenv("LANGFUSE_HOST")
            or "https://cloud.langfuse.com"
        )
        _client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
        logger.info("Langfuse initialized: host=%s", host)
    except ImportError:
        logger.warning("langfuse package not installed — run: pip install langfuse")
    except Exception as exc:
        logger.warning("Langfuse init failed: %s", exc)

    return _client


def is_enabled() -> bool:
    return get_langfuse() is not None
