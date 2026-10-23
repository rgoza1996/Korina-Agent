"""The build/readiness contract must include every tracked browser module."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from korina.asset_manifest import BUNDLED_ASSETS, REQUIRED_FRONTEND_ASSETS

REPO = Path(__file__).resolve().parents[2]


def test_asset_contract_matches_source_modules_and_excludes_generated_state():
    source = REPO / 'Korina'
    modules = {path.relative_to(source).as_posix() for path in (source / 'js').glob('*.js')}
    assert modules == {name for name in REQUIRED_FRONTEND_ASSETS if name.startswith('js/')}
    assert set(REQUIRED_FRONTEND_ASSETS) - modules == {'index.html', 'styles.css'}
    assert set(BUNDLED_ASSETS) - set(REQUIRED_FRONTEND_ASSETS) == {
        'Ack/ack_phrases.json', 'config/config.example.json',
    }
    assert len(BUNDLED_ASSETS) == len(set(BUNDLED_ASSETS))
    for relative in BUNDLED_ASSETS:
        assert not Path(relative).is_absolute() and '..' not in Path(relative).parts
        assert (source / relative).is_file()


def test_asset_manifest_loads_without_runtime_or_site_dependencies(tmp_path):
    code = '''import runpy, sys
manifest = runpy.run_path(sys.argv[1])
assert manifest['BUNDLED_ASSETS']
assert not any(name == 'korina' or name.startswith('korina.') for name in sys.modules)
assert not any(name == 'fastapi' or name.startswith('fastapi.') for name in sys.modules)
'''
    result = subprocess.run([sys.executable, '-S', '-c', code, str(REPO / 'korina/asset_manifest.py')],
                            cwd=tmp_path, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
