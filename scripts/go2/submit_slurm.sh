#!/usr/bin/env bash
# Submit one fresh InternNav job; this does not inspect or alter other projects.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
project_root="$(pwd -P)"
probe=()
if [[ $# -gt 1 || ( $# -eq 1 && $1 != --probe ) ]]; then
  echo "Usage: bash scripts/go2/submit_slurm.sh [--probe]" >&2; exit 2
fi
if [[ ${1:-} == --probe ]]; then probe=(--probe); fi
if [[ -f config/slurm.env ]]; then source config/slurm.env; fi
if [[ -z ${INTERNNAV_PARTITION:-} || -z ${INTERNNAV_GRES:-} ]]; then
  echo "Set INTERNNAV_PARTITION and INTERNNAV_GRES from this cluster's sinfo output, in config/slurm.env or the command environment." >&2
  exit 2
fi
if [[ ! ${INTERNNAV_GRES} =~ ^gpu(:[^:,[:space:]]+)?:1$ ]]; then
  echo "INTERNNAV_GRES must request exactly one GPU, e.g. gpu:<actual-4090-type>:1 or gpu:1 on a confirmed 4090 partition." >&2
  exit 2
fi
if ! command -v sbatch >/dev/null 2>&1; then
  echo "sbatch is unavailable; run this on your Slurm login node." >&2; exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  echo "Missing this project's .venv/bin/python; finish the isolated dependency installation first." >&2; exit 1
fi
if [[ ${#probe[@]} -eq 0 ]]; then
  if [[ -f config/go2.env ]]; then source config/go2.env; fi
  if [[ ! -f "${MODEL_PATH:-checkpoints/InternVLA-N1-DualVLN}/config.json" ||
        ! -f checkpoints/depth_anything_v2_metric_hypersim_vits.pth ]]; then
    echo "Download/upload both model checkpoints before a server job; use --probe to verify the GPU first." >&2
    exit 1
  fi
fi
mkdir -p logs/slurm
args=(--parsable --job-name=internnav --export=ALL --chdir="$project_root"
      --partition="$INTERNNAV_PARTITION" --gres="$INTERNNAV_GRES"
      --output="$project_root/logs/slurm/internnav-%j.out"
      --error="$project_root/logs/slurm/internnav-%j.err")
if [[ -n ${INTERNNAV_ACCOUNT:-} ]]; then args+=(--account="$INTERNNAV_ACCOUNT"); fi
if [[ -n ${INTERNNAV_QOS:-} ]]; then args+=(--qos="$INTERNNAV_QOS"); fi
if [[ ${#probe[@]} -gt 0 ]]; then
  args+=(--time=00:10:00)
elif [[ -n ${INTERNNAV_TIME:-} ]]; then
  args+=(--time="$INTERNNAV_TIME")
fi
export INTERNNAV_PROJECT_ROOT="$project_root"
submitted="$(sbatch "${args[@]}" "$project_root/run_internnav_server.sh" "${probe[@]}")"
job_id="${submitted%%;*}"
if [[ ! $job_id =~ ^[0-9]+$ ]]; then
  echo "Could not parse sbatch output: $submitted" >&2
  echo "Check squeue before submitting again; a job may already exist." >&2
  exit 1
fi
printf '%s\n' "$job_id" > logs/slurm/last-job-id
echo "Submitted InternNav job: $job_id"
echo "Status: squeue -j $job_id"
echo "Output: $project_root/logs/slurm/internnav-$job_id.out"
echo "Errors: $project_root/logs/slurm/internnav-$job_id.err"
