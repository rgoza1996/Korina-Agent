"""Contracts for the per-utterance STT backend pill.

The pill (#sttBackendHealth + #sttBackendDot) is updated by api.js's
`recordSttBackend(payload, errorMsg)` helper, which is invoked from
recorder.js at every code path that completes or fails an STT
request. This test enforces:

1. The pill exists in index.html.
2. api.js exports `recordSttBackend` and reads the #sttBackendHealth /
   #sttBackendDot elements.
3. recorder.js calls `recordSttBackend` on:
   - SSE 'done' (success)
   - SSE 'error' (failure)
   - HTTP fetch rejection (network error)
   - HTTP !r.ok (4xx/5xx)
   - /api/transcribe/partial success
   - /api/transcribe/partial !r.ok (4xx/5xx)
   - /api/transcribe/partial fetch rejection
4. The pill labels the four expected backend values.
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


def test_recorder_wires_sse_done():
    src = RECORDER_JS.read_text()
    # The SSE 'done' branch in transcribeBlob must call recordSttBackend.
    done_block = src[src.find("ev === 'done'"):src.find("ev === 'error'")]
    assert 'recordSttBackend(payload, undefined)' in done_block, \
        "SSE 'done' branch must call recordSttBackend(payload, undefined)"


def test_recorder_wires_sse_error():
    src = RECORDER_JS.read_text()
    # The SSE 'error' branch must call recordSttBackend(null, message)
    # before throwing.
    error_block = src[src.find("ev === 'error'"):src.find("if (!final) throw")]
    assert 'recordSttBackend(null' in error_block, \
        "SSE 'error' branch must call recordSttBackend(null, ...)"
    assert 'throw new Error(payload.detail' in error_block, \
        "SSE 'error' branch must still throw after recording"


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


def test_recorder_wires_partial_success_and_error():
    src = RECORDER_JS.read_text()
    partial_start = src.find('export async function transcribePartialBlob')
    partial_body = src[partial_start:]
    # Success path - the function calls recordSttBackend after !r.ok.
    assert 'recordSttBackend({' in partial_body, \
        "transcribePartialBlob must call recordSttBackend on success"
    # !r.ok path - the function calls recordSttBackend(null, ...) before throw.
    assert '!r.ok' in partial_body, "transcribePartialBlob still has !r.ok check"
    # The order matters: !r.ok records then throws.
    ok_idx = partial_body.find('!r.ok')
    throw_idx = partial_body.find('throw new Error(j.detail', ok_idx)
    record_idx = partial_body.find('recordSttBackend(null', ok_idx, throw_idx)
    assert record_idx != -1 and record_idx < throw_idx, \
        "recordSttBackend(null, ...) must be called BEFORE throw in the !r.ok branch"