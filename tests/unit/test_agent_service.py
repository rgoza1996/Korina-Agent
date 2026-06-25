from __future__ import annotations

import pytest

from korina.services.agent_service import classify_agent_priority, sanitize_agent_report


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        (
            "Priority: critical\nPermission request: delete the staging cache?",
            "critical",
        ),
        (
            "Priority: critical\nWhisper output is garbled and transcript uncertainty is high.",
            "normal",
        ),
        (
            "Priority: low\nBookkeeping only.",
            "low",
        ),
        (
            "No priority line here, just ordinary state.",
            "normal",
        ),
        (
            "Permission request: run the migration that may modify files?",
            "critical",
        ),
    ],
)
def test_classify_agent_priority_core_cases(report: str, expected: str):
    assert classify_agent_priority(report) == expected


def test_sanitize_agent_report_strips_internal_blocks_and_interrupt_labels():
    raw = """
<think>private chain of thought</think>
Priority: normal
[Ack Phrase] Okay, got it.
Korina Agent Interrupt: do not say this line
Visible status line.
Ack Phrase another internal label
"""

    cleaned = sanitize_agent_report(raw)

    assert "think" not in cleaned.lower()
    assert "Ack Phrase" not in cleaned
    assert "Korina Agent Interrupt" not in cleaned
    assert "Visible status line." in cleaned


def test_sanitize_agent_report_handles_unclosed_think_block():
    assert sanitize_agent_report("Before\n<think>never closed\nAfter") == "Before"
