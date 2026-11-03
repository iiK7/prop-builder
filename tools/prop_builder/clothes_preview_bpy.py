"""CodeWalker XML (ped .yft + component / prop .ydd) -> one .glb per drawable, for the clothes page's 3D preview.

Run with Blender's Python module (bpy, as glb_to_cwxml.py) by clothes_preview.py:
    python clothes_preview_bpy.py <job.json> <result.json>

job.json: {"sollumz": "<folder holding Sollumz/>", "skeleton": "<ped>.yft.xml" | null,
           "parts": [{"key": "uppr_000", "xml": ".../x^uppr_000_u.ydd.xml", "glb": ".../uppr_000.glb", "bone": null | "SKEL_Head" | "RB_L_ForeArmRoll,SKEL_L_Hand"}]}
Every part is imported on its own with the ped skeleton (Sollumz "external skeleton"), so all meshes come out in the
skeleton's rest pose (= bind pose) and line up without skinning. A prop ("bone" set) is moved to that bone's rest
transform, as the game anchors it. The .glb holds meshes with UVs and normals only: one plain material per shader,
named "m<n>"; result.json says which textures each material uses (the page loads them as PNG).
"""
import json
import os
import sys
import traceback

import bpy  # noqa: I001  (bpy must be imported before addon_utils)
import addon_utils

job_path, result_path = sys.argv[1:3]
job = json.load(open(job_path))
sys.path.insert(0, job["sollumz"])
bpy.ops.wm.read_factory_settings(use_empty=True)
addon_utils.enable("Sollumz", default_set=True)
from Sollumz.known_paths import data_directory_path  # noqa: E402

# Sollumz saves its name-table cache there on the first hash it has to look up; a fresh runner has no such folder
os.makedirs(data_directory_path(), exist_ok=True)

SAMPLERS = {"diffuse": "DiffuseSampler", "normal": "BumpSampler"}


def wipe():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.armatures, bpy.data.materials, bpy.data.images):
        for b in list(coll):
            coll.remove(b)


def tex_name(node):
    img = getattr(node, "image", None)
    if img is None:
        return None
    n = os.path.basename(img.filepath or "") or img.name
    for ext in (".dds", ".png"):
        if n.lower().endswith(ext):
            n = n[: -len(ext)]
    return n or None


def import_xml(path, skeleton):
    kw = dict(directory=os.path.dirname(path), files=[{"name": os.path.basename(path)}], use_custom_settings=True,
              split_by_group=False)
    if skeleton:
        kw.update(dwd_import_external_skeleton="SAVED", dwd_import_external_skeleton_saved_path=skeleton)
    before = set(bpy.data.objects)
    r = bpy.ops.sollumz.import_assets(**kw)
    if "FINISHED" not in r:
        raise RuntimeError(f"Sollumz could not import {os.path.basename(path)}")
    return [o for o in bpy.data.objects if o not in before]


def bone_matrix(names):
    """rest transform of the first of these bones ("A,B") the skeleton has: (matrix, bone name)"""
    for name in names.split(","):
        for o in bpy.data.objects:
            if o.type == "ARMATURE" and name in o.data.bones:
                return o.matrix_world @ o.data.bones[name].matrix_local, name
    return None, None


def export_part(part, skeleton):
    wipe()
    if part.get("bone") and skeleton:
        import_xml(skeleton, None)   # the ped skeleton, only to read the anchor bone
    objs = import_xml(part["xml"], None if part.get("bone") else skeleton)
    meshes = [o for o in objs if o.type == "MESH" and len(o.data.vertices) > 0]
    if not meshes:
        raise RuntimeError("no mesh in the drawable")
    anchor = bone = None
    if part.get("bone"):
        anchor, bone = bone_matrix(part["bone"])
        if anchor is not None:
            for o in meshes:
                o.matrix_world = anchor @ o.matrix_world
    mats, slots = {}, {}
    for o in meshes:
        for slot in o.material_slots:
            m = slot.material
            if m is None:
                continue
            if m.name not in slots:
                info = {}
                if m.node_tree:
                    for key, node_name in SAMPLERS.items():
                        node = m.node_tree.nodes.get(node_name)
                        t = tex_name(node) if node is not None else None
                        if t and t.lower() != "givemechecker":
                            info[key] = t
                    shader = getattr(m, "shader_properties", None)
                    info["shader"] = getattr(shader, "name", "") or m.get("sollumz_shader", "") or ""
                plain = bpy.data.materials.new(f"m{len(slots)}")
                plain.use_backface_culling = False
                slots[m.name] = plain
                mats[plain.name] = info
            slot.material = slots[m.name]
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    os.makedirs(os.path.dirname(part["glb"]), exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=part["glb"], export_format="GLB", use_selection=True, export_apply=False,
                              export_skins=False, export_animations=False, export_morph=False,
                              export_materials="EXPORT", export_image_format="NONE", export_vertex_color="NONE",
                              export_yup=True)
    verts = sum(len(o.data.vertices) for o in meshes)
    return {"materials": mats, "verts": verts, "anchored": bone}


results = {}
for part in job["parts"]:
    try:
        results[part["key"]] = export_part(part, job.get("skeleton"))
        print(f"OK {part['key']} {results[part['key']]['verts']} verts", flush=True)
    except Exception as e:  # one broken drawable must not stop the rest
        results[part["key"]] = {"error": str(e)[:300]}
        print(f"FAIL {part['key']}: {e}\n{traceback.format_exc()[-800:]}", flush=True)
json.dump(results, open(result_path, "w"), indent=1)
sys.stdout.flush()
# bpy keeps a thread alive at interpreter exit (about 100 s): leave right away
os._exit(0)
