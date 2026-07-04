
from __future__ import annotations

import json


def test_load_config_seeds_from_example_and_save_round_trip(config_module, app_dir):
    cfg = config_module.load_config()

    assert cfg["voice"] == "af_heart"
    assert cfg["llm_provider"] == "openai-compatible"
    assert (app_dir / "config.json").exists()

    saved = config_module.save_config({**cfg, "voice": "af_bella", "unknown_key": "drop me"})
    reloaded = config_module.load_config()

    assert saved["voice"] == "af_bella"
    assert reloaded["voice"] == "af_bella"
    assert "unknown_key" not in saved
    assert "unknown_key" not in reloaded


def test_load_config_migrates_legacy_agent_and_delivery_keys(config_module, app_dir):
    legacy = dict(config_module.DEFAULT_CONFIG)
    legacy.pop("agent_injection_mode", None)
    legacy[config_module.LEGACY_AGENT_MODE_KEY] = config_module.LEGACY_INJECTION_MODE_VALUE
    legacy["agent_busy_delivery_mode"] = config_module.LEGACY_DELIVERY_VALUE
    legacy["not_a_real_key"] = "must be filtered"
    (app_dir / "config.json").write_text(json.dumps(legacy))

    loaded = config_module.load_config()

    assert loaded["agent_injection_mode"] == "one-at-a-time"
    assert loaded["agent_busy_delivery_mode"] == "injection"
    assert "not_a_real_key" not in loaded


def test_unknown_keys_are_filtered_on_save(config_module):
    saved = config_module.save_config({"voice": "af_sky", "surprise": 123})

    assert saved["voice"] == "af_sky"
    assert "surprise" not in saved
    assert "surprise" not in config_module.load_config()


def test_audio_unsupported_cache_survives_save_config(config_module):
    triple = config_module.audio_unsupported_key(
        "llama.cpp", "http://127.0.0.1:8080/v1/chat/completions", "model.gguf"
    )
    cfg = config_module.load_config()
    cfg["audio_unsupported"] = {
        triple: {
            "reason": "body_match:audio",
            "since": "2026-06-25T00:00:00+00:00",
            "last_error": "audio not supported",
        }
    }

    config_module.save_config(cfg)
    cache = config_module.config_audio_unsupported()

    assert cache[triple]["reason"] == "body_match:audio"
    assert cache[triple]["last_error"] == "audio not supported"
