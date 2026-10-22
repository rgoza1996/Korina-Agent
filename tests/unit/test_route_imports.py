"""A single route can load without importing unrelated audio/provider stacks."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


REPO = Path(__file__).resolve().parents[2]


def isolated_import(source, tmp_path):
    return subprocess.run([sys.executable, '-c', source], cwd=tmp_path,
                          env={**os.environ, 'PYTHONPATH': str(REPO)},
                          capture_output=True, text=True, timeout=10)


def test_chat_route_import_does_not_require_audio_or_load_other_routes(tmp_path):
    result = isolated_import('''
import importlib.abc
import sys

class WithoutAudio(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'numpy', 'soundfile', 'faster_whisper', 'torch'}:
            raise AssertionError('Chat imported unrelated audio dependency: ' + fullname)

sys.meta_path.insert(0, WithoutAudio())
import korina.routes
assert not any(name.startswith('korina.routes.') for name in sys.modules)
from korina.routes import chat
assert any(route.path == '/api/chat' for route in chat.router.routes)
assert [name for name in sys.modules if name.startswith('korina.routes.')] == ['korina.routes.chat']
''', tmp_path)
    assert result.returncode == 0, result.stderr


def test_existing_public_route_exports_still_load_and_unknown_names_fail(tmp_path):
    result = isolated_import('''
import importlib
import korina.routes
namespace = {}
exec('from korina.routes import *', namespace)
for name in korina.routes.__all__:
    assert namespace[name] is importlib.import_module('korina.routes.' + name)
    assert namespace[name].router.routes
try:
    korina.routes.no_such_route
except AttributeError:
    pass
else:
    raise AssertionError('Unknown route did not raise AttributeError')
''', tmp_path)
    assert result.returncode == 0, result.stderr
