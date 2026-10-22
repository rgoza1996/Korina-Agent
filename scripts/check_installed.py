"""Verify an installed wheel outside its checkout, using a disposable data tree.

Run with the wheel environment's Python.
The optional source-assets directory verifies that every shipped UI file is
identical to the checkout that produced the wheel.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-assets', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='korina-installed-') as directory:
        os.environ.pop('KORINA_APP_DIR', None)
        os.environ['XDG_DATA_HOME'] = directory

        # Import only after selecting disposable state, so no user config or
        # ACK cache is read or changed by this verification process.
        from fastapi.testclient import TestClient
        from korina.asset_manifest import BUNDLED_ASSETS
        from korina.app_factory import create_app
        from korina.config import load_config, update_config_values
        from korina.util.paths import APP_DIR, CONFIG_PATH, ACK_DIR

        assert APP_DIR.name == '_assets', f'Expected wheel assets, got {APP_DIR}'
        assert CONFIG_PATH.parent == Path(directory) / 'korina'
        assert not (APP_DIR / 'config.json').exists()
        assert not list(APP_DIR.rglob('*.wav')), 'Wheel contains generated audio'

        assets = BUNDLED_ASSETS
        for asset in assets:
            assert (APP_DIR / asset).is_file(), f'Missing wheel asset: {asset}'
        expected_modules = sorted(Path(asset).name for asset in assets if asset.startswith('js/'))
        assert sorted(path.name for path in (APP_DIR / 'js').glob('*.js')) == expected_modules
        if args.source_assets:
            expected = sorted(path.name for path in (args.source_assets / 'js').glob('*.js'))
            assert expected_modules == expected
            for asset in assets:
                assert (APP_DIR / asset).read_bytes() == (args.source_assets / asset).read_bytes(), asset

        update_config_values({'ack_enabled': 'off', 'agent_enabled': 'off'})
        with TestClient(create_app()) as client:
            for url in ['/', '/styles.css', *['/js/' + Path(asset).name for asset in assets if asset.startswith('js/')]]:
                response = client.get(url)
                assert response.status_code == 200, (url, response.status_code)
            assert client.get('/api/agent/capabilities').json()['protocol_version'] == 1
            before = CONFIG_PATH.read_bytes()
            assert client.post('/api/config', json={'speed': -1}).status_code == 422
            assert CONFIG_PATH.read_bytes() == before, 'Rejected settings changed the config'
            response = client.post('/api/config', json={'voice': 'af_bella'})
            assert response.status_code == 200, response.text
            assert client.get('/api/config').json()['voice'] == 'af_bella'
        assert load_config()['voice'] == 'af_bella'
        assert ACK_DIR.is_dir()
        assert not (APP_DIR / 'config.json').exists()
        print(json.dumps({'ok': True, 'assets_verified': len(assets), 'writable_state_outside_package': True}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
