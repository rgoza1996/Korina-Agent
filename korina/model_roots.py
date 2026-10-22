"""Shared limits and validation for explicitly configured model directories."""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, StringConstraints

MAX_MODEL_ROOTS = 128
MAX_MODEL_ROOT_PATH_CHARS = 4096
ModelRootPath = Annotated[str, StringConstraints(
    strict=True, strip_whitespace=True, min_length=1,
    max_length=MAX_MODEL_ROOT_PATH_CHARS, pattern=r'^[^\x00]*$',
)]
ModelRootList = Annotated[list[ModelRootPath], Field(max_length=MAX_MODEL_ROOTS)]


def validate_model_root_paths(roots: list[str]) -> list[str]:
    """Validate new paths before any config mutation, including OS failures."""
    for root in roots:
        try:
            path = Path(root)
            if not path.exists():
                raise ValueError(f'path not found: {root}')
            if not path.is_dir():
                raise ValueError(f'not a directory: {root}')
        except OSError as exc:
            raise ValueError(f'invalid root path: {exc.strerror or type(exc).__name__}') from exc
    return roots
