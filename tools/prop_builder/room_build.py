"""Assemble a room from detected objects: scale each model to its real height, lay them out like
the photo, add floor + walls in the photo's colours. Writes, in <out_dir>:
  objects/<id>.glb  each object normalised (base at origin), ready to become a prop
  shell.glb         floor, four walls (door in the front wall, window in a side wall) and ceiling
  room.glb          everything together without the front wall and ceiling, for the 3D preview
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
RH, WT, FL = 3.0, 0.15, 0.08          # room height, wall thickness, floor thickness
DOOR_W, DOOR_H = 0.9, 2.1
WIN_W, WIN_H, WIN_SILL = 1.2, 1.1, 0.9


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
    lum = 0.3 * rgb[0] + 0.59 * rgb[1] + 0.11 * rgb[2]
    if lum < 0.25:  # photos are often darker than the real room; keep surfaces readable
        rgb = [min(1.0, c * 0.25 / max(lum, 0.02)) for c in rgb]
    img = bpy.data.images.new(name, 8, 8)
    px = np.tile(list(rgb) + [1.0], 64).astype(np.float32)
    px[:-4] *= np.repeat(np.random.default_rng(1).uniform(0.97, 1.03, 64), 4)[:-4]
    img.pixels.foreach_set(np.clip(px, 0, 1)); img.pack()
    m = bpy.data.materials.new(name); m.use_nodes = True
    t = m.node_tree.nodes.new("ShaderNodeTexImage"); t.image = img
    bsdf = m.node_tree.nodes["Principled BSDF"]
    m.node_tree.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.9
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

# 2) door in the front wall where the furniture leaves the most room; keep a clear patch inside it
def overlap(a, b):
    return (max(0.0, (a["w"] + b["w"]) / 2 - abs(a["x"] - b["x"])),
            max(0.0, (a["d"] + b["d"]) / 2 - abs(a["y"] - b["y"])))


def door_zone(x):
    return {"x": x, "y": -RD / 2 + WT + 0.45, "w": DOOR_W + 0.3, "d": 0.9, "fixed": True}


span = RW / 2 - WT - DOOR_W / 2 - 0.15
door_x = min(np.linspace(-span, span, 9),
             key=lambda x: (round(sum(math.prod(overlap(door_zone(x), p)) for p in placed), 3), abs(x)))
blocks = placed + [door_zone(door_x)]

# 3) push overlapping footprints apart (the door patch never moves), then keep everything inside the walls
for _ in range(80):
    moved = False
    for i, a in enumerate(blocks):
        for b in blocks[i + 1:]:
            ox = (a["w"] + b["w"]) / 2 + 0.1 - abs(a["x"] - b["x"])
            oy = (a["d"] + b["d"]) / 2 + 0.1 - abs(a["y"] - b["y"])
            if ox <= 0 or oy <= 0:
                continue
            fa, fb = (0.0, 1.0) if a.get("fixed") else (1.0, 0.0) if b.get("fixed") else (0.5, 0.5)
            if ox < oy:
                sx = ox * (1 if a["x"] >= b["x"] else -1); a["x"] += sx * fa; b["x"] -= sx * fb
            else:
                sy = oy * (1 if a["y"] >= b["y"] else -1); a["y"] += sy * fa; b["y"] -= sy * fb
            moved = True
    for p in placed:
        p["x"] = min(max(p["x"], -RW / 2 + WT + p["w"] / 2), RW / 2 - WT - p["w"] / 2)
        p["y"] = min(max(p["y"], -RD / 2 + WT + p["d"] / 2), RD / 2 - WT - p["d"] / 2)
    if not moved:
        break

# window on the side wall with less tall furniture in front of it
def blocked(side):
    return max([p["height"] for p in placed
                if abs(side * RW / 2 - p["x"]) - p["w"] / 2 < 0.7 and abs(p["y"]) - p["d"] / 2 < WIN_W / 2
                and p["height"] > WIN_SILL] + [0.0])


win_side = min((1, -1), key=blocked)

# 4) shell in the photo's colours
reset()
fm, wm = colour_material("floor", info["floor"]), colour_material("wall", info["wall"])
cm = colour_material("ceiling", [min(1.0, c * 1.1) for c in info["wall"]])


def wall(along_x, a0, a1, t0, t1, holes, mat):
    """Wall running along x (or y) from a0 to a1, thickness t0..t1, with (centre, width, z0, z1) openings."""
    def piece(s0, s1, z0, z1):
        if s1 - s0 > 1e-3 and z1 - z0 > 1e-3:
            pieces.append(box(s0, t0, z0, s1, t1, z1, mat) if along_x else box(t0, s0, z0, t1, s1, z1, mat))
    pieces, cur = [], a0
    for c, w, z0, z1 in sorted(holes):
        piece(cur, c - w / 2, FL, RH)
        piece(c - w / 2, c + w / 2, FL, z0)
        piece(c - w / 2, c + w / 2, z1, RH)
        cur = c + w / 2
    piece(cur, a1, FL, RH)
    return pieces


win_x = (RW / 2 - WT, RW / 2) if win_side > 0 else (-RW / 2, -RW / 2 + WT)
open_parts = [box(-RW / 2, -RD / 2, 0, RW / 2, RD / 2, FL, fm)]
open_parts += wall(True, -RW / 2, RW / 2, RD / 2 - WT, RD / 2, [], wm)                              # back
open_parts += wall(False, -RD / 2 + WT, RD / 2 - WT, *win_x, [(0.0, WIN_W, WIN_SILL, WIN_SILL + WIN_H)], wm)  # window
other_x = (-RW / 2, -RW / 2 + WT) if win_side > 0 else (RW / 2 - WT, RW / 2)
open_parts += wall(False, -RD / 2 + WT, RD / 2 - WT, *other_x, [], wm)
closed_parts = wall(True, -RW / 2, RW / 2, -RD / 2, -RD / 2 + WT, [(door_x, DOOR_W, FL, FL + DOOR_H)], wm)  # front
closed_parts.append(box(-RW / 2, -RD / 2, RH, RW / 2, RD / 2, RH + 0.1, cm))                         # ceiling
for o in open_parts + closed_parts:
    bpy.ops.object.select_all(action="DESELECT"); o.select_set(True)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.mode_set(mode="EDIT"); bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.cube_project(cube_size=1.0); bpy.ops.object.mode_set(mode="OBJECT")
bpy.ops.export_scene.gltf(filepath=f"{out}/shell.glb", export_format="GLB")
for o in closed_parts:   # the preview shows the room from the door side, like a doll's house
    bpy.data.objects.remove(o, do_unlink=True)

# 5) whole room for the preview
for p in placed:
    o = import_joined(f"{out}/objects/{p['id']}.glb")
    o.location = (p["x"], p["y"], FL)
bpy.ops.export_scene.gltf(filepath=f"{out}/room.glb", export_format="GLB")

layout = {"width": RW, "depth": RD, "floor": FL, "door_x": round(float(door_x), 3), "window": "right" if win_side > 0 else "left",
          "objects": [{"id": p["id"], "class": p["class"], "x": round(p["x"], 3), "y": round(p["y"], 3)} for p in placed]}
json.dump(layout, open(f"{out}/layout.json", "w"), indent=1)
print(json.dumps(layout))
sys.stdout.flush()
os._exit(0)
