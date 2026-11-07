#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.tools/uv-cache}"
export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-$PWD/.tools/python}"
backend="${1:-cu124}"
case "$backend" in
  cpu|cu124) ;;
  *) echo "Usage: bash scripts/go2/install_server.sh [cu124|cpu]" >&2; exit 2 ;;
esac
if command -v uv >/dev/null 2>&1; then
  uv_bin="$(command -v uv)"
else
  python3 -m venv .tools/uv
  .tools/uv/bin/python -m pip install uv==0.13.5
  uv_bin="$PWD/.tools/uv/bin/uv"
fi
"$uv_bin" python install 3.10
if [[ ! -x .venv/bin/python ]]; then
  "$uv_bin" venv --python 3.10 .venv
fi
.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3,10), "Use a Python 3.10 .venv"'
"$uv_bin" pip sync --python .venv/bin/python --require-hashes \
  --torch-backend "$backend" "requirements/go2-server-${backend}.lock"
.venv/bin/python -m go2.doctor
echo "Installed $backend runtime. See docs/GO2_EDU.md for model download and startup."
