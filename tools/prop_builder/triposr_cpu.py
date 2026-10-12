"""Image -> mesh with vertex colours using TripoSR on the CPU (no GPU quota, slower, lower quality).
    python triposr_cpu.py <image> <out_raw.glb>
Env: TRIPOSR_DIR = checkout of VAST-AI-Research/TripoSR
torchmcubes needs a compiler, so a PyMCubes-backed stand-in is registered under its name.
"""
import os
import sys
import types

import mcubes
import numpy as np
import torch
from PIL import Image


def _marching_cubes(volume, threshold):
    verts, faces = mcubes.marching_cubes(volume.cpu().numpy(), threshold)
    # torchmcubes returns (z, y, x); TripoSR swaps the axes back afterwards
    return torch.from_numpy(np.ascontiguousarray(verts[:, ::-1])).float(), torch.from_numpy(faces.astype(np.int64))


sys.modules["torchmcubes"] = types.SimpleNamespace(marching_cubes=_marching_cubes)
sys.path.insert(0, os.environ["TRIPOSR_DIR"])
import trimesh  # noqa: E402
from tsr.system import TSR  # noqa: E402
from tsr.utils import remove_background, resize_foreground  # noqa: E402

src, out = sys.argv[1], sys.argv[2]
torch.set_num_threads(os.cpu_count() or 4)

image = Image.open(src)
if image.mode != "RGBA" or np.asarray(image)[..., 3].min() > 250:
    import rembg
    image = remove_background(image.convert("RGB"), rembg.new_session())
image = resize_foreground(image, 0.85)
a = np.asarray(image).astype(np.float32) / 255.0
a = a[:, :, :3] * a[:, :, 3:4] + (1 - a[:, :, 3:4]) * 0.5     # grey background, as TripoSR expects
image = Image.fromarray((a * 255).astype(np.uint8))

model = TSR.from_pretrained("stabilityai/TripoSR", config_name="config.yaml", weight_name="model.ckpt")
model.renderer.set_chunk_size(8192)
model.to("cpu")
with torch.no_grad():
    codes = model([image], device="cpu")
mesh = model.extract_mesh(codes, True, resolution=192)[0]
# TripoSR is Z-up; glTF is Y-up (same turn TripoSR's own demo applies before writing GLB)
mesh.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
mesh.export(out)
print("triposr glb:", out, len(mesh.faces), "faces")
