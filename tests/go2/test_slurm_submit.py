"""Submission contract only: fake sbatch, no real scheduler or GPU allocation."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    (root / 'scripts/go2').mkdir(parents=True)
    source = Path(__file__).resolve().parents[2]
    shutil.copyfile(source / 'scripts/go2/submit_slurm.sh', root / 'scripts/go2/submit_slurm.sh')
    (root / 'run_internnav_server.sh').write_text('#!/bin/bash\n')
    (root / '.venv/bin').mkdir(parents=True)
    (root / '.venv/bin/python').symlink_to('/bin/true')
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    sbatch = fake_bin / 'sbatch'
    sbatch.write_text('#!/usr/bin/env python3\nimport os,json,sys\n'
                      'with open(os.environ["SUBMIT_LOG"],"w") as f: json.dump({"args":sys.argv[1:],"project":os.environ.get("INTERNNAV_PROJECT_ROOT")},f)\n'
                      'print("73142;testcluster")\n')
    sbatch.chmod(0o755)
    env = os.environ.copy()
    env.update(PATH=str(fake_bin) + os.pathsep + env['PATH'], SUBMIT_LOG=str(tmp_path / 'submit-log'))
    env.pop('INTERNNAV_PARTITION', None)
    env.pop('INTERNNAV_GRES', None)
    return root, env


def run(project, *args, **settings):
    root, env = project
    env = {**env, **settings}
    return subprocess.run(['bash', 'scripts/go2/submit_slurm.sh', *args], cwd=root, env=env,
                          text=True, capture_output=True)


def test_missing_real_resource_names_does_not_submit(project):
    result = run(project, '--probe')
    assert result.returncode == 2
    assert not Path(project[1]['SUBMIT_LOG']).exists()


@pytest.mark.parametrize('gres', ['gpu:rtx4090:1', 'gpu:1'])
def test_probe_submits_one_new_job_with_separate_logs(project, gres):
    result = run(project, '--probe', INTERNNAV_PARTITION='test-4090', INTERNNAV_GRES=gres)
    assert result.returncode == 0, result.stderr
    root, env = project
    call = json.loads(Path(env['SUBMIT_LOG']).read_text())
    assert call['project'] == str(root)
    assert '--partition=test-4090' in call['args']
    assert '--gres=' + gres in call['args']
    assert '--time=00:10:00' in call['args']
    assert call['args'][-1] == '--probe'
    assert (root / 'logs/slurm/last-job-id').read_text() == '73142\n'
    assert any(str(root / 'logs/slurm/internnav-%j.out') in value for value in call['args'])


def test_multi_gpu_request_is_rejected(project):
    result = run(project, '--probe', INTERNNAV_PARTITION='test-4090', INTERNNAV_GRES='gpu:rtx4090:2')
    assert result.returncode == 2
    assert not Path(project[1]['SUBMIT_LOG']).exists()


def test_normal_job_without_weights_does_not_submit(project):
    result = run(project, INTERNNAV_PARTITION='test-4090', INTERNNAV_GRES='gpu:rtx4090:1')
    assert result.returncode == 1
    assert 'both model checkpoints' in result.stderr
    assert not Path(project[1]['SUBMIT_LOG']).exists()
