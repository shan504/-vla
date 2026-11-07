"""Real model-component computations with tiny random weights, not trained inference."""
import torch

from internnav.model.basemodel.internvla_n1.internvla_n1 import (
    InternVLAN1ForCausalLM, InternVLAN1ModelConfig,
)
from internnav.model.basemodel.internvla_n1.nextdit_crossattn_traj import (
    NextDiTCrossAttn, NextDiTCrossAttnConfig,
)
from transformers.models.qwen2_5_vl.configuration_qwen2_5_vl import Qwen2_5_VLVisionConfig


def test_tiny_internvla_multimodal_forward():
    vision = Qwen2_5_VLVisionConfig(
        depth=1, hidden_size=32, intermediate_size=64, num_heads=4,
        out_hidden_size=32, patch_size=2, spatial_merge_size=1,
        temporal_patch_size=1, fullatt_block_indexes=[0], window_size=4,
    )
    config = InternVLAN1ModelConfig(
        vocab_size=64, hidden_size=32, intermediate_size=64, num_hidden_layers=1,
        num_attention_heads=4, num_key_value_heads=2, vision_config=vision.to_dict(),
        rope_scaling={'type': 'mrope', 'mrope_section': [1, 1, 2]},
        max_position_embeddings=128, system1='nextdit', n_query=2,
        image_token_id=60, video_token_id=61, vision_start_token_id=62, vision_end_token_id=63,
    )
    model = InternVLAN1ForCausalLM(config).eval()
    with torch.inference_mode():
        output = model(
            input_ids=torch.tensor([[1, 62, 60, 60, 60, 60, 63, 2]]),
            pixel_values=torch.randn(4, 12), image_grid_thw=torch.tensor([[1, 2, 2]]),
        )
    assert output.logits.shape == (1, 8, 64)
    assert torch.isfinite(output.logits).all()


def test_trajectory_decoder_forward_and_checkpointing_api():
    config = NextDiTCrossAttnConfig(
        input_size=4, in_channels=32, dim=32, n_layers=1, n_heads=4,
        n_kv_heads=4, multiple_of=8, latent_embedding_size=16,
    )
    model = NextDiTCrossAttn(config).eval()
    assert model.model.gradient_checkpointing
    with torch.inference_mode():
        output = model(torch.randn(1, 4, 32), torch.tensor([0.5]), torch.randn(1, 2, 16))
    assert output.shape == (1, 4, 32)
    assert torch.isfinite(output).all()
