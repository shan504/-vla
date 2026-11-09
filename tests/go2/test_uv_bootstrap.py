"""The bootstrap works without importing pip, venv or ensurepip."""
import hashlib
import importlib.util
from pathlib import Path
import zipfile

import pytest

spec = importlib.util.spec_from_file_location("bootstrap_uv", Path(__file__).resolve().parents[2] / "scripts/go2/bootstrap_uv.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def test_verified_binary_only_installed_and_failed_venv_preserved(tmp_path):
    wheel = tmp_path / "uv.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("uv.data/scripts/uv", b"verified-test-binary")
        archive.writestr("../../escape", b"never extract this file")
    partial = tmp_path / "tools/bin/python3"
    partial.parent.mkdir(parents=True)
    partial.write_text("partial venv left by ensurepip failure")
    target = partial.parent / "uv"
    bootstrap.install_binary(target, wheel.as_uri(), hashlib.sha256(wheel.read_bytes()).hexdigest())
    assert target.read_bytes() == b"verified-test-binary"
    assert target.stat().st_mode & 0o111
    assert partial.read_text() == "partial venv left by ensurepip failure"
    assert not (tmp_path / "escape").exists()


def test_bad_hash_does_not_install(tmp_path):
    wheel = tmp_path / "uv.whl"
    wheel.write_bytes(b"corrupt download")
    target = tmp_path / "bin/uv"
    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        bootstrap.install_binary(target, wheel.as_uri(), "0" * 64)
    assert not target.exists()


def test_existing_binary_is_preserved(tmp_path):
    target = tmp_path / "uv"
    target.write_bytes(b"existing user file")
    with pytest.raises(RuntimeError, match="overwrite"):
        bootstrap.install_binary(target)
    assert target.read_bytes() == b"existing user file"
