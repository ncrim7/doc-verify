"""Guard tests for src.config after the 1.3 trim."""
import importlib

import pytest

import src.config as config


@pytest.fixture
def reload_config(monkeypatch):
    """Reload src.config with a patched environment."""
    def _reload(**env):
        for k, v in env.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, v)
        return importlib.reload(config)
    yield _reload
    importlib.reload(config)   # restore module to ambient env


def test_single_openai_provider_by_default():
    assert list(config.LLM_PROVIDERS) == ["openai"]
    assert config.LLM_PROVIDERS["openai"]["backend"] == "openai"


def test_default_model_is_gpt5_nano_with_no_temperature(reload_config):
    cfg = reload_config(LLM_MODEL=None)
    p = cfg.LLM_PROVIDERS["openai"]
    assert p["model"] == "gpt-5-nano"
    assert p["temperature"] is None            # gpt-5* rejects explicit temperature
    assert p["cost_per_1k_tokens"] == 0.00005


def test_gpt41_model_keeps_zero_temperature(reload_config):
    p = reload_config(LLM_MODEL="gpt-4.1-nano").LLM_PROVIDERS["openai"]
    assert p["model"] == "gpt-4.1-nano"
    assert p["temperature"] == 0.0
    assert p["cost_per_1k_tokens"] == 0.00010


def test_unknown_model_falls_back_to_nano_cost(reload_config):
    p = reload_config(LLM_MODEL="some-future-model").LLM_PROVIDERS["openai"]
    assert p["cost_per_1k_tokens"] == 0.00005


def test_dataset_splits_sum_to_one():
    s = config.DATASET_CONFIG["splits"]
    assert round(s["train"] + s["val"] + s["test"], 6) == 1.0


def test_dead_config_blocks_removed():
    for name in ("LANGFUSE_CONFIG", "TELEGRAM_CONFIG"):
        assert not hasattr(config, name)
    assert "priority" not in config.LLM_PROVIDERS["openai"]
    assert "per_type" not in config.DATASET_CONFIG


def test_groq_is_opt_in_only():
    assert "groq" not in config.LLM_PROVIDERS
