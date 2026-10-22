"""Setuptools build hook for the UI shipped beside the Python source tree."""
from __future__ import annotations

import shutil
from pathlib import Path

from setuptools.command.build_py import build_py


class BuildPy(build_py):
    """Bundle the tracked UI without moving it or including runtime secrets."""

    def run(self) -> None:
        super().run()
        source = Path(__file__).parent / 'Korina'
        destination = Path(self.build_lib) / 'korina' / '_assets'
        if destination.exists():
            shutil.rmtree(destination)
        files = [
            source / 'index.html',
            source / 'styles.css',
            source / 'Ack' / 'ack_phrases.json',
            source / 'config' / 'config.example.json',
            *sorted((source / 'js').glob('*.js')),
        ]
        for asset in files:
            target = destination / asset.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(asset, target)
