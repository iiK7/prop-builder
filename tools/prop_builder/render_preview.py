"""Render a small preview PNG of a GLB (CPU Cycles, works headless).
    python render_preview.py <model.glb> <out.png>
"""
import math
import os
import sys

import bpy
import mathutils

glb, out = sys.argv[1:3]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)
sc = bpy.context.scene
meshes = [o for o in sc.objects if o.type == "MESH"]
pts = [o.matrix_world @ mathutils.Vector(c) for o in meshes for c in o.bound_box]
mn = mathutils.Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
mx = mathutils.Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
centre, radius = (mn + mx) / 2, max((mx - mn).length / 2, 1e-3)

cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 50
cam = bpy.data.objects.new("cam", cam_data)
sc.collection.objects.link(cam)
d = radius / math.tan(cam_data.angle / 2) * 1.15
direction = mathutils.Vector((1.0, -1.4, 0.8)).normalized()
cam.location = centre + direction * d
cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
sc.camera = cam

for loc, energy in (((3, -3, 5), 4.0), ((-4, 2, 3), 1.5)):
    light = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    light.data.energy = energy
    light.location = loc
    light.rotation_euler = (mathutils.Vector((0, 0, 0)) - mathutils.Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    sc.collection.objects.link(light)

world = bpy.data.worlds.new("w")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.16, 0.18, 0.21, 1)
sc.world = world

sc.render.engine = "CYCLES"
sc.cycles.device = "CPU"
sc.cycles.samples = 24
sc.render.resolution_x = sc.render.resolution_y = 512
sc.render.film_transparent = False
sc.render.filepath = out
bpy.ops.render.render(write_still=True)
print("preview:", out)
sys.stdout.flush()
os._exit(0)
