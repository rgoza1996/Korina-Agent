from __future__ import annotations

from korina.services.response_llm import format_voice_reply


def test_format_voice_reply_splits_multi_sentence_single_line():
    text = "First sentence. Second question? Third exclamation! Final ellipsis…"

    assert format_voice_reply(text) == (
        "First sentence.\n"
        "Second question?\n"
        "Third exclamation!\n"
        "Final ellipsis…"
    )


def test_format_voice_reply_preserves_existing_newline_boundaries():
    text = "Line one has two sentences. Split it.\nAlready separate."

    assert format_voice_reply(text) == (
        "Line one has two sentences.\n"
        "Split it.\n"
        "Already separate."
    )


def test_format_voice_reply_empty_input_returns_empty():
    assert format_voice_reply("") == ""
    assert format_voice_reply("   \n  ") == ""
