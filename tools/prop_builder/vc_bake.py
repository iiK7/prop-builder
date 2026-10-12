"""Vertex-coloured mesh -> textured GLB (bakes the colours into a 1024 image), so GTA shaders get a texture.
    python vc_bake.py <in.glb> <out.glb>   (bpy)
"""
import os
import sys

import bpy

src, out = sys.argv[1], sys.argv[2]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
obj = next(o for o in bpy.context.scene.objects if o.type == "MESH")
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
me = obj.data
if not me.color_attributes:
    sys.exit("mesh has no vertex colours")
colour_name = me.color_attributes[0].name

bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.normals_make_consistent(inside=False)
bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.004)
bpy.ops.object.mode_set(mode="OBJECT")

img = bpy.data.images.new("baked", 1024, 1024)
mat = bpy.data.materials.new("baked"); mat.use_nodes = True
nt = mat.node_tree; nt.nodes.clear()
attr = nt.nodes.new("ShaderNodeVertexColor"); attr.layer_name = colour_name
emit = nt.nodes.new("ShaderNodeEmission")
outn = nt.nodes.new("ShaderNodeOutputMaterial")
tex = nt.nodes.new("ShaderNodeTexImage"); tex.image = img
nt.links.new(attr.outputs["Color"], emit.inputs["Color"])
nt.links.new(emit.outputs["Emission"], outn.inputs["Surface"])
nt.nodes.active = tex
me.materials.clear(); me.materials.append(mat)

sc = bpy.context.scene
sc.render.engine = "CYCLES"; sc.cycles.device = "CPU"; sc.cycles.samples = 1
sc.render.bake.margin = 4
bpy.ops.object.bake(type="EMIT")
img.pack()

# final material: plain Principled with the baked image
nt.nodes.clear()
bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled"); bsdf.inputs["Roughness"].default_value = 0.7
outn = nt.nodes.new("ShaderNodeOutputMaterial")
tex = nt.nodes.new("ShaderNodeTexImage"); tex.image = img
nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
nt.links.new(bsdf.outputs["BSDF"], outn.inputs["Surface"])
me.color_attributes.remove(me.color_attributes[colour_name])
bpy.ops.export_scene.gltf(filepath=out, export_format="GLB")
print("baked glb:", out)
sys.stdout.flush()
os._exit(0)
