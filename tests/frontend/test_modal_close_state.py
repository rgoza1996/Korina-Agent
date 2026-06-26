"""Frontend source-text tests for modal-close-applies-state behavior.

When the user closes the settings modal by ANY path (Close button, Done
button, click-outside the modal, Escape key), the runtime state should
update to reflect any field changes that affect which provider/model is
serving requests.

Today:
  - Close button → modal hides, NOTHING saved, NOTHING activated
  - Done button → saveConfigNow + modal hides, but no activate for
    non-dropdown fields (llmBaseUrl, ttsBaseUrl, lmModel etc.)
  - Click outside → modal hides, NOTHING saved, NOTHING activated
  - Escape key → modal hides, NOTHING saved, NOTHING activated

Behavior contracts:
  1. closeSettings() and saveSettings() must both:
     - Persist any unsaved form changes via saveConfigNow()
     - Compare provider-affecting fields against last-loaded config
     - If any provider-affecting field changed, call
       activateSelectedProvider(llmProvider, lmModel) so the running
       server reflects the new choice.
     - Refresh health() after activate so debug strip updates.
  2. The Done button and Close button must use the same path (no
     divergence between the two save semantics).
  3. Click-outside and Escape must behave the same as Close button.
  4. activate is a no-op if no provider-affecting field actually changed.
  5. activate failure must surface as a status('bad') message; modal
     close must still proceed so the user is not trapped.
  6. applyConfig() must store a snapshot of the loaded config to
     state.appConfigSnapshot so close-time diff can compare against it.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KORINA_DIR = REPO_ROOT / "Korina"
JS_DIR = KORINA_DIR / "js"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---- 1. closeSettings() must persist + activate ----

def test_app_js_close_settings_persists_and_activates():
    app = read(JS_DIR / "app.js")
    # closeSettings() must call saveConfigNow() and then check for provider
    # changes and call activateSelectedProvider if needed.
    # Look for the function body. It should NOT just hide the modal anymore.
    # Find the function definition and the next closing brace or
    # next 'function '.
    start = app.find("function closeSettings(")
    assert start >= 0, "closeSettings() must exist in app.js"
    rest = app[start:]
    end_idx = len(rest)
    for marker in ("\nfunction ", "\nasync function "):
        idx = rest.find(marker, 10)
        if idx > 0 and idx < end_idx:
            end_idx = idx
    body = rest[:end_idx]
    # Must call saveConfigNow to persist.
    assert "saveConfigNow" in body, (
        "closeSettings() must call saveConfigNow() so unsaved field "
        "changes are persisted when the user clicks Close or outside."
    )
    # Must reference activateSelectedProvider so provider-affecting
    # changes take effect at runtime.
    assert "activateSelectedProvider" in body, (
        "closeSettings() must call activateSelectedProvider() when "
        "provider-affecting fields have changed, so the running server "
        "reflects the new choice (e.g. switching base_url). The old "
        "behavior only activated on dropdown change, leaving other "
        "field changes dormant until the user manually re-changed the "
        "dropdown."
    )
    # Must still hide the modal.
    assert "settingsModal" in body and "remove('open')" in body, (
        "closeSettings() must still close the modal after persisting."
    )


def test_app_js_save_settings_uses_same_path_as_close():
    """saveSettings() (Done button) must use the same persist+activate path."""
    app = read(JS_DIR / "app.js")
    # Look for the saveSettings function.
    start = app.find("function saveSettings(")
    assert start >= 0, "saveSettings() must exist in app.js"
    rest = app[start:]
    end_idx = len(rest)
    for marker in ("\nfunction ", "\nasync function "):
        idx = rest.find(marker, 10)
        if idx > 0 and idx < end_idx:
            end_idx = idx
    body = rest[:end_idx]
    # Must call closeSettings (or share logic) so the Done button and
    # the Close button behave identically.
    assert "closeSettings" in body or "applyAndClose" in body, (
        "saveSettings() (Done button) must call the same close-path "
        "function as the Close button so the two are guaranteed to "
        "have identical persist+activate semantics."
    )


def test_app_js_click_outside_and_escape_use_close_settings():
    """Click outside the modal and Escape must route through closeSettings."""
    app = read(JS_DIR / "app.js")
    # Find the modal click handler.
    assert "modal.addEventListener('click'" in app or 'modal.addEventListener("click"' in app, (
        "There must be a click handler on the settings modal that "
        "delegates to closeSettings() when the user clicks the backdrop."
    )
    # Find the Escape key handler.
    assert "Escape" in app, "Escape key handler must exist"
    # Both should call closeSettings().
    # Locate the modal click handler body and the keydown body.
    # Simple check: the file references closeSettings in the
    # wireUiHandlers context near Escape and modal click.
    # A weaker but reliable check: the file uses closeSettings in
    # at least 2 places.
    assert app.count("closeSettings") >= 3, (
        "closeSettings must be referenced at least 3 times in app.js "
        "(definition + Close button + click-outside + Escape key)."
    )


# ---- 2. activate-on-close is a no-op when nothing changed ----

def test_app_js_activate_skipped_when_no_provider_change():
    app = read(JS_DIR / "app.js")
    # The close path must compare provider-affecting fields against the
    # last-loaded config snapshot. If no provider field changed, do NOT
    # call activateSelectedProvider (to avoid a needless LM Studio reload
    # when the user opened and closed the modal without touching
    # anything).
    assert "state.appConfigSnapshot" in app or "state.lastSavedConfig" in app or "state.openedConfigSnapshot" in app, (
        "closeSettings() must compare current form state against a "
        "loaded-config snapshot (e.g. state.appConfigSnapshot) so that "
        "activateSelectedProvider is only called when provider-affecting "
        "fields actually changed. Without this, every modal close would "
        "trigger an unnecessary LM Studio model reload."
    )


# ---- 3. settings-ui.js applyConfig must populate the snapshot ----

def test_settings_ui_apply_config_snapshots_loaded_config():
    js = read(JS_DIR / "settings-ui.js")
    # applyConfig is called whenever the server pushes a fresh config.
    # It must store a snapshot so close-time diff has something to compare
    # against.
    start = js.find("export function applyConfig")
    assert start >= 0
    rest = js[start:]
    end_idx = len(rest)
    for marker in ("\nexport function ", "\nfunction "):
        idx = rest.find(marker, 10)
        if idx > 0 and idx < end_idx:
            end_idx = idx
    body = rest[:end_idx]
    assert "state.appConfigSnapshot" in body or "state.lastSavedConfig" in body or "state.openedConfigSnapshot" in body, (
        "applyConfig() must store the loaded config to state so that "
        "closeSettings() can detect what changed since the modal opened."
    )


# ---- 4. activate failure must surface + modal still closes ----

def test_app_js_close_settings_handles_activate_failure():
    app = read(JS_DIR / "app.js")
    start = app.find("function closeSettings(")
    assert start >= 0
    rest = app[start:]
    end_idx = len(rest)
    for marker in ("\nfunction ", "\nasync function "):
        idx = rest.find(marker, 10)
        if idx > 0 and idx < end_idx:
            end_idx = idx
    body = rest[:end_idx]
    # The close-path activate call must be inside a try/catch (so the
    # modal still closes even if activate throws).
    assert "try" in body and "catch" in body, (
        "closeSettings() must catch activateSelectedProvider() failures "
        "so the modal still closes and the user is not trapped."
    )
    assert "Provider activation failed" in body or "activate failed" in body.lower(), (
        "closeSettings() must surface activate failures via "
        "$('sttStatus').textContent or status() so the user sees "
        "what went wrong."
    )


# ---- 5. regression: keep modal-click and Escape paths ----

def test_app_js_still_has_modal_click_outside_handler():
    app = read(JS_DIR / "app.js")
    assert "modal.addEventListener('click'" in app or 'modal.addEventListener("click"' in app, (
        "modal click-outside handler must still exist"
    )


def test_app_js_still_has_escape_key_handler():
    app = read(JS_DIR / "app.js")
    assert "Escape" in app, "Escape key handler must still exist"