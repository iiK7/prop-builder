"""Clothes designer: a NEW texture (colour / design) for an EXISTING clothing piece.
    python clothes_variant.py <pack.zip | folder> <spec.json> <out dir> [--cwconv PATH] [--name NAME]
                              [--bpy-python PYTHON] [--sollumz DIR]   (garment mode)

Works for freemode MP packs (stream/<ped>_<collection>^jbib_diff_000_a_uni.ytd + <ped>_<collection>.ymt, props as
<ped>_p_<collection>^p_head_diff_000_a.ytd) and addon peds (<ped>.ymt with uppr_diff_000_a_uni.ytd files, in a <ped>/
folder or as <ped>^..., or every texture inside one <ped>.ytd).

A new design is the first letter of that drawable without a file (_b ... _z): a copy of the base texture's .ytd with the
diffuse recoloured and renamed. The .ymt (CPedVariationInfo) is left as it is when it already lists that letter (real packs
often list more aTexData items than files); otherwise items are added up to it and numAvailTex grows by as many.
Props of an addon ped follow its PropsName (meta file). Everything else in the pack is copied as it is.

spec.json:
  {"target": "<ymt name without .ymt>" (needed when the pack has several),
   "component": "jbib" | 11 | "p_head", "drawable": 0, "base": "a",
   "mode": "tint" | "logo" | "prompt" | "analyze",
   "color": "#rrggbb", "strength": 0..1, "keep_skin": true|false,           tint (and the colour of a prompt pattern)
   "logo": {"png": "<base64>", "u": .5, "v": .5, "scale": .3, "rotation": 0, "opacity": 1, "shade": true},
   "prompt": {"text": "...", "engine": "pollinations"|"flux", "tiles": 2, "strength": .85},
   "trimmed": true when the site sent only the files of this piece (the result is then a patch, not a whole resource)}

NEW PIECE ("mode": "garment"): a garment template (garment_templates.py: ثوب، كندورة، عباية، روب، تيشيرت، قميص، هودي،
جاكيت، صدرية، بنطلون، شورت، تنورة، شماغ وعقال، بشت، كاب) fitted to the ped's own body by garment_bpy.py (Blender bpy + Sollumz;
--bpy-python / --sollumz) and added as the NEXT drawable of its component (a lowr that hides the legs for the long ones, a
head prop p_head when the props .ymt can take it), with its .ytd and the .ymt entries (drawable + compInfos, or prop +
aAnchors). Needs the ped's skeleton (<ped>.yft) and base drawables (uppr_000, lowr_000; hand / head / hair / feet _000 help).
  {"mode": "garment", "template": "thobe", "params": {...}, "slot": "uppr" (optional), "target": ...,
   "design": "none" | "tint" | "logo" | "prompt" (+ their fields as above: on the generated texture, never on skin)}

Out dir: <name>.zip, preview_before.png, preview_after.png, info.json; on a refusal error.json (exit code 3).
Env: CWCONV (converter path), TEXT_TO_IMAGE (script for the prompt mode, default text_to_image.py next to this file).
"""
import argparse
import base64
import datetime
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
LETTERS = "abcdefghijklmnopqrstuvwxyz"
COMPONENTS = ["head", "berd", "hair", "uppr", "lowr", "hand", "feet", "teef", "accs", "task", "decl", "jbib"]
PROPS = ["p_head", "p_eyes", "p_ears", "p_mouth", "p_lhand", "p_rhand", "p_lwrist", "p_rwrist", "p_hip", "p_lfoot",
         "p_rfoot"]
FREEMODE = ("mp_m_freemode_01", "mp_f_freemode_01")
TEX_RE = re.compile(r"^(?P<comp>(?:p_)?[a-z]+)_diff_(?P<d>\d{3})_(?P<l>[a-z])(?:_(?P<race>[a-z]+))?$", re.I)
MAX_UNPACKED = 600 * 1024 * 1024
MAX_ZIP_OUT = 90 * 1024 * 1024   # GitHub refuses files over 100 MB


class Refuse(Exception):
    """a clean refusal: code + Arabic text for the site + English for the log"""

    def __init__(self, code, ar, en, **extra):
        super().__init__(en)
        self.code, self.ar, self.en, self.extra = code, ar, en, extra


def log(*a):
    print(*a, flush=True)


