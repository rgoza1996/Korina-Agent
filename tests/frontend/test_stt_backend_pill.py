"""Markup, backend-label, and fetch-rejection wiring for the STT backend pill.

Executable SSE success/failure behavior lives in tests/js/test_transport.mjs.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INDEX = REPO / "Korina" / "index.html"
API_JS = REPO / "Korina" / "js" / "api.js"
RECORDER_JS = REPO / "Korina" / "js" / "recorder.js"


def test_index_has_stt_backend_pill():
    src = INDEX.read_text()
    assert 'id="sttBackendDot"' in src, "missing #sttBackendDot in index.html"
    assert 'id="sttBackendHealth"' in src, "missing #sttBackendHealth in index.html"
    # Should live in the debug-strip row, alongside the other pills.
    debug_strip = src[src.find('pageHealth'):src.find('hostInfo')]
    assert 'sttBackendHealth' in debug_strip, \
        "#sttBackendHealth should be in the debug-strip row (between providerReady and hostInfo)"


def test_api_js_exports_record_stt_backend():
    src = API_JS.read_text()
    assert 'export function recordSttBackend' in src, \
        "api.js must export recordSttBackend(payload, errorMsg)"
    # Must read both elements.
    assert "$('sttBackendHealth')" in src, \
        "recordSttBackend must read #sttBackendHealth"
    assert "$('sttBackendDot')" in src, \
        "recordSttBackend must read #sttBackendDot"
    assert "setDot('sttBackendDot'" in src, \
        "recordSttBackend must call setDot on #sttBackendDot"


def test_api_js_labels_four_backends():
    src = API_JS.read_text()
    # The function should distinguish all four backend values we expect
    # to see from /api/transcribe/stream and /api/transcribe/partial.
    for backend in ('multimodal-stt', 'faster-whisper', 'whisper-fallback', 'whisper-llm'):
        assert f"'{backend}'" in src, \
            f"recordSttBackend must handle backend={backend!r}"


def test_recorder_wires_fetch_rejection_stream():
    src = RECORDER_JS.read_text()
    # transcribeBlob must wrap the fetch in try/catch and record on
    # rejection.
    blob_start = src.find('export async function transcribeBlob')
    blob_end = src.find('export async function transcribePartialBlob')
    blob_body = src[blob_start:blob_end]
    assert 'try {' in blob_body, "transcribeBlob must wrap fetch in try/catch"
    assert 'catch (e)' in blob_body, "transcribeBlob must catch fetch errors"
    assert 'recordSttBackend(null' in blob_body, \
        "transcribeBlob must call recordSttBackend on fetch rejection"
