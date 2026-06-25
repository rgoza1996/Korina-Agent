from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KORINA_DIR = REPO_ROOT / "Korina"
JS_DIR = KORINA_DIR / "js"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_index_uses_module_entrypoint_and_external_stylesheet():
    html = read(KORINA_DIR / "index.html")

    assert '<link rel="stylesheet" href="./styles.css">' in html
    assert '<script type="module" src="./js/app.js"></script>' in html
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>[\s\S]*?</script>", html, re.I)


def test_required_frontend_modules_exist():
    required = {
        "app.js",
        "state.js",
        "providers-ui.js",
        "capability-filter.js",
        "settings-ui.js",
        "barge-in.js",
        "live.js",
        "vad.js",
    }

    missing = [name for name in required if not (JS_DIR / name).is_file()]

    assert missing == []
    assert (KORINA_DIR / "styles.css").is_file()


def test_local_module_imports_reference_existing_files():
    missing: list[str] = []
    import_pattern = re.compile(r"(?:import[\s\S]*?from\s+|import\s*)['\"]\./([^'\"]+)['\"]")

    for js_file in JS_DIR.glob("*.js"):
        for target in import_pattern.findall(read(js_file)):
            if target.startswith("http"):
                continue
            if not (JS_DIR / target).is_file():
                missing.append(f"{js_file.name} -> {target}")

    assert missing == []


def test_fragile_ui_marker_ids_exist_in_served_source():
    html = read(KORINA_DIR / "index.html")

    for marker in [
        'id="lmModel"',
        'id="sttLlmModel"',
        'id="agentModel"',
        'id="sttReasoningHint"',
        'id="llmReasoningHint"',
        'id="sttCapabilityFilterOverride"',
        'id="clearProbeBtn"',
        'id="partialWindowMs"',
        'id="minSpeechMs"',
    ]:
        assert marker in html


def test_barge_in_uses_adaptive_vad_thresholds_not_undefined_constants():
    barge = read(JS_DIR / "barge-in.js")

    assert "state.vadSpeechThreshold" in barge
    assert "state.vadSilenceThreshold" in barge
    assert "SPEECH_THRESHOLD" not in barge
    assert "SILENCE_THRESHOLD" not in barge


def test_model_dropdown_does_not_refresh_on_focus_or_pointerdown():
    combined = "\n".join(read(path) for path in [KORINA_DIR / "index.html", *JS_DIR.glob("*.js")])

    assert "pointerdown" not in combined
    assert "onpointerdown" not in combined
    assert "addEventListener('focus" not in combined
    assert 'addEventListener("focus' not in combined
    assert "onfocus" not in combined
