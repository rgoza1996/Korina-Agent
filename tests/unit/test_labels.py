from __future__ import annotations

from korina.util.labels import display_model_label, safe_slug


def test_safe_slug_normalizes_common_free_form_values():
    assert safe_slug("Hello, Korina!") == "hello_korina"
    assert safe_slug("  Multi   Space  Value  ") == "multi_space_value"
    assert safe_slug("Already_OK-123") == "already_ok-123"


def test_safe_slug_falls_back_for_empty_or_punctuation_only_values():
    assert safe_slug("") == "ack"
    assert safe_slug(" !!! ") == "ack"


def test_display_model_label_shortens_paths_without_losing_parent_context():
    assert display_model_label("/models/family/model-Q4.gguf") == "model-Q4.gguf — family"
    assert display_model_label("openai/gpt-4o-mini") == "gpt-4o-mini — openai"
    assert display_model_label("plain-model") == "plain-model"
    assert display_model_label("") == ""
