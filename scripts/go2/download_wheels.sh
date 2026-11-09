#!/usr/bin/env bash
# Run on the user's Linux/WSL machine; download Linux wheels, never install CUDA here.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
if [[ $# -ne 1 || ${1:-} == --help ]]; then
  echo "Usage: bash scripts/go2/download_wheels.sh /path/to/offline-cu124"
  exit 0
fi
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || {
  echo "Use Ubuntu/WSL2 x86_64, not native Windows Python." >&2; exit 1;
}
unset PYTHONPATH PYTHONHOME
export PYTHONNOUSERSITE=1
export UV_CACHE_DIR="$PWD/.tools/uv-cache"
export UV_PYTHON_INSTALL_DIR="$PWD/.tools/python"
bundle_dir="$1"
mkdir -p "$bundle_dir/wheels"
bundle_dir="$(cd "$bundle_dir" && pwd -P)"
if [[ -f "$bundle_dir/requirements.lock" ]]; then
  cmp -s requirements/go2-server-cu124.lock "$bundle_dir/requirements.lock" || {
    echo "Bundle belongs to another lockfile; select a fresh output directory." >&2; exit 1;
  }
fi
python3 -I - "$bundle_dir" <<'PY'
import os, sys
free = os.statvfs(sys.argv[1])
available = free.f_bavail * free.f_frsize
from pathlib import Path
already_downloaded = sum(p.stat().st_size for p in (Path(sys.argv[1]) / 'wheels').glob('*.whl'))
print('Available local space: {:.1f} GiB'.format(available / 2**30))
if available < max(4 * 2**30, 15 * 2**30 - already_downloaded):
    raise SystemExit('Use a local output drive with at least 15 GiB free for wheels and transfer archive.')
PY
if [[ -x .tools/uv/bin/uv ]]; then
  uv_bin="$PWD/.tools/uv/bin/uv"
elif command -v uv >/dev/null 2>&1; then
  uv_bin="$(command -v uv)"
else
  python3 -I scripts/go2/bootstrap_uv.py
  uv_bin="$PWD/.tools/uv/bin/uv"
fi
"$uv_bin" python install --no-bin 3.10
download_env="$PWD/.tools/wheel-download-env"
if [[ ! -x "$download_env/bin/python" ]]; then
  "$uv_bin" venv --managed-python --python 3.10 "$download_env"
fi
"$uv_bin" pip install --python "$download_env/bin/python" pip==25.3
# The input is a complete resolved lock. Explicit target tags avoid downloading
# Windows wheels and allow Linux x86_64 targets with glibc >= 2.28.
"$download_env/bin/python" -m pip --no-cache-dir download \
  --dest "$bundle_dir/wheels" --require-hashes --only-binary=:all: --no-deps \
  --python-version 3.10 --implementation cp --abi cp310 --abi abi3 --abi none \
  --platform manylinux_2_28_x86_64 --platform manylinux_2_27_x86_64 \
  --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 --platform linux_x86_64 \
  --index-url https://pypi.org/simple --extra-index-url https://download.pytorch.org/whl/cu124 \
  -r requirements/go2-server-cu124.lock
cp requirements/go2-server-cu124.lock "$bundle_dir/requirements.lock"
"$download_env/bin/python" -I - "$bundle_dir" <<'PY'
import hashlib, json, platform, sys
from pathlib import Path
root = Path(sys.argv[1])
files = [root / 'requirements.lock'] + sorted((root / 'wheels').glob('*.whl'))
if len(files) <= 1:
    raise SystemExit('No wheels downloaded; bundle not complete.')
def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()
with (root / 'SHA256SUMS').open('w') as output:
    for path in files:
        output.write('{}  {}\n'.format(digest(path), path.relative_to(root).as_posix()))
(root / 'download-info.json').write_text(json.dumps({
    'target': 'Linux x86_64, CPython 3.10, CUDA 12.4, glibc >= 2.28',
    'download_host': platform.platform(), 'wheel_count': len(files) - 1,
    'contains_model_weights': False,
}, indent=2) + '\n')
print('Verified wheel bundle:', root)
print('Wheel files:', len(files) - 1)
PY
archive_path="$bundle_dir.tar"
if [[ -e "$archive_path" ]]; then
  echo "Transfer archive already exists: $archive_path. Kept it; upload the bundle directory or move your old archive aside to rebuild." >&2
  exit 1
fi
archive_tmp="$(mktemp "$archive_path.tmp.XXXXXX")"
trap 'rm -f -- "$archive_tmp"' EXIT
tar -cf "$archive_tmp" -C "$(dirname "$bundle_dir")" "$(basename "$bundle_dir")"
mv -n -- "$archive_tmp" "$archive_path"
[[ ! -e "$archive_tmp" ]] || { echo "Archive destination appeared during packing; kept it unchanged." >&2; exit 1; }
echo "Upload this archive to the server's confirmed large-storage directory: $archive_path"
echo "This bundle contains Python dependencies, not model weights or an installed virtual environment."
