#!/usr/bin/env bash
# to3d.sh <image> <out.glb> <engine>
# Image to 3D with the engine you pick, nothing else is tried:
#   trellis2 | hunyuan | trellis   free Hugging Face Spaces (daily GPU quota)
#   triposr                        this machine's CPU (no quota, lower quality)
# Writes the engine next to the model, in <out>.engine.
#   Env: PY (bpy python), HF_TOKEN, TRIPOSR_HOME (cache dir for TripoSR's venv and checkout)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PY:-python3}"
IMG="$1"; OUT="$2"; ENGINE_NAME="${3:-trellis2}"
TRIPOSR_REF=107cefdc244c39106fa830359024f6a2f1c78871
HOME_DIR="${TRIPOSR_HOME:-$HOME/.cache/triposr}"

ENGINE="${OUT%.glb}.engine"

if [ "$ENGINE_NAME" != triposr ]; then
    "$PY" -u "$HERE/image_to_glb.py" "$IMG" "$OUT" "$ENGINE_NAME" || exit 1
    echo "$ENGINE_NAME" > "$ENGINE"
    exit 0
fi

VENV="$HOME_DIR/venv"; SRC="$HOME_DIR/TripoSR"
if [ ! -x "$VENV/bin/python" ] || [ ! -d "$SRC/tsr" ]; then
    rm -rf "$VENV" "$SRC"; mkdir -p "$HOME_DIR"
    git init -q "$SRC" && git -C "$SRC" fetch -q --depth 1 https://github.com/VAST-AI-Research/TripoSR.git "$TRIPOSR_REF" \
        && git -C "$SRC" checkout -q FETCH_HEAD || exit 1
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q torch --index-url https://download.pytorch.org/whl/cpu || exit 1
    "$VENV/bin/pip" install -q "omegaconf==2.3.0" "einops==0.7.0" "transformers==4.35.0" "huggingface-hub<0.26" \
        "trimesh==4.0.5" PyMCubes rembg onnxruntime pillow imageio "numpy<2" || exit 1
fi
RAW="${OUT%.glb}.raw.glb"
TRIPOSR_DIR="$SRC" "$VENV/bin/python" -u "$HERE/triposr_cpu.py" "$IMG" "$RAW" || exit 1
"$PY" -u "$HERE/vc_bake.py" "$RAW" "$OUT" | grep -E "^baked|rror" || true
[ -s "$OUT" ] && echo triposr > "$ENGINE"
