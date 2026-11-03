"""Clothes designer 3D preview: a ped pack -> one .glb per drawable + PNG textures + parts.json, for the page's viewer.
    python clothes_preview.py <pack.zip | folder> <out dir> [--overlay built.zip] [--target <ymt name>]
                              [--cwconv PATH] [--bpy-python PYTHON] [--sollumz DIR] [--max-tex 1024] [--max-parts 300]

1. cwconv res-to-xml: the ped body (<ped>.yft, holds the skeleton) and every component / prop drawable (.ydd) to
   CodeWalker XML.
2. clothes_preview_bpy.py (Blender's bpy + Sollumz, the same pinned versions as the props workflow): each drawable
   imported with the ped skeleton and exported as glb/<comp>_<nnn>.glb in the skeleton's rest pose, so the parts line up
   when the page loads them together; props are moved to their anchor bone. No skinning (the preview has no animation).
3. cwconv ytd-export: every <comp>_diff_<nnn>_<letter> texture (and the drawables' embedded normal maps) as
   tex/<texture name>.png, at most --max-tex pixels on a side.
--overlay: a built design (clothes_variant.py's zip): its files win over the pack's, so the new letter is in the list.

parts.json:
  {"name", "ped", "skeleton": bool, "components": {"uppr": [{"drawable": 0, "glb": "glb/uppr_000.glb",
   "textures": ["a", "b"], "tex": {"a": "tex/uppr_diff_000_a_uni.png"}, "materials": {"m0": {"diffuse": ..., "normal": ...}},
   "verts": 6102}]}, "props": {"p_head": [... + "bone"]}, "textures": {"<texture name>": "tex/<name>.png"},
   "skipped": [{"file", "reason"}], "design": {...} when --design is given (+ "pieces" for a new garment)}
Exit 3 with error.json on a refusal (same codes as clothes_variant.py).
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import clothes_variant as cv  # noqa: E402

DRW_RE = re.compile(r"^(?P<comp>(?:p_)?[a-z]+)_(?P<d>\d{3})(?:_(?P<s>[a-z]+))?$", re.I)
# where the game puts a prop: the anchor bone's rest transform (the prop drawable is modelled in that bone's space);
# the first bone of the list that the skeleton has. Wrists: the forearm roll bone (checked on a real pack: a watch lands
# at 0.96 of forearm -> hand on it, on the fingers with SKEL_L_Hand).
ANCHOR_BONES = {"p_head": "SKEL_Head", "p_eyes": "SKEL_Head", "p_ears": "SKEL_Head", "p_mouth": "SKEL_Head",
                "p_lhand": "PH_L_Hand,SKEL_L_Hand", "p_rhand": "PH_R_Hand,SKEL_R_Hand",
                "p_lwrist": "RB_L_ForeArmRoll,SKEL_L_Hand", "p_rwrist": "RB_R_ForeArmRoll,SKEL_R_Hand",
                "p_hip": "SKEL_Pelvis", "p_lfoot": "SKEL_L_Foot", "p_rfoot": "SKEL_R_Foot"}


def log(*a):
    print(*a, flush=True)


def stem_of(path, ext):
    b = os.path.basename(path)
    return b[: -len(ext)] if b.lower().endswith(ext) else b


def save_png(img, path, max_side):
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    img.save(path, "PNG", optimize=True)


def scan(pack, files, target):
    """(ped stem, yft path or None, props name, allowed prefixes for components, for props)"""
    ymts = {stem_of(f, ".ymt").lower(): f for f in files if f.lower().endswith(".ymt")}
    yfts = {stem_of(f, ".yft").lower(): f for f in files if f.lower().endswith(".yft") and not f.lower().endswith("_hi.yft")}
    if target:
        stem = target.lower().removesuffix(".ymt")
    else:
        both = sorted(set(ymts) & set(yfts))
        stem = both[0] if both else (sorted(yfts)[0] if yfts else (sorted(ymts)[0] if ymts else None))
    if stem is None:
        raise cv.Refuse("no_ymt", "ما لقيت بيد (yft) ولا ملف ymt بالباك.", "no .yft or .ymt in the pack")
    props_name = cv.props_names(pack, files).get(stem)
    comp_pre = {stem}
    prop_pre = {stem, cv.prop_prefix(stem)} | ({props_name} if props_name else set())
    return stem, yfts.get(stem), props_name, comp_pre, prop_pre


def run(src, out_dir, overlay=None, target=None, bpy_python=None, sollumz=None, max_tex=1024, max_parts=300, name=None):
    os.makedirs(out_dir, exist_ok=True)
    work = tempfile.mkdtemp(prefix="clothes_preview_")
    pack = os.path.join(work, "pack")
    files = cv.unpack(src, pack)
    if overlay:
        # the built design: copied over the pack by file name, so its new .ytd / changed .ymt are what the preview reads
        ov = os.path.join(work, "overlay")
        by_base = {os.path.basename(f).lower(): f for f in files}
        for f in cv.unpack(overlay, ov):
            dest = by_base.get(os.path.basename(f).lower(), "_design/" + f)
            os.makedirs(os.path.dirname(os.path.join(pack, dest)), exist_ok=True)
            shutil.copyfile(os.path.join(ov, f), os.path.join(pack, dest))
            if dest not in files:
                files.append(dest)
    stem, yft, props_name, comp_pre, prop_pre = scan(pack, files, target)
    log(f"ped {stem} yft={yft} props={props_name}")
    xml_dir = os.path.join(work, "xml")
    os.makedirs(xml_dir, exist_ok=True)
    glb_dir, tex_dir = os.path.join(out_dir, "glb"), os.path.join(out_dir, "tex")
    shutil.rmtree(glb_dir, ignore_errors=True)
    shutil.rmtree(tex_dir, ignore_errors=True)
    os.makedirs(glb_dir)
    os.makedirs(tex_dir)
    skipped = []

    skeleton = None
    if yft:
        try:
            cv.cw("res-to-xml", os.path.join(pack, yft), xml_dir)
            skeleton = os.path.join(xml_dir, stem_of(yft, ".yft") + ".yft.xml")
        except RuntimeError as e:
            skipped.append({"file": os.path.basename(yft), "reason": str(e)[-200:]})

    # drawables of this ped (components) and of its PropsName (props)
    drawables = {}
    for f in files:
        if not f.lower().endswith(".ydd"):
            continue
        pre, nm = cv.local_name(f[:-4] + ".ytd")
        m = DRW_RE.match(nm)
        if not m:
            continue
        comp, d = m.group("comp").lower(), int(m.group("d"))
        if comp in cv.COMPONENTS and pre in comp_pre or comp in cv.PROPS and pre in prop_pre:
            drawables[(comp, d)] = f
    if not drawables:
        raise cv.Refuse("no_drawable", "ما لقيت ولا قطعة (ydd) لهالبيد بالباك.", f"no .ydd files for {stem}")
    keys = sorted(drawables, key=lambda k: (k[0] in cv.PROPS, (cv.COMPONENTS + cv.PROPS).index(k[0]), k[1]))
    if len(keys) > max_parts:
        skipped += [{"file": os.path.basename(drawables[k]), "reason": f"more than {max_parts} drawables"} for k in keys[max_parts:]]
        keys = keys[:max_parts]

    # textures: one <comp>_diff_<nnn>_<letter>[_race].ytd per letter (the game uses the dictionary of that file name,
    # whatever the texture inside is called), or every texture inside one <ped>.ytd
    letters, textures, spare = {}, {}, {}

    def keep(img_path, tname):
        rel = f"tex/{tname}.png"
        save_png(Image.open(img_path), os.path.join(out_dir, rel), max_tex)
        textures[tname] = rel
        return rel

    for f in sorted(files):
        if not f.lower().endswith(".ytd"):
            continue
        pre, nm = cv.local_name(f)
        m = cv.TEX_RE.match(nm)
        whole = not m and (nm.lower() in (stem, cv.prop_prefix(stem)) or nm.lower() == pre and pre in comp_pre | prop_pre)
        if not m and not whole:
            continue
        if m and ((m.group("comp").lower(), int(m.group("d"))) not in drawables
                  or pre not in (prop_pre if m.group("comp").lower() in cv.PROPS else comp_pre)):
            continue
        out = os.path.join(work, "ytd", re.sub(r"[^\w.-]", "_", os.path.basename(f)))
        try:
            texinfo = cv.ytd_export(os.path.join(pack, f), out)
        except cv.Refuse as e:
            skipped.append({"file": os.path.basename(f), "reason": e.en})
            continue
        for t in texinfo:
            # CodeWalker can't decode BC7: Pillow reads the exported .dds instead
            if not t.get("png") and t["tex"].get("dds") and os.path.exists(os.path.join(out, t["tex"]["dds"])):
                try:
                    Image.open(os.path.join(out, t["tex"]["dds"])).load()
                    t["png"] = t["tex"]["dds"]
                except Exception:
                    pass
        readable = [t for t in texinfo if t.get("png")]
        if m:
            # the diffuse of this file: the texture named like a diffuse, else the one marked DIFFUSE, else the first
            pick = next((t for t in readable if cv.TEX_RE.match(t["tex"]["name"] or "")), None) \
                or next((t for t in readable if t["tex"]["usage"].upper() == "DIFFUSE"), None) \
                or next((t for t in readable if not re.search(r"(_n|normal|spec|_s)$", (t["tex"]["name"] or "").lower())), None)
            if pick is None:
                skipped.append({"file": os.path.basename(f), "reason": "no readable diffuse texture"})
                continue
            key = (m.group("comp").lower(), int(m.group("d")))
            letters.setdefault(key, {})[m.group("l").lower()] = keep(os.path.join(out, pick["png"]), nm.lower())
            for t in readable:
                spare.setdefault((t["tex"]["name"] or "").lower(), os.path.join(out, t["png"]))
        else:
            for t in readable:
                tname = (t["tex"]["name"] or "").lower()
                tm = cv.TEX_RE.match(tname)
                if tm and (tm.group("comp").lower(), int(tm.group("d"))) in drawables and tname not in textures:
                    key = (tm.group("comp").lower(), int(tm.group("d")))
                    letters.setdefault(key, {}).setdefault(tm.group("l").lower(), keep(os.path.join(out, t["png"]), tname))
                else:
                    spare.setdefault(tname, os.path.join(out, t["png"]))

    # drawables -> XML -> glb
    parts = []
    for comp, d in keys:
        f = drawables[(comp, d)]
        try:
            cv.cw("res-to-xml", os.path.join(pack, f), xml_dir)
        except RuntimeError as e:
            skipped.append({"file": os.path.basename(f), "reason": str(e)[-200:]})
            continue
        parts.append({"key": f"{comp}_{d:03d}", "comp": comp, "drawable": d,
                      "xml": os.path.join(xml_dir, os.path.basename(f) + ".xml"),
                      "glb": os.path.join(glb_dir, f"{comp}_{d:03d}.glb"),
                      "bone": ANCHOR_BONES.get(comp) if comp in cv.PROPS and skeleton else None,
                      "embedded": os.path.join(xml_dir, stem_of(f, ".ydd"))})
    job = {"sollumz": sollumz or os.path.join(HERE, ".deps"), "skeleton": skeleton,
           "parts": [{k: p[k] for k in ("key", "xml", "glb", "bone")} for p in parts]}
    job_path, res_path = os.path.join(work, "job.json"), os.path.join(work, "result.json")
    json.dump(job, open(job_path, "w"))
    r = subprocess.run([bpy_python or sys.executable, os.path.join(HERE, "clothes_preview_bpy.py"), job_path, res_path],
                       capture_output=True, text=True, timeout=3600)
    for line in (r.stdout or "").splitlines():
        if line.startswith(("OK ", "FAIL ", "Traceback")) or "Error" in line:
            log("  " + line)
    if not os.path.exists(res_path):
        raise RuntimeError(f"the Blender step stopped ({r.returncode}): {((r.stdout or '') + (r.stderr or ''))[-800:]}")
    results = json.load(open(res_path))

    comps, props = {}, {}
    for p in parts:
        res = results.get(p["key"], {"error": "not converted"})
        if "error" in res or not os.path.exists(p["glb"]):
            skipped.append({"file": os.path.basename(drawables[(p["comp"], p["drawable"])]), "reason": res.get("error", "no glb")})
            continue
        # embedded textures the materials use (normal maps, skin) that no .ytd gave
        for info in res["materials"].values():
            for kind in ("diffuse", "normal"):
                t = (info.get(kind) or "").lower()
                if not t or t in textures:
                    continue
                if t in spare:
                    keep(spare[t], t)
                    continue
                dds = os.path.join(p["embedded"], t + ".dds")
                if os.path.exists(dds):
                    try:
                        keep(dds, t)
                    except Exception as e:  # a format Pillow can't decode: the page shows the mesh without it
                        log(f"  {t}.dds not decoded: {e}")
        tex = dict(sorted(letters.get((p["comp"], p["drawable"]), {}).items()))
        entry = {"drawable": p["drawable"], "glb": f"glb/{p['key']}.glb", "textures": list(tex), "tex": tex,
                 "materials": res["materials"], "verts": res["verts"]}
        if p["bone"]:
            entry["bone"] = res.get("anchored") or None   # the bone it sits on (None: not in this skeleton, at the origin)
        (props if p["comp"] in cv.PROPS else comps).setdefault(p["comp"], []).append(entry)
    shutil.rmtree(work, ignore_errors=True)
    if not comps and not props:
        raise cv.Refuse("no_drawable", "ما قدرت أحوّل ولا قطعة لمعاينة ثلاثية الأبعاد.", "no drawable could be converted",
                        skipped=skipped[:50])
    out = {"name": name, "ped": stem, "props_name": props_name, "skeleton": skeleton is not None,
           "components": comps, "props": props, "textures": textures, "skipped": skipped,
           "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pack")
    ap.add_argument("out")
    ap.add_argument("--overlay")
    ap.add_argument("--target")
    ap.add_argument("--design", help="info.json of the built design: copied into parts.json (component, drawable, letter)")
    ap.add_argument("--cwconv")
    ap.add_argument("--bpy-python")
    ap.add_argument("--sollumz", help="folder that holds Sollumz/")
    ap.add_argument("--name")
    ap.add_argument("--max-tex", type=int, default=1024)
    ap.add_argument("--max-parts", type=int, default=300)
    a = ap.parse_args()
    if a.cwconv:
        cv.CW_PATH = a.cwconv
    os.makedirs(a.out, exist_ok=True)
    try:
        parts = run(a.pack, a.out, a.overlay, a.target, a.bpy_python, a.sollumz, a.max_tex, a.max_parts, a.name)
    except cv.Refuse as e:
        log(f"::error::{e.en}")
        json.dump({"code": e.code, "message": e.ar, "detail": e.en, **e.extra}, open(os.path.join(a.out, "error.json"), "w"),
                  ensure_ascii=False)
        return 3
    if a.design and os.path.exists(a.design):
        d = json.load(open(a.design))
        if d.get("status") == "built":
            spec = d.get("spec") or {}
            parts["design"] = {"component": d.get("component", spec.get("component")),
                               "drawable": d.get("drawable", spec.get("drawable")),
                               "letter": d.get("letter"), "texture": d.get("texture")}
            if d.get("pieces"):   # a new garment: every new drawable (the piece + the lowr that hides the legs)
                parts["design"]["pieces"] = [{"component": x["component"], "drawable": x["drawable"], "letter": x["letter"]}
                                             for x in d["pieces"]]
                parts["design"]["template"] = d.get("template")
    with open(os.path.join(a.out, "parts.json"), "w") as f:
        json.dump(parts, f, ensure_ascii=False, indent=1)
    n = sum(len(v) for v in parts["components"].values()) + sum(len(v) for v in parts["props"].values())
    log(f"OK preview {parts['ped']}: {n} drawables, {len(parts['textures'])} textures, {len(parts['skipped'])} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
