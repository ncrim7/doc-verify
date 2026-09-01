"""
Quick connectivity test for all configured LLM providers.

Usage:
    python scripts/test_api_connections.py
    python scripts/test_api_connections.py --provider openai
"""
import sys
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import LLM_PROVIDERS

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def test_openai(cfg: dict) -> tuple[bool, str]:
    try:
        import openai
        client = openai.OpenAI(api_key=cfg["api_key"])
        resp = client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "user", "content": "Reply with OK"}],
            max_tokens=5,
        )
        reply = resp.choices[0].message.content.strip()
        return True, f"Response: '{reply}'"
    except Exception as exc:
        return False, str(exc)


def test_gemini(cfg: dict) -> tuple[bool, str]:
    try:
        import warnings
        warnings.filterwarnings("ignore", category=FutureWarning)
        import google.generativeai as genai
        genai.configure(api_key=cfg["api_key"])
        model = genai.GenerativeModel("gemini-2.0-flash")
        resp = model.generate_content("Reply with OK")
        return True, f"Response: '{resp.text.strip()}'"
    except Exception as exc:
        return False, str(exc)


def test_groq(cfg: dict) -> tuple[bool, str]:
    try:
        from groq import Groq
        client = Groq(api_key=cfg["api_key"])
        resp = client.chat.completions.create(
            model=cfg["model"],
            messages=[{"role": "user", "content": "Reply with OK"}],
            max_tokens=5,
        )
        reply = resp.choices[0].message.content.strip()
        return True, f"Response: '{reply}'"
    except Exception as exc:
        return False, str(exc)


TESTERS = {
    "openai": test_openai,
    "gemini": test_gemini,
    "groq":   test_groq,
}


def main() -> None:
    p = argparse.ArgumentParser(description="Test LLM API connections")
    p.add_argument("--provider", choices=list(TESTERS.keys()),
                   help="Test only this provider (default: all)")
    args = p.parse_args()

    providers = [args.provider] if args.provider else list(TESTERS.keys())

    logger.info("LLM API Connection Tests")
    logger.info("=" * 40)

    all_ok = True
    for name in providers:
        cfg = LLM_PROVIDERS.get(name, {})
        if not cfg.get("api_key"):
            logger.info("  %-8s  ⚠  No API key configured (set %s_API_KEY in .env)",
                        name.upper(), name.upper())
            all_ok = False
            continue

        tester = TESTERS[name]
        ok, msg = tester(cfg)
        icon = "✓" if ok else "✗"
        level = logger.info if ok else logger.error
        level("  %-8s  %s  %s", name.upper(), icon, msg)
        if not ok:
            all_ok = False

    logger.info("=" * 40)
    if all_ok:
        logger.info("All providers connected successfully.")
    else:
        logger.warning("Some providers failed — check your .env keys.")
        sys.exit(1)


if __name__ == "__main__":
    main()
