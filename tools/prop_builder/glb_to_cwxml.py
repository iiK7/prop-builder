"""GLB -> Sollumz drawable -> CodeWalker XML (<name>.ydr.xml, <name>.ytyp.xml, textures as PNG).

Run with Blender's Python module (bpy) and Sollumz on sys.path:
    python glb_to_cwxml.py <model.glb> <out_dir> <prop_name> <sollumz_parent_dir> [height_m] [max_tris]
"""
import os
import sys

import bpy  # noqa: I001  (bpy must be imported before addon_utils)
import addon_utils
import mathutils

glb, outdir, name, addon_parent = sys.argv[1:5]
height = float(sys.argv[5]) if len(sys.argv) > 5 and float(sys.argv[5]) > 0 else 0.0
max_tris = int(sys.argv[6]) if len(sys.argv) > 6 else 20000
COLLISION_TRIS = 400

sys.path.insert(0, addon_parent)
bpy.ops.wm.read_factory_settings(use_empty=True)
addon_utils.enable("Sollumz", default_set=True)
from Sollumz.tools.meshhelper import get_uv_map_name, get_color_attr_name  # noqa: E402
from Sollumz.ybn.collision_materials import create_collision_material_from_index  # noqa: E402
from Sollumz.ydr.operators.materials import MaterialConverter  # noqa: E402

sc = bpy.context.scene


