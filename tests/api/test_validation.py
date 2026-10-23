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


def test_request_bounds_reject_oversized_or_malformed_inputs(client):
    cases = [
        ('/api/chat', {'message': 'x' * 8193}),
        ('/api/chat', {'message': 'hello', 'history': [{'role': 'user', 'content': 'x'}] * 65}),
        ('/api/chat', {'message': 'hello', 'history': ['not-a-turn']}),
        ('/api/chat', {'message': 'hello', 'history': [{'role': 'user', 'content': 'x' * 8193}]}),
        ('/api/chat', {'message': 'hello', 'max_tokens': 0}),
        ('/api/chat', {'message': 'hello', 'temperature': 100}),
        ('/api/agent/transcript', {'transcript': [{'role': 'user', 'content': 'x'}] * 65}),
        ('/api/agent/transcript', {'transcript': [{'role': 'user'}]}),
        ('/api/agent/transcript', {'delivery_mode': 'typo'}),
        ('/api/agent/permission-answer', {'answer': 'x' * 513}),
        ('/api/agent/state-report', {'previous_report': 'x' * 8193}),
    ]
    for endpoint, body in cases:
        response = client.post(endpoint, json=body)
        assert response.status_code == 422, (endpoint, response.text)


def test_transcript_accepts_browser_wire_shape(client):
    response = client.post('/api/agent/transcript', json={
        'transcript': [{'role': 'user', 'content': 'Hello'}, {'role': 'assistant', 'content': 'Hi'}],
        'delivery_mode': 'prompt', 'reason': 'turn complete', 'turn_count': 1,
    })
    assert response.status_code == 200


def test_agent_state_report_does_not_block_status_route(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from korina.routes import agent

    started = threading.Event()
    release = threading.Event()

    def slow_report(req, *, config):
        started.set()
        assert release.wait(3)
        return 'Priority: normal\nReady.'

    monkeypatch.setattr(agent, 'load_config', lambda: {'agent_enabled': 'on'})
    monkeypatch.setattr(agent, 'generate_agent_state_report', slow_report)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = executor.submit(client.post, '/api/agent/state-report', json={'transcript': []})
        assert started.wait(2)
        try:
            status = executor.submit(client.get, '/api/agent/status').result(timeout=2)
            assert status.status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=2).status_code == 200


def test_background_transcript_keeps_status_and_events_responsive(client, config_module, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    from korina.agents import gateway
    from korina.agents.korina_adapter import KorinaAgentAdapter
    from korina.services import agent_service

    agent_service.shutdown_agent_worker()
    monkeypatch.setattr(gateway, '_active', KorinaAgentAdapter())
    config_module.update_config_values({'agent_enabled': 'on'})
    entered, release = threading.Event(), threading.Event()

    def held_report(req, **kwargs):
        entered.set()
        assert release.wait(5), 'test must release the background report worker'
        return 'Priority: normal\nThe background report completed.'

    monkeypatch.setattr(agent_service, 'generate_agent_state_report', held_report)
    try:
        with ThreadPoolExecutor(max_workers=2) as requests:
            submission = requests.submit(client.post, '/api/agent/transcript', json={
                'transcript': [{'role': 'user', 'content': 'Remember the appointment.'}],
                'delivery_mode': 'prompt',
            }).result(timeout=2)
            assert submission.status_code == 200 and submission.json()['accepted'] is True
            assert entered.wait(2), 'the real built-in worker must be running'
            polls = [requests.submit(client.get, path) for path in (
                '/api/agent/status', '/api/agent/events?after=0',
            )]
            status, events = [pending.result(timeout=2) for pending in polls]
            assert not release.is_set(), 'both polls must finish before model completion'
            assert status.status_code == events.status_code == 200
            assert status.json()['busy'] is True and events.json()['busy'] is True
            assert any(event.get('status') == 'working' for event in events.json()['events'])
    finally:
        release.set()
        worker = agent_service._executor
        agent_service.shutdown_agent_worker()
        if worker is not None:
            worker.shutdown(wait=True, cancel_futures=True)
