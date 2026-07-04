
from __future__ import annotations


def test_get_model_capability_registry_hit(reload_korina_modules):
    model_capability = reload_korina_modules("korina.services.model_capability")[0]

    cap = model_capability.get_model_capability("/models/gemma-4-E2B_q4_0-it.gguf")

    assert cap["supports_audio_input"] is True
    assert cap["source"] == "local_gguf"
    assert cap["has_mmproj"] is True
    assert cap["_inference"] == "registry"


def test_mmproj_on_disk_detection(reload_korina_modules, tmp_path):
    model = tmp_path / "custom-textless-model.gguf"
    model.write_text("gguf")
    (tmp_path / "custom-textless-model-mmproj.gguf").write_text("mmproj")
    model_capability = reload_korina_modules(
        "korina.services.model_catalog", "korina.services.model_capability"
    )[1]

    cap = model_capability.get_model_capability(str(model))

    assert cap["supports_audio_input"] is True
    assert cap["source"] == "local_gguf"
    assert cap["has_mmproj"] is True
    assert cap["_inference"] == "mmproj_present"


def test_allowlist_and_blacklist_behavior(reload_korina_modules):
    model_capability = reload_korina_modules("korina.services.model_capability")[0]

    assert model_capability.get_model_capability("vendor/gpt-4o-audio-preview")["supports_audio_input"] is True
    assert model_capability.get_model_capability("/models/orpheus-3b.gguf")["supports_audio_input"] is False


def test_qwen3_vl_is_not_audio_unless_runtime_allowlisted(reload_korina_modules):
    model_capability = reload_korina_modules("korina.services.model_capability")[0]
    model_id = "local/qwen3-vl-8b-instruct.gguf"

    default_cap = model_capability.get_model_capability(model_id)
    allowlisted_cap = model_capability.get_model_capability(model_id, allowlist=(model_id,))

    assert default_cap["supports_audio_input"] is False
    assert default_cap["_inference"] in {"registry", "blacklist"}
    assert allowlisted_cap["supports_audio_input"] is True
    assert allowlisted_cap["_inference"] == "runtime_allowlist"


def test_multimodal_allowlist_is_a_persisted_config_key(config_module):
    saved = config_module.save_config({"multimodal_stt_model_allowlist": ["local/qwen3-vl-8b"]})

    assert config_module.config_multimodal_stt_model_allowlist(saved) == ("local/qwen3-vl-8b",)
