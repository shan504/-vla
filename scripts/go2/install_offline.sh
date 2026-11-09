#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/go2/install_offline.sh /path/to/bundle" >&2
  exit 2
fi
# Resolve the bundle before changing directories so relative arguments remain useful.
bundle="$(cd "$1" && pwd -P)"
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
project_root="$(pwd -P)"
unset PYTHONPATH PYTHONHOME
export PYTHONNOUSERSITE=1
export UV_CACHE_DIR="$project_root/.tools/uv-cache"
export UV_PYTHON_INSTALL_DIR="$project_root/.tools/python"
export TMPDIR="$project_root/.tools/tmp"
export UV_OFFLINE=1
export UV_PYTHON_DOWNLOADS=never
export UV_LINK_MODE=copy

if [[ -x .tools/uv/bin/uv ]]; then
  uv_bin="$project_root/.tools/uv/bin/uv"
elif command -v uv >/dev/null 2>&1; then
  uv_bin="$(command -v uv)"
else
  echo "Offline installation requires an existing uv binary (.tools/uv/bin/uv or PATH)." >&2
  exit 1
fi

# Validate manifest names before sha256sum can open any listed file. Only the
# pinned lock and wheel files inside this bundle may be read.
python3 -I - "$bundle" <<'PY'
import os
from pathlib import Path
import re
import sys

bundle = Path(sys.argv[1]).resolve()
def inside(path):
    return os.path.commonpath([str(bundle), str(path.resolve())]) == str(bundle)

manifest = bundle / 'SHA256SUMS'
if not manifest.is_file() or not inside(manifest):
    raise SystemExit('Missing or external SHA256SUMS manifest')
entries = set()
for line in manifest.read_text(encoding='utf-8').splitlines():
    match = re.fullmatch(r'[0-9a-fA-F]{64} [ *](requirements\.lock|wheels/[A-Za-z0-9_.+%\-]+\.whl)', line)
    if not match:
        raise SystemExit('Invalid manifest entry; only requirements.lock and wheels/*.whl are allowed')
    name = match.group(1)
    path = bundle / name
    if name in entries or not path.is_file() or not inside(path):
        raise SystemExit('Duplicate, missing, or external bundle file: ' + name)
    entries.add(name)
wheels = bundle / 'wheels'
if not wheels.is_dir() or not inside(wheels):
    raise SystemExit('Missing or external wheels directory')
expected = {'requirements.lock'}
for path in wheels.iterdir():
    if path.suffix != '.whl' or not path.is_file() or not inside(path):
        raise SystemExit('Unexpected or external entry in wheels directory')
    expected.add('wheels/' + path.name)
if len(expected) < 2 or entries != expected:
    raise SystemExit('SHA256SUMS must list requirements.lock and every wheel exactly once')
PY
(cd "$bundle" && sha256sum --check --strict --quiet SHA256SUMS)
if ! cmp --silent "$bundle/requirements.lock" requirements/go2-server-cu124.lock; then
  echo "Bundle requirements.lock differs from this repository's cu124 lock; download a matching bundle." >&2
  exit 1
fi
echo "Bundle integrity and pinned cu124 lock: OK"

mkdir -p "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR" "$TMPDIR"
if [[ -e .venv || -L .venv ]]; then
  if [[ ! -x .venv/bin/python ]]; then
    echo "Existing .venv is incomplete; it has been preserved. Move it aside before creating a fresh environment." >&2
    exit 1
  fi
else
  "$uv_bin" venv --offline --no-python-downloads --managed-python --python 3.10 .venv
fi
.venv/bin/python -I - <<'PY'
from pathlib import Path
import site
import sys

expected = Path('.venv').resolve()
assert sys.version_info[:2] == (3, 10), 'Offline runtime requires Python 3.10'
assert Path(sys.prefix).resolve() == expected and sys.prefix != sys.base_prefix, 'Expected this project\'s .venv'
assert 'include-system-site-packages = false' in (expected / 'pyvenv.cfg').read_text().lower(), 'System site-packages must be disabled'
assert not site.ENABLE_USER_SITE, 'User site-packages must be disabled'
print('Installing only into:', sys.prefix)
PY
"$uv_bin" pip sync --offline --no-index --find-links "$bundle/wheels" \
  --require-hashes --link-mode copy --no-cache --python .venv/bin/python "$bundle/requirements.lock"
.venv/bin/python -m go2.doctor
echo "Offline cu124 dependencies installed. Model weights are a separate download."
