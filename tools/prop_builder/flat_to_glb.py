"""Product photo -> simple 3D product with the photo as its label (text and logos stay sharp).
    python flat_to_glb.py <image> <out.glb> <shape> <height_m>
shape: bag (chips/popcorn pillow bag) | box (carton) | board (sign, poster, painting) | can (drink can, jar)
"""
import math
import os
import sys

import bpy  # noqa: I001  (bpy must be imported before bmesh)
import bmesh
import numpy as np
from PIL import Image

src, out, shape, height = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4] or 0)
height = height if height > 0 else {"bag": 0.3, "box": 0.25, "board": 1.0, "can": 0.12}[shape]

# --- crop the product out of a plain background ---------------------------------------
im = Image.open(src).convert("RGBA")
a = np.asarray(im).astype(int)
if a[..., 3].min() < 250:                       # transparent PNG: use alpha
    mask = a[..., 3] > 20
else:                                           # plain background: differs from the corner colour
    corners = np.array([a[0, 0], a[0, -1], a[-1, 0], a[-1, -1]])[:, :3]
    bg = np.median(corners, 0)
    mask = np.abs(a[..., :3] - bg).sum(-1) > 45
ys, xs = np.nonzero(mask)
if len(xs):
    pad = 2
    im = im.crop((max(xs.min() - pad, 0), max(ys.min() - pad, 0), min(xs.max() + pad, im.width), min(ys.max() + pad, im.height)))
im = im.convert("RGB")
if max(im.size) < 1024:                         # upscale small web images so the label stays crisp
    s = 1024 / max(im.size)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
aspect = im.width / im.height
edge = np.asarray(im.resize((8, 8)))[[0, -1]].reshape(-1, 3).mean(0) / 255  # side colour from the top/bottom edges
label_path = os.path.join(os.path.dirname(out) or ".", "label.png")
im.save(label_path)

# --- scene ----------------------------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)

def material(name, image=None, color=None, metal=0.0):
    m = bpy.data.materials.new(name); m.use_nodes = True
    bsdf = m.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.45 if metal else 0.6
    bsdf.inputs["Metallic"].default_value = metal
    if image is None:  # every material gets an image so the GTA shader has a texture
        image = bpy.data.images.new(name + "_px", 4, 4)
        image.pixels.foreach_set(np.tile(list(color) + [1.0], 16).astype(np.float32))
        image.pack()
    t = m.node_tree.nodes.new("ShaderNodeTexImage"); t.image = image
    m.node_tree.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])
    return m

label = bpy.data.images.load(label_path); label.pack()
M_LABEL = material("label", label)
M_SIDE = material("side", color=edge)
M_METAL = material("metal", color=(0.75, 0.76, 0.78), metal=1.0)

H = height
W = H * aspect
me = bpy.data.meshes.new(shape); bm = bmesh.new(); uv = bm.loops.layers.uv.new("UVMap")

def quad(pts, uvs, mat):
    f = bm.faces.new([bm.verts.new(p) for p in pts]); f.material_index = mat
    for loop, t in zip(f.loops, uvs):
        loop[uv].uv = t
    return f

if shape == "bag":
    D = min(W, H) * 0.32                                   # puff thickness at the centre
    nx, ny, seam = 28, 36, 0.07
    def puff(u, v):
        if v < seam or v > 1 - seam:
            return 0.0
        vv = (v - seam) / (1 - 2 * seam)
        return D / 2 * math.sin(math.pi * u) ** 0.6 * math.sin(math.pi * vv) ** 0.5
    for side in (1, -1):
        for i in range(nx):
            for j in range(ny):
                cs = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
                pts, uvs = [], []
                for (a_, b_) in cs:
                    u, v = a_ / nx, b_ / ny
                    pts.append(((u - 0.5) * W, side * puff(u, v), v * H))
                    uvs.append((1 - u if side > 0 else u, v))
                if side > 0:                               # front faces -Y... keep outward normals
                    pts, uvs = pts[::-1], uvs[::-1]
                quad(pts, uvs, 0)
elif shape in ("box", "board"):
    D = W * 0.35 if shape == "box" else 0.03
    x0, x1, y0, y1 = -W / 2, W / 2, -D / 2, D / 2
    quad([(x0, y0, 0), (x1, y0, 0), (x1, y0, H), (x0, y0, H)], [(0, 0), (1, 0), (1, 1), (0, 1)], 0)  # front (-Y)
    quad([(x1, y1, 0), (x0, y1, 0), (x0, y1, H), (x1, y1, H)],
         [(0, 0), (1, 0), (1, 1), (0, 1)], 0 if shape == "box" else 1)                                # back
    full = [(0, 0), (1, 0), (1, 1), (0, 1)]
    quad([(x0, y1, 0), (x0, y0, 0), (x0, y0, H), (x0, y1, H)], full, 1)                               # left
    quad([(x1, y0, 0), (x1, y1, 0), (x1, y1, H), (x1, y0, H)], full, 1)                               # right
    quad([(x0, y0, H), (x1, y0, H), (x1, y1, H), (x0, y1, H)], full, 1)                               # top
    quad([(x0, y1, 0), (x1, y1, 0), (x1, y0, 0), (x0, y0, 0)], full, 1)                               # bottom
else:  # can: the label wraps all the way round
    R = W / (2 * math.pi); n = 48; rim = H * 0.04
    for i in range(n):
        a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
        p = lambda a, z: (R * math.sin(a), -R * math.cos(a), z)
        quad([p(a0, rim), p(a1, rim), p(a1, H - rim), p(a0, H - rim)], [(i / n, 0), ((i + 1) / n, 0), ((i + 1) / n, 1), (i / n, 1)], 0)
        for z0, z1 in ((0, rim), (H - rim, H)):
            quad([p(a0, z0), p(a1, z0), p(a1, z1), p(a0, z1)], [(0, 0)] * 4, 2)
    for z, flip in ((H, False), (0, True)):
        ring = [bm.verts.new((R * 0.97 * math.sin(2 * math.pi * i / n), -R * 0.97 * math.cos(2 * math.pi * i / n), z)) for i in range(n)]
        f = bm.faces.new(ring[::-1] if flip else ring); f.material_index = 2

bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
bm.normal_update()
bm.to_mesh(me); bm.free()
obj = bpy.data.objects.new(shape, me); bpy.context.scene.collection.objects.link(obj)
for m in (M_LABEL, M_SIDE, M_METAL):
    me.materials.append(m)
bpy.context.view_layer.objects.active = obj; obj.select_set(True)
bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="SELECT"); bpy.ops.mesh.normals_make_consistent(inside=False)
bpy.ops.object.mode_set(mode="OBJECT")
bpy.ops.object.shade_smooth() if shape in ("bag", "can") else None
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB")
print(f"glb: {out} {shape} {W:.2f} x {H:.2f} m")
sys.stdout.flush()
os._exit(0)
