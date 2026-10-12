"""Turn an assembled room (room_build.py output) into one FiveM resource:
<out>/<name>/stream/*.ydr|ytyp, fxmanifest.lua, client.lua (/spawnroom <name>), and <out>/<name>.zip
    python room_package.py <room_dir> <name> <out_dir>
Env: PY (bpy python), DEPS (Sollumz + cwconv)
"""
import json
import os
import shutil
import subprocess
import sys

room, name, out = sys.argv[1:4]
here = os.path.dirname(os.path.abspath(__file__))
PY, DEPS = os.environ.get("PY", "python3"), os.environ["DEPS"]
cw = f"{DEPS}/cwconv/cwconv"
layout = json.load(open(f"{room}/layout.json"))
res = f"{out}/{name}"
shutil.rmtree(res, ignore_errors=True)
os.makedirs(f"{res}/stream")


def build(glb, prop):
    work = f"{out}/.work/{prop}"
    shutil.rmtree(work, ignore_errors=True); os.makedirs(work)
    log = subprocess.run([PY, "-u", f"{here}/glb_to_cwxml.py", glb, work, prop, DEPS, "0"], capture_output=True, text=True)
    if "export: {'FINISHED'}" not in log.stdout:
        print(log.stdout[-2000:], log.stderr[-2000:]); raise SystemExit(f"blender failed for {prop}")
    for f in os.listdir(work):
        if f.endswith(".png"):
            subprocess.run([PY, f"{here}/png_to_dds.py", f"{work}/{f}", f"{work}/{f[:-4]}.dds"], check=True, capture_output=True)
    subprocess.run([cw, f"{work}/{prop}.ydr.xml", f"{res}/stream/{prop}.ydr"], check=True)
    subprocess.run([cw, f"{work}/{prop}.ytyp.xml", f"{res}/stream/{prop}.ytyp"], check=True)
    print("built", prop)


props = [(f"{room}/shell.glb", f"{name}_shell")]
props += [(f"{room}/objects/{o['id']}.glb", f"{name}_{o['id']}") for o in layout["objects"]]
for glb, prop in props:
    build(glb, prop)

ytyps = "\n".join(f"data_file 'DLC_ITYP_REQUEST' 'stream/{p}.ytyp'" for _, p in props)
open(f"{res}/fxmanifest.lua", "w").write(f"""fx_version 'cerulean'
game 'gta5'
lua54 'yes'

description 'Room {name} (built from a photo by tools/prop_builder)'

{ytyps}

client_script 'client.lua'
""")
objs = "\n".join(f"        {{ model = `{name}_{o['id']}`, x = {o['x']}, y = {o['y']} }}, -- {o['class']}" for o in layout["objects"])
lua = open(f"{here}/template/room_client.lua").read()
lua = lua.replace("__NAME__", name).replace("__DEPTH__", str(layout["depth"])).replace("__FLOOR__", str(layout["floor"])).replace("__OBJECTS__", objs)
open(f"{res}/client.lua", "w").write(lua)
zp = shutil.make_archive(f"{out}/{name}", "zip", out, name)
print("done:", zp)
