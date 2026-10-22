"""Setuptools build hook for the UI shipped beside the Python source tree."""
from __future__ import annotations

import shutil
from pathlib import Path
from runpy import run_path

from setuptools.command.build_py import build_py


class BuildPy(build_py):
    """Bundle the tracked UI without moving it or including runtime secrets."""

    def run(self) -> None:
        super().run()
        source = Path(__file__).parent / 'Korina'
        destination = Path(self.build_lib) / 'korina' / '_assets'
        if destination.exists():
            shutil.rmtree(destination)
        # Load only the constant module: build isolation has no app dependencies.
        assets = run_path(str(Path(__file__).parent / 'korina' / 'asset_manifest.py'))['BUNDLED_ASSETS']
        for relative in assets:
            asset = source / relative
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(asset, target)
