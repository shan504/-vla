#!/usr/bin/env bash
#SBATCH --job-name=internnav
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=02:00:00
#SBATCH --output=internnav-%j.out
#SBATCH --error=internnav-%j.err
set -euo pipefail

if [[ -z "${SLURM_JOB_ID:-}" ]]; then
  echo "This entry point requires a Slurm GPU allocation; submit with scripts/go2/submit_slurm.sh." >&2
  exit 1
fi
probe=0
if [[ $# -eq 1 && "$1" == --probe ]]; then
  probe=1
elif [[ $# -ne 0 ]]; then
  echo "Usage: run_internnav_server.sh [--probe]; configure normal jobs with config/go2.env or environment variables." >&2
  exit 2
fi
# Slurm runs a copy under its spool directory. Never derive the checkout from
# BASH_SOURCE here; the submit helper exports its verified project root.
project_root="${INTERNNAV_PROJECT_ROOT:-${SLURM_SUBMIT_DIR:-}}"
if [[ -z "$project_root" || ! -d "$project_root" ]]; then
  echo "Set INTERNNAV_PROJECT_ROOT or submit from the InternNav repository directory." >&2
  exit 1
fi
cd "$project_root"
if [[ ! -f go2/server.py || ! -x .venv/bin/python ]]; then
  echo "Expected the InternNav checkout and its installed .venv in: $PWD" >&2
  exit 1
fi
if [[ -f config/go2.env ]]; then
  set -a
  source config/go2.env
  set +a
fi
unset PYTHONPATH PYTHONHOME
export PYTHONNOUSERSITE=1
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-4}"
export MKL_NUM_THREADS="$OMP_NUM_THREADS"

MODEL_PATH="${MODEL_PATH:-checkpoints/InternVLA-N1-DualVLN}"
MODEL_DEVICE="${MODEL_DEVICE:-cuda:0}"
SERVER_HOST="${SERVER_HOST:-127.0.0.1}"
SERVER_PORT="${SERVER_PORT:-5801}"
ATTENTION="${ATTENTION:-sdpa}"
PLAN_STEP_GAP="${PLAN_STEP_GAP:-4}"
export MODEL_DEVICE
if [[ ! "$SERVER_PORT" =~ ^[0-9]{1,5}$ ]] || ((10#$SERVER_PORT < 1 || 10#$SERVER_PORT > 65535)); then
  echo "SERVER_PORT must be an integer in 1..65535." >&2
  exit 1
fi
if [[ "$probe" == 0 && ! -f "$MODEL_PATH/config.json" ]]; then
  echo "Missing $MODEL_PATH/config.json; download the model before submitting this offline job." >&2
  exit 1
fi

runtime_host="${SLURMD_NODENAME:-$(hostname)}"
printf 'InternNav job=%s node=%s project=%s\n' "$SLURM_JOB_ID" "$runtime_host" "$PWD"
printf 'Model=%s device=%s bind=%s:%s\n' "$MODEL_PATH" "$MODEL_DEVICE" "$SERVER_HOST" "$SERVER_PORT"
.venv/bin/python - <<'PY'
import os
import re
import torch

device = torch.device(os.environ['MODEL_DEVICE'])
if device.type != 'cuda' or not torch.cuda.is_available():
    raise SystemExit('InternNav Slurm inference requires a visible allocated CUDA GPU.')
name = torch.cuda.get_device_name(device)
print('Allocated CUDA device:', name, flush=True)
if not re.search(r'(?<!\d)4090(?!\d)', name):
    raise SystemExit('Expected the requested RTX 4090 allocation; refusing to start on: ' + name)
PY
if [[ "$probe" == 1 ]]; then
  .venv/bin/python -u -m go2.doctor --gpu
  echo "InternNav GPU probe complete; model weights were not loaded and no server was started."
  exit 0
fi
.venv/bin/python -m go2.doctor --gpu --model-path "$MODEL_PATH"

health_host="$SERVER_HOST"
if [[ "$health_host" == 0.0.0.0 ]]; then health_host=127.0.0.1; fi
if [[ "$health_host" == :: ]]; then health_host='[::1]'; fi
printf 'Starting model service; after loading completes, check on %s:\n' "$runtime_host"
printf 'curl --fail http://%s:%s/healthz\n' "$health_host" "$SERVER_PORT"
exec .venv/bin/python -u -m go2.server \
  --model-path "$MODEL_PATH" --host "$SERVER_HOST" --port "$SERVER_PORT" \
  --device "$MODEL_DEVICE" --attention "$ATTENTION" --plan-step-gap "$PLAN_STEP_GAP"