# ---------------- converter ----------------
def cw(*args, ok=(0,)):
    exe = os.environ.get("CWCONV") or CW_PATH
    r = subprocess.run([exe, *map(str, args)], capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode not in ok:
        raise RuntimeError(f"cwconv {args[0]} failed ({r.returncode}): {out.strip()[-600:]}")
    return r.returncode, out


def ymt_to_xml(path, xml_path):
    try:
        _, out = cw("ymt-to-xml", path, xml_path)
    except RuntimeError as e:
        raise Refuse("unreadable", f"ما قدرت أقرا {os.path.basename(path)} (مشفّر أو خربان).",
                     f"could not read {os.path.basename(path)}: {e}")
    fmt = re.search(r"^FORMAT (\w+)", out, re.M)
    return fmt.group(1) if fmt else "RSC"


def ytd_export(path, folder):
    try:
        cw("ytd-export", path, folder)
    except RuntimeError as e:
        raise Refuse("unreadable", f"ما قدرت أقرا {os.path.basename(path)} (مشفّر أو خربان).",
                     f"could not read {os.path.basename(path)}: {e}")
    with open(os.path.join(folder, "textures.json")) as f:
        return json.load(f)["textures"]


# ---------------- pack ----------------
def unpack(src, dest):
    """the pack as a folder; refuses escrow, odd paths and zip bombs"""
    if os.path.isdir(src):
        shutil.copytree(src, dest, dirs_exist_ok=True)
    else:
        try:
            z = zipfile.ZipFile(src)
        except zipfile.BadZipFile:
            raise Refuse("bad_zip", "الملف مو zip سليم.", "the upload is not a valid zip")
        total = 0
        for i in z.infolist():
            name = i.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/") or re.match(r"^[a-zA-Z]:", name):
                raise Refuse("bad_zip", "الـzip فيه مسارات غريبة.", f"unsafe path in the zip: {name}")
            total += i.file_size
            if total > MAX_UNPACKED:
                raise Refuse("too_big", "الباك كبير مرة (أكثر من 600MB بعد الفك). ارفع ملفات القطعة بس.",
                             "the pack unpacks to more than 600 MB")
            if i.flag_bits & 0x1:
                raise Refuse("encrypted", "الـzip عليه كلمة سر.", "the zip is password protected")
        z.extractall(dest)
    files = []
    for root, _, names in os.walk(dest):
        for n in names:
            files.append(os.path.relpath(os.path.join(root, n), dest).replace("\\", "/"))
    if any(f.lower().endswith(".fxap") for f in files):
        raise Refuse("encrypted", "هالباك مشفّر (escrow / .fxap): ما ينفتح ولا ينعدل. لازم النسخة الأصلية بدون تشفير.",
                     "escrow-encrypted resource (.fxap)")
    return sorted(files)


def resource_root(files):
    """folder of the resource (fxmanifest, or the parent of stream/), relative; None = the files are loose"""
    for f in files:
        if os.path.basename(f).lower() in ("fxmanifest.lua", "__resource.lua"):
            return os.path.dirname(f)
    for f in files:
        parts = f.split("/")
        if "stream" in [p.lower() for p in parts[:-1]]:
            return "/".join(parts[:[p.lower() for p in parts].index("stream")])
    return None


def local_name(path):
    """(prefix, name without .ytd): 'a/b/X^jbib_diff_000_a_uni.ytd' -> ('X', ...), 'pw_roscoe/uppr_...' -> ('pw_roscoe', ...)"""
    base = os.path.basename(path)
    stem = base[:-4] if base.lower().endswith(".ytd") else base
    if "^" in stem:
        pre, stem = stem.rsplit("^", 1)
        return pre.lower(), stem
    return os.path.basename(os.path.dirname(path)).lower(), stem


def prop_prefix(stem):
    for ped in FREEMODE:
        if stem.startswith(ped + "_") and not stem.startswith(ped + "_p_"):
            return ped + "_p_" + stem[len(ped) + 1:]
    return stem + "_p"


# ---------------- ymt (CodeWalker meta XML) ----------------
def items(node):
    return [] if node is None else node.findall("Item")


def ival(node, tag, default=0):
    e = node.find(tag)
    if e is None:
        return default
    v = e.get("value")
    if v is None:
        v = (e.text or "").strip()
    try:
        return int(v)
    except ValueError:
        return default


def set_val(node, tag, v):
    node.find(tag).set("value", str(v))


class Ymt:
    def __init__(self, xml_path):
        self.path = xml_path
        self.tree = ET.parse(xml_path)
        self.root = self.tree.getroot()
        if self.root.tag != "CPedVariationInfo":
            raise ValueError(f"root is {self.root.tag}, not CPedVariationInfo")
        txt = (self.root.findtext("availComp") or "").split()
        self.avail = [int(x) for x in txt] if len(txt) == 12 else [255] * 12
        self.comps = items(self.root.find("aComponentData3"))
        pi = self.root.find("propInfo")
        self.props = items(pi.find("aPropMetaData")) if pi is not None else []

    def component(self, idx):
        slot = self.avail[idx] if 0 <= idx < 12 else 255
        return self.comps[slot] if slot != 255 and slot < len(self.comps) else None

    def drawables(self, idx):
        c = self.component(idx)
        return items(c.find("aDrawblData3")) if c is not None else []

    def prop(self, anchor, drawable):
        for p in self.props:
            if ival(p, "anchorId", -1) == anchor and ival(p, "propId", -1) == drawable:
                return p
        return None

    def summary(self):
        out = []
        for i, name in enumerate(COMPONENTS):
            ds = self.drawables(i)
            if ds:
                c = self.component(i)
                out.append({"component": name, "numAvailTex": ival(c, "numAvailTex"),
                            "drawables": [len(items(d.find("aTexData"))) for d in ds]})
        props = {}
        for p in self.props:
            a = ival(p, "anchorId", -1)
            if 0 <= a < len(PROPS):
                props.setdefault(PROPS[a], {})[ival(p, "propId")] = len(items(p.find("texData")))
        for k, v in props.items():
            out.append({"component": k, "drawables": [v.get(i, 0) for i in range(max(v) + 1)]})
        return out

    def save(self, path):
        ET.indent(self.tree, " ")
        self.tree.write(path, encoding="UTF-8", xml_declaration=True)


def textures_of(ymt, comp, drawable):
    """(the item holding the texture list, the list node, its items)"""
    if comp in PROPS:
        p = ymt.prop(PROPS.index(comp), drawable)
        if p is None:
            return None, None, []
        node = p.find("texData")
        return p, node, items(node)
    ds = ymt.drawables(COMPONENTS.index(comp))
    if drawable >= len(ds):
        return None, None, []
    node = ds[drawable].find("aTexData")
    return ds[drawable], node, items(node)


RACE_IDS = {"uni": 0, "whi": 1, "bla": 2, "chi": 3, "lat": 4, "ara": 5, "kor": 8, "pak": 10}


def fit_slots(ymt, comp, drawable, new_index, base_index, race):
    """make the .ymt hold texture `new_index` of this drawable. Real packs often list MORE aTexData items than there
    are files (the game skips the missing ones): an existing slot is used as it is; only when the new texture is past
    the list are items added (copies of the base one), and numAvailTex grows by the number added.
    The new slot's texId (the race part of the name: 0 uni, 1 whi, 2 bla ...) follows the base texture's file.
    Returns (items added, texId changed, notes)."""
    notes = []
    owner, node, texs = textures_of(ymt, comp, drawable)
    n = len(texs)
    base_item = texs[min(base_index, n - 1)]
    added = 0
    while len(items(node)) <= new_index:
        new = ET.fromstring(ET.tostring(base_item))
        if comp in PROPS:
            # props: texId counts the textures (0, 1, 2 ...) in the packs seen; keep the pack's own way otherwise
            ids = [ival(t, "texId") for t in items(node)]
            if ids == list(range(len(ids))):
                set_val(new, "texId", len(ids))
        node.append(new)
        added += 1
    changed = False
    if comp not in PROPS:
        want = RACE_IDS.get((race or "").lower(), ival(base_item, "texId"))
        slot = items(node)[new_index]
        if ival(slot, "texId") != want:
            notes.append(f"slot {LETTERS[new_index]}: texId {ival(slot, 'texId')} -> {want} (the race of the base file)")
            set_val(slot, "texId", want)
            changed = True
        c = ymt.component(COMPONENTS.index(comp))
        before = ival(c, "numAvailTex")
        total = sum(len(items(d.find("aTexData"))) for d in ymt.drawables(COMPONENTS.index(comp)))
        if before != total - added:
            notes.append(f"numAvailTex is {before} while the component lists {total - added} textures (left as it was"
                         + (f", +{added})" if added else ")"))
        if added:
            set_val(c, "numAvailTex", before + added)
    return added, changed, notes


# ---------------- textures ----------------
FOURCC = {"D3DFMT_DXT1": ("DXT1", 8), "D3DFMT_DXT3": ("DXT3", 16), "D3DFMT_DXT5": ("DXT5", 16)}
RAW = ("D3DFMT_A8R8G8B8", "D3DFMT_X8R8G8B8", "D3DFMT_A8B8G8R8")


def load_dds(path):
    im = Image.open(path)
    im.load()
    return im.convert("RGBA")


def encode_dds(img, fmt, levels, path):
    """DDS with `levels` mips in `fmt` (DXT1/3/5 or A8R8G8B8); returns the format really written"""
    w, h = img.size
    if fmt in FOURCC:
        pf, block = FOURCC[fmt]
    elif fmt in RAW:
        pf, block = None, 0
    else:
        raise ValueError(fmt)
    data, mip = [], img
    for i in range(levels):
        mw, mh = max(1, w >> i), max(1, h >> i)
        m = img if i == 0 else img.resize((mw, mh), Image.LANCZOS)
        if pf:
            buf = io.BytesIO()
            m.save(buf, "DDS", pixel_format=pf)
            body = buf.getvalue()[128:]
            want = max(1, (mw + 3) // 4) * max(1, (mh + 3) // 4) * block
            if len(body) != want:
                raise RuntimeError(f"mip {i}: {len(body)} bytes, expected {want}")
        else:
            a = np.asarray(m.convert("RGBA"), dtype=np.uint8)
            body = a[..., [2, 1, 0, 3]].tobytes()   # B G R A in memory = A8R8G8B8
        data.append(body)
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | (0x20000 if levels > 1 else 0) | (0x80000 if pf else 0x8)
    pitch = len(data[0]) if pf else w * 4
    caps = 0x1000 | (0x400008 if levels > 1 else 0)
    if pf:
        pixfmt = struct.pack("<II4sIIIII", 32, 0x4, pf.encode(), 0, 0, 0, 0, 0)
    else:
        pixfmt = struct.pack("<IIIIIIII", 32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    head = b"DDS " + struct.pack("<IIIIIII", 124, flags, h, w, pitch, 0, levels) + b"\0" * 44 + pixfmt + \
        struct.pack("<IIIII", caps, 0, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(head + b"".join(data))
    return fmt if pf else "D3DFMT_A8R8G8B8"


def lum(rgb):
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def skin_mask(rgb):
    """YCbCr skin rule (rgb 0..1) -> bool"""
    r, g, b = rgb[..., 0] * 255, rgb[..., 1] * 255, rgb[..., 2] * 255
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return (cb >= 77) & (cb <= 127) & (cr >= 133) & (cr <= 173) & (y > 40)


def work_mask(rgba, keep_skin):
    """0..1 where the design may change the texture: visible pixels, minus skin when asked; soft edge"""
    rgb, a = rgba[..., :3], rgba[..., 3]
    m = (a > 8 / 255).astype(np.float32)
    if keep_skin:
        m *= (~skin_mask(rgb)).astype(np.float32)
    im = Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))
    return np.asarray(im, dtype=np.float32) / 255


def parse_color(s):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(s or "").strip())
    if not m:
        raise Refuse("bad_spec", "اللون لازم يكون مثل ‎#aa3322.", f"bad colour {s!r}")
    v = int(m.group(1), 16)
    return np.array([(v >> 16) & 255, (v >> 8) & 255, v & 255], dtype=np.float32) / 255


def recolour(rgba, colour_map, mask, strength):
    """colour_map (h, w, 3) or (3,): new base colour per pixel, the original shading and detail kept"""
    rgb = rgba[..., :3]
    L = lum(rgb)
    weights = mask > 0.5
    Lm = float(L[weights].mean()) if weights.any() else 0.5
    Lm = max(Lm, 0.04)
    ratio = np.clip(L / Lm, 0, 3)[..., None]
    target = np.broadcast_to(colour_map, rgb.shape)
    out = target * ratio
    # dark colours would flatten the detail (black * ratio = black): put some of the original contrast back
    dark = (1 - lum(target))[..., None]
    out = out + (L - Lm)[..., None] * 0.35 * dark
    out = np.clip(out, 0, 1)
    k = (mask * float(np.clip(strength, 0, 1)))[..., None]
    res = rgba.copy()
    res[..., :3] = rgb * (1 - k) + out * k
    return res


def apply_tint(rgba, spec):
    mask = work_mask(rgba, spec.get("keep_skin", False))
    return recolour(rgba, parse_color(spec.get("color")), mask, spec.get("strength", 1.0))


def apply_logo(rgba, spec):
    lg = spec.get("logo") or {}
    try:
        logo = Image.open(io.BytesIO(base64.b64decode(lg.get("png") or ""))).convert("RGBA")
    except Exception:
        raise Refuse("bad_spec", "صورة الشعار ما انقرت (لازم PNG).", "the logo is not a readable image")
    H, W = rgba.shape[:2]
    scale = float(np.clip(float(lg.get("scale", 0.3)), 0.01, 4))
    lw = max(1, int(round(W * scale)))
    lh = max(1, int(round(logo.height * lw / logo.width)))
    logo = logo.resize((lw, lh), Image.LANCZOS)
    rot = float(lg.get("rotation", 0)) % 360
    if rot:
        logo = logo.rotate(-rot, resample=Image.BICUBIC, expand=True)   # the site turns clockwise
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cx, cy = float(lg.get("u", 0.5)) * W, float(lg.get("v", 0.5)) * H
    layer.paste(logo, (int(round(cx - logo.width / 2)), int(round(cy - logo.height / 2))), logo)
    L = np.asarray(layer, dtype=np.float32) / 255
    a = L[..., 3:4] * float(np.clip(float(lg.get("opacity", 1)), 0, 1)) * (rgba[..., 3:4] > 8 / 255)
    col = L[..., :3]
    if lg.get("shade", True):
        base = lum(rgba[..., :3])
        under = a[..., 0] > 0.05
        Lm = float(base[under].mean()) if under.any() else 0.5
        col = np.clip(col * np.clip(base / max(Lm, 0.04), 0.55, 1.3)[..., None], 0, 1)
    res = rgba.copy()
    res[..., :3] = rgba[..., :3] * (1 - a) + col * a
    return res


def apply_prompt(rgba, spec, work):
    p = spec.get("prompt") or {}
    text = str(p.get("text") or "").strip()
    if len(text) < 3:
        raise Refuse("bad_spec", "اكتب وصف التصميم.", "empty design description")
    engine = p.get("engine") or "pollinations"
    if engine not in ("pollinations", "flux"):
        raise Refuse("bad_spec", "مصدر الصورة لازم pollinations أو flux.", f"unknown engine {engine}")
    script = os.environ.get("TEXT_TO_IMAGE") or os.path.join(HERE, "text_to_image.py")
    png, info = os.path.join(work, "pattern.png"), os.path.join(work, "pattern.json")
    r = subprocess.run([sys.executable, "-u", script, text[:200], png, info, engine, "pattern"])
    if r.returncode != 0 or not os.path.exists(png):
        raise Refuse("prompt_failed", "ما قدرت أسوي صورة من الوصف (جرّب مرة ثانية أو مصدر ثاني).",
                     "description to image failed")
    H, W = rgba.shape[:2]
    tiles = int(np.clip(int(p.get("tiles", 2)), 1, 16))
    pat = Image.open(png).convert("RGB")
    side = max(4, int(math.ceil(max(W, H) / tiles)))
    pat = pat.resize((side, side), Image.LANCZOS)
    big = Image.new("RGB", (side * tiles, side * tiles))
    for y in range(tiles):
        for x in range(tiles):
            big.paste(pat, (x * side, y * side))
    pmap = np.asarray(big.crop((0, 0, W, H)), dtype=np.float32) / 255
    mask = work_mask(rgba, spec.get("keep_skin", False))
    # the pattern is the new colour, the garment's own shading stays: normalise the pattern around its mean so
    # the folds of the original keep their light and dark
    return recolour(rgba, pmap, mask, p.get("strength", 0.85)), json.load(open(info))


def to_float(img):
    return np.asarray(img.convert("RGBA"), dtype=np.float32) / 255


def to_image(a):
    return Image.fromarray(np.clip(np.round(a * 255), 0, 255).astype(np.uint8), "RGBA")


def preview(img, path):
    im = img.copy()
    im.thumbnail((1024, 1024), Image.LANCZOS)
    bg = Image.new("RGBA", im.size, (40, 46, 52, 255))
    Image.alpha_composite(bg, im).convert("RGB").save(path, "PNG")


# ---------------- manifest ----------------
def manifest_for(root_dir):
    metas, shop, peds = [], [], []
    for dp, _, names in os.walk(root_dir):
        for n in names:
            if n.lower().endswith(".meta"):
                rel = os.path.relpath(os.path.join(dp, n), root_dir).replace("\\", "/")
                metas.append(rel)
                head = open(os.path.join(dp, n), "rb").read(4096).decode("utf-8", "ignore")
                if "ShopPedApparel" in head:
                    shop.append(rel)
                elif "CPedModelInfo__InitDataList" in head:
                    peds.append(rel)
    lines = ["fx_version 'cerulean'", "game 'gta5'", "", "-- made by the clothes designer (prop-builder)", ""]
    if metas:
        lines.append("files {")
        lines += [f"    '{m}'," for m in sorted(metas)]
        lines += ["}", ""]
    lines += [f"data_file 'SHOP_PED_APPAREL_META_FILE' '{m}'" for m in sorted(shop)]
    lines += [f"data_file 'PED_METADATA_FILE' '{m}'" for m in sorted(peds)]
    return "\n".join(lines).rstrip() + "\n", shop, peds


# ---------------- main ----------------
def props_names(pack, files):
    """{ped name: PropsName} from the peds .meta files (an addon ped's props can belong to another name)"""
    out = {}
    for f in files:
        if not f.lower().endswith((".meta", ".xml")):
            continue
        try:
            text = open(os.path.join(pack, f), "rb").read(2_000_000).decode("utf-8", "ignore")
        except OSError:
            continue
        if "CPedModelInfo__InitDataList" not in text:
            continue
        for item in re.findall(r"<Item\b.*?</Item>", text, re.S):
            nm, pn = re.search(r"<Name>\s*([^<\s]+)", item), re.search(r"<PropsName>\s*([^<\s]*)", item)
            if nm and pn and pn.group(1) and pn.group(1).lower() != "null":
                out[nm.group(1).lower()] = pn.group(1).lower()
    return out


def find_base_files(files, stem, comp, drawable, props_name=None):
    """the .ytd files of this drawable, grouped by prefix: {prefix: {letter: (path, race)}}"""
    groups = {}
    for f in files:
        if not f.lower().endswith(".ytd"):
            continue
        pre, name = local_name(f)
        m = TEX_RE.match(name)
        if not m or m.group("comp").lower() != comp or int(m.group("d")) != drawable:
            continue
        groups.setdefault(pre, {})[m.group("l").lower()] = (f, m.group("race"))
    want = {stem.lower(), prop_prefix(stem.lower())} | ({props_name} if props_name else set()) if comp in PROPS else {stem.lower()}
    for pre in groups:
        if pre in want:
            return pre, groups[pre]
    if len(groups) == 1:
        pre = next(iter(groups))
        return pre, groups[pre]
    if groups:
        raise Refuse("mismatch", "لقيت ملفات لهالقطعة من أكثر من باك، وما أعرف أي واحد يطابق الـymt.",
                     f"several packs have this piece: {sorted(groups)}", candidates=sorted(groups))
    return None, {}


def run(src, spec, out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    work = tempfile.mkdtemp(prefix="clothes_")
    pack = os.path.join(work, "pack")
    files = unpack(src, pack)
    warnings = []

    mode = spec.get("mode", "tint")
    if mode == "garment":
        return run_garment(pack, files, work, spec, out_dir, name)
    if mode not in ("tint", "logo", "prompt", "analyze"):
        raise Refuse("bad_spec", "نوع التصميم غلط.", f"unknown mode {mode}")

    ymts = [f for f in files if f.lower().endswith(".ymt")]
    if not ymts:
        raise Refuse("no_ymt", "ما فيه ملف .ymt بالباك. هذي غالباً قطعة من اللعبة الأصلية: لازم تتحول لباك مضاف "
                     "(addon) فيه ymt خاص فيه قبل ما ينضاف لها تصميم.",
                     "no .ymt in the pack: base-game items must be relocated as an addon pack first")
    stem_of = {f: os.path.basename(f)[:-4] for f in ymts}
    target = str(spec.get("target") or "").strip()
    if target.lower().endswith(".ymt"):
        target = target[:-4]
    picked = [f for f in ymts if stem_of[f].lower() == target.lower()] if target else ymts
    if not picked:
        raise Refuse("mismatch", f"ما لقيت {target}.ymt بالباك.", f"{target}.ymt not in the pack",
                     candidates=sorted(stem_of.values()))
    if len(picked) > 1:
        raise Refuse("pick_ymt", "الباك فيه أكثر من ymt: اختر واحد.", "several .ymt files: give target",
                     candidates=sorted(stem_of.values()))
    ymt_rel, stem = picked[0], stem_of[picked[0]]
    ymt_xml = os.path.join(work, "ymt.xml")
    fmt = ymt_to_xml(os.path.join(pack, ymt_rel), ymt_xml)
    if fmt == "RBF":
        raise Refuse("unreadable", "الـymt بصيغة قديمة (RBF) ما تنعدل هنا.", "RBF ymt is not supported")
    try:
        ymt = Ymt(ymt_xml)
    except Exception as e:
        raise Refuse("unreadable", "الـymt مو ملف ملابس (CPedVariationInfo).", str(e))
    kind = "mp" if stem.lower().startswith(FREEMODE) else "ped"
    summary = ymt.summary()

    comp = spec.get("component")
    if isinstance(comp, int) or (isinstance(comp, str) and comp.isdigit()):
        comp = COMPONENTS[int(comp)] if 0 <= int(comp) < 12 else None
    comp = str(comp or "").lower()
    if comp not in COMPONENTS and comp not in PROPS:
        raise Refuse("bad_spec", "اختر القطعة (مثل jbib أو uppr أو p_head).", f"unknown component {comp!r}")
    try:
        drawable = int(spec.get("drawable", -1))
    except (TypeError, ValueError):
        drawable = -1
    if not 0 <= drawable <= 999:
        raise Refuse("bad_spec", "رقم القطعة (drawable) غلط.", "bad drawable number")
    props_name = props_names(pack, files).get(stem.lower()) if comp in PROPS else None
    if props_name and props_name != stem.lower():
        other = [f for f in ymts if stem_of[f].lower() == props_name]
        if not other:
            raise Refuse("props_ymt", f"إكسسوارات (props) هالبيد تابعة لـ {props_name} (PropsName بملف الميتا)، وملف "
                         f"{props_name}.ymt مو بالباك، فما أقدر أضيف لها تكستر. القطع العادية (uppr، lowr…) تشتغل عادي.",
                         f"props of {stem} belong to PropsName {props_name}; {props_name}.ymt is not in the pack")
        ymt_rel, stem = other[0], stem_of[other[0]]
        fmt = ymt_to_xml(os.path.join(pack, ymt_rel), ymt_xml)
        ymt = Ymt(ymt_xml)
        summary = ymt.summary()
        warnings.append(f"props follow PropsName {props_name}: {props_name}.ymt is the one changed")
    owner, tnode, texs = textures_of(ymt, comp, drawable)
    if comp not in PROPS and ymt.component(COMPONENTS.index(comp)) is None:
        raise Refuse("no_component", f"الـymt ما فيه {comp} أصلاً.", f"{comp} is not in {stem}.ymt", summary=summary)
    if owner is None or tnode is None:
        raise Refuse("no_drawable", f"{comp} رقم {drawable:03d} مو موجود بالـymt.",
                     f"{comp} {drawable:03d} is not in {stem}.ymt", summary=summary)
    n = len(texs)
    if n == 0:
        raise Refuse("mismatch", "هالقطعة ما لها ولا تكستر بالـymt.", "the drawable has no textures in the ymt")

    base_letter = str(spec.get("base") or "a").lower()[:1] or "a"
    if base_letter not in LETTERS:
        raise Refuse("bad_spec", "التكستر الأساسي لازم حرف من a لين z.", f"bad base letter {base_letter}")
    base_index = LETTERS.index(base_letter)

    prefix, by_letter = find_base_files(files, stem, comp, drawable, props_name)
    layout = "files" if by_letter else None
    container = None
    if not by_letter:
        # every texture inside one dictionary (<ped>.ytd): look for <comp>_diff_<ddd>_<letter> in it
        for f in files:
            if f.lower().endswith(".ytd") and os.path.basename(f)[:-4].lower() in (stem.lower(), prop_prefix(stem.lower())):
                texinfo = ytd_export(os.path.join(pack, f), os.path.join(work, "dict_probe"))
                hits = {}
                for t in texinfo:
                    m = TEX_RE.match(t["tex"]["name"] or "")
                    if m and m.group("comp").lower() == comp and int(m.group("d")) == drawable:
                        hits[m.group("l").lower()] = (t["tex"]["name"], m.group("race"))
                if hits:
                    layout, container, by_letter = "dict", f, hits
                    break
    if not by_letter:
        raise Refuse("no_diffuse", f"ما لقيت ملف التكستر ({comp}_diff_{drawable:03d}_a…ytd) لهالقطعة بالباك.",
                     f"no {comp}_diff_{drawable:03d}_*.ytd for {stem}", summary=summary)

    letters = "".join(sorted(by_letter))
    if base_letter not in by_letter:
        raise Refuse("bad_spec", f"التكستر الأساسي _{base_letter} ما له ملف (الموجود: {', '.join(letters)}).",
                     f"base texture {base_letter} has no file")
    # the new texture takes the first letter without a file: a hole between the files, else the next one.
    # The game loads <comp>_diff_<ddd>_<letter> for every aTexData item; items without a file are skipped.
    free = next((i for i in range(26) if LETTERS[i] not in by_letter), None)
    new_letter = LETTERS[free] if free is not None else None
    gaps = [l for l in LETTERS[:LETTERS.index(letters[-1])] if l not in by_letter]
    beyond = [l for l in letters if LETTERS.index(l) >= n]
    if gaps:
        warnings.append(f"letters without a file: {', '.join(gaps)}")
    if beyond:
        warnings.append(f"files past the {n} textures of the .ymt (the game never loads them until the .ymt lists them): "
                        f"{', '.join(beyond)}")
    slot = None if free is None else ("existing" if free < n else "added")

    if layout == "files":
        stem_ydd = [f for f in files if f.lower().endswith(".ydd") and local_name(f)[0] == prefix
                    and re.match(rf"^{re.escape(comp)}_{drawable:03d}_[a-z](_\d+)?\.ydd$", os.path.basename(f).split("^")[-1], re.I)]
        if not stem_ydd:
            warnings.append(f"no {comp}_{drawable:03d}_*.ydd next to the textures (model not in the upload)")

    # ---- the base texture ----
    base_dir = os.path.join(work, "base")
    if layout == "files":
        base_path = by_letter[base_letter][0]
        texinfo = ytd_export(os.path.join(pack, base_path), base_dir)
    else:
        base_path = container
        texinfo = ytd_export(os.path.join(pack, container), base_dir)
    base_tex_name = by_letter[base_letter][0] if layout == "dict" else None
    diff = None
    for t in texinfo:
        nm = t["tex"]["name"] or ""
        if layout == "dict":
            if nm == base_tex_name:
                diff = t
        elif TEX_RE.match(nm) and TEX_RE.match(nm).group("l").lower() == base_letter:
            diff = t
    if diff is None and layout == "files" and len(texinfo) == 1:
        diff = texinfo[0]
    if diff is None:
        raise Refuse("no_diffuse", "ملف التكستر ما فيه صورة diffuse.", f"no diffuse texture in {base_path}")
    tex = diff["tex"]
    before = load_dds(os.path.join(base_dir, tex["dds"]))
    preview(before, os.path.join(out_dir, "preview_before.png"))

    info = {"name": name, "mode": mode, "kind": kind, "target": stem, "ymt": ymt_rel, "component": comp,
            "drawable": drawable, "textures": n, "letters": letters, "next": new_letter, "slot": slot, "gaps": gaps,
            "layout": layout,
            "base": {"letter": base_letter, "file": base_path, "texture": tex["name"], "width": tex["width"],
                     "height": tex["height"], "format": tex["format"], "mips": tex["mips"]},
            "summary": summary, "warnings": warnings}
    if mode == "analyze":
        info["status"] = "analyzed"
        return info
    if new_letter is None:
        raise Refuse("full", "هالقطعة فيها 26 تكستر (a لين z): ما فيه مكان لحرف جديد.",
                     "26 textures already (a..z)")

    # ---- the design ----
    rgba = to_float(before)
    extra = {}
    if mode == "tint":
        out = apply_tint(rgba, spec)
    elif mode == "logo":
        out = apply_logo(rgba, spec)
    else:
        out, extra = apply_prompt(rgba, spec, work)
    out[..., 3] = rgba[..., 3]               # the cut-out stays as it was
    after = to_image(out)
    preview(after, os.path.join(out_dir, "preview_after.png"))

    # ---- the new .ytd ----
    fmt_in, mips = tex["format"], int(tex["mips"])
    fmt_out = fmt_in
    if fmt_in not in FOURCC and fmt_in not in RAW:
        has_alpha = bool((np.asarray(after)[..., 3] < 250).any())
        if fmt_in == "D3DFMT_BC7":
            fmt_out = "D3DFMT_DXT5" if has_alpha else "D3DFMT_DXT1"
            warnings.append(f"the base texture is BC7, which this tool can't write: saved as {fmt_out[7:]}")
        else:
            raise Refuse("format", f"صيغة التكستر {fmt_in} ما تنعدل هنا.", f"texture format {fmt_in} not supported")
    old_name = tex["name"]
    race = by_letter[base_letter][1]
    m = TEX_RE.match(old_name)
    if m:
        new_name = f"{m.group('comp')}_diff_{m.group('d')}_{new_letter}" + (f"_{m.group('race')}" if m.group("race") else "")
    else:
        new_name = f"{comp}_diff_{drawable:03d}_{new_letter}" + (f"_{race}" if race else "")
    # files: the copy keeps the .dds name (the texture is renamed in the build); dict: one more .dds next to the rest
    dds_out = os.path.join(base_dir, (new_name + ".dds") if layout == "dict" else tex["dds"])
    written = encode_dds(after, fmt_out, mips, dds_out)

    xml_path = os.path.join(base_dir, "textures.xml")
    tx = ET.parse(xml_path)
    renames = []
    if layout == "dict":
        # one more texture in the same dictionary: copy the base item, new name and a new .dds
        src_item = next(i for i in tx.getroot().findall("Item") if i.findtext("Name") == old_name)
        new_item = ET.fromstring(ET.tostring(src_item))
        new_item.find("Name").text = new_name
        new_item.find("FileName").text = new_name + ".dds"
        tx.getroot().append(new_item)
        tx.write(xml_path, encoding="UTF-8", xml_declaration=True)
        out_rel = container
    else:
        # a copy of the base file: every texture named after the base letter gets the new letter
        for it in tx.getroot().findall("Item"):
            nm = it.findtext("Name") or ""
            mm = TEX_RE.match(nm)
            if nm == old_name:
                renames.append((nm, new_name))
            elif mm and mm.group("l").lower() == base_letter and int(mm.group("d")) == drawable:
                renames.append((nm, nm[:mm.start("l")] + new_letter + nm[mm.end("l"):]))
        bstem = os.path.basename(base_path)[:-4]
        pre_part, _, bname = bstem.rpartition("^")
        bm = TEX_RE.match(bname)
        file_stem = (bname[:bm.start("l")] + new_letter + bname[bm.end("l"):]) if bm else new_name
        out_rel = os.path.join(os.path.dirname(base_path), (pre_part + "^" if pre_part else "") + file_stem + ".ytd").replace("\\", "/")
        if out_rel in files:
            raise Refuse("mismatch", f"{os.path.basename(out_rel)} موجود من قبل بس الـymt ما يحسبه.",
                         f"{out_rel} already exists")
    new_ytd = os.path.join(work, "new.ytd")
    args = ["ytd-build", base_dir, new_ytd]
    for a, b in renames:
        args += ["--rename", f"{a}={b}"]
    if written == fmt_in:
        args.append("--strict")
    cw(*args)
    check = ytd_export(new_ytd, os.path.join(work, "check"))
    got = {t["tex"]["name"]: t["tex"] for t in check}
    if new_name not in got:
        raise RuntimeError(f"the new texture {new_name} is not in the built .ytd: {sorted(got)}")
    g = got[new_name]
    if (g["width"], g["height"], g["mips"]) != (tex["width"], tex["height"], mips) or g["format"] != written:
        raise RuntimeError(f"built texture differs: {g} vs {tex}")
    if layout == "dict" and len(got) != len(texinfo) + 1:
        raise RuntimeError("the dictionary lost a texture")

    # ---- the .ymt ----
    added, texid_changed, notes = fit_slots(ymt, comp, drawable, free, base_index, race)
    warnings += notes
    ymt_changed = bool(added or texid_changed)
    new_ymt = os.path.join(work, "new.ymt")
    if ymt_changed:
        ymt.save(ymt_xml)
        cw("xml-to-ymt", ymt_xml, new_ymt, *(["--pso"] if fmt == "PSO" else []))
    else:
        shutil.copy(os.path.join(pack, ymt_rel), new_ymt)
    back_xml = os.path.join(work, "back.xml")
    ymt_to_xml(new_ymt, back_xml)
    back = Ymt(back_xml)
    after_n = len(textures_of(back, comp, drawable)[2])
    if after_n != n + added or after_n <= free:
        raise RuntimeError("the .ymt does not list the new texture")
    if comp not in PROPS:
        want = ival(ymt.component(COMPONENTS.index(comp)), "numAvailTex")
        if ival(back.component(COMPONENTS.index(comp)), "numAvailTex") != want:
            raise RuntimeError("numAvailTex did not survive the rebuild")

    # ---- the resource ----
    root_rel = resource_root(files)
    res_name = os.path.basename(root_rel) if root_rel else ""
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", res_name or "") or res_name.lower() == "stream":
        res_name = name
    stage = os.path.join(work, "out", res_name)
    changed = []
    trimmed = bool(spec.get("trimmed"))
    if root_rel is None:
        # loose stream files: a resource around them
        os.makedirs(os.path.join(stage, "stream"))
        for f in files:
            dst = os.path.join(stage, f if f.lower().endswith(".meta") else os.path.join("stream", f))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(os.path.join(pack, f), dst)
        rel = lambda f: (f if f.lower().endswith(".meta") else "stream/" + f)
    else:
        shutil.copytree(os.path.join(pack, root_rel) if root_rel else pack, stage)
        rel = lambda f: os.path.relpath(f, root_rel).replace("\\", "/") if root_rel else f
    if ymt_changed:
        shutil.copy(new_ymt, os.path.join(stage, rel(ymt_rel)))
        changed.append(rel(ymt_rel))
    dst = os.path.join(stage, rel(out_rel))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy(new_ytd, dst)
    changed.append(rel(out_rel))
    manifest = os.path.join(stage, "fxmanifest.lua")
    if not os.path.exists(manifest) and not os.path.exists(os.path.join(stage, "__resource.lua")):
        text, shop, peds = manifest_for(stage)
        open(manifest, "w").write(text)
        changed.append("fxmanifest.lua")
        if kind == "mp" and not shop:
            warnings.append("no ShopPedApparel .meta in the pack: the manifest has no SHOP_PED_APPAREL_META_FILE")
    else:
        mf = open(manifest if os.path.exists(manifest) else os.path.join(stage, "__resource.lua"), errors="ignore").read()
        _, shop, _ = manifest_for(stage)
        for s in shop:
            if os.path.basename(s) not in mf:
                warnings.append(f"{s} is not named in the pack's fxmanifest")

    zip_path = os.path.join(out_dir, f"{name}.zip")

    def write_zip(only):
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for dp, _, names in os.walk(stage):
                for nm in sorted(names):
                    full = os.path.join(dp, nm)
                    r = os.path.relpath(full, stage).replace("\\", "/")
                    if only is None or r in only:
                        z.write(full, f"{res_name}/{r}")

    patch = trimmed
    write_zip(set(changed) if patch else None)
    if not patch and os.path.getsize(zip_path) > MAX_ZIP_OUT:
        patch = True
        write_zip(set(changed))
        warnings.append("the whole resource is over 90 MB: the zip has only the new and changed files")
    shutil.rmtree(work, ignore_errors=True)

    info.update({"status": "built", "letter": new_letter, "texture": new_name, "resource": res_name,
                 "files": changed, "patch": patch, "format": written, "mips": mips,
                 "ymt_textures": {"before": n, "after": n + added}, "ymt_changed": ymt_changed, "zip": f"{name}.zip",
                 "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")})
    if comp not in PROPS:
        info["numAvailTex"] = ival(back.component(COMPONENTS.index(comp)), "numAvailTex")
    if mode == "tint":
        info["color"] = spec.get("color")
    if extra:
        info["description"] = extra
    return info



# ---------------- garment mode: a NEW drawable from a template (garment_templates.py + garment_bpy.py) ----------------
BODY_PARTS = ("uppr", "lowr", "hand", "head", "hair", "feet")
DRW_FILE_RE = re.compile(r"^(?P<comp>(?:p_)?[a-z]+)_(?P<d>\d{3})(?:_(?P<s>[a-z]+))?\.ydd$", re.I)
BPY_PYTHON = None
SOLLUMZ_DIR = None


def drawable_files(files):
    """{(prefix, comp, drawable): path} of every .ydd"""
    out = {}
    for f in files:
        if not f.lower().endswith(".ydd"):
            continue
        pre, _ = local_name(f[:-4] + ".ytd")
        m = DRW_FILE_RE.match(os.path.basename(f).split("^")[-1])
        if m:
            out.setdefault((pre, m.group("comp").lower(), int(m.group("d"))), f)
    return out


def pick_ymt(pack, files, spec):
    ymts = [f for f in files if f.lower().endswith(".ymt")]
    if not ymts:
        raise Refuse("no_ymt", "ما فيه ملف .ymt بالباك: القطعة الجديدة تنضاف لـymt البيد (لازم باك بيد مضاف أو باك ملابس فيه ymt).",
                     "no .ymt in the pack")
    stem_of = {f: os.path.basename(f)[:-4] for f in ymts}
    target = str(spec.get("target") or "").strip()
    if target.lower().endswith(".ymt"):
        target = target[:-4]
    picked = [f for f in ymts if stem_of[f].lower() == target.lower()] if target else ymts
    if not picked:
        raise Refuse("mismatch", f"ما لقيت {target}.ymt بالباك.", f"{target}.ymt not in the pack",
                     candidates=sorted(stem_of.values()))
    if len(picked) > 1:
        # several: the one that has a ped .yft with the same name
        yfts = {os.path.basename(f)[:-4].lower() for f in files if f.lower().endswith(".yft")}
        both = [f for f in picked if stem_of[f].lower() in yfts]
        if len(both) != 1:
            raise Refuse("pick_ymt", "الباك فيه أكثر من ymt: اختر واحد.", "several .ymt files: give target",
                         candidates=sorted(stem_of.values()))
        picked = both
    return picked[0], stem_of[picked[0]], ymts, stem_of


def skin_colour(pack, files, prefixes, work):
    """the ped's skin tone (median of the skin pixels of its hand / head texture), for legs under shorts / skirts"""
    for part in ("hand", "head", "uppr"):
        for f in sorted(files):
            if not f.lower().endswith(".ytd"):
                continue
            pre, nm = local_name(f)
            m = TEX_RE.match(nm)
            if not m or pre not in prefixes or m.group("comp").lower() != part or int(m.group("d")) != 0:
                continue
            try:
                tex = ytd_export(os.path.join(pack, f), os.path.join(work, "skin_" + part))
                t = next(x for x in tex if TEX_RE.match(x["tex"]["name"] or ""))
                a = to_float(load_dds(os.path.join(work, "skin_" + part, t["tex"]["dds"])))
                sk = skin_mask(a[..., :3]) & (a[..., 3] > 0.5)
                if sk.sum() > 200:
                    return [int(x) for x in np.median(a[..., :3][sk], 0) * 255]
            except Exception:  # an unreadable texture: try the next one
                continue
    return [150, 110, 85]


def ymt_add_drawable(ymt, comp_idx, new_idx):
    """one more drawable (one texture, letter a, uni) in component comp_idx + its compInfos entry"""
    c = ymt.component(comp_idx)
    node = c.find("aDrawblData3")
    ds = items(node)
    if len(ds) != new_idx:
        raise RuntimeError(f"component {comp_idx} has {len(ds)} drawables, the new one would be {new_idx}")
    nd = ET.fromstring(ET.tostring(ds[0]))
    set_val(nd, "propMask", 1)
    if nd.find("numAlternatives") is not None:
        set_val(nd, "numAlternatives", 0)
    td = nd.find("aTexData")
    for x in items(td)[1:]:
        td.remove(x)
    set_val(items(td)[0], "texId", 0)
    cd = nd.find("clothData")
    if cd is not None and cd.find("ownsCloth") is not None:
        cd.find("ownsCloth").set("value", "false")
    node.append(nd)
    set_val(c, "numAvailTex", ival(c, "numAvailTex") + 1)
    infos = ymt.root.find("compInfos")
    added_info = False
    if infos is not None and items(infos):
        same = [i for i, x in enumerate(items(infos)) if ival(x, "pedXml_compIdx", -1) == comp_idx]
        if same:
            ni = ET.fromstring(ET.tostring(items(infos)[same[-1]]))
            set_val(ni, "pedXml_drawblIdx", new_idx)
            infos.insert(same[-1] + 1, ni)
            added_info = True
    return added_info


ANCHOR_NAMES = {0: "ANCHOR_HEAD", 1: "ANCHOR_EYES", 2: "ANCHOR_EARS", 3: "ANCHOR_MOUTH", 4: "ANCHOR_LEFT_HAND",
                5: "ANCHOR_RIGHT_HAND", 6: "ANCHOR_LEFT_WRIST", 7: "ANCHOR_RIGHT_WRIST", 8: "ANCHOR_HIP",
                9: "ANCHOR_LEFT_FOOT", 10: "ANCHOR_RIGHT_FOOT"}
PROP_ITEM = """<Item><audioId>none</audioId><expressionMods>0 0 0 0 0</expressionMods><texData itemType="CPedPropTexData">
<Item><inclusions>0</inclusions><exclusions>0</exclusions><texId value="0" /><inclusionId value="0" />
<exclusionId value="0" /><distribution value="255" /></Item></texData><renderFlags /><propFlags value="0" />
<flags value="0" /><anchorId value="0" /><propId value="0" /><stickyness value="0" /></Item>"""


def ymt_next_prop(ymt, anchor):
    ids = [ival(p, "propId") for p in ymt.props if ival(p, "anchorId", -1) == anchor]
    return max(ids) + 1 if ids else 0


def ymt_add_prop(ymt, anchor, new_id):
    """one more prop (one texture) on this anchor: aPropMetaData item + the anchor's count in aAnchors"""
    root = ymt.root
    pi = root.find("propInfo")
    if pi is None:
        pi = ET.SubElement(root, "propInfo")
        ET.SubElement(pi, "numAvailProps", value="0")
        ET.SubElement(pi, "aPropMetaData", itemType="CPedPropMetaData")
        ET.SubElement(pi, "aAnchors", itemType="CAnchorProps")
    meta = pi.find("aPropMetaData")
    if meta is None:
        meta = ET.SubElement(pi, "aPropMetaData", itemType="CPedPropMetaData")
    props = items(meta)
    same = [i for i, p in enumerate(props) if ival(p, "anchorId", -1) == anchor]
    new = ET.fromstring(ET.tostring(props[same[-1]])) if same else ET.fromstring(PROP_ITEM)
    td = new.find("texData")
    for x in items(td)[1:]:
        td.remove(x)
    set_val(items(td)[0], "texId", 0)
    rf = new.find("renderFlags")
    if rf is not None:
        rf.text = None
    for tag, v in (("propFlags", 0), ("flags", 0), ("anchorId", anchor), ("propId", new_id)):
        if new.find(tag) is not None:
            set_val(new, tag, v)
    if same:
        meta.insert(same[-1] + 1, new)
    else:
        meta.append(new)
    n = pi.find("numAvailProps")
    if n is not None:
        set_val(pi, "numAvailProps", ival(pi, "numAvailProps") + 1)
    anchors = pi.find("aAnchors")
    if anchors is None:
        anchors = ET.SubElement(pi, "aAnchors", itemType="CAnchorProps")
    name = ANCHOR_NAMES[anchor]
    it = next((a for a in items(anchors) if (a.findtext("anchor") or "").strip() == name), None)
    if it is None:
        it = ET.SubElement(anchors, "Item")
        ET.SubElement(it, "props").text = ""
        ET.SubElement(it, "anchor").text = name
    counts = (it.findtext("props") or "").split()
    note = None
    if len(counts) != new_id:
        note = f"aAnchors {name} lists {len(counts)} props, the new prop is number {new_id}"
        counts = (counts + ["1"] * new_id)[:new_id]
    it.find("props").text = " ".join(counts + ["1"])
    ymt.props = items(meta)
    return note


def ydd_vertex_count(xml_path):
    x = open(xml_path, encoding="utf-8", errors="ignore").read()
    return sum(len(b.strip().splitlines()) for b in re.findall(r"<Data>(.*?)</Data>", x, re.S))


def run_garment(pack, files, work, spec, out_dir, name):
    import garment_templates as G
    warnings = []
    tid = str(spec.get("template") or "")
    try:
        params = G.clean_params(tid, spec.get("params"))
    except G.ParamError as e:
        raise Refuse("bad_spec", e.ar, e.en)
    tpl = G.BY_ID[tid]
    ymt_rel, stem, ymts, stem_of = pick_ymt(pack, files, spec)
    kind = "mp" if stem.lower().startswith(FREEMODE) else "ped"
    try:
        slot = G.slot_for(tid, spec.get("slot") or None, kind)
    except G.ParamError as e:
        raise Refuse("bad_spec", e.ar, e.en)
    base_ped = next((p for p in FREEMODE if stem.lower().startswith(p)), stem.lower())
    dfiles = drawable_files(files)
    body_pre = [stem.lower(), base_ped]

    # ---- the body: skeleton + base drawables of this ped
    yft = next((f for f in files if f.lower().endswith(".yft") and not f.lower().endswith("_hi.yft")
                and os.path.basename(f)[:-4].lower() in body_pre), None)
    body = {}
    for part in BODY_PARTS:
        for pre in body_pre:
            if (pre, part, 0) in dfiles:
                body[part] = dfiles[(pre, part, 0)]
                break
    missing = [x for x, ok in ((f"{base_ped}.yft", yft), (f"{base_ped}^uppr_000", body.get("uppr")),
                               (f"{base_ped}^lowr_000", body.get("lowr"))) if not ok]
    if missing:
        raise Refuse("no_body", "القطعة الجديدة تنقاس على جسم البيد نفسه، وناقص من الباك: " + "، ".join(missing) +
                     (". لبيد الـfreemode لازم ترفع معه ملفات الجسم من اللعبة (mp_m_freemode_01.yft و uppr_000 و lowr_000)."
                      if kind == "mp" else "."), f"the ped body is not in the pack: {missing}")

    # ---- the slot: a component of this ymt, or a head prop (needs the props .ymt)
    props_name = props_names(pack, files).get(stem.lower())
    prop_ymt_rel = None
    if props_name and props_name != stem.lower():
        prop_ymt_rel = next((f for f in ymts if stem_of[f].lower() == props_name), None)
    else:
        prop_ymt_rel = ymt_rel
    if slot == "auto":
        slot = "p_head" if prop_ymt_rel else "berd"
        if slot == "berd":
            warnings.append(f"props of {stem} belong to {props_name} ({props_name}.ymt is not in the pack): built as berd")
    if slot == "p_head" and not prop_ymt_rel:
        raise Refuse("props_ymt", f"إكسسوارات الراس (p_head) لهالبيد تابعة لـ {props_name} وملفه {props_name}.ymt مو بالباك. "
                     "اختر berd (مكان القناع) بدالها.", f"props of {stem} belong to {props_name}; {props_name}.ymt is not in the pack")
    is_prop = slot in PROPS

    # ---- the .ymt(s)
    ymt_xml = os.path.join(work, "ymt.xml")
    fmt = ymt_to_xml(os.path.join(pack, ymt_rel), ymt_xml)
    if fmt == "RBF":
        raise Refuse("unreadable", "الـymt بصيغة قديمة (RBF) ما تنعدل هنا.", "RBF ymt is not supported")
    ymt = Ymt(ymt_xml)
    pymt, pfmt, pymt_xml = ymt, fmt, ymt_xml
    if is_prop and prop_ymt_rel != ymt_rel:
        pymt_xml = os.path.join(work, "pymt.xml")
        pfmt = ymt_to_xml(os.path.join(pack, prop_ymt_rel), pymt_xml)
        pymt = Ymt(pymt_xml)

    # where the new files go: next to the ped's own uppr_000 (same folder, same "prefix^" or folder style)
    ref = body["uppr"]
    ref_dir = os.path.dirname(ref)
    caret = "^" in os.path.basename(ref)

    def rel_for(prefix, filename):
        if caret:
            return (ref_dir + "/" if ref_dir else "") + f"{prefix}^{filename}"
        return (os.path.dirname(ref_dir) + "/" if os.path.dirname(ref_dir) else "") + f"{prefix}/{filename}"

    pieces = []
    if is_prop:
        anchor = PROPS.index(slot)
        n = ymt_next_prop(pymt, anchor)
        pp = props_name or prop_prefix(stem.lower())
        pieces.append({"role": "main", "comp": slot, "prop": True, "bone": "SKEL_Head", "index": n, "prefix": pp,
                       "dict": f"{pp}^{slot}_{n:03d}", "drawable": f"{slot}_{n:03d}", "diffuse": f"{slot}_diff_{n:03d}_a",
                       "normal": f"{slot}_normal_{n:03d}", "spec": f"{slot}_spec_{n:03d}"})
    else:
        ci = COMPONENTS.index(slot)
        if ymt.component(ci) is None:
            raise Refuse("no_component", f"الـymt ما فيه {slot} أصلاً: اختر مكان ثاني للقطعة.", f"{slot} is not in {stem}.ymt",
                         summary=ymt.summary())
        n = len(ymt.drawables(ci))
        pieces.append({"role": "main", "comp": slot, "index": n, "prefix": stem.lower(), "dict": f"{stem.lower()}^{slot}_{n:03d}_u",
                       "drawable": f"{slot}_{n:03d}_u", "diffuse": f"{slot}_diff_{n:03d}_a_uni",
                       "normal": f"{slot}_normal_{n:03d}", "spec": f"{slot}_spec_{n:03d}"})
    hide = params.get("hide_lower", "keep")
    if hide in ("empty", "cut") and slot != "lowr" and ymt.component(4) is not None:
        n = len(ymt.drawables(4))
        pieces.append({"role": "lower", "comp": "lowr", "mode": hide, "index": n, "prefix": stem.lower(),
                       "dict": f"{stem.lower()}^lowr_{n:03d}_u", "drawable": f"lowr_{n:03d}_u",
                       "diffuse": f"lowr_diff_{n:03d}_a_uni", "normal": f"lowr_normal_{n:03d}", "spec": f"lowr_spec_{n:03d}"})
    for pc in pieces:
        for ext in (".ydd",):
            r = rel_for(pc["prefix"], pc["dict"].split("^")[-1] + ext)
            if r in files:
                warnings.append(f"{os.path.basename(r)} is in the pack but not in the .ymt: replaced")

    # ---- CodeWalker XML of the body for Blender
    xml_dir = os.path.join(work, "xml")
    os.makedirs(xml_dir, exist_ok=True)

    def to_xml(f):
        try:
            cw("res-to-xml", os.path.join(pack, f), xml_dir)
        except RuntimeError as e:
            raise Refuse("unreadable", f"ما قدرت أقرا {os.path.basename(f)} (مشفّر أو خربان).", f"{f}: {e}")
        return os.path.join(xml_dir, os.path.basename(f) + ".xml")
    sk_xml = to_xml(yft)
    body_xml = {k: to_xml(v) for k, v in body.items()}
    scaffold = {"uppr": body_xml["uppr"], "lowr": body_xml["lowr"]}
    if not is_prop and slot not in scaffold:
        own = next((dfiles[(p, slot, 0)] for p in body_pre if (p, slot, 0) in dfiles), None)
        scaffold[slot] = to_xml(own) if own else body_xml["uppr"]
    skin = skin_colour(pack, files, set(body_pre), work)
    gout = os.path.join(work, "garment")
    job = {"sollumz": SOLLUMZ_DIR or os.path.join(HERE, ".deps"), "skeleton": sk_xml, "body": body_xml,
           "scaffold": scaffold, "template": tid, "params": params, "pieces": pieces, "skin": skin, "out": gout}
    job_path, res_path = os.path.join(work, "garment_job.json"), os.path.join(work, "garment_result.json")
    json.dump(job, open(job_path, "w"))
    log(f"garment {tid} on {slot} ({stem}), Blender ...")
    r = subprocess.run([BPY_PYTHON or sys.executable, os.path.join(HERE, "garment_bpy.py"), job_path, res_path],
                       capture_output=True, text=True, timeout=1800)
    if not os.path.exists(res_path):
        raise RuntimeError(f"the Blender step stopped ({r.returncode}): {((r.stdout or '') + (r.stderr or ''))[-1500:]}")
    res = json.load(open(res_path))
    if "error" in res:
        log(res.get("trace", ""))
        raise Refuse("garment_failed", "ما قدرت أفصّل القطعة على جسم هالبيد.", f"garment build failed: {res['error']}")
    built = {p["role"]: p for p in res["pieces"]}

    # ---- the design on the generated texture (only where the mask allows: not on skin, gold trim, agal)
    main = built["main"]
    base_img = Image.open(main["diffuse_png"]).convert("RGBA")
    rgba = to_float(base_img)
    m = np.asarray(Image.open(main["mask_png"]).convert("L"), np.float32) / 255
    design = spec.get("design") or "none"
    extra = {}
    if design == "tint":
        out = recolour(rgba, parse_color(spec.get("color")), m, float(spec.get("strength", 1.0)))
    elif design == "logo":
        out = apply_logo(rgba, spec)
        out = rgba * (1 - m[..., None]) + out * m[..., None]
    elif design == "prompt":
        out, extra = apply_prompt(rgba, {**spec, "keep_skin": False}, work)
        out = rgba * (1 - m[..., None]) + out * m[..., None]
    elif design == "none":
        out = rgba
    else:
        raise Refuse("bad_spec", "نوع التصميم غلط.", f"unknown design {design}")
    out[..., 3] = 1
    after = to_image(out)
    preview(base_img, os.path.join(out_dir, "preview_before.png"))
    preview(after, os.path.join(out_dir, "preview_after.png"))

    # ---- binaries: .ydd from the XML, .ytd per piece
    new_files = {}   # rel path in the pack -> local file
    tex_dir = os.path.join(work, "ytd_src")
    os.makedirs(tex_dir, exist_ok=True)
    piece_info = []
    for pc in pieces:
        b = built[pc["role"]]
        ydd_rel = rel_for(pc["prefix"], pc["dict"].split("^")[-1] + ".ydd")
        ydd_out = os.path.join(work, os.path.basename(ydd_rel))
        cw(b["xml"], ydd_out)
        rt = os.path.join(work, "rt_" + pc["role"])
        os.makedirs(rt, exist_ok=True)
        _, o = cw("res-to-xml", ydd_out, rt)
        back = os.path.join(rt, os.path.basename(ydd_out) + ".xml")
        nv_in, nv_back = ydd_vertex_count(b["xml"]), ydd_vertex_count(back)
        if "DRAWABLES 1" not in o or nv_in != nv_back or nv_back == 0:
            raise RuntimeError(f"{os.path.basename(ydd_out)} did not survive the round trip ({nv_in} -> {nv_back} vertices; {o.strip()[-200:]})")
        new_files[ydd_rel] = ydd_out
        ytd_rel = rel_for(pc["prefix"], pc["diffuse"] + ".ytd")
        ytd_out = os.path.join(work, os.path.basename(ytd_rel))
        if pc["role"] == "main":
            dds = os.path.join(tex_dir, pc["diffuse"] + ".dds")
            encode_dds(after, "D3DFMT_DXT1", 11, dds)
            cw("ytd-build", dds, ytd_out)
        elif pc.get("mode") == "cut" and b.get("mode") == "cut":
            pre_, by_letter = find_base_files(files, stem, "lowr", 0)
            if not by_letter.get("a"):
                raise Refuse("no_diffuse", "ما لقيت تكستر البنطلون الأساسي (lowr_diff_000_a) عشان أقصّه.", "no lowr_diff_000_a")
            exp = os.path.join(work, "lowr_base")
            tex = ytd_export(os.path.join(pack, by_letter["a"][0]), exp)
            old = next(t["tex"]["name"] for t in tex if TEX_RE.match(t["tex"]["name"] or "") or len(tex) == 1)
            cw("ytd-build", exp, ytd_out, "--rename", f"{old}={pc['diffuse']}")
        else:
            dds = os.path.join(tex_dir, pc["diffuse"] + ".dds")
            encode_dds(Image.open(b["diffuse_png"]).convert("RGBA"), "D3DFMT_DXT1", 7, dds)
            cw("ytd-build", dds, ytd_out)
        chk = {t["tex"]["name"] for t in ytd_export(ytd_out, os.path.join(work, "chk_" + pc["role"]))}
        if pc["diffuse"] not in chk:
            raise RuntimeError(f"{pc['diffuse']} is not in the built .ytd: {sorted(chk)}")
        new_files[ytd_rel] = ytd_out
        piece_info.append({"role": pc["role"], "component": pc["comp"], "drawable": pc["index"], "letter": "a",
                           "texture": pc["diffuse"], "ydd": ydd_rel, "ytd": ytd_rel, "verts": b["verts"],
                           "faces": b["faces"], "bones": len(b.get("groups") or {}),
                           "max_influences": b.get("max_influences"), **({"mode": b.get("mode")} if pc["role"] == "lower" else {})})

    # ---- the .ymt entries
    changed_ymts = {}
    comp_infos = []
    for pc in pieces:
        if pc.get("prop"):
            note = ymt_add_prop(pymt, PROPS.index(pc["comp"]), pc["index"])
            if note:
                warnings.append(note)
        else:
            if ymt_add_drawable(ymt, COMPONENTS.index(pc["comp"]), pc["index"]):
                comp_infos.append(pc["comp"])
    targets = [(ymt, ymt_rel, ymt_xml, fmt)] if not is_prop or prop_ymt_rel == ymt_rel else []
    if any(pc["role"] == "lower" for pc in pieces) and not targets:
        targets.append((ymt, ymt_rel, ymt_xml, fmt))
    if is_prop:
        targets.append((pymt, prop_ymt_rel, pymt_xml, pfmt))
    for y, rel, xp, f_ in {id(t[0]): t for t in targets}.values():
        y.save(xp)
        outp = os.path.join(work, "new_" + os.path.basename(rel))
        cw("xml-to-ymt", xp, outp, *(["--pso"] if f_ == "PSO" else []))
        bx = os.path.join(work, "back_" + os.path.basename(rel) + ".xml")
        ymt_to_xml(outp, bx)
        back = Ymt(bx)
        for pc in pieces:
            if pc.get("prop") and y is pymt:
                if back.prop(PROPS.index(pc["comp"]), pc["index"]) is None:
                    raise RuntimeError("the .ymt does not list the new prop")
            elif not pc.get("prop") and y is ymt:
                ci = COMPONENTS.index(pc["comp"])
                if len(back.drawables(ci)) != pc["index"] + 1 or ival(back.component(ci), "numAvailTex") != ival(ymt.component(ci), "numAvailTex"):
                    raise RuntimeError(f"the .ymt does not list {pc['comp']} {pc['index']}")
        changed_ymts[rel] = outp
    new_files.update(changed_ymts)

    # ---- the resource (as clothes designs: the whole pack + new files, or only the new ones when big / trimmed)
    root_rel = resource_root(files)
    res_name = os.path.basename(root_rel) if root_rel else ""
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", res_name or "") or res_name.lower() == "stream":
        res_name = name
    stage = os.path.join(work, "out", res_name)
    if root_rel is None:
        os.makedirs(os.path.join(stage, "stream"))
        for f in files:
            dst = os.path.join(stage, f if f.lower().endswith(".meta") else os.path.join("stream", f))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(os.path.join(pack, f), dst)
        rel = lambda f: (f if f.lower().endswith(".meta") else "stream/" + f)  # noqa: E731
    else:
        shutil.copytree(os.path.join(pack, root_rel) if root_rel else pack, stage)
        rel = lambda f: os.path.relpath(f, root_rel).replace("\\", "/") if root_rel else f  # noqa: E731
    changed = []
    for f, local in new_files.items():
        dst = os.path.join(stage, rel(f))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(local, dst)
        changed.append(rel(f))
    if not os.path.exists(os.path.join(stage, "fxmanifest.lua")) and not os.path.exists(os.path.join(stage, "__resource.lua")):
        open(os.path.join(stage, "fxmanifest.lua"), "w").write(manifest_for(stage)[0])
        changed.append("fxmanifest.lua")
    zip_path = os.path.join(out_dir, f"{name}.zip")

    def write_zip(only):
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for dp, _, names in os.walk(stage):
                for nm in sorted(names):
                    full = os.path.join(dp, nm)
                    r_ = os.path.relpath(full, stage).replace("\\", "/")
                    if only is None or r_ in only:
                        z.write(full, f"{res_name}/{r_}")
    patch = bool(spec.get("trimmed"))
    write_zip(set(changed) if patch else None)
    if not patch and os.path.getsize(zip_path) > MAX_ZIP_OUT:
        patch = True
        write_zip(set(changed))
        warnings.append("the whole resource is over 90 MB: the zip has only the new and changed files")
    warnings += [f"template: {w}" for w in res["report"].get("notes", [])]
    shutil.rmtree(work, ignore_errors=True)
    mp = piece_info[0]
    info = {"name": name, "mode": "garment", "status": "built", "kind": kind, "target": stem, "ymt": ymt_rel,
            "template": tid, "template_label": tpl["label"], "params": params, "slot": slot,
            "design": design, "component": mp["component"], "drawable": mp["drawable"], "letter": "a",
            "texture": mp["texture"], "pieces": piece_info, "resource": res_name, "files": changed, "patch": patch,
            "comp_infos": comp_infos, "ymt_changed": True, "zip": f"{name}.zip", "warnings": warnings,
            "landmarks": res["report"].get("landmarks"), "uv_islands": res["report"].get("islands"), "summary": ymt.summary(),
            "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}
    if design == "tint":
        info["color"] = spec.get("color")
    if extra:
        info["description"] = extra
    return info


CW_PATH = os.path.join(HERE, ".deps", "cwconv", "cwconv")


def main():
    global CW_PATH, BPY_PYTHON, SOLLUMZ_DIR
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pack")
    ap.add_argument("spec")
    ap.add_argument("out")
    ap.add_argument("--cwconv")
    ap.add_argument("--name")
    ap.add_argument("--bpy-python", help="garment mode: the Python with bpy (default: this one)")
    ap.add_argument("--sollumz", help="garment mode: the folder holding Sollumz/")
    a = ap.parse_args()
    if a.cwconv:
        CW_PATH = a.cwconv
    BPY_PYTHON, SOLLUMZ_DIR = a.bpy_python, a.sollumz
    os.makedirs(a.out, exist_ok=True)
    try:
        with open(a.spec, encoding="utf-8") as f:
            spec = json.load(f)
        if not isinstance(spec, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as e:
        spec = None
        err = Refuse("bad_spec", "ملف الطلب (json) خربان.", f"bad spec: {e}")
    name = a.name or (spec or {}).get("name") or os.path.splitext(os.path.basename(a.pack))[0]
    name = re.sub(r"[^a-z0-9_]", "_", str(name).lower())[:40] or "clothes"
    try:
        if spec is None:
            raise err
        info = run(a.pack, spec, a.out, name)
    except Refuse as e:
        log(f"::error::{e.en}")
        json.dump({"code": e.code, "message": e.ar, "detail": e.en, **e.extra}, open(os.path.join(a.out, "error.json"), "w"),
                  ensure_ascii=False)
        return 3
    with open(os.path.join(a.out, "info.json"), "w") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    log(json.dumps({k: v for k, v in info.items() if k != "summary"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
