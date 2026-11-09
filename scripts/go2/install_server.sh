#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
export UV_CACHE_DIR="$PWD/.tools/uv-cache"
export UV_PYTHON_INSTALL_DIR="$PWD/.tools/python"
# Inherited Python paths from a cluster module or Conda must not affect this venv.
unset PYTHONPATH PYTHONHOME
export PYTHONNOUSERSITE=1
backend="${1:-cu124}"
case "$backend" in
  cpu|cu124) ;;
  *) echo "Usage: bash scripts/go2/install_server.sh [cu124|cpu]" >&2; exit 2 ;;
esac
if command -v uv >/dev/null 2>&1; then
  uv_bin="$(command -v uv)"
elif [[ -x .tools/uv/bin/uv ]]; then
  uv_bin="$PWD/.tools/uv/bin/uv"
else
  python3 -I scripts/go2/bootstrap_uv.py
  uv_bin="$PWD/.tools/uv/bin/uv"
fi
"$uv_bin" python install --no-bin 3.10
if [[ ! -x .venv/bin/python ]]; then
  "$uv_bin" venv --managed-python --python 3.10 .venv
fi
.venv/bin/python - <<'PY'
from pathlib import Path
import site
import sys
expected = Path('.venv').resolve()
assert sys.version_info[:2] == (3, 10), 'Use a Python 3.10 .venv'
assert Path(sys.prefix).resolve() == expected and sys.prefix != sys.base_prefix, 'Expected this project\'s .venv'
assert 'include-system-site-packages = false' in (expected / 'pyvenv.cfg').read_text().lower(), 'System site-packages must be disabled'
assert not site.ENABLE_USER_SITE, 'User site-packages must be disabled'
print('Installing only into:', sys.prefix)
PY
"$uv_bin" pip sync --python .venv/bin/python --require-hashes \
  --torch-backend "$backend" "requirements/go2-server-${backend}.lock"
.venv/bin/python -m go2.doctor
echo "Installed $backend runtime. See docs/GO2_EDU.md for model download and startup."
