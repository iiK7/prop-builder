"""Clothes designer: a NEW texture (colour / design) for an EXISTING clothing piece.
    python clothes_variant.py <pack.zip | folder> <spec.json> <out dir> [--cwconv PATH] [--name NAME]

Works for freemode MP packs (stream/<ped>_<collection>^jbib_diff_000_a_uni.ytd + <ped>_<collection>.ymt, props as
<ped>_p_<collection>^p_head_diff_000_a.ytd) and addon peds (<ped>.ymt with uppr_diff_000_a_uni.ytd files, in a <ped>/
folder or as <ped>^..., or every texture inside one <ped>.ytd).

A new design is the next free letter of that drawable (_b, _c ... _z): a copy of the base texture's .ytd with the
diffuse recoloured and renamed, and the .ymt (CPedVariationInfo) with one more aTexData item for that drawable and
numAvailTex of the component raised by one. Everything else in the pack is copied as it is.

spec.json:
  {"target": "<ymt name without .ymt>" (needed when the pack has several),
   "component": "jbib" | 11 | "p_head", "drawable": 0, "base": "a",
   "mode": "tint" | "logo" | "prompt" | "analyze",
   "color": "#rrggbb", "strength": 0..1, "keep_skin": true|false,           tint (and the colour of a prompt pattern)
   "logo": {"png": "<base64>", "u": .5, "v": .5, "scale": .3, "rotation": 0, "opacity": 1, "shade": true},
   "prompt": {"text": "...", "engine": "pollinations"|"flux", "tiles": 2, "strength": .85},
   "trimmed": true when the site sent only the files of this piece (the result is then a patch, not a whole resource)}

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


def add_texture(ymt, comp, drawable, base_index):
    """append a copy of the base texture's item; numAvailTex +1 (components). Returns notes."""
    notes = []
    owner, node, texs = textures_of(ymt, comp, drawable)
    new = ET.fromstring(ET.tostring(texs[base_index]))
    if comp in PROPS:
        # props: texId counts the textures (0, 1, 2 ...) in the packs seen; keep the pack's own way otherwise
        ids = [ival(t, "texId") for t in texs]
        if ids == list(range(len(ids))):
            set_val(new, "texId", len(ids))
        else:
            notes.append("prop texId kept from the base texture (the pack does not number them 0, 1, 2...)")
    node.append(new)
    if comp not in PROPS:
        c = ymt.component(COMPONENTS.index(comp))
        before = ival(c, "numAvailTex")
        total = sum(len(items(d.find("aTexData"))) for d in ymt.drawables(COMPONENTS.index(comp)))
        set_val(c, "numAvailTex", before + 1)
        if before != total - 1:
            notes.append(f"numAvailTex was {before} while the drawables have {total - 1} textures; raised by one anyway")
    return notes


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
def find_base_files(files, stem, comp, drawable):
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
    want = {stem.lower(), prop_prefix(stem.lower())} if comp in PROPS else {stem.lower()}
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
    owner, tnode, texs = textures_of(ymt, comp, drawable)
    if comp not in PROPS and ymt.component(COMPONENTS.index(comp)) is None:
        raise Refuse("no_component", f"الـymt ما فيه {comp} أصلاً.", f"{comp} is not in {stem}.ymt", summary=summary)
    if owner is None or tnode is None:
        raise Refuse("no_drawable", f"{comp} رقم {drawable:03d} مو موجود بالـymt.",
                     f"{comp} {drawable:03d} is not in {stem}.ymt", summary=summary)
    n = len(texs)
    if n == 0:
        raise Refuse("mismatch", "هالقطعة ما لها ولا تكستر بالـymt.", "the drawable has no textures in the ymt")

    base_letter = str(spec.get("base") or "a").lower()
    if base_letter not in LETTERS[:n]:
        raise Refuse("bad_spec", f"التكستر الأساسي _{base_letter} مو موجود (الموجود a لين {LETTERS[n - 1]}).",
                     f"base texture {base_letter} does not exist")
    base_index = LETTERS.index(base_letter)
    new_letter = LETTERS[n] if n < 26 else None

    prefix, by_letter = find_base_files(files, stem, comp, drawable)
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
    if letters != LETTERS[:len(letters)]:
        raise Refuse("mismatch", f"حروف التكسترات فيها فراغ ({', '.join(letters)}): الترقيم لازم يكون a, b, c… بدون نقص.",
                     f"texture letters have a gap: {letters}")
    if len(letters) != n:
        raise Refuse("mismatch", f"الـymt يقول {n} تكستر لهالقطعة بس الملفات {len(letters)} ({', '.join(letters)}).",
                     f"ymt says {n} textures, files have {len(letters)}")

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
            "drawable": drawable, "textures": n, "letters": letters, "next": new_letter, "layout": layout,
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
    m = TEX_RE.match(old_name)
    if m:
        new_name = f"{m.group('comp')}_diff_{m.group('d')}_{new_letter}" + (f"_{m.group('race')}" if m.group("race") else "")
    else:
        race = by_letter[base_letter][1]
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
        pre_part = os.path.basename(base_path).rsplit("^", 1)[0] + "^" if "^" in os.path.basename(base_path) else ""
        out_rel = os.path.join(os.path.dirname(base_path), pre_part + new_name + ".ytd").replace("\\", "/")
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
    notes = add_texture(ymt, comp, drawable, base_index)
    warnings += notes
    ymt.save(ymt_xml)
    new_ymt = os.path.join(work, "new.ymt")
    cw("xml-to-ymt", ymt_xml, new_ymt, *(["--pso"] if fmt == "PSO" else []))
    back_xml = os.path.join(work, "back.xml")
    ymt_to_xml(new_ymt, back_xml)
    back = Ymt(back_xml)
    if len(textures_of(back, comp, drawable)[2]) != n + 1:
        raise RuntimeError("the rebuilt .ymt does not have the new texture")
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
                 "ymt_textures": {"before": n, "after": n + 1}, "zip": f"{name}.zip",
                 "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")})
    if comp not in PROPS:
        info["numAvailTex"] = ival(back.component(COMPONENTS.index(comp)), "numAvailTex")
    if mode == "tint":
        info["color"] = spec.get("color")
    if extra:
        info["description"] = extra
    return info


CW_PATH = os.path.join(HERE, ".deps", "cwconv", "cwconv")


def main():
    global CW_PATH
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pack")
    ap.add_argument("spec")
    ap.add_argument("out")
    ap.add_argument("--cwconv")
    ap.add_argument("--name")
    a = ap.parse_args()
    if a.cwconv:
        CW_PATH = a.cwconv
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
