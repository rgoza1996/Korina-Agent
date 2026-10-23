"""Canonical relative asset names, safe to load during an isolated build.

Custom runtime trees may change file content, but must provide every frontend
file. The ACK manifest and settings example are bundled separately because
neither is required to serve the browser application.
"""

REQUIRED_FRONTEND_ASSETS = (
    'index.html',
    'styles.css',
    'js/acks.js',
    'js/agent-ui.js',
    'js/api-errors.js',
    'js/api.js',
    'js/app.js',
    'js/audio-probes.js',
    'js/barge-in.js',
    'js/bounded-recorder.js',
    'js/capability-filter.js',
    'js/channel-ui.js',
    'js/dom.js',
    'js/endpoints.js',
    'js/history.js',
    'js/labels.js',
    'js/live.js',
    'js/local-models.js',
    'js/model-catalog.js',
    'js/partial-queue.js',
    'js/providers-ui.js',
    'js/recorder.js',
    'js/response-body.js',
    'js/settings-ui.js',
    'js/speech.js',
    'js/sse.js',
    'js/state.js',
    'js/vad.js',
)

BUNDLED_ASSETS = (
    *REQUIRED_FRONTEND_ASSETS,
    'Ack/ack_phrases.json',
    'config/config.example.json',
)
