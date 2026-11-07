#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
if [[ -f config/go2.env ]]; then
  set -a
  source config/go2.env
  set +a
fi
exec .venv/bin/python -m go2.server \
  --model-path "${MODEL_PATH:-checkpoints/InternVLA-N1-DualVLN}" \
  --host "${SERVER_HOST:-127.0.0.1}" --port "${SERVER_PORT:-5801}" \
  --device "${MODEL_DEVICE:-cuda:0}" --attention "${ATTENTION:-sdpa}" \
  --plan-step-gap "${PLAN_STEP_GAP:-4}" "$@"
