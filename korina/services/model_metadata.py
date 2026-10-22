"""Request-scoped filesystem estimates for the model discovery display."""
from __future__ import annotations

import os
from pathlib import Path

MAX_METADATA_ENTRIES = 10_000


class ModelMetadataScan:
    """Check each parent once and share a finite entry budget across parents.

    This is a display heuristic. Provider activation keeps its independent
    selected-file/mmproj lookup and never consults this cache or budget.
    """

    def __init__(self, max_entries: int = MAX_METADATA_ENTRIES):
        self._parents: dict[Path, bool] = {}
        self.incomplete_models: set[str] = set()
        self.info: dict = {'max_entries': max_entries, 'visited_entries': 0,
                     'directories_scanned': 0, 'truncated': False,
                     'incomplete': False, 'errors': []}
        self._incomplete_parents: set[Path] = set()

    def has_mmproj(self, model_id: str) -> bool:
        path = Path(model_id)
        parent = path.parent
        try:
            # A nonexistent endpoint ID cannot borrow a real sibling's
            # capability just because its parent has already been scanned.
            if not path.is_file():
                return False
            if parent in self._parents:
                if parent in self._incomplete_parents:
                    self.incomplete_models.add(model_id)
                return self._parents[parent]
            if self.info['visited_entries'] >= self.info['max_entries']:
                self.info['truncated'] = True
                self._incomplete(parent, model_id)
                self._parents[parent] = False
                return False
            self.info['directories_scanned'] += 1
            with os.scandir(parent) as entries:
                while self.info['visited_entries'] < self.info['max_entries']:
                    entry = next(entries, None)
                    if entry is None:
                        break
                    self.info['visited_entries'] += 1
                    name = entry.name.lower()
                    if 'mmproj' in name and name.endswith('.gguf') and entry.is_file():
                        self._parents[parent] = True
                        return True
                else:
                    self.info['truncated'] = True
                    self._incomplete(parent, model_id)
        except (OSError, ValueError) as exc:
            if len(self.info['errors']) < 8:
                self.info['errors'].append(f'Model metadata unavailable ({type(exc).__name__})')
            self._incomplete(parent, model_id)
        self._parents[parent] = False
        return False

    def _incomplete(self, parent: Path, model_id: str) -> None:
        self.info['incomplete'] = True
        self._incomplete_parents.add(parent)
        self.incomplete_models.add(model_id)
