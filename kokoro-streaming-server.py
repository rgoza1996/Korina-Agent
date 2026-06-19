"""
Kokoro TTS Streaming Server — FastAPI + uvicorn

Endpoints:
  POST /stream/speech       — SSE + base64 PCM (one event per chunk)
  POST /stream/wav         — streaming WAV: pre-allocated header, chunks appended
                             as raw PCM. Browser plays progressively via MSE.
  POST /v1/audio/speech     — OpenAI-compatible buffered WAV
  GET  /voices              — list available voices
  GET  /health              — health check
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import asyncio
import base64
import io
import json
import os
import struct
import threading
import queue
import numpy as np
import soundfile as sf

app = FastAPI(title="Kokoro TTS Streaming Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pipelines = {}
last_device = None
SAMPLE_RATE = 24000
WAV_HEADER_SIZE = 44  # standard 24kHz 16-bit mono WAV header


def cuda_available():
    import torch
    return torch.cuda.is_available()


def normalize_device(requested=None):
    value = (requested or '').strip().lower()
    if value in ('gpu', 'cuda'):
        return 'cuda'
    if value == 'cpu':
        return 'cpu'
    return 'cuda' if cuda_available() else 'cpu'


def get_pipeline(device=None):
    global last_device
    resolved = normalize_device(device)
    if resolved not in pipelines:
        from kokoro import KPipeline
        print(f"[kokoro] Loading on device: {resolved}")
        pipelines[resolved] = KPipeline(lang_code="a", device=resolved)
        print(f"[kokoro] Pipeline loaded successfully on {resolved}")
    last_device = resolved
    return pipelines[resolved]


def to_int16(audio):
    """Convert torch tensor or numpy array to int16 numpy array."""
    import torch
    if isinstance(audio, torch.Tensor):
        audio = audio.detach().cpu().numpy()
    if audio.dtype != np.int16:
        audio = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    return audio


def patch_wav_header(data: bytes, num_samples: int) -> bytes:
    """Patch RIFF size and data size in a WAV header."""
    size = num_samples * 2  # 16-bit mono = 2 bytes/sample
    ba = bytearray(data)
    struct.pack_into("<I", ba, 4, 36 + size)   # RIFF chunk size
    struct.pack_into("<I", ba, 40, size)        # data chunk size
    return bytes(ba)


# ── SSE endpoint (base64 PCM) ───────────────────────────────────────────────

def sse_iterator(generator, device=None):
    for gs, ps, audio in generator:
        audio = to_int16(audio)
        pcm_b64 = base64.b64encode(audio.tobytes()).decode()
        payload = json.dumps({
            "gs": str(gs), "ps": str(ps),
            "sample_rate": SAMPLE_RATE,
            "device": device,
            "samples": len(audio),
            "audio": pcm_b64,
        })
        yield f"data: {payload}\n\n"


# ── WAV streaming endpoint ───────────────────────────────────────────────────

def write_wav_chunks(text, voice, speed, device=None):
    """
    Generator that yields WAV file content after each chunk.
    Strategy: pre-allocate file with dummy header, append PCM chunks,
    yield the full file content after each append (chunked transfer encoding).
    A background thread does the actual writing so we never block the async loop.
    """
    temp_path = "/tmp/kokoro_stream.wav"
    chunks_q = queue.Queue()
    stop_event = threading.Event()

    def writer():
        # Pre-allocate with dummy header
        with open(temp_path, "wb") as f:
            f.write(b'RIFF')
            f.write(struct.pack("<I", 0))          # placeholder file size
            f.write(b'WAVE')
            f.write(b'fmt ')
            f.write(struct.pack("<I", 16))         # fmt chunk size
            f.write(struct.pack("<HHIIHH",
                                1, 1, SAMPLE_RATE,  # PCM, mono
                                SAMPLE_RATE * 2,     # byte rate
                                2, 16))              # block align, 16-bit
            f.write(b'data')
            f.write(struct.pack("<I", 0))           # placeholder data size

        total_samples = 0
        pipe = get_pipeline(device)

        try:
            for gs, ps, audio in pipe(text, voice=voice, speed=speed):
                if stop_event.is_set():
                    break
                audio = to_int16(audio)
                if len(audio) == 0:
                    continue

                # Append PCM
                with open(temp_path, "r+b") as f:
                    f.seek(0, 2)  # seek to end
                    f.write(audio.tobytes())

                total_samples += len(audio)

                # Read entire file and yield it
                with open(temp_path, "rb") as f:
                    raw = f.read()

                # Patch header with current sizes
                patched = patch_wav_header(raw, total_samples)
                chunks_q.put(patched)

        except Exception as e:
            print(f"[kokoro] writer error: {e}")
            chunks_q.put(None)
        finally:
            chunks_q.put(None)  # sentinel: end of stream

    t = threading.Thread(target=writer, daemon=True)
    t.start()

    while True:
        chunk = chunks_q.get()
        if chunk is None:
            break
        yield chunk


# ── SSE endpoint ──────────────────────────────────────────────────────────────

@app.post("/stream/speech")
def stream_speech(request: dict):
    try:
        text  = request.get("input", request.get("text", ""))
        voice = request.get("voice", "af_heart")
        speed = float(request.get("speed", 1.0))
        device = normalize_device(request.get("device"))
        if not text:
            raise HTTPException(status_code=400, detail="No text provided")

        pipe = get_pipeline(device)
        generator = pipe(text, voice=voice, speed=speed)
        return StreamingResponse(
            sse_iterator(generator, device),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
        )
    except HTTPException:
        raise
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ── WAV streaming endpoint ───────────────────────────────────────────────────

@app.post("/stream/wav")
def stream_wav(request: dict):
    """
    Progressive WAV streaming.
    Pre-allocates a WAV with dummy header, appends PCM chunks as they arrive,
    and yields the entire (growing) file after each chunk.
    The client uses Media Source Extensions to play chunks as they arrive.
    """
    try:
        text  = request.get("input", request.get("text", ""))
        voice = request.get("voice", "af_heart")
        speed = float(request.get("speed", 1.0))
        device = normalize_device(request.get("device"))
        if not text:
            raise HTTPException(status_code=400, detail="No text provided")

        return StreamingResponse(
            write_wav_chunks(text, voice, speed, device),
            media_type="audio/wav",
            headers={
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Transfer-Encoding": "chunked",
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ── Buffered WAV endpoint ─────────────────────────────────────────────────────

@app.post("/v1/audio/speech")
def openai_speech(request: dict):
    try:
        text  = request.get("input", request.get("text", ""))
        voice = request.get("voice", "af_heart")
        speed = float(request.get("speed", 1.0))
        device = normalize_device(request.get("device"))
        if not text:
            raise HTTPException(status_code=400, detail="No text provided")

        pipe = get_pipeline(device)
        segments = list(pipe(text, voice=voice, speed=speed))
        audio_segments = [to_int16(s[2]) for s in segments]
        audio_segments = [a for a in audio_segments if len(a) > 0]
        if not audio_segments:
            raise HTTPException(status_code=500, detail="No audio generated")

        full_audio = np.concatenate(audio_segments)
        buffer = io.BytesIO()
        sf.write(buffer, full_audio, SAMPLE_RATE, format="WAV")
        buffer.seek(0)
        return StreamingResponse(buffer, media_type="audio/wav")

    except HTTPException:
        raise
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok", "loaded": bool(pipelines), "loaded_devices": sorted(pipelines.keys()), "device": last_device, "cuda_available": cuda_available()}


@app.get("/voices")
def voices():
    return {
        "voices": [
            "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
            "am_adam", "am_michael", "bf_emma", "bf_george",
        ]
    }


if __name__ == "__main__":
    import uvicorn
    print("Starting Kokoro TTS Streaming Server on port 8880")
    uvicorn.run(app, host="0.0.0.0", port=8880, reload=False)
