#!/usr/bin/env bash
# Owned compatibility runtime. No sudo, cloud secrets, paid provider or shared edits.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MODEL_ROOT="${FOOTYLEVELS_MODEL_ROOT:-$HOME/skill-lab}"
RUNTIME_ROOT="$BACKEND_ROOT/runtime/skill_lab"
export CUDA_VISIBLE_DEVICES="${FOOTYLEVELS_GPU_UUID:?Set FOOTYLEVELS_GPU_UUID to the selected A100 UUID}"
[[ "$CUDA_VISIBLE_DEVICES" =~ ^GPU-[a-f0-9-]+$ ]] || { echo 'Select exactly one GPU UUID' >&2; exit 64; }
export PORT="${PORT:-8010}"
export SAM3D_REPO="$MODEL_ROOT/vendor/sam-3d-body"
export SAM3D_CHECKPOINT="$MODEL_ROOT/checkpoints/sam-3d-body-dinov3/model.ckpt"
export SAM3_PYTHON="$MODEL_ROOT/.venv-seg/bin/python"
export SAM3D_PYTHON="$MODEL_ROOT/.venv-gpu/bin/python"
export SAM3D_OBJECTS_PYTHON="$MODEL_ROOT/.venv-objects/bin/python"
export SAM3_CHECKPOINT="$MODEL_ROOT/checkpoints/sam3/sam3.pt"
export SAM3D_OBJECTS_CONFIG="$MODEL_ROOT/checkpoints/sam-3d-objects/checkpoints/pipeline.yaml"
export SKILL_LAB_DATA="${FOOTYLEVELS_DATA_DIR:-$HOME/.local/share/footylevels/runtime/data}"
export FOOTYLEVELS_PRELOAD_BODY=1
export FOOTYLEVELS_MODEL_BRIDGE=1
export PATH="$MODEL_ROOT/.media/bin:$PATH"
unset FAL_KEY GOOGLE_APPLICATION_CREDENTIALS SUPABASE_SECRET_KEY
mkdir -p "$SKILL_LAB_DATA"
cd "$RUNTIME_ROOT"
"$SAM3D_PYTHON" "$SCRIPT_DIR/verify_runtime_environment.py"
exec "$SAM3D_PYTHON" -m uvicorn backend.app:app --host 127.0.0.1 --port "$PORT" --workers 1 --no-access-log
