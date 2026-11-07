"""Check actual model imports, CUDA and optional downloaded checkpoint files."""
import argparse
import importlib.metadata
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", action="store_true", help="Require CUDA and BF16 support")
    parser.add_argument("--model-path", type=Path)
    args = parser.parse_args()
    import torch
    from internnav.model.basemodel.internvla_n1.internvla_n1 import InternVLAN1ForCausalLM
    from internnav.model.basemodel.internvla_n1.nextdit_crossattn_traj import NextDiTCrossAttn
    from internnav.model.encoder.depth_anything.depth_anything_v2.dpt import DepthAnythingV2

    assert InternVLAN1ForCausalLM and NextDiTCrossAttn and DepthAnythingV2
    for name in ("torch", "torchvision", "transformers", "diffusers", "flask"):
        print(f"{name}: {importlib.metadata.version(name)}")
    cuda = torch.cuda.is_available()
    print(f"Model, trajectory decoder and depth encoder imports: OK; CUDA: {cuda}")
    if args.gpu:
        if not cuda:
            parser.error("CUDA is unavailable. Install the cu124 runtime on your NVIDIA GPU workstation.")
        if not torch.cuda.is_bf16_supported():
            parser.error("This deployment requires a BF16-capable NVIDIA GPU.")
        print(f"GPU: {torch.cuda.get_device_name(0)}; BF16: supported")
    if args.model_path:
        config_path = args.model_path / "config.json"
        if not config_path.is_file():
            parser.error(f"Missing {config_path}; run python -m go2.download_models")
        config = json.loads(config_path.read_text())
        if "nextdit" not in config.get("system1", ""):
            parser.error("Expected DualVLN nextdit checkpoint, not the separate NavDP model.")
        index = args.model_path / "model.safetensors.index.json"
        names = set(json.loads(index.read_text())["weight_map"].values()) if index.exists() else {"model.safetensors"}
        for name in names:
            if not (args.model_path / name).is_file():
                parser.error(f"Missing weight shard: {name}")
        if not Path("checkpoints/depth_anything_v2_metric_hypersim_vits.pth").is_file():
            parser.error("Missing DepthAnything checkpoint in checkpoints/")
        print("Model files: present (this does not test full model inference)")


if __name__ == "__main__":
    main()
