"""Checkpoint architecture regressions using random one-layer models, not trained inference."""

import pytest
import torch
from diffusers.models.attention import LuminaFeedForward
from safetensors.torch import load_file

from internnav.model.basemodel.internvla_n1.nextdit_crossattn_traj import (
    NextDiTCrossAttn,
    NextDiTCrossAttnConfig,
)
from internnav.model.basemodel.internvla_n1.nextdit_traj import LuminaNextDiTBlock


def block(dim=384, multiple_of=256, multiplier=None):
    return LuminaNextDiTBlock(
        dim=dim, num_attention_heads=6 if dim == 384 else 4,
        num_kv_heads=6 if dim == 384 else 4, multiple_of=multiple_of,
        ffn_dim_multiplier=multiplier, norm_eps=1e-5,
        qk_norm=True, cross_attention_dim=16,
    )


def test_reproduces_reported_old_dependency_dimension_mismatch_and_loads_correctly():
    # The uploaded DualVLN weights have 1024-wide gated MLP projections at
    # dim=384. The old caller + Diffusers 0.33 instead constructs width 1536.
    trained_shape = LuminaFeedForward(dim=384, inner_dim=1024, multiple_of=256)
    widened = LuminaFeedForward(dim=384, inner_dim=1536, multiple_of=256)
    with pytest.raises(RuntimeError, match=r'size mismatch for linear_1.weight'):
        widened.load_state_dict(trained_shape.state_dict(), strict=True)
    restored = block().feed_forward
    assert restored.linear_1.weight.shape == (1024, 384)
    assert restored.linear_2.weight.shape == (384, 1024)
    assert restored.linear_3.weight.shape == (1024, 384)
    restored.load_state_dict(trained_shape.state_dict(), strict=True)
    inputs = torch.randn(1, 4, 384)
    with torch.inference_mode():
        torch.testing.assert_close(restored(inputs), trained_shape(inputs))


@pytest.mark.parametrize('dim,multiple_of,multiplier,width', [
    (32, 8, None, 88), (384, 256, None, 1024), (384, 256, 0.5, 512),
])
def test_training_dimension_reduction_precedes_multiplier_and_rounding(dim, multiple_of, multiplier, width):
    feed_forward = block(dim, multiple_of, multiplier).feed_forward
    assert feed_forward.linear_1.out_features == width


def test_actual_nextdit_save_reload_and_forward_preserves_checkpoint_width(tmp_path):
    config = NextDiTCrossAttnConfig(
        input_size=4, in_channels=384, dim=384, n_layers=1, n_heads=6,
        n_kv_heads=6, multiple_of=256, latent_embedding_size=16,
        _gradient_checkpointing=False,
    )
    model = NextDiTCrossAttn(config).eval()
    model.save_pretrained(tmp_path, safe_serialization=True)
    state = load_file(tmp_path / 'model.safetensors')
    assert state['model.layers.0.feed_forward.linear_1.weight'].shape == (1024, 384)
    reloaded = NextDiTCrossAttn.from_pretrained(
        tmp_path, low_cpu_mem_usage=True, device_map={'': 'cpu'},
    ).eval()
    assert reloaded.model.layers[0].feed_forward.linear_1.weight.shape == (1024, 384)
    inputs = torch.randn(1, 4, 384)
    timestep = torch.tensor([0.5])
    latent = torch.randn(1, 2, 16)
    with torch.inference_mode():
        output = reloaded(inputs, timestep, latent)
        reference = model(inputs, timestep, latent)
    assert output.shape == (1, 4, 384)
    assert torch.isfinite(output).all()
    torch.testing.assert_close(output, reference)
