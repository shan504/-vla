"""Offline installer orchestration tests; uv is stubbed, no packages are installed."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/go2/install_offline.sh'


def write_manifest(bundle):
    files = [bundle / 'requirements.lock', *sorted((bundle / 'wheels').glob('*.whl'))]
    (bundle / 'SHA256SUMS').write_text(''.join(
        hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.relative_to(bundle).as_posix() + '\n'
        for path in files
    ))


@pytest.fixture
def prepared(tmp_path):
    project = tmp_path / 'project'
    (project / 'scripts/go2').mkdir(parents=True)
    shutil.copyfile(SCRIPT, project / 'scripts/go2/install_offline.sh')
    (project / 'requirements').mkdir()
    lock = 'example==1.0 --hash=sha256:' + '0' * 64 + '\n'
    (project / 'requirements/go2-server-cu124.lock').write_text(lock)
    bundle = tmp_path / 'bundle'
    (bundle / 'wheels').mkdir(parents=True)
    (bundle / 'requirements.lock').write_text(lock)
    (bundle / 'wheels/example-1.0-py3-none-any.whl').write_bytes(b'fixture wheel, not a package')
    write_manifest(bundle)
    uv = project / '.tools/uv/bin/uv'
    uv.parent.mkdir(parents=True)
    uv.write_text(f'#!{sys.executable}\n' + '''import json, os, sys, subprocess
from pathlib import Path
with open(os.environ['FAKE_LOG'], 'a') as stream:
    stream.write(json.dumps({'args': sys.argv[1:], 'offline': os.environ.get('UV_OFFLINE'),
        'tmp': os.environ.get('TMPDIR'), 'cache': os.environ.get('UV_CACHE_DIR'),
        'python_dir': os.environ.get('UV_PYTHON_INSTALL_DIR'),
        'pythonpath_present': 'PYTHONPATH' in os.environ, 'pythonhome_present': 'PYTHONHOME' in os.environ}) + '\\n')
if sys.argv[1] == 'venv':
    subprocess.run([str(Path(sys.base_prefix) / 'bin/python3.10'), '-m', 'venv', '--without-pip', '.venv'], check=True)
''')
    uv.chmod(0o755)
    (project / 'go2').mkdir()
    (project / 'go2/__init__.py').write_text('')
    (project / 'go2/doctor.py').write_text("from pathlib import Path\nPath('doctor-ran').touch()\n")
    log = tmp_path / 'uv-log'
    env = os.environ.copy()
    env.update(FAKE_LOG=str(log), PYTHONPATH='must-be-unset', PYTHONHOME='must-be-unset')
    return project, bundle, log, env


def run_installer(prepared):
    project, bundle, _, env = prepared
    return subprocess.run(['bash', 'scripts/go2/install_offline.sh', str(bundle)], cwd=project, env=env,
                          text=True, capture_output=True)


@pytest.mark.parametrize('damage', ['checksum', 'traversal', 'lock_mismatch', 'unlisted_wheel'])
def test_invalid_bundle_fails_before_uv_runs(prepared, damage):
    project, bundle, log, _ = prepared
    if damage == 'checksum':
        (bundle / 'wheels/example-1.0-py3-none-any.whl').write_bytes(b'corrupted')
    elif damage == 'traversal':
        (bundle / 'SHA256SUMS').write_text('0' * 64 + '  ../outside\n')
    elif damage == 'lock_mismatch':
        (bundle / 'requirements.lock').write_text('different==2.0\n')
        write_manifest(bundle)
    elif damage == 'unlisted_wheel':
        (bundle / 'wheels/other-1.0-py3-none-any.whl').write_bytes(b'unlisted')
    result = run_installer(prepared)
    assert result.returncode != 0
    assert not log.exists()
    assert not (project / '.venv').exists()


@pytest.mark.skipif(sys.version_info[:2] != (3, 10), reason='Installer runtime intentionally requires Python 3.10')
def test_install_is_offline_and_preserves_existing_environment(prepared):
    project, _, log, _ = prepared
    result = run_installer(prepared)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0]['args'] == ['venv', '--offline', '--no-python-downloads', '--managed-python', '--python', '3.10', '.venv']
    sync = calls[1]['args']
    assert sync[:2] == ['pip', 'sync']
    for flag in ['--offline', '--no-index', '--require-hashes', '--no-cache']:
        assert flag in sync
    assert sync[sync.index('--link-mode') + 1] == 'copy'
    assert sync[sync.index('--python') + 1] == '.venv/bin/python'
    for call in calls:
        assert call['offline'] == '1'
        assert call['tmp'] == str(project / '.tools/tmp')
        assert call['cache'] == str(project / '.tools/uv-cache')
        assert call['python_dir'] == str(project / '.tools/python')
        assert not call['pythonpath_present'] and not call['pythonhome_present']
    assert (project / 'doctor-ran').exists()
    marker = project / '.venv/user-file'
    marker.write_text('preserve')
    assert run_installer(prepared).returncode == 0
    assert marker.read_text() == 'preserve'
    assert len(log.read_text().splitlines()) == 3  # second run reuses the existing venv
