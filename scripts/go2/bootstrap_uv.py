#!/usr/bin/env python3
"""Install a checksum-pinned uv binary with only the Python standard library."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile


WHEEL_URL = (
    "https://files.pythonhosted.org/packages/3a/ad/"
    "3c2a81d66d1839063662e5b6c0002ec31f8ee987dbc5295747627ca6160a/"
    "uv-0.13.5-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
)
WHEEL_SHA256 = "fa8d26e08e34c29c0c4e820cf3181757086d547240b4f4d31ff1d8bd810ca946"


def install_binary(target, url=WHEEL_URL, expected_sha256=WHEEL_SHA256):
    target = Path(target)
    if target.exists():
        raise RuntimeError("Refusing to overwrite an existing uv binary: {}".format(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".uv-bootstrap-", dir=target.parent) as temp:
        archive = Path(temp) / "uv.whl"
        digest = hashlib.sha256()
        # Default HTTPS and CA verification remain enabled.
        with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != expected_sha256:
            raise RuntimeError("uv wheel SHA256 mismatch; refusing to install")
        with zipfile.ZipFile(archive) as wheel:
            members = [info for info in wheel.infolist()
                       if not info.is_dir() and Path(info.filename).name == "uv"]
            if len(members) != 1:
                raise RuntimeError("Expected one uv executable in the verified wheel")
            binary = Path(temp) / "uv"
            with wheel.open(members[0]) as source, binary.open("wb") as output:
                shutil.copyfileobj(source, output)
        binary.chmod(0o755)
        os.replace(binary, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[2] / ".tools/uv/bin/uv")
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
        parser.exit(1, "This server installer requires Linux x86_64.\n")
    print("Bootstrapping uv 0.13.5 from PyPI (verified SHA256; no pip/venv/sudo required).", flush=True)
    try:
        install_binary(args.output)
        subprocess.run([str(args.output.resolve()), "--version"], check=True)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, "uv bootstrap failed: {}\n".format(exc))


if __name__ == "__main__":
    main()