def select_only(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def tri_count(obj):
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def decimate_to(obj, target):
    tris = tri_count(obj)
    if tris <= target:
        return
    select_only(obj)
    mod = obj.modifiers.new("decimate", "DECIMATE")
    mod.ratio = max(0.01, target / tris)
    bpy.ops.object.modifier_apply(modifier=mod.name)


# --- import and merge into one mesh -------------------------------------------------
bpy.ops.import_scene.gltf(filepath=glb)
meshes = [o for o in sc.objects if o.type == "MESH"]
if not meshes:
    sys.exit("no mesh in the model")
bpy.ops.object.select_all(action="DESELECT")
for o in meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
if len(meshes) > 1:
    bpy.ops.object.join()
obj = bpy.context.view_layer.objects.active
for o in [o for o in sc.objects if o.type != "MESH"]:
    bpy.data.objects.remove(o, do_unlink=True)
obj.name = name


def bounds(o):
    co = [o.matrix_world @ v.co for v in o.data.vertices]
    mn = mathutils.Vector((min(v.x for v in co), min(v.y for v in co), min(v.z for v in co)))
    mx = mathutils.Vector((max(v.x for v in co), max(v.y for v in co), max(v.z for v in co)))
    return mn, mx


# real-world size: scale so the model is <height> metres tall
mn, mx = bounds(obj)
if height > 0 and mx.z - mn.z > 1e-6:
    s = height / (mx.z - mn.z)
    obj.data.transform(mathutils.Matrix.Scale(s, 4))
    mn, mx = bounds(obj)
# origin at the base centre so the prop sits on the ground when spawned
base = mathutils.Vector(((mn.x + mx.x) / 2, (mn.y + mx.y) / 2, mn.z))
obj.data.transform(mathutils.Matrix.Translation(-base))
obj.location = (0, 0, 0)
mn, mx = bounds(obj)

decimate_to(obj, max_tris)
mn, mx = bounds(obj)

# --- one material per base-colour image ----------------------------------------------
def img_of(m):
    if not (m and m.node_tree):
        return None
    for n in m.node_tree.nodes:
        if n.type == "BSDF_PRINCIPLED" and n.inputs["Base Color"].is_linked:
            return getattr(n.inputs["Base Color"].links[0].from_node, "image", None)
    return None


first, remap = {}, {}
for i, slot in enumerate(obj.material_slots):
    remap[i] = first.setdefault(img_of(slot.material) or slot.material, i)
for poly in obj.data.polygons:
    poly.material_index = remap.get(poly.material_index, 0)
select_only(obj)
bpy.ops.object.material_slot_remove_unused()
if not obj.material_slots:
    m = bpy.data.materials.new("plain")
    m.use_nodes = True
    obj.data.materials.append(m)

# Sollumz expects these attribute names
me = obj.data
if not me.uv_layers:
    me.uv_layers.new()
me.uv_layers[0].name = get_uv_map_name(0)
ca = me.color_attributes.new(get_color_attr_name(0), "BYTE_COLOR", "CORNER")
for d in ca.data:
    d.color = (1, 1, 1, 1)

# --- collision source: a light copy of the visual mesh --------------------------------
col_src = obj.copy()
col_src.data = obj.data.copy()
sc.collection.objects.link(col_src)
col_src.name = name + "_col"
decimate_to(col_src, COLLISION_TRIS)
col_src.data.materials.clear()

# --- drawable ------------------------------------------------------------------------
sc.auto_create_embedded_col = False
sc.center_drawable_to_selection = False
select_only(obj)
bpy.ops.sollumz.converttodrawable()
drawable = next(o for o in sc.objects if o.sollum_type == "sollumz_drawable")
model = next(o for o in sc.objects if o.sollum_type == "sollumz_drawable_model")
drawable.name = name

select_only(model)
# whole-prop options, also from material names: "_noshadow" casts no shadow (smoke, glows),
# "_far" stays drawn up to FAR_LOD m away instead of 150 (a smoke column seen across the map)
FAR_LOD = 1500.0
mat_names = [(sl.material.name if sl.material else "").lower() for sl in model.material_slots]
no_shadow = any("_noshadow" in n for n in mat_names)
lod_dist = FAR_LOD if any("_far" in n for n in mat_names) else 150.0
if no_shadow:
    model.data.drawable_model_properties.render_mask = 0xFF & ~(1 << 1)  # every view but the shadow
    print("no shadow")
# materials named with "_glow" get a self-lit shader (emissive, for fire and lights) and ones named
# with "_alpha" a see-through one that uses the texture's alpha; "_glow_alpha" both. Others: auto.
for slot in model.material_slots:
    m = slot.material
    low = (m.name if m else "").lower()
    glow, alpha = "_glow" in low, "_alpha" in low
    if not (glow or alpha):
        continue
    preset = ("emissivestrong_alpha.sps" if alpha else "emissivestrong.sps") if glow else "alpha.sps"
    try:
        new = MaterialConverter(model, m).convert(preset)
        mult = new.node_tree.nodes.get("emissiveMultiplier") if glow else None
        if mult is not None:
            mult.set(0, 4.0)
        print("material", m.name, "->", preset)
    except Exception as e:  # fall back to the auto shader below
        print("material", m.name, preset, "failed:", e)
bpy.ops.sollumz.autoconvertmaterials()

# embed diffuse textures (written as PNG; build_prop.sh converts them to DDS)
for i, m in enumerate(model.data.materials):
    n = m.node_tree.nodes.get("DiffuseSampler") if m.node_tree else None
    if n and n.image:
        tex = f"{name}_d{i}"
        n.image.name = tex
        n.image.filepath_raw = f"{outdir}/{tex}.png"
        n.image.file_format = "PNG"
        n.image.save()
        n.texture_properties.embedded = True

# collision composite parented to the drawable
sc.bound_child_type = "sollumz_bound_geometrybvh"
sc.create_seperate_composites = False
sc.center_composite_to_selection = False
select_only(col_src)
bpy.ops.sollumz.converttocomposite()
composite = next(o for o in sc.objects if o.sollum_type == "sollumz_bound_composite")
composite.parent = drawable
for b in [o for o in sc.objects if o.sollum_type in ("sollumz_bound_poly_triangle", "sollumz_bound_geometrybvh") and o.type == "MESH"]:
    b.data.materials.clear()
    b.data.materials.append(create_collision_material_from_index(0))

# --- export --------------------------------------------------------------------------
select_only(drawable)
res = bpy.ops.sollumz.export_assets(directory=outdir, direct_export=True, use_custom_settings=True,
                                    target_formats={"CWXML"}, target_versions={"GEN8"})
print("export:", res, "tris:", tri_count(model), "collision tris:", COLLISION_TRIS)

size = mx - mn
# flags 0, not 32 (Static), and physicsDictionary = its own name: that links the archetype to the
# collision inside the .ydr; without it a prop made by a script has no physics (walked through)
ytyp = f"""<?xml version="1.0" encoding="UTF-8"?>
<CMapTypes>
 <extensions/>
 <archetypes>
  <Item type="CBaseArchetypeDef">
   <lodDist value="{lod_dist:.1f}"/>
   <flags value="0"/>
   <specialAttribute value="0"/>
   <bbMin x="{mn.x:.4f}" y="{mn.y:.4f}" z="{mn.z:.4f}"/>
   <bbMax x="{mx.x:.4f}" y="{mx.y:.4f}" z="{mx.z:.4f}"/>
   <bsCentre x="0.0" y="0.0" z="{size.z / 2:.4f}"/>
   <bsRadius value="{size.length / 2:.4f}"/>
   <hdTextureDist value="20.0"/>
   <name>{name}</name>
   <textureDictionary>{name}</textureDictionary>
   <clipDictionary/>
   <drawableDictionary/>
   <physicsDictionary>{name}</physicsDictionary>
   <assetType>ASSET_TYPE_DRAWABLE</assetType>
   <assetName>{name}</assetName>
   <extensions/>
  </Item>
 </archetypes>
 <name>{name}</name>
 <dependencies/>
 <compositeEntityTypes/>
</CMapTypes>"""
with open(f"{outdir}/{name}.ytyp.xml", "w") as fp:
    fp.write(ytyp)
print(f"size: {size.x:.2f} x {size.y:.2f} x {size.z:.2f} m")
sys.stdout.flush()
os._exit(0)  # bpy as a module hangs on interpreter shutdown
