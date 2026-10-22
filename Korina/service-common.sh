#!/usr/bin/env bash
# Shared by the optional source-checkout service wrappers.

korina_select_python() {
  local setting="$1" override="$2" preferred="$3" candidate normalized
  if [[ -n "$override" ]]; then
    candidate="$override"
    if [[ "$candidate" != */* ]]; then
      candidate="$(command -v "$candidate" || true)"
    fi
  elif [[ -f "$preferred" && -x "$preferred" ]]; then
    candidate="$preferred"
  else
    candidate="$(command -v python3 || true)"
  fi
  if [[ -z "$candidate" || ! -f "$candidate" || ! -x "$candidate" ]]; then
    printf 'ERROR: %s must name an executable Python interpreter: %s\n' "$setting" "${override:-$preferred}" >&2
    return 1
  fi
  if ! normalized="$("$candidate" -c 'import os, sys
path = os.path.abspath(sys.executable)
if any(ord(c) < 32 for c in path):
    raise ValueError("Interpreter paths cannot contain control characters")
print(path)')"; then
    printf 'ERROR: %s could not run as a Python interpreter: %s\n' "$setting" "$candidate" >&2
    return 1
  fi
  # abspath retains the virtual environment's executable symlink. Resolving
  # it to the base Python would select a different package environment.
  if [[ ! -f "$normalized" || ! -x "$normalized" ]]; then
    printf 'ERROR: %s returned an invalid interpreter path\n' "$setting" >&2
    return 1
  fi
  printf '%s\n' "$normalized"
}

korina_normalize_app_dir() {
  # Resolve relative input against the caller before any wrapper changes cwd.
  # absolute() retains symlinks and '..' traversal through them; resolve() and
  # normpath() could select a different runtime tree.
  "$1" - "$2" <<'PYTHON'
from pathlib import Path
import sys

path = str(Path(sys.argv[1]).expanduser().absolute())
if any(ord(char) < 32 for char in path):
    raise ValueError('KORINA_APP_DIR cannot contain control characters')
print(path)
PYTHON
}
