"""Real auxiliary weight loading under HF/Accelerate meta initialization."""

import warnings
from types import SimpleNamespace

import pytest
import torch
from accelerate import init_empty_weights
from torch import nn

from internnav.model.basemodel.internvla_n1 import internvla_n1_arch as architecture
from internnav.model.encoder.depth_anything.depth_anything_v2 import dpt


class TinyBackbone(nn.Linear):
    def __init__(self):
        super().__init__(3, 2)
        self.register_buffer('offset', torch.ones(2))

    def forward(self, values):
        return super().forward(values) + self.offset


class TinyDepthAnything(nn.Module):
    """Small real parameters plus a discarded head, using the production builder."""

    def __init__(self, **kwargs):
        super().__init__()
        self.pretrained = TinyBackbone()
        self.depth_head = nn.Linear(2, 1)


@pytest.fixture
def depth_checkpoint(tmp_path, monkeypatch):
    model = TinyDepthAnything()
    with torch.no_grad():
        model.pretrained.weight.copy_(torch.tensor([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]))
        model.pretrained.bias.copy_(torch.tensor([0.1, 0.2]))
        model.pretrained.offset.copy_(torch.tensor([0.5, 0.25]))
        model.depth_head.weight.fill_(0.75)
        model.depth_head.bias.fill_(0.125)
    state = model.state_dict()
    path = tmp_path / 'depth_anything_v2_metric_hypersim_vits.pth'
    torch.save(state, path)
    monkeypatch.setattr(architecture, 'MODEL_PATH_TO', str(tmp_path))
    monkeypatch.setattr(dpt, 'DepthAnythingV2', TinyDepthAnything)
    return model, state, path


@pytest.mark.parametrize('include_buffers', [False, True])
def test_meta_depth_checkpoint_is_materialized_with_real_values(depth_checkpoint, include_buffers):
    reference, state, _ = depth_checkpoint
    values = torch.tensor([[1.0, 2.0, 3.0]])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        with init_empty_weights(include_buffers=include_buffers):
            backbone = architecture.build_depthanythingv2(SimpleNamespace())
            assert all(not parameter.is_meta for parameter in backbone.parameters())
            assert all(not buffer.is_meta for buffer in backbone.buffers())
    assert not any('copying from a non-meta parameter' in str(item.message) for item in caught)
    for name, tensor in backbone.state_dict().items():
        torch.testing.assert_close(tensor, state['pretrained.' + name])
        assert tensor.device.type == 'cpu'
    torch.testing.assert_close(backbone(values), reference.pretrained(values))
    # The returned module must support ordinary moves/forward, not remain a meta shell.
    backbone.to('cpu')(values).sum().backward()
    assert backbone.weight.grad is not None


def test_cpu_depth_loading_retains_strict_load_and_values(depth_checkpoint):
    reference, _, _ = depth_checkpoint
    backbone = architecture.build_depthanythingv2(SimpleNamespace())
    values = torch.tensor([[2.0, -1.0, 0.5]])
    torch.testing.assert_close(backbone(values), reference.pretrained(values))


@pytest.mark.parametrize('corruption', ['missing', 'unexpected', 'shape'])
def test_meta_depth_loading_still_rejects_incompatible_checkpoint(depth_checkpoint, corruption):
    _, state, path = depth_checkpoint
    if corruption == 'missing':
        del state['depth_head.bias']
    elif corruption == 'unexpected':
        state['unknown_weight'] = torch.ones(1)
    else:
        state['pretrained.weight'] = torch.ones(3, 3)
    torch.save(state, path)
    with init_empty_weights(), pytest.raises(RuntimeError):
        architecture.build_depthanythingv2(SimpleNamespace())
