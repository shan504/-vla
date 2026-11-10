"""RGB-only inference data flow and mapping; these do not run pretrained weights."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from go2 import infer_rgb_once as once


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    image = tmp_path / 'go2_front.png'
    Image.new('RGBA', (1280, 720), (12, 34, 56, 255)).save(image)
    model = tmp_path / 'checkpoints/InternVLA-N1-DualVLN'
    model.mkdir(parents=True)
    (model / 'config.json').write_text(json.dumps({'system1': 'nextdit'}))
    (model / 'model.safetensors').write_bytes(b'fake-file-presence-only')
    (model.parent / 'depth_anything_v2_metric_hypersim_vits.pth').write_bytes(b'fake-aux-file')
    return SimpleNamespace(image=image, model_path=model, instruction='Approach the doorway.',
                           output_dir=tmp_path / 'result', resize_w=384, resize_h=384,
                           device='cuda:0', attention='sdpa', num_history=8, plan_step_gap=4)


class Agent:
    llm_output = '(192, 96)'

    def __init__(self, actions=None, pixel=None):
        self.actions, self.pixel = actions, pixel
        self.calls = []

    def reset(self):
        self.calls.append('reset')

    def step(self, *args, **kwargs):
        pytest.fail('RGB-only path must not call the RGB-D/S1 wrapper')

    def step_s1(self, *args, **kwargs):
        pytest.fail('RGB-only path must not invoke S1')

    def step_s2(self, rgb, depth, pose, instruction, intrinsic):
        self.calls.append((rgb, depth, pose, instruction, intrinsic))
        return self.actions, object(), self.pixel


def test_real_rgba_input_converts_and_runs_s2_only_with_source_mapping(sample):
    rgb = once.check_inputs(sample)
    assert rgb.mode == 'RGB'
    agent = Agent(pixel=[96, 192])
    timings = iter([10.0, 12.5, 20.0, 23.25])
    synchronizations = []
    result = once.infer(sample, rgb, lambda args: agent, lambda: synchronizations.append(True), lambda: next(timings))
    assert agent.calls[0] == 'reset'
    observed, depth, pose, instruction, intrinsic = agent.calls[1]
    assert observed.shape == (720, 1280, 3)
    np.testing.assert_array_equal(observed[0, 0], [12, 34, 56])
    assert depth is None and intrinsic is None
    np.testing.assert_array_equal(pose, np.eye(4))
    assert instruction == sample.instruction
    assert result['mode'] == 'rgb_s2_only'
    assert result['source_pixel_xy'] == [320.0, 360.0]
    assert result['model_load_seconds'] == 2.5
    assert result['inference_seconds'] == 3.25
    assert len(synchronizations) == 3
    assert json.loads((sample.output_dir / 'result.json').read_text()) == result
    with Image.open(sample.output_dir / 'preview.png') as preview:
        assert preview.size == (1280, 720)
        assert preview.getpixel((320, 360)) == (255, 0, 0)
    assert 'trajectory' not in result


def test_discrete_stop_is_saved_without_pixel_or_trajectory(sample):
    agent = Agent(actions=[0])
    agent.llm_output = 'STOP'
    result = once.infer(sample, once.check_inputs(sample), lambda args: agent, lambda: None)
    assert result['discrete_actions'] == [0]
    assert 'source_pixel_xy' not in result and 'trajectory' not in result


@pytest.mark.parametrize('actions,pixel', [([], None), ([99], None), ([True], None),
                                         (None, None), (None, [384, 192]), (None, [-1, 12]),
                                         (None, [float('nan'), 12]), (None, [1])])
def test_invalid_model_outputs_are_not_silently_clamped(actions, pixel):
    with pytest.raises(ValueError):
        once.prediction(actions, pixel, (1280, 720), (384, 384))


@pytest.mark.parametrize('missing', ['image', 'config', 'shard', 'auxiliary'])
def test_missing_inputs_fail_before_loading_model(sample, missing):
    targets = {'image': sample.image, 'config': sample.model_path / 'config.json',
               'shard': sample.model_path / 'model.safetensors',
               'auxiliary': sample.model_path.parent / 'depth_anything_v2_metric_hypersim_vits.pth'}
    targets[missing].unlink()
    with pytest.raises((OSError, ValueError)):
        once.check_inputs(sample)
    assert not sample.output_dir.exists()


def test_sharded_preflight_rejects_incomplete_checkpoint(sample):
    (sample.model_path / 'model.safetensors.index.json').write_text(json.dumps({'weight_map': {'x': 'missing.safetensors'}}))
    with pytest.raises(ValueError, match='missing model shard'):
        once.check_inputs(sample)


def test_cli_preflight_does_not_import_torch_or_create_outputs(sample):
    module = Path(once.__file__).resolve()
    code = '''
import importlib.abc, runpy, sys
class NoTorch(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('torch', 'internnav'):
            raise AssertionError('CPU preflight imported model code: ' + fullname)
sys.meta_path.insert(0, NoTorch())
sys.argv = [sys.argv[1], '--image', sys.argv[2], '--instruction', 'Approach doorway', '--check-input']
runpy.run_path(sys.argv[0], run_name='__main__')
'''
    result = subprocess.run([sys.executable, '-c', code, str(module), str(sample.image)],
                            text=True, capture_output=True, check=True)
    assert json.loads(result.stdout)['source_size_wh'] == [1280, 720]
    assert not sample.output_dir.exists()
