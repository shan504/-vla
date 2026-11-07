"""Download public upstream weights; record resolved revisions for repeatable reuse."""
import argparse
import hashlib
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="main", help="DualVLN revision (prefer a commit SHA)")
    parser.add_argument("--depth-revision", default="main")
    parser.add_argument("--output", type=Path, default=Path("checkpoints"))
    args = parser.parse_args()
    # Use standard HTTPS downloads; no Xet client/service is required.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from huggingface_hub import HfApi, hf_hub_download, snapshot_download

    repo = "InternRobotics/InternVLA-N1-DualVLN"
    depth_repo = "depth-anything/Depth-Anything-V2-Metric-Hypersim-Small"
    depth_name = "depth_anything_v2_metric_hypersim_vits.pth"
    api = HfApi()
    # Resolve moving tags first, then download exactly those commits.
    revision = api.model_info(repo, revision=args.revision).sha
    depth_revision = api.model_info(depth_repo, revision=args.depth_revision).sha
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / "InternVLA-N1-DualVLN"
    snapshot_download(repo, revision=revision, local_dir=target,
                      allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt", "*.jinja"])
    if not list(target.glob("*.safetensors")):
        raise RuntimeError("Downloaded model has no safetensors weights")
    index = target / "model.safetensors.index.json"
    if index.exists():
        missing = [name for name in set(json.loads(index.read_text())["weight_map"].values())
                   if not (target / name).is_file()]
        if missing:
            raise RuntimeError(f"Missing weight shards: {missing}")
    depth = Path(hf_hub_download(depth_repo, depth_name, revision=depth_revision, local_dir=args.output))
    digest = hashlib.sha256(depth.read_bytes()).hexdigest()
    manifest = {"model": {"repo": repo, "revision": revision},
                "depth": {"repo": depth_repo, "revision": depth_revision, "sha256": digest}}
    (args.output / "model-revisions.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    print("Weights downloaded. Start from the repository root so DepthAnything is found.")


if __name__ == "__main__":
    main()
