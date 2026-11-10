"""Slurm entry point orchestration with a mocked Torch device, not a GPU test."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / 'run_internnav_server.sh'


@pytest.fixture
def job(tmp_path):
    project = tmp_path / 'checkout'
    (project / 'go2').mkdir(parents=True)
    (project / 'go2/server.py').write_text('')
    (project / 'checkpoints/InternVLA-N1-DualVLN').mkdir(parents=True)
    (project / 'checkpoints/InternVLA-N1-DualVLN/config.json').write_text('{}')
    (project / '.venv/bin').mkdir(parents=True)
    python = project / '.venv/bin/python'
    python.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
from types import SimpleNamespace
with open(os.environ['FAKE_LOG'], 'a') as stream:
    stream.write(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd(),
        'offline': [os.environ.get('HF_HUB_OFFLINE'), os.environ.get('TRANSFORMERS_OFFLINE')],
        'no_user_site': os.environ.get('PYTHONNOUSERSITE'),
        'pythonpath_present': 'PYTHONPATH' in os.environ, 'pythonhome_present': 'PYTHONHOME' in os.environ}) + '\\n')
if sys.argv[1:] == ['-']:
    def device(value):
        return SimpleNamespace(type=value.split(':')[0])
    sys.modules['torch'] = SimpleNamespace(device=device, cuda=SimpleNamespace(
        is_available=lambda: os.environ.get('FAKE_CUDA', '1') == '1',
        get_device_name=lambda selected: os.environ.get('FAKE_GPU_NAME', 'NVIDIA GeForce RTX 4090')))
    exec(sys.stdin.read())
elif 'go2.doctor' in sys.argv:
    raise SystemExit(int(os.environ.get('FAKE_DOCTOR_EXIT', '0')))
elif 'go2.server' in sys.argv:
    raise SystemExit(int(os.environ.get('FAKE_SERVER_EXIT', '0')))
else:
    raise SystemExit(99)
''')
    python.chmod(0o755)
    # Execute a spool copy outside the checkout to catch dirname-based lookup.
    spool = tmp_path / 'slurm-spool'
    spool.mkdir()
    script = spool / 'slurm_script'
    shutil.copyfile(SCRIPT, script)
    log = tmp_path / 'python-log'
    env = os.environ.copy()
    for key in ['INTERNNAV_PROJECT_ROOT', 'SLURM_SUBMIT_DIR', 'MODEL_PATH', 'MODEL_DEVICE', 'SERVER_HOST', 'SERVER_PORT', 'ATTENTION', 'PLAN_STEP_GAP']:
        env.pop(key, None)
    env.update(SLURM_JOB_ID='12345', SLURM_SUBMIT_DIR=str(project), SLURMD_NODENAME='gpu-node',
               FAKE_LOG=str(log), PYTHONPATH='must-be-cleared', PYTHONHOME='must-be-cleared')
    return project, spool, script, log, env


def run_job(job, *arguments):
    _, spool, script, _, env = job
    return subprocess.run(['bash', str(script), *arguments], cwd=spool, env=env, text=True, capture_output=True)


def calls(job):
    log = job[3]
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_spool_execution_uses_submit_directory_and_offline_defaults(job):
    result = run_job(job)
    assert result.returncode == 0, result.stdout + result.stderr
    logged = calls(job)
    assert [call['args'][:3] for call in logged] == [['-'], ['-m', 'go2.doctor', '--gpu'], ['-u', '-m', 'go2.server']]
    for call in logged:
        assert call['cwd'] == str(job[0])
        assert call['offline'] == ['1', '1'] and call['no_user_site'] == '1'
        assert not call['pythonpath_present'] and not call['pythonhome_present']
    server = logged[-1]['args']
    assert server[server.index('--host') + 1] == '127.0.0.1'
    assert server[server.index('--port') + 1] == '5801'
    assert server[server.index('--device') + 1] == 'cuda:0'
    assert 'job=12345 node=gpu-node' in result.stdout
    assert 'curl --fail http://127.0.0.1:5801/healthz' in result.stdout


def test_explicit_project_root_and_project_config(job):
    project, _, _, _, env = job
    env.update(INTERNNAV_PROJECT_ROOT=str(project), SLURM_SUBMIT_DIR='/unrelated/spool')
    (project / 'config').mkdir()
    (project / 'config/go2.env').write_text('SERVER_HOST=0.0.0.0\nSERVER_PORT=5900\nPLAN_STEP_GAP=7\n')
    assert run_job(job).returncode == 0
    server = calls(job)[-1]['args']
    assert server[server.index('--host') + 1] == '0.0.0.0'
    assert server[server.index('--port') + 1] == '5900'
    assert server[server.index('--plan-step-gap') + 1] == '7'


def test_outside_slurm_refused_without_starting_python(job):
    job[4].pop('SLURM_JOB_ID')
    result = run_job(job)
    assert result.returncode != 0 and 'requires a Slurm GPU allocation' in result.stderr
    assert not calls(job)


@pytest.mark.parametrize('changes', [{'FAKE_GPU_NAME': 'NVIDIA A100'}, {'FAKE_CUDA': '0'}, {'MODEL_DEVICE': 'cpu'}])
def test_wrong_or_missing_gpu_prevents_doctor_and_server(job, changes):
    job[4].update(changes)
    result = run_job(job)
    assert result.returncode != 0
    assert len(calls(job)) == 1
    assert '4090' in result.stderr or 'allocated CUDA GPU' in result.stderr


def test_missing_model_refused_before_gpu_check(job):
    (job[0] / 'checkpoints/InternVLA-N1-DualVLN/config.json').unlink()
    result = run_job(job)
    assert result.returncode != 0 and 'download the model before submitting' in result.stderr
    assert not calls(job)


@pytest.mark.parametrize('variable,expected_calls', [('FAKE_DOCTOR_EXIT', 2), ('FAKE_SERVER_EXIT', 3)])
def test_underlying_failure_exit_status_is_preserved(job, variable, expected_calls):
    job[4][variable] = '23'
    assert run_job(job).returncode == 23
    assert len(calls(job)) == expected_calls


def test_scheduler_resources_do_not_guess_partition_or_gres():
    directives = [line for line in SCRIPT.read_text().splitlines() if line.startswith('#SBATCH')]
    assert '#SBATCH --job-name=internnav' in directives
    assert '#SBATCH --cpus-per-task=4' in directives
    assert '#SBATCH --mem=32G' in directives
    assert not any('--partition' in line or '--gres' in line for line in directives)


def test_probe_needs_no_weights_and_never_starts_server(job):
    (job[0] / 'checkpoints/InternVLA-N1-DualVLN/config.json').unlink()
    result = run_job(job, '--probe')
    assert result.returncode == 0, result.stdout + result.stderr
    logged = calls(job)
    assert len(logged) == 2
    assert logged[-1]['args'] == ['-u', '-m', 'go2.doctor', '--gpu']
    assert logged[-1]['offline'] == ['1', '1']
    assert 'probe complete' in result.stdout
    assert 'no server was started' in result.stdout


@pytest.mark.parametrize('arguments', [('--unknown',), ('--probe', '--probe')])
def test_unknown_arguments_are_rejected(job, arguments):
    assert run_job(job, *arguments).returncode == 2
    assert not calls(job)
