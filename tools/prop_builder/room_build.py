"""Assemble a room from detected objects: scale each model to its real height, lay them out like
the photo, add floor + walls in the photo's colours. Writes, in <out_dir>:
  objects/<id>.glb  each object normalised (base at origin), ready to become a prop
  shell.glb         floor and walls
  room.glb          everything together, for the 3D preview
  layout.json       where each prop goes relative to the room centre
    python room_build.py <objects.json> <glb_dir> <out_dir> <width_m> <depth_m>
"""
import json
import math
import os
import sys

import bpy  # noqa: I001
import mathutils
import numpy as np

info_path, glb_dir, out, RW, RD = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5])
os.makedirs(f"{out}/objects", exist_ok=True)
info = json.load(open(info_path))
RH, WT = 3.0, 0.15


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_joined(path):
    before = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.context.scene.objects if o not in before]
    extra = [o.name for o in new if o.type != "MESH"]
    meshes = [o for o in new if o.type == "MESH"]
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    for n in extra:
        if n in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects[n], do_unlink=True)
    return obj


def bounds(o):
    co = [o.matrix_world @ v.co for v in o.data.vertices]
    return (mathutils.Vector([min(c[i] for c in co) for i in range(3)]),
            mathutils.Vector([max(c[i] for c in co) for i in range(3)]))


def colour_material(name, rgb):
    img = bpy.data.images.new(name, 8, 8)
    px = np.tile(list(rgb) + [1.0], 64).astype(np.float32)
    px[:-4] *= np.repeat(np.random.default_rng(1).uniform(0.97, 1.03, 64), 4)[:-4]
    img.pixels.foreach_set(np.clip(px, 0, 1)); img.pack()
    m = bpy.data.materials.new(name); m.use_nodes = True
    t = m.node_tree.nodes.new("ShaderNodeTexImage"); t.image = img
    m.node_tree.links.new(t.outputs["Color"], m.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    return m


def box(x0, y0, z0, x1, y1, z1, mat):
    bpy.ops.mesh.primitive_cube_add(location=((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    o = bpy.context.object
    o.scale = ((x1 - x0) / 2, (y1 - y0) / 2, (z1 - z0) / 2)
    bpy.ops.object.transform_apply(scale=True)
    o.data.materials.append(mat)
    return o


# 1) normalise each object and work out its footprint
placed = []
for ob in info["objects"]:
    path = f"{glb_dir}/{ob['id']}.glb"
    if not os.path.exists(path):
        print("skip (no model):", ob["id"], ob["class"])
        continue
    reset()
    o = import_joined(path)
    mn, mx = bounds(o)
    s = ob["height"] / max(mx.z - mn.z, 1e-6)
    o.data.transform(mathutils.Matrix.Scale(s, 4))
    mn, mx = bounds(o)
    o.data.transform(mathutils.Matrix.Translation(-mathutils.Vector(((mn.x + mx.x) / 2, (mn.y + mx.y) / 2, mn.z))))
    o.location = (0, 0, 0)
    mn, mx = bounds(o)
    bpy.ops.object.select_all(action="DESELECT"); o.select_set(True)
    bpy.ops.export_scene.gltf(filepath=f"{out}/objects/{ob['id']}.glb", export_format="GLB", use_selection=True)
    x0, y0, x1, y1 = ob["box"]
    # left/right from the photo; nearer the camera the lower its base sits in the photo
    t = min(max((y1 - 0.45) / 0.55, 0.0), 1.0)
    placed.append({**ob, "w": mx.x - mn.x, "d": mx.y - mn.y,
                   "x": ((x0 + x1) / 2 - 0.5) * (RW - 1.0),
                   "y": (RD / 2 - 0.6) - t * (RD - 1.4)})

# 2) push overlapping footprints apart, then keep everything inside the walls
for _ in range(60):
    moved = False
    for a in placed:
        for b in placed:
            if a is b:
                continue
            ox = (a["w"] + b["w"]) / 2 + 0.1 - abs(a["x"] - b["x"])
            oy = (a["d"] + b["d"]) / 2 + 0.1 - abs(a["y"] - b["y"])
            if ox > 0 and oy > 0:
                if ox < oy:
                    step = ox / 2 * (1 if a["x"] >= b["x"] else -1); a["x"] += step; b["x"] -= step
                else:
                    step = oy / 2 * (1 if a["y"] >= b["y"] else -1); a["y"] += step; b["y"] -= step
                moved = True
    for p in placed:
        p["x"] = min(max(p["x"], -RW / 2 + WT + p["w"] / 2), RW / 2 - WT - p["w"] / 2)
        p["y"] = min(max(p["y"], -RD / 2 + p["d"] / 2), RD / 2 - WT - p["d"] / 2)
    if not moved:
        break

# 3) shell: floor + back/left/right walls in the photo's colours
reset()
fm, wm = colour_material("floor", info["floor"]), colour_material("wall", info["wall"])
parts = [box(-RW / 2, -RD / 2, 0, RW / 2, RD / 2, 0.08, fm),
         box(-RW / 2, RD / 2 - WT, 0.08, RW / 2, RD / 2, RH, wm),
         box(-RW / 2, -RD / 2, 0.08, -RW / 2 + WT, RD / 2 - WT, RH, wm),
         box(RW / 2 - WT, -RD / 2, 0.08, RW / 2, RD / 2 - WT, RH, wm)]
bpy.ops.object.select_all(action="DESELECT")
for p in parts:
    p.select_set(True)
bpy.context.view_layer.objects.active = parts[0]
bpy.ops.object.join()
bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.cube_project(cube_size=1.0); bpy.ops.object.mode_set(mode="OBJECT")
bpy.ops.export_scene.gltf(filepath=f"{out}/shell.glb", export_format="GLB")

# 4) whole room for the preview
for p in placed:
    o = import_joined(f"{out}/objects/{p['id']}.glb")
    o.location = (p["x"], p["y"], 0.08)
bpy.ops.export_scene.gltf(filepath=f"{out}/room.glb", export_format="GLB")

layout = {"width": RW, "depth": RD, "floor": 0.08,
          "objects": [{"id": p["id"], "class": p["class"], "x": round(p["x"], 3), "y": round(p["y"], 3)} for p in placed]}
json.dump(layout, open(f"{out}/layout.json", "w"), indent=1)
print(json.dumps(layout))
sys.stdout.flush()
os._exit(0)
