from __future__ import annotations


def test_chat_missing_required_message_returns_422(client):
    response = client.post("/api/chat", json={"history": []})

    assert response.status_code == 422


def test_provider_activate_missing_provider_returns_422(client):
    response = client.post("/api/llm/provider/activate", json={"model": "remote"})

    assert response.status_code == 422


def test_transcribe_missing_file_returns_422(client):
    response = client.post("/api/transcribe")

    assert response.status_code == 422


def test_agent_state_report_rejects_invalid_transcript_type(client):
    response = client.post("/api/agent/state-report", json={"transcript": "not-a-list"})

    assert response.status_code == 422
