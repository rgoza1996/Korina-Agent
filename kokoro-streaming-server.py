"""Compatibility entrypoint for the optional Kokoro speech server (:8880)."""
from korina.tts.server import app as app, main

if __name__ == '__main__':
    main()
