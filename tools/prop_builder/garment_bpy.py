"""Garment templates -> new ped drawables (CodeWalker XML), fitted to the ped's own body. Blender bpy + Sollumz.
Run by clothes_variant.py ("mode": "garment") with the Python that has bpy (as clothes_preview_bpy.py):
    python garment_bpy.py <job.json> <result.json>

job.json:
  {"sollumz": "<folder holding Sollumz/>", "skeleton": "<ped>.yft.xml",
   "body": {"uppr": xml, "lowr": xml, "hand": xml|null, "head": xml|null, "hair": xml|null, "feet": xml|null},
   "scaffold": {"<comp>": xml}            drawables imported only for their Sollumz structure (uppr, lowr, target comp)
   "template": "thobe", "params": {...}  (garment_templates.clean_params)
   "pieces": [{"role": "main" | "lower", "comp": "uppr", "prop": false, "mode": "empty" | "cut" (lower),
               "dict": "mac^uppr_003_u", "drawable": "uppr_003_u", "diffuse": "uppr_diff_003_a_uni",
               "normal": "uppr_normal_003", "spec": "uppr_spec_003", "bone": "SKEL_Head" (props)}],
   "skin": [r, g, b], "out": "<dir>"}
result.json: {"pieces": [{"role", "xml", "diffuse_png", "mask_png", "verts", "faces", "groups", "max_influences"}],
              "report": {...}} or {"error": "..."}

How a garment is made (every size comes from the ped, nothing is fixed to one body):
- landmarks from the skeleton's bones (neck, shoulders, spine, pelvis, thighs, knees, ankles, head) and the shoe tops;
- panels = grids of points (rows x columns): trunk rings around the torso / both legs (convex hull of the body in each
  horizontal slice, smoothed, eased, flared below the widest girth), sleeves along shoulder -> elbow -> wrist, one tube
  per trouser leg, shells around the head (hood, shemagh, cap: rays cast at the head + hair from outside), tori (agal,
  belt), cords (tassel, hoodie strings);
- each panel has its UV island; a closed ring is two half panels split at the back, so the front of the trunk is the
  middle of its island (logos land on the chest) and an open front is a gap in the middle;
- inner lining (reversed faces 4 mm inside) so openings, hems and cuffs are not see-through;
- weights: nearest point of the matching body part (torso, arms, head, legs), barycentric; skirts smoothed around the
  ring, below the crotch thighs + pelvis instead of calves / feet, 4 bones max, normalised; vertex colours the same way;
- texture: fabric (weave / knit / twill / denim / terry / satin / wool), seams, hems, placket, buttons, zip, pockets,
  trims drawn in the UV islands, a normal map from it, a low spec map; a mask says where the designer's colour / logo /
  pattern may go (not on skin, gold trim, agal).
"""
import json
import math
import os
import sys
import traceback

import bpy  # noqa: I001  (bpy before addon_utils)
import addon_utils

job_path, result_path = sys.argv[1:3]
job = json.load(open(job_path))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, job["sollumz"])
bpy.ops.wm.read_factory_settings(use_empty=True)
addon_utils.enable("Sollumz", default_set=True)
from Sollumz.known_paths import data_directory_path  # noqa: E402

os.makedirs(data_directory_path(), exist_ok=True)

import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

from clothes_variant import encode_dds  # noqa: E402
from garment_templates import BY_ID  # noqa: E402

OUT = job["out"]
os.makedirs(OUT + "/xml", exist_ok=True)
os.makedirs(OUT + "/tex", exist_ok=True)
os.makedirs(OUT + "/emb", exist_ok=True)
TPL = BY_ID[job["template"]]
P = job["params"]
report = {"template": TPL["id"]}
TAU = 2 * math.pi


def log(*a):
    print(*a, flush=True)


# ===================================================================================================== import / body
def imp(path, skeleton=True):
    kw = dict(directory=os.path.dirname(path), files=[{"name": os.path.basename(path)}], use_custom_settings=True,
              split_by_group=False)
    if skeleton:
        kw.update(dwd_import_external_skeleton="SAVED", dwd_import_external_skeleton_saved_path=job["skeleton"])
    before = set(bpy.data.objects)
    r = bpy.ops.sollumz.import_assets(**kw)
    if "FINISHED" not in r:
        raise RuntimeError(f"Sollumz could not import {os.path.basename(path)}")
    return [o for o in bpy.data.objects if o not in before]


def mesh_of(objs):
    ms = [o for o in objs if o.type == "MESH" and len(o.data.vertices) > 8]
    return max(ms, key=lambda o: len(o.data.vertices)) if ms else None


def vdata(o):
    """world positions (n,3), weights (n, groups), group names, dominant group per vertex, triangles, colours"""
    me = o.data
    n = len(me.vertices)
    pos = np.array([tuple(o.matrix_world @ v.co) for v in me.vertices])
    names = [g.name for g in o.vertex_groups]
    W = np.zeros((n, max(1, len(names))))
    for v in me.vertices:
        for g in v.groups:
            if g.group < len(names):
                W[v.index, g.group] = g.weight
    if not names:
        names = ["SKEL_ROOT"]
        W[:, 0] = 1
    dom = [names[i] for i in W.argmax(1)]
    me.calc_loop_triangles()
    tris = np.array([tuple(t.vertices) for t in me.loop_triangles], dtype=int).reshape(-1, 3)
    cols = {}
    for ca in me.color_attributes:
        if ca.domain == "POINT":
            arr = np.zeros(n * 4)
            ca.data.foreach_get("color", arr)
            cols[ca.name] = arr.reshape(n, 4)
        else:
            arr = np.zeros(len(me.loops) * 4)
            ca.data.foreach_get("color", arr)
            arr = arr.reshape(-1, 4)
            vi = np.array([lp.vertex_index for lp in me.loops])
            acc = np.zeros((n, 4))
            cnt = np.zeros(n)
            np.add.at(acc, vi, arr)
            np.add.at(cnt, vi, 1)
            cols[ca.name] = acc / np.maximum(cnt, 1)[:, None]
    return dict(pos=pos, W=W, names=names, dom=dom, tris=tris, cols=cols)


def merge(parts):
    parts = [(d, m) for d, m in parts if d is not None and m.any()]
    if not parts:
        return None
    names = sorted({n for d, _ in parts for n in d["names"]})
    pos, W, tris, off = [], [], [], 0
    cols = {"Color 1": [], "Color 2": []}
    for d, mask in parts:
        idx = np.nonzero(mask)[0]
        remap = -np.ones(len(d["pos"]), int)
        remap[idx] = np.arange(len(idx)) + off
        pos.append(d["pos"][idx])
        w = np.zeros((len(idx), len(names)))
        for j, nm in enumerate(d["names"]):
            w[:, names.index(nm)] = d["W"][idx, j]
        W.append(w)
        t = remap[d["tris"]] if len(d["tris"]) else np.zeros((0, 3), int)
        tris.append(t[(t >= 0).all(1)])
        for k in cols:
            cols[k].append(d["cols"].get(k, np.ones((len(d["pos"]), 4)))[idx])
        off += len(idx)
    src = dict(pos=np.vstack(pos), W=np.vstack(W), names=names, tris=np.vstack(tris),
               cols={k: np.vstack(v) for k, v in cols.items()})
    src["bvh"] = BVHTree.FromPolygons([tuple(p) for p in src["pos"]], [tuple(t) for t in src["tris"]]) if len(src["tris"]) else None
    return src


ARM_BONES = ("UpperArm", "ArmRoll", "Forearm", "Hand", "Finger", "Elbow", "Wrist")
LEG = {1: ("L_Thigh", "L_Calf", "L_Foot", "L_Toe", "L_Knee", "L_ThighRoll", "L_Skirt"),
       -1: ("R_Thigh", "R_Calf", "R_Foot", "R_Toe", "R_Knee", "R_ThighRoll", "R_Skirt")}


def is_arm(name):
    return any(k in name for k in ARM_BONES)


body_objs = {}
for part, path in (job.get("body") or {}).items():
    if path:
        body_objs[part] = imp(path)
ARM = next(o for objs in body_objs.values() for o in objs if o.type == "ARMATURE")
D = {k: (vdata(mesh_of(v)) if mesh_of(v) else None) for k, v in body_objs.items()}
U, L, H, HD, HR, F = (D.get(k) for k in ("uppr", "lowr", "hand", "head", "hair", "feet"))
if U is None or L is None:
    raise SystemExit("body: uppr and lowr are needed")


def bone(name, default=None):
    b = ARM.data.bones.get(name)
    if b is None:
        if default is not None:
            return np.array(default, float)
        raise KeyError(name)
    return np.array(ARM.matrix_world @ b.head_local)


def mask_of(d, fn):
    return None if d is None else np.array([fn(n) for n in d["dom"]], bool)


SRC = {
    "trunk": merge([(U, mask_of(U, lambda n: not is_arm(n))), (L, np.ones(len(L["pos"]), bool))]),
    "arm": merge([(U, mask_of(U, is_arm)), (H, mask_of(H, lambda n: is_arm(n) and "Finger" not in n))]),
    "legs": merge([(L, np.ones(len(L["pos"]), bool))]),
    "shirt": merge([(U, np.ones(len(U["pos"]), bool))]),
    "head": merge([(HD, np.ones(len(HD["pos"]), bool) if HD else np.zeros(0, bool)),
                   (HR, np.ones(len(HR["pos"]), bool) if HR and len(HR["pos"]) > 50 else np.zeros(0, bool))]),
}
if SRC["arm"] is None:
    SRC["arm"] = SRC["shirt"]
_ua_x = abs(float(np.array(ARM.matrix_world @ ARM.data.bones["SKEL_L_UpperArm"].head_local)[0])) if "SKEL_L_UpperArm" in ARM.data.bones else 0.2
_armpit = float(np.array(ARM.matrix_world @ ARM.data.bones["SKEL_L_UpperArm"].head_local)[2]) - 0.13 if "SKEL_L_UpperArm" in ARM.data.bones else 0.4
SRC["core"] = merge([(U, np.array([not is_arm(n) and (p[2] < _armpit or abs(p[0]) <= _ua_x - 0.02)
                                   for n, p in zip(U["dom"], U["pos"])])), (L, np.ones(len(L["pos"]), bool))])
ALL_NAMES = sorted({n for s in SRC.values() if s for n in s["names"]} | {b.name for b in ARM.data.bones})

# ---------------------------------------------------------------- landmarks (all from this ped)
Z = {}
Z["neck"] = float(bone("SKEL_Neck_1")[2])
Z["head"] = float(bone("SKEL_Head")[2])
Z["shoulder"] = float(bone("SKEL_L_UpperArm")[2])
Z["chest"] = float(bone("SKEL_Spine3", bone("SKEL_Spine2"))[2])
Z["pelvis"] = float(bone("SKEL_Pelvis")[2])
Z["thigh"] = float(bone("SKEL_L_Thigh")[2])
Z["crotch"] = Z["thigh"] - 0.05
Z["knee"] = float(bone("SKEL_L_Calf")[2])
Z["foot"] = float(bone("SKEL_L_Foot")[2])
Z["midthigh"] = Z["thigh"] + (Z["knee"] - Z["thigh"]) * 0.45
Z["shoe"] = (float(F["pos"][:, 2].max()) if F is not None else Z["foot"] + 0.03) - 0.05   # hem into the shoe tops
Z["lowr_top"] = float(L["pos"][:, 2].max())
Z["waist"] = min(Z["lowr_top"] - 0.012, Z["pelvis"] + 0.10)
Z["uppr_top"] = float(U["pos"][:, 2].max())
report["landmarks"] = {k: round(v, 3) for k, v in Z.items()}
HEAD_C = bone("SKEL_Head") + np.array([0.0, -0.005, 0.0])
if SRC["head"] is not None:
    hp = SRC["head"]["pos"]
    top = hp[hp[:, 2] > Z["head"] - 0.02]
    HEAD_C = np.array([float(np.median(top[:, 0])), float((top[:, 1].min() + top[:, 1].max()) / 2), Z["head"] + 0.06])
report["head_centre"] = [round(float(x), 3) for x in HEAD_C]


def lerp(a, b, t):
    return a + (b - a) * t


def pct(name, default=None):
    return float(P.get(name, default)) / 100.0


def cm(name, default=None):
    return float(P.get(name, default)) / 100.0


# ===================================================================================================== geometry helpers
def hull2d(p):
    p = sorted(map(tuple, p))
    if len(p) < 3:
        return np.array(p)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for q in p:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], q) <= 0:
            lo.pop()
        lo.append(q)
    for q in reversed(p):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], q) <= 0:
            hi.pop()
        hi.append(q)
    return np.array(lo[:-1] + hi[:-1])


def ray_hull(h, c, d):
    t = 1e9
    for i in range(len(h)):
        a, b = h[i], h[(i + 1) % len(h)]
        e = b - a
        n = np.array([e[1], -e[0]])
        nd = n @ d
        if nd > 1e-9:
            t = min(t, n @ (a - c) / nd)
    return t


def dir_of(phi):
    """horizontal direction; phi 0 = the ped's back (+Y), pi/2 = its left (+X), pi = front (-Y)"""
    return np.array([math.sin(phi), math.cos(phi)])


def smooth_rows(a, n, axis=0, wrap=False):
    a = np.array(a, float)
    for _ in range(n):
        if wrap:
            a = (np.roll(a, 1, axis) + 2 * a + np.roll(a, -1, axis)) / 4
        else:
            b = a.copy()
            sl = [slice(None)] * a.ndim
            s0, s1, s2 = list(sl), list(sl), list(sl)
            s0[axis], s1[axis], s2[axis] = slice(None, -2), slice(1, -1), slice(2, None)
            b[tuple(s1)] = (a[tuple(s0)] + 2 * a[tuple(s1)] + a[tuple(s2)]) / 4
            a = b
    return a


def periodic_interp(x, xp, fp):
    """fp sampled at xp (0..2pi, ascending) -> values at x, periodic"""
    xp = np.asarray(xp)
    fp = np.asarray(fp)
    X = np.concatenate([xp - TAU, xp, xp + TAU])
    if fp.ndim == 1:
        return np.interp(np.mod(x, TAU), X, np.tile(fp, 3))
    return np.stack([np.interp(np.mod(x, TAU), X, np.tile(fp[:, k], 3)) for k in range(fp.shape[1])], -1)


def push_out(pts, src, ease, centre_fn=None):
    """points that are inside the body (or closer than `ease`) moved out along the surface normal"""
    bvh = src["bvh"]
    out = np.array(pts, float)
    for k, p in enumerate(out):
        loc, nrm, _, dist = bvh.find_nearest(Vector(p))
        if loc is None:
            continue
        q, n = np.array(loc), np.array(nrm)
        if centre_fn is not None:
            c = centre_fn(p)
            if n @ (q - c) < 0:
                n = -n
        inside = (p - q) @ n < 0
        if inside or dist < ease:
            out[k] = q + n * ease
    return out


# ===================================================================================================== panels / UV atlas
class Panel:
    """a grid of points rows x cols (+ u per column, v per row in its UV rect), outward faces, lining optional"""

    def __init__(self, name, pts, uv, src, axis=None, closed_cols=False, lining=True, mask=255, lining_dir=None,
                 blend=None):
        self.name, self.pts, self.uv, self.src = name, np.asarray(pts, float), np.asarray(uv, float), src
        self.axis = None if axis is None else np.asarray(axis, float)   # per point: a point inside (for orientation)
        self.closed_cols, self.lining, self.mask, self.lining_dir, self.blend = closed_cols, lining, mask, lining_dir, blend


panels = []
ISL = {}   # island name -> (u0, v0, u1, v1)


def island(name, u0, v0, u1, v1):
    ISL[name] = (u0, v0, u1, v1)
    return ISL[name]


def uv_in(isl, s, t):
    """s, t in 0..1 inside the island -> texture u, v"""
    u0, v0, u1, v1 = ISL[isl]
    return np.stack([u0 + (u1 - u0) * np.asarray(s), v0 + (v1 - v0) * np.asarray(t)], -1)


def add_grid(name, pts, s, t, isl, src, axis=None, **kw):
    """pts (R, C, 3); s per column (C) or (R, C); t per row (R) -> panel with UVs in island isl"""
    pts = np.asarray(pts, float)
    R, C = pts.shape[:2]
    S = np.broadcast_to(np.asarray(s, float), (R, C)) if np.ndim(s) == 1 else np.asarray(s, float)
    T = np.broadcast_to(np.asarray(t, float)[:, None], (R, C))
    uv = uv_in(isl, S, T)
    panels.append(Panel(name, pts, uv, src, axis, **kw))
    return panels[-1]


# ===================================================================================================== trunk shell
def trunk_rings(z_top, z_bot, src_key="trunk", dz=0.02, na=96, smooth_c=30):
    """radius tables of the body: rings[i][k] at zs[i], angle 2pi k / na (phi from the back), centres"""
    zs = list(np.arange(z_top, z_bot - 1e-6, -dz))
    if zs[-1] > z_bot + 1e-6:
        zs.append(z_bot)
    Pp = SRC[src_key]["pos"]
    hulls, centres = [], []
    cy = float(np.median(Pp[:, 1]))
    for z in zs:
        band = Pp[np.abs(Pp[:, 2] - z) < (0.025 if z > Z["crotch"] else 0.03)][:, :2]
        h = hull2d(band) if len(band) >= 3 else np.zeros((0, 2))
        hulls.append(h)
        centres.append(h.mean(0) if len(h) >= 3 else np.array([0.0, cy]))
    centres = smooth_rows(np.array(centres), smooth_c)
    angs = TAU * np.arange(na) / na
    rings = np.array([[ray_hull(h, c, dir_of(a)) if len(h) >= 3 else 1e9 for a in angs] for h, c in zip(hulls, centres)])
    bad = [i for i in range(len(zs)) if not rings[i].max() < 1.0]
    good = [i for i in range(len(zs)) if i not in bad]
    if not good:
        raise RuntimeError("the body has no torso at these heights")
    for i in bad:
        j = min(good, key=lambda g: abs(g - i))
        rings[i], centres[i] = rings[j], centres[j]
    rm = rings.copy()
    for i in range(len(zs)):
        rm[i] = rings[max(0, i - 2): i + 3].max(0)
    rings = smooth_rows(rm, 6)
    return np.array(zs), rings, centres, angs


def neckline_loop(centre=None):
    """the shirt's open edge around the neck: (centre xy, phi table, radius table, z table); centre: measure the
    radii from this point instead of the loop's own centre"""
    import bmesh
    o = mesh_of(body_objs["uppr"])
    bm = bmesh.new()
    bm.from_mesh(o.data)
    bm.transform(o.matrix_world)
    nk = bone("SKEL_Neck_1")
    pts = set()
    for e in bm.edges:
        if e.is_boundary:
            for v in e.verts:
                if v.co.z > Z["neck"] - 0.10 and math.hypot(v.co.x - nk[0], v.co.y - nk[1]) < 0.17:
                    pts.add(tuple(v.co))
    bm.free()
    b = np.array(sorted(pts))
    if len(b) < 12:   # no open neck edge: a ring around the neck bone
        a = TAU * np.arange(48) / 48
        r = 0.075
        b = np.stack([nk[0] + np.sin(a) * r, nk[1] + np.cos(a) * r, np.full(48, Z["neck"] - 0.03)], 1)
    nc = b[:, :2].mean(0)
    b = b[np.hypot(b[:, 0] - nc[0], b[:, 1] - nc[1]) > 0.05]
    nc = b[:, :2].mean(0) if centre is None else np.asarray(centre, float)
    ang = np.arctan2(b[:, 0] - nc[0], b[:, 1] - nc[1]) % TAU
    o_ = np.argsort(ang)
    ang, b = ang[o_], b[o_]
    rad = np.hypot(b[:, 0] - nc[0], b[:, 1] - nc[1])
    # one value per direction (the loop is not always star shaped): the outermost
    na = 96
    grid = TAU * np.arange(na) / na
    R = periodic_interp(grid, ang, rad) + 0.006
    Zt = periodic_interp(grid, ang, b[:, 2])
    for _ in range(6):
        R = np.maximum(smooth_rows(R, 1, wrap=True), R * 0.98)
    Zt = smooth_rows(Zt, 24, wrap=True)
    return nc, grid, R, Zt


def neck_radius(grid, z):
    """the neck itself (head drawable) at height z: (its centre, radius per angle of grid); None without a head"""
    if SRC["head"] is None:
        return None
    hp = SRC["head"]["pos"]
    zz = max(z, float(hp[:, 2].min()) + 0.012)
    band = hp[np.abs(hp[:, 2] - zz) < 0.015][:, :2]
    h = hull2d(band) if len(band) >= 3 else np.zeros((0, 2))
    if len(h) < 3:
        return None
    c = h.mean(0)
    r = np.array([ray_hull(h, c, dir_of(a)) for a in grid])
    return (c, r) if 0 < r.min() and r.max() < 0.3 else None


def wrap_to(pts, src_key, centre, ease):
    """push points out horizontally (from the centre line) to the outside of the body + ease, where the body is further
    out than they are (shoulders, trapezius); a ray from outside inwards finds the outermost surface"""
    bvh = SRC[src_key]["bvh"]
    out = pts.copy()
    for k, p in enumerate(pts):
        d = np.array([p[0] - centre[0], p[1] - centre[1], 0.0])
        r0 = np.linalg.norm(d)
        if r0 < 1e-6:
            continue
        d /= r0
        o = np.array([centre[0], centre[1], p[2]]) + d * 0.7
        loc, _, _, _ = bvh.ray_cast(Vector(o), Vector(-d), 0.7)
        if loc is None:
            continue
        r = math.hypot(loc[0] - centre[0], loc[1] - centre[1]) + ease
        if r > r0:
            out[k, :2] = np.array(centre[:2]) + d[:2] * r
    return out


class Trunk:
    """a garment shell around the torso (and both legs below the crotch): rows from the neckline (or a waist height)
    down to the hem; gap(z) = front opening in metres (0 = closed)"""

    def __init__(self, z_hem, ease, flare=0.0, top="neck", z_top=None, gap=None, collar="none", isl="trunk",
                 src_key="trunk", nc=40, flare_from=None, neck_inset=0.0, neck_ease=0.012, layer=None):
        self.ease, self.gap, self.isl, self.collar = ease, gap or (lambda z: 0.0), isl, collar
        z_sh = Z["shoulder"]
        self.layer = LAYER if layer is None else layer
        if top == "neck":
            zs, rings, centres, angs = trunk_rings(z_sh, z_hem, "core" if src_key == "trunk" and not self.layer else src_key)
        else:
            zs, rings, centres, angs = trunk_rings(z_top, z_hem, src_key)
        # below the widest girth the cloth hangs: never narrower, then flares to the hem
        start = flare_from if flare_from is not None else Z["pelvis"]
        upto = [i for i, z in enumerate(zs) if z >= start - 0.15] or [0]
        gi = int(np.argmax(rings.mean(1)[: max(upto) + 1]))
        for i in range(gi + 1, len(zs)):
            grow = 0.0018 * (0.4 + flare * 2.0) if zs[i] < start else 0.0
            rings[i] = np.maximum(rings[i], rings[i - 1] + grow)
            centres[i] = centres[i - 1]
        rings = smooth_rows(rings, 3, axis=1, wrap=True)
        rings = smooth_rows(rings, 4) + ease
        self.zs, self.rings, self.centres, self.angs = zs, rings, centres, angs
        # rows: (nominal z, centre, function phi -> point)
        rows = []
        if top == "neck":
            nc_, ngrid, nR, nZ = neckline_loop()
            # the neckline hugs the neck (a shirt's own neck hole is often much wider): measured around the neck's
            # own centre, never wider than the shirt's hole
            nk = neck_radius(ngrid, float(nZ.mean())) if not LAYER else None
            if LAYER:
                nR = nR + ease * 0.5
            if nk is not None:
                nc_, nr_ = nk
                _, _, nR, nZ = neckline_loop(nc_)
                nR = smooth_rows(np.clip(nR, nr_ + neck_ease * 0.7, nr_ + neck_ease + 0.012), 4, wrap=True)
            report["neck"] = {"r": [round(float(nR.min()), 3), round(float(nR.max()), 3)], "z": round(float(nZ.mean()), 3),
                              "hugs_neck": nk is not None}
            self.neck = (nc_, ngrid, nR - neck_inset, nZ - neck_inset)
            zc0 = float(np.interp(math.pi, ngrid, nZ))   # nominal neckline height = its front
            self.z_neck = zc0
            nominal_top = zc0 + 0.04
            if collar in ("band", "shirt"):
                for s_ in (1.0, 0.5):
                    rows.append(("collar", zc0 + (nominal_top - zc0) * s_, nc_, s_))
            rows.append(("neck", zc0, nc_, 0.0))
            NU = 8
            for i in range(1, NU):
                rows.append(("yoke", zc0 + (z_sh - zc0) * i / NU, None, i / NU))
            self.top_nominal = nominal_top if collar in ("band", "shirt") else zc0
        else:
            self.neck = None
            self.z_neck = None
            self.top_nominal = zs[0]
        for i, z in enumerate(zs):
            rows.append(("body", float(z), None, i))
        self.rows = rows
        self.z_hem = float(zs[-1])
        self.nc = nc

    def ring_at(self, phi, i):
        c = self.centres[i]
        r = periodic_interp(phi, self.angs, self.rings[i])
        d = np.stack([np.sin(phi), np.cos(phi)], -1)
        return np.stack([c[0] + d[:, 0] * r, c[1] + d[:, 1] * r, np.full(len(phi), self.zs[i])], -1), c

    def points(self, phi, row):
        kind, zn, c, s_ = row
        if kind == "body":
            p, c = self.ring_at(phi, s_)
            return p, c
        nc_, ngrid, nR, nZ = self.neck
        d = np.stack([np.sin(phi), np.cos(phi)], -1)
        r = periodic_interp(phi, ngrid, nR)
        z = periodic_interp(phi, ngrid, nZ)
        neck = np.stack([nc_[0] + d[:, 0] * r, nc_[1] + d[:, 1] * r, z], -1)
        if kind == "collar":
            k = 1 - 0.05 * s_
            p = neck.copy()
            p[:, 0] = nc_[0] + (p[:, 0] - nc_[0]) * k
            p[:, 1] = nc_[1] + (p[:, 1] - nc_[1]) * k
            p[:, 2] += 0.034 * s_
            return p, nc_
        if kind == "neck":
            return neck, nc_
        t = s_
        tt = t * t * (3 - 2 * t)
        h0, c0 = self.ring_at(phi, 0)
        c = nc_ * (1 - t) + c0 * t
        p = neck * (1 - tt) + h0 * tt
        p[:, 2] = neck[:, 2] * (1 - t) + h0[:, 2] * t
        # the yoke lies over the shoulders: raised to the top of the body (rays from above) + ease; a replacing top
        # follows the torso without the deltoids (the sleeve covers those), a top worn over the base uppr clears all of it
        bvh = SRC["shirt" if self.layer else "core"]["bvh"]
        for k, q in enumerate(p):
            loc, _, _, _ = bvh.ray_cast(Vector((q[0], q[1], q[2] + 0.4)), Vector((0, 0, -1.0)), 0.6)
            if loc is not None and loc[2] + self.ease * 0.8 > q[2]:
                p[k, 2] = loc[2] + self.ease * 0.8
        return p, c

    def circumference(self, i):
        p, _ = self.ring_at(self.angs, i)
        return float(np.sum(np.linalg.norm(np.roll(p, -1, 0)[:, :2] - p[:, :2], axis=1)))

    def v_of(self, z):
        """texture v of the row at nominal height z (after build)"""
        order = np.argsort(self.row_nom)
        return float(np.interp(z, self.row_nom[order], self.row_v[order]))

    def metres(self, dz):
        """a length along the cloth -> v units"""
        return dz / max(self.length, 0.1)

    def build(self, name="trunk", lining=True, src="trunk", mask=255):
        """two half panels (left: front -> back over the ped's left, right: back -> front), split at the back"""
        nc = self.nc
        halves = {"L": [], "R": []}
        for row in self.rows:
            zn = row[1]
            g = self.gap(zn)
            # radius at the front to turn the opening width into an angle
            if row[0] == "body":
                rf = float(periodic_interp(np.array([math.pi]), self.angs, self.rings[row[3]])[0])
            else:
                rf = float(periodic_interp(np.array([math.pi]), self.neck[1], self.neck[2])[0])
            ga = min(2.6 if row[0] == "body" else 1.1, g / max(rf, 0.05)) if g > 0 else 0.0
            # left half: phi from pi - ga/2 (front edge) down to 0 (back); right: 2pi (back) down to pi + ga/2
            phL = np.linspace(math.pi - ga / 2, 0.0, nc + 1)
            phR = np.linspace(TAU, math.pi + ga / 2, nc + 1)
            for key, ph in (("L", phL), ("R", phR)):
                p, c = self.points(ph, row)
                halves[key].append((p, c, ph))
        # the yoke rows (neckline -> shoulder ring): smooth along the ring and across the rows
        yk = [i for i, r in enumerate(self.rows) if r[0] == "yoke"]
        if yk:
            for key in ("L", "R"):
                arr = np.array([h[0] for h in halves[key]])
                for _ in range(3):
                    sm = arr.copy()
                    for i in yk:
                        sm[i, 1:-1] = (arr[i, :-2] + 2 * arr[i, 1:-1] + arr[i, 2:]) / 4
                        sm[i] = (arr[i - 1] + 2 * sm[i] + arr[i + 1]) / 4
                    arr = np.maximum(arr, sm) * [0, 0, 1] + sm * [1, 1, 0]
                for i in yk:
                    halves[key][i] = (arr[i], halves[key][i][1], halves[key][i][2])
        out = []
        # v = length along the cloth from the hem (so the collar / yoke are not squeezed into a few pixels)
        allp = np.concatenate([np.array([h[0] for h in halves["L"]]), np.array([h[0] for h in halves["R"]])], 1)
        step = np.linalg.norm(allp[1:] - allp[:-1], axis=2).mean(1)
        cum = np.concatenate([[0.0], np.cumsum(step[::-1])])[::-1]
        self.row_nom = np.array([r[1] for r in self.rows])
        self.row_v = cum / max(cum[0], 1e-6)
        self.length = float(cum[0])
        t = self.row_v
        for key in ("L", "R"):
            pts = np.array([h[0] for h in halves[key]])
            phs = np.array([h[2] for h in halves[key]])
            axis = np.array([[[h[1][0], h[1][1], q[2]] for q in h[0]] for h in halves[key]])
            s = 1 - phs / TAU     # u: back seam at both edges, front in the middle
            out.append(add_grid(f"{name}_{key}", pts, s, t, self.isl, src, axis, lining=lining, mask=mask))
        self.rowz = [r[1] for r in self.rows]
        self.halves = halves
        if self.collar == "shirt":
            add_collar_flap(self)
        return out

    def surface(self, phi, z):
        """a point of the shell (outer side) at angle phi, height z (body rows only)"""
        i = int(np.argmin(np.abs(self.zs - z)))
        p, c = self.ring_at(np.array([phi]), i)
        return p[0], c


def add_collar_flap(T_):
    """a shirt collar: the stand's top edge folds over, outwards and down, with points at the front"""
    island("flap", 0.67, 0.0, 0.83, 0.29)
    for key in ("L", "R"):
        top = T_.halves[key][0]
        pts0, c, ph = top
        rows = []
        front = np.abs(np.cos((ph - math.pi) / 2))   # 1 at the front edge, 0 at the back
        for k, (out, dz) in enumerate(((0.004, 0.003), (0.016, -0.004), (0.026, -0.022), (0.031, -0.04))):
            d = pts0[:, :2] - np.asarray(c[:2])
            d /= np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-6)
            row = pts0.copy()
            row[:, :2] += d * out
            row[:, 2] += dz - (0.03 * front ** 6 if k == 3 else 0.0)
            rows.append(row)
        axis = np.array([[[c[0], c[1], q[2]] for q in r] for r in rows])
        s_ = 1 - ph / TAU
        add_grid(f"flap_{key}", np.array(rows), s_, np.linspace(1, 0, 4), "flap", "trunk", axis)


# ===================================================================================================== sleeves
def sleeve(side, length, width=1.0, cuff_flare=0.0, ease=0.02, isl=None, n_rows=30, ns=24, start_in=0.07):
    """a tube from inside the shoulder to `length` (0..1 of shoulder -> wrist), radius from the arm around it"""
    s = "L" if side > 0 else "R"
    ua, fa, hd = bone(f"SKEL_{s}_UpperArm"), bone(f"SKEL_{s}_Forearm"), bone(f"SKEL_{s}_Hand")
    start = ua + np.array([-start_in * np.sign(ua[0]), 0.0, -0.01])
    wrist = fa + (hd - fa) * 0.93
    path = [start, ua, fa, wrist]
    seg = [np.linalg.norm(path[i + 1] - path[i]) for i in range(3)]
    total = sum(seg)
    AP = SRC["arm"]["pos"]
    rows = []
    for k in range(n_rows + 1):
        sk = length * k / n_rows
        d = total * sk
        i = 0
        while i < 2 and d > seg[i]:
            d -= seg[i]
            i += 1
        p = path[i] + (path[i + 1] - path[i]) * min(1.0, d / seg[i])
        tdir = (path[i + 1] - path[i]) / seg[i]
        a = np.cross(tdir, np.array([0, 1.0, 0]))
        a /= np.linalg.norm(a)
        b = np.cross(tdir, a)
        rel = AP - p
        along = rel @ tdir
        sel_ = (np.abs(along) < 0.03) & (np.linalg.norm(rel - np.outer(along, tdir), axis=1) < 0.13)
        sel_ &= (AP[:, 0] * side > 0.05)
        q = rel[sel_]
        if len(q) >= 3:
            q2 = np.stack([q @ a, q @ b], 1)
            c2 = q2.mean(0)
            rad = float(np.percentile(np.linalg.norm(q2 - c2, axis=1), 86))
            centre = p + a * c2[0] + b * c2[1]
        else:
            rad, centre = 0.06, p
        rows.append([sk, centre, tdir, a, b, rad])
    r = smooth_rows(np.array([x[5] for x in rows]), 4)
    c = smooth_rows(np.array([x[1] for x in rows]), 4)
    pts, axis = [], []
    ang = TAU * np.arange(ns + 1) / ns
    for i, x in enumerate(rows):
        f = x[0] / max(length, 1e-6)
        # narrower where it starts inside the shoulder (no puff above the shoulder line), flared towards the cuff
        cuff = (0.62 + 0.38 * min(1.0, x[0] / 0.32) ** 0.7) * (1 + cuff_flare * max(0.0, (f - 0.45) / 0.55) ** 1.5)
        base = min(max(r[i], 0.045), 0.085) + ease
        rad = base * width * cuff if x[0] > 0.2 else base * cuff * (1 + (width - 1) * x[0] / 0.2)
        x[5], x[1] = rad, c[i] - np.array([0, 0, 0.025 * max(0.0, 1 - x[0] / 0.3)])
        # angle 0 = under the arm (-b side), so the UV seam runs along the underside
        ring = np.array([x[1] + (x[3] * math.sin(t) - x[4] * math.cos(t)) * rad for t in ang])
        if x[0] < 0.3:
            # the sleeve cap meets the shoulder: its top eased down to the cloth over the shoulder (no puff)
            for k, q in enumerate(ring):
                if q[2] > x[1][2]:
                    zt = shoulder_top(q)
                    if zt is not None and q[2] > zt + 0.015:
                        ring[k, 2] = q[2] - (q[2] - zt - 0.015) * 0.6 * (1 - x[0] / 0.3)
        pts.append(ring)
        axis.append([x[1]] * (ns + 1))
    pts = np.array(pts)
    isl = isl or f"sleeve_{s}"
    s_ = ang / TAU if side > 0 else 1 - ang / TAU
    t = 1 - np.array([x[0] for x in rows]) / max(length, 1e-6)
    pan = add_grid(f"sleeve_{s}", pts, s_, t, isl, "arm", np.array(axis))
    pan.meta = {"rows": rows, "length": length, "total": total}
    return pan


def shoulder_top(p):
    """height of the cloth that lies over the shoulder at p (the yoke's surface: top of the torso + ease)"""
    bvh = SRC["shirt" if LAYER else "core"]["bvh"]
    loc, _, _, _ = bvh.ray_cast(Vector((p[0], p[1], p[2] + 0.4)), Vector((0, 0, -1.0)), 0.6)
    return None if loc is None else loc[2] + 0.02


def arm_reach(side):
    """how far down the arm (0..1 of shoulder -> wrist) the base uppr reaches: a replacing sleeve must reach it"""
    s = "L" if side > 0 else "R"
    ua, fa, hd = bone(f"SKEL_{s}_UpperArm"), bone(f"SKEL_{s}_Forearm"), bone(f"SKEL_{s}_Hand")
    start = ua + np.array([-0.07 * np.sign(ua[0]), 0.0, -0.01])
    wrist = fa + (hd - fa) * 0.93
    path = [start, ua, fa, wrist]
    seg = [np.linalg.norm(path[i + 1] - path[i]) for i in range(3)]
    acc = [0, seg[0], seg[0] + seg[1], sum(seg)]
    pts = U["pos"][np.array([is_arm(n) for n in U["dom"]]) & (U["pos"][:, 0] * side > 0.05)]
    if not len(pts):
        return 0.0
    best = 0.0
    for i in range(3):
        a, b = path[i], path[i + 1]
        tt = np.clip((pts - a) @ (b - a) / (seg[i] ** 2), 0, 1)
        near = np.linalg.norm(pts - (a + np.outer(tt, b - a)), axis=1) < 0.15
        if near.any():
            best = max(best, (acc[i] + float(tt[near].max()) * seg[i]) / acc[3])
    return best


# ===================================================================================================== legs
def leg_tube(side, z_top, z_hem, ease, width=1.0, isl=None, nl=40, inner=True, src_key="legs", flare=0.0,
             name=None, mask=255, lining=True, shrink=None):
    Pp = SRC[src_key]["pos"]
    dom = np.array([any(k in n for k in LEG[side]) for n in L["dom"]]) if src_key == "legs" else np.ones(len(Pp), bool)
    zs = list(np.arange(z_top, z_hem - 1e-6, -0.02))
    if zs[-1] > z_hem + 1e-6:
        zs.append(z_hem)
    hulls, cents = [], []
    for z in zs:
        band = np.abs(Pp[:, 2] - z) < 0.025
        sel_ = band & ((Pp[:, 0] * side > -0.005) if z > Z["crotch"] else (dom | (Pp[:, 0] * side > 0.02)))
        q = Pp[sel_][:, :2]
        h = hull2d(q) if len(q) >= 3 else np.zeros((0, 2))
        hulls.append(h)
        cents.append(h.mean(0) if len(h) >= 3 else np.array([0.1 * side, 0.02]))
    cents = smooth_rows(np.array(cents), 12)
    ang = TAU * np.arange(nl + 1) / nl          # 0 = the inner side (towards the other leg)
    rings = []
    for h, c in zip(hulls, cents):
        rr = []
        for a in ang:
            d = np.array([-side * math.cos(a), -math.sin(a)])   # a = 0 inner side, pi/2 front, pi outer, 3pi/2 back
            rr.append(ray_hull(h, c, d) if len(h) >= 3 else 1e9)
        rings.append(rr)
    rings = np.array(rings)
    good = [i for i in range(len(zs)) if rings[i].max() < 1.0]
    for i in range(len(zs)):
        if i not in good:
            j = min(good, key=lambda g: abs(g - i))
            rings[i] = rings[j]
            cents[i] = cents[j]
    rings = smooth_rows(np.maximum.reduce([np.roll(rings, s_, 0) for s_ in (-1, 0, 1)]), 4)
    pts, axis = [], []
    for i, z in enumerate(zs):
        c = cents[i]
        sc = width * (1 + flare * max(0.0, (Z["knee"] - z) / max(Z["knee"] - Z["shoe"], 0.1)))
        if shrink is not None:
            sc = shrink(z)
        row = []
        for k, a in enumerate(ang):
            d = np.array([-side * math.cos(a), -math.sin(a)])
            r = rings[i][k] * sc + ease
            p = np.array([c[0] + d[0] * r, c[1] + d[1] * r, z])
            if inner and z > Z["crotch"] - 0.01:
                p[0] = side * max(side * p[0], 0.0015)   # the two legs meet in the middle (hidden inner wall)
            row.append(p)
        pts.append(row)
        axis.append([[c[0], c[1], z]] * len(ang))
    s = "L" if side > 0 else "R"
    isl = isl or f"leg_{s}"
    s_ = ang / TAU if side > 0 else 1 - ang / TAU
    t = (np.array(zs) - zs[-1]) / max(zs[0] - zs[-1], 1e-6)
    pan = add_grid(name or f"leg_{s}", np.array(pts), s_, t, isl, src_key, np.array(axis), mask=mask, lining=lining)
    pan.meta = {"zs": np.array(zs), "cents": cents}
    return pan


def tuck_under_shirt(pans, gap=0.008):
    """lower garments where the base uppr hangs over them: stay inside it (the shirt tail covers the waistband)"""
    bvh = SRC["shirt"]["bvh"]
    z_lo = float(U["pos"][:, 2].min())
    moved = 0
    for pan in pans:
        R, C = pan.pts.shape[:2]
        for i in range(R):
            for j in range(C):
                p = pan.pts[i, j]
                if p[2] < z_lo + 0.005:
                    continue
                a = pan.axis[i, j]
                d = np.array([p[0] - a[0], p[1] - a[1], 0.0])
                r = np.linalg.norm(d)
                if r < 1e-6:
                    continue
                d /= r
                loc, _, _, _ = bvh.ray_cast(Vector((a[0], a[1], p[2])), Vector(d), 0.6)
                if loc is None:
                    continue
                h = math.hypot(loc[0] - a[0], loc[1] - a[1]) - gap
                if r > h > 0.02:
                    pan.pts[i, j, :2] = np.array(a[:2]) + d[:2] * h
                    moved += 1
    report["tucked_points"] = moved


# ===================================================================================================== head shells
HEAD_BVH = SRC["head"]["bvh"] if SRC["head"] is not None else None


def head_radius(phi, e):
    """distance from the head centre to the outside of head + hair in direction (phi, elevation e)"""
    d = np.array([math.cos(e) * math.sin(phi), math.cos(e) * math.cos(phi), math.sin(e)])
    if HEAD_BVH is None:
        return 0.11, d
    o = HEAD_C + d * 0.6
    loc, _, _, _ = HEAD_BVH.ray_cast(Vector(o), Vector(-d), 0.6)
    if loc is None:
        return None, d
    return float(np.linalg.norm(np.array(loc) - HEAD_C)), d


def head_grid(phis, es):
    R = np.full((len(es), len(phis)), np.nan)
    for i, e in enumerate(es):
        for j, ph in enumerate(phis):
            r, _ = head_radius(ph, e)
            if r is not None:
                R[i, j] = r
    # holes (no hit: under the jaw, the neck) from the row above / neighbours
    for i in range(len(es)):
        row = R[i]
        if np.isnan(row).all():
            R[i] = R[i - 1] if i else 0.11
        elif np.isnan(row).any():
            ok = ~np.isnan(row)
            R[i] = np.interp(np.arange(len(row)), np.nonzero(ok)[0], row[ok])
    return R


def head_shell(name, isl, e_lo, e_hi, gap_deg, ease, nr=26, nc=28, extra=None, src="head", mask=255, lining=True,
               back_drop=0.0, drape=None):
    """shell over the head from elevation e_lo to e_hi (degrees); gap_deg(e) = face opening (front) in degrees;
    split at the back into two halves (like the trunk)"""
    es = np.radians(np.linspace(e_hi, e_lo, nr))
    out = []
    for key in ("L", "R"):
        pts, axis, S = [], [], []
        for e in es:
            g = math.radians(gap_deg(math.degrees(e)))
            ph = np.linspace(math.pi - g / 2, 0.0, nc + 1) if key == "L" else np.linspace(TAU, math.pi + g / 2, nc + 1)
            R = head_grid(ph, [e])[0]
            row = []
            for j, phi in enumerate(ph):
                d = np.array([math.cos(e) * math.sin(phi), math.cos(e) * math.cos(phi), math.sin(e)])
                r = R[j] + ease + (extra(phi, math.degrees(e)) if extra else 0.0)
                p = HEAD_C + d * r
                if back_drop:
                    p[2] -= back_drop * max(0.0, math.cos(phi)) * max(0.0, -math.sin(e))
                row.append(p)
            pts.append(row)
            axis.append([HEAD_C] * len(ph))
            S.append(1 - ph / TAU)
        pts = np.array(pts)
        for j in range(pts.shape[1]):
            pts[:, j] = smooth_rows(pts[:, j], 1)
        axis, S = np.array(axis), np.array(S)
        blend = None
        if drape is not None:
            extra_rows = drape(pts)
            n_x = len(extra_rows)
            blend = (len(pts), n_x)
            pts = np.concatenate([pts, extra_rows], 0)
            ax2 = np.repeat(axis[-1:], n_x, 0).copy()
            ax2[:, :, 2] = np.minimum(HEAD_C[2], extra_rows[:, :, 2] + 0.05)
            axis = np.concatenate([axis, ax2], 0)
            S = np.concatenate([S, np.repeat(S[-1:], n_x, 0)], 0)
        t = np.linspace(1, 0, len(pts))
        out.append(add_grid(f"{name}_{key}", pts, S, t, isl, src, axis, mask=mask, lining=lining, blend=blend))
    return out


# ===================================================================================================== tubes (cords, tori)
def in_front(path, gap=0.007):
    """move cord points in front of the garment built so far (ray from the front, -Y side)"""
    tris, pts = [], []
    for pan in panels:
        R, C = pan.pts.shape[:2]
        base = len(pts)
        pts += [tuple(p) for p in pan.pts.reshape(-1, 3)]
        for i in range(R - 1):
            for j in range(C - 1):
                a = base + i * C + j
                tris += [(a, a + 1, a + C + 1), (a, a + C + 1, a + C)]
    if not tris:
        return np.asarray(path)
    bvh = BVHTree.FromPolygons(pts, tris)
    out = np.array(path, float)
    for k, p in enumerate(out):
        loc, _, _, _ = bvh.ray_cast(Vector((p[0], p[1] - 0.6, p[2])), Vector((0, 1, 0)), 1.2)
        if loc is not None:
            out[k, 1] = min(p[1], loc[1] - gap)
    return out


def tube(name, path, radius, isl, src, closed=False, ns=10, mask=255, cap_end=True):
    """a round tube along a path (N,3); radius scalar or per point; closed = a torus"""
    path = np.asarray(path, float)
    n = len(path)
    radius = np.broadcast_to(np.asarray(radius, float), (n,))
    pts, axis = [], []
    up = np.array([0, 0, 1.0])
    for i in range(n):
        if closed:
            tng = path[(i + 1) % n] - path[i - 1]
        else:
            tng = path[min(i + 1, n - 1)] - path[max(i - 1, 0)]
        tng /= max(np.linalg.norm(tng), 1e-9)
        a = np.cross(tng, up if abs(tng @ up) < 0.9 else np.array([1.0, 0, 0]))
        a /= np.linalg.norm(a)
        b = np.cross(tng, a)
        ring = [path[i] + (a * math.cos(t) + b * math.sin(t)) * radius[i] for t in TAU * np.arange(ns + 1) / ns]
        pts.append(ring)
        axis.append([path[i]] * (ns + 1))
    pts = np.array(pts)
    if closed:
        pts = np.concatenate([pts, pts[:1]], 0)
        axis = axis + axis[:1]
    t = np.linspace(1, 0, len(pts))
    s = np.arange(ns + 1) / ns
    return add_grid(name, pts, s, t, isl, src, np.array(axis), mask=mask, lining=False)


# ===================================================================================================== build the template
TEX = 1024
FEATURES = []   # drawing instructions for the texture painter: (kind, args)
BASE = np.array([int(P.get("color", "#cccccc")[i:i + 2], 16) for i in (1, 3, 5)], np.float32) if "color" in P else None
lower_cut = None   # for hide_lower == "cut": height above which the base lowr is removed
# a top on jbib / task / accs is worn OVER the base uppr: it keeps clear of it (neck hole, sleeves)
LAYER = job["pieces"][0]["comp"] in ("jbib", "task", "accs") and TPL["group"] in ("upper", "long", "extra")


def long_robe(collar, sleeve_len, sleeve_w, ease, flare, gap=None, cuff_flare=0.0, inner_layer=False, v_depth=None,
              trunk_isl=(0.0, 0.0, 0.66, 1.0)):
    island("trunk", *trunk_isl)
    hem = lerp(Z["knee"], Z["shoe"], pct("length", 100))
    def vgap(z, _v=v_depth):   # a V from the neckline down to v_depth
        zt = T_.z_neck if T_.z_neck is not None else Z["neck"]
        w0 = 0.07 + _v * 0.15
        return w0 if z >= zt - 0.001 else max(0.0, w0 * (1 - (zt - z) / _v))
    T_ = Trunk(hem, ease, flare, collar=collar, gap=vgap if v_depth is not None else gap)
    T_.build()
    reach = max(arm_reach(1), arm_reach(-1))
    if sleeve_len < reach + 0.02:
        report.setdefault("notes", []).append(f"sleeve {sleeve_len:.2f} -> {reach:.2f}: the base uppr reaches further down the arm")
        sleeve_len = reach + 0.02
    island("sleeve_L", 0.67, 0.30, 0.83, 1.0)
    island("sleeve_R", 0.84, 0.30, 1.0, 1.0)
    sl = [sleeve(1, sleeve_len, sleeve_w, cuff_flare, ease=0.02), sleeve(-1, sleeve_len, sleeve_w, cuff_flare, ease=0.02)]
    if inner_layer:
        island("inner", 0.84, 0.0, 1.0, 0.29)
        Ti = Trunk(hem + 0.03, max(ease - 0.012, 0.006), flare * 0.8, collar="none", neck_inset=0.006)
        Ti.isl = "inner"
        Ti.build(name="inner", lining=False, mask=0 if TPL["id"] == "robe" else 255)
    return T_, sl, hem


def build_template():
    global lower_cut
    tid = TPL["id"]
    ease = cm("ease", 2)
    if tid in ("thobe", "kandura"):
        collar = P.get("collar", "none") if tid == "thobe" else "none"
        T_, sl, hem = long_robe(collar, pct("sleeve"), pct("sleeve_width"), ease, pct("flare"))
        FEATURES.append(("long_trunk", T_, {"placket": tid == "thobe", "buttons": int(P.get("buttons", 0)),
                                            "collar": collar, "folds": True, "fabric": "cotton"}))
        FEATURES.append(("sleeves", sl, {"cuff": True}))
        if tid == "kandura" and cm("tassel", 0) > 0.01:
            add_tassel(T_, cm("tassel"))
        lower_cut = hem + 0.06
    elif tid == "abaya":
        open_ = P.get("opening") == "open"
        gap = (lambda z, w=cm("open_width"): w) if open_ else None
        T_, sl, hem = long_robe("none", pct("sleeve"), 1.0 + (pct("sleeve_width") - 1) * 0.35, ease, pct("flare"),
                                gap=gap, cuff_flare=(pct("sleeve_width") - 1) * 1.4, inner_layer=open_)
        FEATURES.append(("long_trunk", T_, {"folds": True, "fabric": "crepe", "trim": P.get("trim"), "open": open_}))
        FEATURES.append(("sleeves", sl, {"cuff": False, "trim": P.get("trim")}))
        lower_cut = hem + 0.06
    elif tid == "robe":
        T_, sl, hem = long_robe("none", pct("sleeve"), pct("sleeve_width"), ease, pct("flare"),
                                v_depth=cm("v_depth"), inner_layer=True)
        FEATURES.append(("long_trunk", T_, {"folds": True, "fabric": P.get("fabric", "terry"), "lapel": True}))
        FEATURES.append(("sleeves", sl, {"cuff": True, "fabric": P.get("fabric", "terry")}))
        if P.get("belt"):
            add_belt(T_)
        lower_cut = hem + 0.06
    elif tid == "bisht":
        island("trunk", 0.0, 0.0, 0.66, 1.0)
        hem = lerp(Z["knee"], Z["shoe"], pct("length")) + 0.02
        T_ = Trunk(hem, ease, pct("flare"), collar="none", gap=lambda z, w=cm("open_width"): w)
        T_.build()
        island("sleeve_L", 0.67, 0.30, 0.83, 1.0)
        island("sleeve_R", 0.84, 0.30, 1.0, 1.0)
        sl = [sleeve(1, pct("sleeve"), 1.3, 0.55, ease=0.035), sleeve(-1, pct("sleeve"), 1.3, 0.55, ease=0.035)]
        FEATURES.append(("long_trunk", T_, {"fabric": "wool", "trim": P.get("trim"), "open": True, "gold": True}))
        FEATURES.append(("sleeves", sl, {"trim": P.get("trim"), "gold": True}))
    elif tid in ("tshirt", "shirt", "hoodie", "jacket", "vest"):
        island("trunk", 0.0, 0.0, 0.66, 1.0)
        hem = lerp(Z["pelvis"], Z["midthigh"], pct("length"))
        collar, gap, vd = "none", None, None
        if tid == "shirt":
            collar = "shirt"
            gap = lambda z: 0.022 if z > T_.z_neck + 0.002 else 0.0  # noqa: E731
        if tid == "jacket":
            collar = P.get("collar", "band")
            if P.get("opening") == "open":
                gap = lambda z, w=cm("open_width"): w  # noqa: E731
        vneck = (tid == "tshirt" and P.get("neck") == "v") or tid == "vest"
        if vneck:
            vd = 0.10 if tid == "tshirt" else cm("v_depth")
        T_ = Trunk(hem, ease, 0.0, collar=collar, gap=gap)
        if vneck:
            def gapf(z, T_=T_, vd=vd):
                zt = T_.z_neck
                w0 = 0.08 if tid == "tshirt" else 0.12
                if z >= zt - 0.001:
                    return w0
                return max(0.0, w0 * (1 - (zt - z) / vd))
            T_.gap = gapf
        T_.build()
        sl = []
        if tid != "vest":
            island("sleeve_L", 0.67, 0.30, 0.83, 1.0)
            island("sleeve_R", 0.84, 0.30, 1.0, 1.0)
            ln = pct("sleeve")
            if True:
                reach = max(arm_reach(1), arm_reach(-1))
                if ln < reach + 0.02:
                    report.setdefault("notes", []).append(f"sleeve {ln:.2f} -> {reach + 0.03:.2f} (the base uppr reaches further)")
                    ln = reach + 0.03
            w = pct("sleeve_width", 100) if "sleeve_width" in P else 1.0
            sl = [sleeve(1, ln, w, 0.0, ease=0.016 + ease * 0.3), sleeve(-1, ln, w, 0.0, ease=0.016 + ease * 0.3)]
        fab = {"tshirt": "knit", "hoodie": "fleece", "jacket": "nylon", "vest": "suiting", "shirt": "poplin"}[tid]
        FEATURES.append(("top_trunk", T_, {"fabric": fab, "kind": tid, "buttons": int(P.get("buttons", 0)),
                                           "pocket": P.get("pocket") or P.get("pockets"), "open": P.get("opening") == "open",
                                           "zip": tid == "jacket", "collar": collar}))
        if sl:
            FEATURES.append(("sleeves", sl, {"cuff": tid in ("shirt", "hoodie", "jacket"), "fabric": fab,
                                             "rib": tid in ("hoodie",)}))
        if tid == "hoodie" and P.get("hood") != "none":
            add_hood(T_, P.get("hood"))
            if P.get("strings"):
                add_strings(T_)
    elif tid in ("trousers", "shorts"):
        island("leg_L", 0.0, 0.0, 0.42, 1.0)
        island("leg_R", 0.42, 0.0, 0.84, 1.0)
        if tid == "trousers":
            hem = lerp(Z["knee"], Z["shoe"], pct("length"))
        else:
            hem = lerp(Z["crotch"] - 0.05, Z["knee"] + 0.02, pct("length"))
        legs = [leg_tube(s, Z["waist"], hem, ease, pct("width")) for s in (1, -1)]
        tuck_under_shirt(legs)
        FEATURES.append(("trousers", legs, {"fabric": P.get("fabric", "twill") if tid == "trousers" else "twill"}))
        if hem > Z["shoe"] + 0.08:
            add_skin_legs(hem + 0.04)
    elif tid == "skirt":
        island("trunk", 0.0, 0.0, 0.70, 1.0)
        hem = lerp(Z["crotch"] - 0.08, Z["shoe"] + 0.04, pct("length"))
        T_ = Trunk(hem, ease, pct("flare") * 1.6, top="z", z_top=Z["waist"], src_key="legs", flare_from=Z["crotch"] + 0.05)
        tuck_under_shirt(T_.build())
        FEATURES.append(("skirt", T_, {"fabric": "twill", "pleats": P.get("pleats")}))
        if hem > Z["shoe"] + 0.08:
            add_skin_legs(hem + 0.05, isl=(0.71, 0.0, 1.0, 1.0))
    elif tid == "shemagh":
        add_shemagh()
    elif tid == "cap":
        add_cap()
    else:
        raise RuntimeError(f"no builder for {tid}")


def add_tassel(T_, length):
    island("tassel", 0.67, 0.0, 0.83, 0.12)
    z0 = T_.z_neck - 0.012
    path, rad = [], []
    n = 16
    for k in range(n + 1):
        z = z0 - length * k / n
        p, c = T_.surface(math.pi, z) if z < T_.zs[0] else (None, None)
        if p is None:
            nc_, ngrid, nR, nZ = T_.neck
            r = float(periodic_interp(np.array([math.pi]), ngrid, nR)[0])
            p = np.array([nc_[0], nc_[1] - r, z])
        path.append(p + np.array([0, -0.007, 0]))
        rad.append(0.0035)
    path = list(in_front(path, 0.008))
    tip = path[-1]
    for k in range(1, 7):
        path.append(tip + np.array([0, -0.002 * k, -0.011 * k]))
        rad.append(0.004 + 0.009 * math.sin(min(1.0, k / 5) * math.pi / 2) * (1 if k < 6 else 0.3))
    tube("tassel", path, rad, "tassel", "trunk", ns=10)


def add_belt(T_):
    island("belt", 0.67, 0.0, 0.83, 0.29)
    zb = Z["pelvis"] + 0.04
    i = int(np.argmin(np.abs(T_.zs - zb)))
    ph = np.linspace(0, TAU, 65)
    ring, c = T_.ring_at(ph, i)
    rows = []
    for dz, out in ((0.022, 0.006), (0.0, 0.009), (-0.022, 0.006)):
        d = np.stack([np.sin(ph), np.cos(ph), np.zeros_like(ph)], -1)
        rows.append(ring + d * out + np.array([0, 0, dz]))
    axis = np.array([[[c[0], c[1], ring[0][2]]] * len(ph)] * 3)
    add_grid("belt", np.array(rows), ph / TAU, np.array([1, 0.5, 0]), "belt", "trunk", axis, lining=False)
    # the knot's two ends hanging at the front
    front, _ = T_.surface(math.pi, zb)
    for k, sx in enumerate((-1, 1)):
        top = front + np.array([0.03 * sx, -0.016, -0.005])
        rows, axis = [], []
        for j in range(9):
            t = j / 8
            cen = top + np.array([0.025 * sx * t, -0.004 - 0.01 * t, -0.30 * t])
            rows.append([cen + np.array([-0.022, 0, 0]), cen + np.array([0.022, 0, 0])])
            axis.append([cen + np.array([0, 0.05, 0])] * 2)
        add_grid(f"belt_end{k}", np.array(rows), np.array([0.0, 1.0]), np.linspace(1, 0, 9), "belt", "trunk",
                 np.array(axis), lining=True, lining_dir=np.array([0, 1.0, 0]))


def add_hood(T_, mode):
    if mode == "up":
        island("hood", 0.67, 0.0, 0.83, 0.29)
        head_shell("hood", "hood", -48, 88, lambda e: 128 if e < 18 else max(0.0, 128 * (1 - (e - 18) / 50)),
                   0.028, extra=lambda phi, e: 0.022 * max(0.0, math.cos(phi)) * (1 - abs(e) / 90))
        FEATURES.append(("hood", None, {}))
    else:
        island("hood", 0.67, 0.0, 0.83, 0.29)
        nc_, ngrid, nR, nZ = T_.neck
        ph = np.linspace(math.radians(-115), math.radians(115), 25) % TAU
        r = periodic_interp(ph, ngrid, nR)
        z = periodic_interp(ph, ngrid, nZ)
        path = np.stack([nc_[0] + np.sin(ph) * (r + 0.045), nc_[1] + np.cos(ph) * (r + 0.045), z - 0.05], -1)
        path = push_out(path, SRC["shirt"], 0.04)
        rad = 0.035 * np.sin(np.linspace(0.25, math.pi - 0.25, len(path))) ** 0.5
        tube("hood_down", path, rad, "hood", "trunk", ns=14)
        FEATURES.append(("hood", None, {}))


def add_strings(T_):
    island("strings", 0.84, 0.0, 1.0, 0.12)
    for k, sx in enumerate((-1, 1)):
        nc_, ngrid, nR, nZ = T_.neck
        ph = math.pi + sx * 0.35
        r = float(periodic_interp(np.array([ph]), ngrid, nR)[0])
        p0 = np.array([nc_[0] + math.sin(ph) * r, nc_[1] + math.cos(ph) * r,
                       float(periodic_interp(np.array([ph]), ngrid, nZ)[0]) - 0.02])
        path = [p0 + np.array([0.004 * sx * t, -0.006 * t, -0.17 * t]) for t in np.linspace(0, 1, 10)]
        tube(f"string{k}", in_front(path, 0.006), 0.0035, "strings", "trunk", ns=8)


def add_skin_legs(z_top, isl=(0.84, 0.0, 1.0, 1.0)):
    """lowr replaced by something shorter: the legs below it in the ped's skin colour (no leg mesh exists to copy)"""
    u0, v0, u1, v1 = isl
    island("skin_L", u0, v0, (u0 + u1) / 2, v1)
    island("skin_R", (u0 + u1) / 2, v0, u1, v1)
    ze = Z["shoe"] + 0.02

    def shrink(z):
        k = (z_top - z) / max(z_top - ze, 0.1)
        return 0.62 - 0.14 * k
    for s in (1, -1):
        leg_tube(s, z_top, ze, 0.0, isl=f"skin_{'L' if s > 0 else 'R'}", nl=24, inner=False, name=f"skin_{s}", mask=0,
                 lining=False, shrink=shrink)
    FEATURES.append(("skin", None, {}))


def add_shemagh():
    island("shemagh", 0.0, 0.0, 1.0, 0.80)
    island("agal", 0.0, 0.81, 1.0, 1.0)
    ease = cm("ease", 1.0)
    drape = pct("drape", 60)

    def gap(e):
        return 96 if e < 12 else max(0.0, 96 * (1 - (e - 12) / 26))
    z_end = Z["shoulder"] - 0.06 - 0.20 * drape

    def drape_rows(pts, n_extra=9):
        last = pts[-1]
        zm = float(last[:, 2].mean())
        new, prev = [], last.copy()
        for k in range(1, n_extra + 1):
            t = k / n_extra
            d = prev[:, :2] - HEAD_C[:2]
            nd = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-6)
            row = prev.copy()
            row[:, 2] = np.minimum(prev[:, 2] - 0.004, last[:, 2] + (z_end - zm) * t)
            row[:, :2] += nd * 0.004
            row = wrap_to(row, "trunk", HEAD_C, 0.014 + ease)
            row = push_out(row, SRC["trunk"], 0.010 + ease)
            new.append(row)
            prev = row
        return np.array(new)
    head_shell("shemagh", "shemagh", -42, 88, gap, ease + 0.006,
               extra=lambda phi, e: 0.012 * max(0.0, math.cos(phi)) * max(0.0, -e / 90 + 0.3), drape=drape_rows)
    FEATURES.append(("shemagh", None, {"pattern": P.get("pattern", "red")}))
    if P.get("agal", True):
        for k, de in enumerate((40, 50)):
            e = math.radians(de)
            ph = np.linspace(0, TAU, 49)[:-1]
            path = []
            for phi in ph:
                r, d = head_radius(phi, e)
                path.append(HEAD_C + d * ((r or 0.11) + ease + 0.006 + 0.011))
            path = smooth_rows(np.array(path), 3, wrap=True)
            tube(f"agal{k}", path, 0.0075, "agal", "head", closed=True, ns=10, mask=0)


def add_cap():
    island("crown", 0.0, 0.0, 0.70, 1.0)
    island("brim", 0.71, 0.0, 1.0, 1.0)
    ease = cm("ease", 0.8)
    back = bool(P.get("backwards"))
    front = 0.0 if back else math.pi

    def e_rim(phi):
        c = math.cos(phi - front)     # 1 at the front of the cap
        return 8 + 12 * max(0.0, c) ** 1.2 - 2 * max(0.0, -c)
    nr, nc = 18, 64
    pts, axis = [], []
    ph = np.linspace(0, TAU, nc + 1)
    for i in range(nr):
        t = i / (nr - 1)
        row = []
        for phi in ph:
            e = math.radians(lerp(88, e_rim(phi), t))
            r, d = head_radius(phi, e)
            r = (r or 0.11) + ease + 0.006 * (1 - t) ** 2
            row.append(HEAD_C + d * r)
        pts.append(row)
        axis.append([HEAD_C] * len(ph))
    pts = np.array(pts)
    for j in range(pts.shape[1]):
        pts[:, j] = smooth_rows(pts[:, j], 1)
    pts = smooth_rows(pts, 2, axis=1)
    rim = pts[-1]
    add_grid("crown", pts, ph / TAU, np.linspace(1, 0, nr), "crown", "head", np.array(axis))
    # brim: a curved plate from the front arc of the rim
    L_ = cm("brim", 7)
    arc = [j for j, phi in enumerate(ph[:-1]) if math.cos(phi - front) > 0.45]
    arc = sorted(arc, key=lambda j: (ph[j] - front - math.pi) % TAU)
    rows, ax = [], []
    nb = 8
    for k in range(nb + 1):
        t = k / nb
        row = []
        for j in arc:
            p = rim[j]
            d = p[:2] - HEAD_C[:2]
            d /= np.linalg.norm(d)
            fwd = np.array([math.sin(front), math.cos(front)])
            dd = d * 0.55 + fwd * 0.45
            dd /= np.linalg.norm(dd)
            q = p + np.array([dd[0], dd[1], 0]) * L_ * t * (0.6 + 0.4 * math.cos(ph[j] - front))
            q[2] -= 0.012 * t * t + 0.004 * t
            row.append(q)
        rows.append(row)
        ax.append([HEAD_C + np.array([0, 0, 0.3])] * len(arc))
    s = np.linspace(0, 1, len(arc))
    add_grid("brim", np.array(rows), s, np.linspace(1, 0, nb + 1), "brim", "head", np.array(ax), lining=True,
             lining_dir=np.array([0, 0, -1.0]))
    FEATURES.append(("cap", None, {"back": back}))


# ===================================================================================================== mesh assembly
def assemble():
    verts, uvs, faces, srcs, masks, axes, blends = [], [], [], [], [], [], []
    face_mask = []
    twin = []   # lining vertex -> its outer vertex (same weights, so the two layers never part)
    for pan in panels:
        R, C = pan.pts.shape[:2]
        base = len(verts)
        for i in range(R):
            for j in range(C):
                verts.append(pan.pts[i, j])
                uvs.append(pan.uv[i, j])
                srcs.append(pan.src)
                masks.append(pan.mask)
                axes.append(pan.axis[i, j] if pan.axis is not None else pan.pts[i].mean(0))
                if pan.blend and i >= pan.blend[0]:
                    blends.append(min(1.0, (i - pan.blend[0] + 1) / max(1, pan.blend[1] * 0.5)))
                elif pan.blend and i >= pan.blend[0] - 2:
                    blends.append(0.15 * (i - pan.blend[0] + 3))
                else:
                    blends.append(0.0 if pan.blend else None)
                twin.append(-1)
        idx = lambda i, j: base + i * C + j  # noqa: E731
        quads = []
        for i in range(R - 1):
            for j in range(C - 1):
                quads.append((idx(i, j), idx(i, j + 1), idx(i + 1, j + 1), idx(i + 1, j)))
        # outward: normal away from the axis
        flips = 0
        oriented = []
        for q in quads:
            P4 = np.array([verts[k] for k in q])
            n = np.cross(P4[2] - P4[0], P4[3] - P4[1])
            ax = np.mean([axes[k] for k in q], 0)
            ref = (P4.mean(0) - ax) if pan.lining_dir is None else -np.asarray(pan.lining_dir)
            if n @ ref < 0:
                q = q[::-1]
                flips += 1
            oriented.append(q)
        faces += oriented
        face_mask += [pan.mask] * len(oriented)
        if pan.lining:
            lb = len(verts)
            for i in range(R):
                for j in range(C):
                    p = pan.pts[i, j]
                    if pan.lining_dir is not None:
                        d = np.asarray(pan.lining_dir, float) / np.linalg.norm(pan.lining_dir)
                    else:
                        a = pan.axis[i, j] if pan.axis is not None else pan.pts[i].mean(0)
                        d = a - p
                        d /= max(np.linalg.norm(d), 1e-9)
                    verts.append(p + d * 0.004)
                    uvs.append(pan.uv[i, j])
                    srcs.append(pan.src)
                    masks.append(pan.mask)
                    axes.append(axes[base + i * C + j])
                    blends.append(blends[base + i * C + j])
                    twin.append(base + i * C + j)
            off = lb - base
            faces += [tuple(k + off for k in q[::-1]) for q in oriented]
            face_mask += [pan.mask] * len(oriented)
            borders = [[(0, j) for j in range(C)], [(R - 1, j) for j in range(C)],
                       [(i, 0) for i in range(R)], [(i, C - 1) for i in range(R)]]
            inward = [(1, 0), (-1, 0), (0, 1), (0, -1)]
            for edge, (di, dj) in zip(borders, inward):
                for (i0, j0), (i1, j1) in zip(edge[:-1], edge[1:]):
                    a_, b_ = idx(i0, j0), idx(i1, j1)
                    q = (a_, b_, b_ + off, a_ + off)
                    P4 = np.array([verts[k] for k in q])
                    n = np.cross(P4[2] - P4[0], P4[3] - P4[1])
                    ii, jj = min(max(i0 + di, 0), R - 1), min(max(j0 + dj, 0), C - 1)
                    if n @ (P4.mean(0) - pan.pts[ii, jj]) < 0:
                        q = q[::-1]
                    faces.append(q)
                    face_mask.append(pan.mask)
    return verts, uvs, faces, srcs, masks, blends, twin


def transfer(src, pts):
    bvh = src["bvh"]
    W = np.zeros((len(pts), len(src["names"])))
    C = {k: np.ones((len(pts), 4)) for k in ("Color 1", "Color 2")}
    for j, p in enumerate(pts):
        loc, _, fi, _ = bvh.find_nearest(Vector(p))
        tri = src["tris"][fi]
        a, b, c = (src["pos"][t] for t in tri)
        v0, v1, v2 = b - a, c - a, np.array(loc) - a
        d00, d01, d11, d20, d21 = v0 @ v0, v0 @ v1, v1 @ v1, v2 @ v0, v2 @ v1
        den = d00 * d11 - d01 * d01 or 1e-12
        bv = (d11 * d20 - d01 * d21) / den
        bw = (d00 * d21 - d01 * d20) / den
        bc = np.clip(np.array([1 - bv - bw, bv, bw]), 0, 1)
        bc /= bc.sum()
        W[j] = bc @ src["W"][tri]
        for k in C:
            if k in src["cols"]:
                C[k][j] = bc @ src["cols"][k][tri]
    return W, C


def weights(verts, faces, srcs, blends, twin, rigid=None):
    n = len(verts)
    V = np.array(verts)
    Wall = np.zeros((n, len(ALL_NAMES)))
    Call = {k: np.ones((n, 4)) for k in ("Color 1", "Color 2")}
    if rigid:
        Wall[:, ALL_NAMES.index(rigid)] = 1.0
        return Wall, Call, {}
    outer = [i for i in range(n) if twin[i] < 0]
    for key in sorted(set(srcs)):
        src = SRC[key] if SRC.get(key) is not None else SRC["trunk"]
        vids = [i for i in outer if srcs[i] == key]
        if not vids:
            continue
        Wp, Cp = transfer(src, V[vids])
        for j, nm in enumerate(src["names"]):
            Wall[vids, ALL_NAMES.index(nm)] += Wp[:, j]
        for k in Cp:
            Call[k][vids] = Cp[k]
    # blended rows (shemagh drape): head weights -> torso weights
    bl = [i for i in outer if blends[i]]
    if bl:
        Wt, _ = transfer(SRC["trunk"], V[bl])
        a = np.array([blends[i] for i in bl])[:, None]
        Wb = np.zeros((len(bl), len(ALL_NAMES)))
        for j, nm in enumerate(SRC["trunk"]["names"]):
            Wb[:, ALL_NAMES.index(nm)] = Wt[:, j]
        Wall[bl] = Wall[bl] * (1 - a) + Wb * a
    names = ALL_NAMES
    z = V[:, 2]
    # skirts (trunk shells below the crotch): smoothed around the ring, thighs + pelvis instead of calves and feet
    skirtish = np.array([srcs[i] == "trunk" and z[i] < Z["crotch"] + 0.02 and twin[i] < 0 for i in range(n)])
    if skirtish.any():
        # the same point in two panels (the front / back seams of the halves) is one vertex here, so the smoothing runs
        # across the seams: the cloth between the legs is shared by both legs, not split along the seam
        key = {}
        canon = np.array([key.setdefault((round(v[0], 5), round(v[1], 5), round(v[2], 5)), i) for i, v in enumerate(V)])
        nbr = {}
        for f in faces:
            for i in range(len(f)):
                a_, b_ = canon[f[i]], canon[f[(i + 1) % len(f)]]
                if a_ != b_:
                    nbr.setdefault(a_, set()).add(b_)
                    nbr.setdefault(b_, set()).add(a_)
        sk = np.array(sorted({canon[i] for i in np.nonzero(skirtish)[0]}))
        Wall[sk] = np.array([Wall[canon == c].mean(0) for c in sk])
        for _ in range(40):
            Wn = Wall.copy()
            for i in sk:
                nb = [j for j in nbr.get(i, ()) if skirtish[j]]
                if nb:
                    Wn[i] = 0.5 * Wall[i] + 0.5 * Wall[nb].mean(0)
            Wall = Wn
        Wall = Wall[canon]
        for sd in ("L", "R"):
            th = names.index(f"SKEL_{sd}_Thigh") if f"SKEL_{sd}_Thigh" in names else None
            if th is None:
                continue
            for b in (f"SKEL_{sd}_Foot", f"SKEL_{sd}_Calf", f"MH_{sd}_Knee", f"SKEL_{sd}_Toe0"):
                if b in names:
                    Wall[skirtish, th] += Wall[skirtish, names.index(b)]
                    Wall[skirtish, names.index(b)] = 0
        if "SKEL_Pelvis" in names:
            ip = names.index("SKEL_Pelvis")
            low = skirtish & (z < Z["crotch"])
            a = np.clip((Z["crotch"] - z) / 0.25, 0, 1) * 0.35
            Wall[low] *= (1 - a[low])[:, None]
            Wall[low, ip] += a[low]
    # trousers / skin legs: no foot bones near the hem (ankle turns would twist the cuff)
    legish = np.array([srcs[i] == "legs" for i in range(n)])
    for sd in ("L", "R"):
        if f"SKEL_{sd}_Foot" in names and f"SKEL_{sd}_Calf" in names:
            fi, ci = names.index(f"SKEL_{sd}_Foot"), names.index(f"SKEL_{sd}_Calf")
            Wall[legish, ci] += Wall[legish, fi]
            Wall[legish, fi] = 0
    # the lining (and the rims between the layers) moves exactly with the outside
    tw = np.array(twin)
    lin = np.nonzero(tw >= 0)[0]
    if len(lin):
        Wall[lin] = Wall[tw[lin]]
        for k in Call:
            Call[k][lin] = Call[k][tw[lin]]
    idx = np.argsort(-Wall, 1)[:, :4]
    lim = np.zeros_like(Wall)
    rows = np.arange(n)[:, None]
    lim[rows, idx] = Wall[rows, idx]
    lim[lim < 0.01] = 0
    s = lim.sum(1, keepdims=True)
    empty = s[:, 0] < 1e-6
    if empty.any():
        lim[empty, ALL_NAMES.index("SKEL_Spine3" if "SKEL_Spine3" in ALL_NAMES else ALL_NAMES[0])] = 1
        s = lim.sum(1, keepdims=True)
    lim /= s
    return lim, Call, {}


def make_object(name, verts, uvs, faces, W, C, rigid_local=None):
    me = bpy.data.meshes.new(name)
    V = [tuple(map(float, v)) for v in verts]
    if rigid_local is not None:
        V = [tuple(rigid_local @ Vector(v)) for v in V]
    me.from_pydata(V, [], [tuple(f) for f in faces])
    me.update()
    uvl = me.uv_layers.new(name="UVMap 0")
    for poly in me.polygons:
        for li, vi in zip(poly.loop_indices, poly.vertices):
            uvl.data[li].uv = tuple(map(float, uvs[vi]))
    uv1 = me.uv_layers.new(name="UVMap 1")
    for i in range(len(me.loops)):
        uv1.data[i].uv = uvl.data[i].uv
    for k in ("Color 1", "Color 2"):
        ca = me.color_attributes.new(name=k, type="BYTE_COLOR", domain="CORNER")
        for lp in me.loops:
            ca.data[lp.index].color = tuple(float(x) for x in C[k][lp.vertex_index])
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    # merge the seams (back of the trunk, under the sleeves): same position, UVs stay per corner
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bm.to_mesh(me)
    bm.free()
    me.shade_smooth()
    if W is not None:
        n = len(me.vertices)
        # remove_doubles keeps the first of each group: re-map weights by position
        from mathutils.kdtree import KDTree
        kd = KDTree(len(verts))
        for i, v in enumerate(V):
            kd.insert(Vector(v), i)
        kd.balance()
        groups = {}
        for nm_i, nm in enumerate(ALL_NAMES):
            if W[:, nm_i].any():
                groups[nm] = obj.vertex_groups.new(name=nm)
        for v in me.vertices:
            _, i, _ = kd.find(v.co)
            for nm_i in np.nonzero(W[i])[0]:
                groups[ALL_NAMES[nm_i]].add([v.index], float(W[i, nm_i]), "REPLACE")
        _ = n
    return obj


# ===================================================================================================== textures
def fabric(kind, base, rng, size=TEX):
    yy, xx = np.mgrid[0:size, 0:size]
    img = np.ones((size, size, 3), np.float32) * base
    lumi = float(base.mean())
    amp = 1.0 if lumi > 60 else 0.55
    if kind in ("cotton", "poplin", "crepe", "wool", "suiting"):
        wu = rng.normal(0, 1, size)[None, :]
        wv = rng.normal(0, 1, size)[:, None]
        img += ((wu + wv) * 1.6 * amp + rng.normal(0, 1.2 * amp, (size, size)))[..., None]
        img += (((xx % 2) ^ (yy % 2)).astype(np.float32) * 1.5 * amp)[..., None]
        if kind == "wool":
            img += (np.sin(xx * 0.9) * 2.0 * amp)[..., None]
        if kind == "suiting":
            img += ((((xx + yy) // 3) % 2).astype(np.float32) * 2.0 * amp)[..., None]
    elif kind in ("knit", "fleece"):
        img += ((np.abs(np.sin(xx * math.pi / 3)) * 4 - 2) * amp + rng.normal(0, 1.5 * amp, (size, size)))[..., None]
        if kind == "fleece":
            img += (np.asarray(Image.fromarray((rng.random((size // 4, size // 4)) * 255).astype(np.uint8)).resize(
                (size, size), Image.BICUBIC), np.float32) / 255 * 7 * amp - 3.5)[..., None]
    elif kind in ("twill", "denim"):
        img += ((((xx + yy) // 2) % 4 < 2).astype(np.float32) * (6 if kind == "denim" else 3) * amp)[..., None]
        img += rng.normal(0, 2.0 * amp, (size, size))[..., None]
        if kind == "denim":
            img[..., 2] += 4
            img += (rng.normal(0, 1, size)[None, :] * 4)[..., None]
    elif kind == "terry":
        loops = rng.random((size // 2, size // 2))
        loops = np.asarray(Image.fromarray((loops * 255).astype(np.uint8)).resize((size, size), Image.NEAREST), np.float32)
        img += ((loops / 255 - 0.5) * 18 * amp)[..., None]
    elif kind == "satin":
        img += (np.sin(yy * 0.05 + np.sin(xx * 0.01) * 3) * 6 * amp)[..., None] + rng.normal(0, 0.8, (size, size))[..., None]
    elif kind == "nylon":
        img += (rng.normal(0, 1, size)[:, None] * 2.5 * amp + rng.normal(0, 1.0, (size, size)))[..., None]
    return img


class Painter:
    def __init__(self):
        self.rng = np.random.default_rng(7)
        base = BASE if BASE is not None else np.array([200, 200, 200], np.float32)
        self.base = base
        self.img = None
        self.mask = Image.new("L", (TEX, TEX), 0)
        self.height = Image.new("L", (TEX, TEX), 128)

    def px(self, u, v):
        return u * TEX, (1 - v) * TEX

    def rect(self, isl):
        u0, v0, u1, v1 = ISL[isl]
        return (int(u0 * TEX), int((1 - v1) * TEX), int(math.ceil(u1 * TEX)), int(math.ceil((1 - v0) * TEX)))


def dark(c, k):
    return tuple(int(max(0, min(255, x * k))) for x in c)


def paint():
    pt = Painter()
    tid = TPL["id"]
    base = pt.base
    fab_kind = {"thobe": "cotton", "kandura": "cotton", "abaya": "crepe", "robe": P.get("fabric", "terry"),
                "tshirt": "knit", "shirt": "poplin", "hoodie": "fleece", "jacket": "nylon", "vest": "suiting",
                "trousers": P.get("fabric", "twill"), "shorts": "twill", "skirt": "twill", "bisht": "wool",
                "shemagh": "cotton", "cap": "twill"}[tid]
    img = fabric(fab_kind, base, pt.rng)
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    md = ImageDraw.Draw(pt.mask)
    hd = ImageDraw.Draw(pt.height)
    col = tuple(int(x) for x in base)
    stitch = dark(col, 0.82) if base.mean() > 90 else tuple(min(255, int(x * 1.35 + 18)) for x in col)
    for name in ISL:
        md.rectangle(pt.rect(name), fill=255)
    for kind, obj, o in FEATURES:
        if kind in ("long_trunk", "top_trunk", "skirt"):
            paint_trunk(pt, d, hd, obj, o, col, stitch, kind)
        elif kind == "sleeves":
            for pan in obj:
                isl = pan.name
                x0, y0, x1, y1 = pt.rect(isl)
                L_ = pan.meta["length"] * pan.meta["total"]
                for cut in ((0.04, 0.012) if o.get("cuff") else (0.012,)):
                    y = y0 + (y1 - y0) * (1 - cut / max(L_, 0.05))
                    d.line([x0, y, x1, y], fill=stitch, width=2)
                    hd.line([x0, y, x1, y], fill=100, width=2)
                if o.get("cuff"):
                    y = y0 + (y1 - y0) * (1 - 0.06 / max(L_, 0.05))
                    d.rectangle([x0, y, x1, y1], fill=dark(col, 0.97))
                    if o.get("rib"):
                        for xx in range(x0, x1, 4):
                            d.line([xx, y, xx, y1], fill=dark(col, 0.9), width=1)
                d.line([x0 + 1, y0, x0 + 1, y1], fill=dark(col, 0.92), width=2)   # seam under the arm
                if o.get("trim"):
                    gold = (196, 160, 74) if o.get("gold") else dark(col, 1.6)
                    y = y0 + (y1 - y0) * (1 - 0.05 / max(L_, 0.05))
                    d.rectangle([x0, y, x1, y1], fill=gold)
                    if o.get("gold"):
                        md.rectangle([x0, int(y), x1, y1], fill=0)
                    for k in range(x0, x1, 9):
                        d.line([k, y + 2, k + 4, y1 - 2], fill=dark(gold, 0.75), width=1)
        elif kind == "trousers":
            paint_trousers(pt, d, hd, obj, o, col, stitch)
        elif kind == "skin":
            sk = tuple(int(x) for x in job.get("skin") or (150, 110, 85))
            for isl in ("skin_L", "skin_R"):
                r = pt.rect(isl)
                d.rectangle(r, fill=sk)
                md.rectangle(r, fill=0)
                noise = pt.rng.normal(0, 3, (r[3] - r[1], r[2] - r[0], 1))
                arr = np.asarray(im, np.float32)
                arr[r[1]:r[3], r[0]:r[2]] += noise
                im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
                d = ImageDraw.Draw(im)
        elif kind == "hood":
            x0, y0, x1, y1 = pt.rect("hood")
            d.rectangle([x0, y0, x1, y0 + 10], fill=dark(col, 0.92))
            d.line([(x0 + x1) / 2, y0, (x0 + x1) / 2, y1], fill=stitch, width=2)
        elif kind == "shemagh":
            im = paint_shemagh(pt, im, o)
            d = ImageDraw.Draw(im)
            ax0, ay0, ax1, ay1 = pt.rect("agal")
            d.rectangle([ax0, ay0, ax1, ay1], fill=(18, 18, 20))
            for k in range(ax0, ax1, 6):
                d.line([k, ay0, k + 6, ay1], fill=(34, 34, 38), width=2)
                hd.line([k, ay0, k + 6, ay1], fill=150, width=2)
            md.rectangle([ax0, ay0, ax1, ay1], fill=0)
        elif kind == "cap":
            x0, y0, x1, y1 = pt.rect("crown")
            for k in range(6):
                x = x0 + (x1 - x0) * (k + 0.5) / 6
                d.line([x, y0, x, y1], fill=stitch, width=2)
                hd.line([x, y0, x, y1], fill=95, width=2)
            d.ellipse([x0 - 14, y0 - 14, x1 + 14, y0 + 16], fill=dark(col, 0.9))
            d.rectangle([x0, y1 - 14, x1, y1], fill=dark(col, 0.85))   # sweatband edge
            bx0, by0, bx1, by1 = pt.rect("brim")
            for k in range(1, 6):
                y = by0 + (by1 - by0) * k / 7
                d.line([bx0, y, bx1, y], fill=stitch, width=2)
                hd.line([bx0, y, bx1, y], fill=100, width=2)
    if "tassel" in ISL:
        r = pt.rect("tassel")
        d.rectangle(r, fill=dark(col, 0.95))
        for k in range(r[0], r[2], 3):
            d.line([k, r[1], k, r[3]], fill=dark(col, 0.85), width=1)
    if "belt" in ISL:
        r = pt.rect("belt")
        d.rectangle(r, fill=dark(col, 0.93))
        for y in (r[1] + 4, r[3] - 5):
            d.line([r[0], y, r[2], y], fill=stitch, width=2)
    if "strings" in ISL:
        r = pt.rect("strings")
        d.rectangle(r, fill=(235, 235, 232))
        md.rectangle(r, fill=0)
    if "inner" in ISL:
        r = pt.rect("inner")
        if tid == "robe":
            sk = tuple(int(x) for x in job.get("skin") or (150, 110, 85))
            d.rectangle(r, fill=sk)
            md.rectangle(r, fill=0)
        else:
            d.rectangle(r, fill=dark(col, 0.9))
    im = im.filter(ImageFilter.GaussianBlur(0.4))
    return im, pt


def paint_trunk(pt, d, hd, T_, o, col, stitch, kind):
    x0, y0, x1, y1 = pt.rect(T_.isl)
    W_ = x1 - x0
    H_ = y1 - y0
    xc = x0 + W_ / 2   # the front

    def ypx(z):
        return y0 + (1 - T_.v_of(z)) * H_
    circ = T_.circumference(min(len(T_.zs) - 1, int(np.argmin(np.abs(T_.zs - Z["chest"])))))
    px_m = W_ / max(circ, 0.3)
    # soft vertical folds below the waist for long garments
    if o.get("folds"):
        arr = np.asarray(d._image, np.float32)
        xs = np.arange(x0, x1)
        fold = np.sin((xs - x0) / W_ * TAU * 22 + np.sin((xs - x0) / 40) * 0.8) * 0.5 + 0.5
        for y in range(y0, y1):
            z = T_.z_hem + (1 - (y - y0) / H_) * (T_.top_nominal - T_.z_hem)
            f = np.clip((Z["pelvis"] - 0.05 - z) / 0.6, 0, 1)
            if f > 0:
                arr[y, x0:x1] -= (fold * f * (9 if col[0] > 90 else 4))[:, None]
        d._image.paste(Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)))
    zt = T_.z_neck if T_.z_neck is not None else T_.zs[0]
    v_top = T_.v_of(zt)

    def ydown(dm):
        """y of the point dm metres below the neckline, along the cloth"""
        return y0 + (1 - (v_top - T_.metres(dm))) * H_

    def dist_to(z):
        return (v_top - T_.v_of(z)) * T_.length
    # side seams, hem stitch
    for f in (0.25, 0.75):
        d.line([x0 + W_ * f, ypx(zt), x0 + W_ * f, y1], fill=dark(col, 0.93), width=2)
    yh = ypx(T_.z_hem + 0.025)
    d.line([x0, yh, x1, yh], fill=stitch, width=2)
    hd.line([x0, yh, x1, yh], fill=100, width=2)
    if o.get("collar") in ("band", "shirt"):
        for z in (zt, zt + 0.034):
            d.line([x0, ypx(z) - 2, x1, ypx(z) - 2], fill=stitch, width=2)
            hd.line([x0, ypx(z) - 2, x1, ypx(z) - 2], fill=95, width=2)
        d.line([xc, ypx(zt + 0.04), xc, ypx(zt)], fill=dark(col, 0.75), width=2)
    elif T_.z_neck is not None and kind != "skirt":
        # round neck: a rib band
        d.rectangle([x0, ypx(zt + 0.0) - 1, x1, ydown(0.018)], fill=dark(col, 0.95))
        d.line([x0, ydown(0.018), x1, ydown(0.018)], fill=stitch, width=2)
    if kind == "skirt":
        d.rectangle([x0, y0, x1, ypx(T_.zs[0] - 0.04)], fill=dark(col, 0.93))
        d.line([x0, ypx(T_.zs[0] - 0.04), x1, ypx(T_.zs[0] - 0.04)], fill=stitch, width=2)
        if o.get("pleats"):
            for k in range(24):
                x = x0 + W_ * (k + 0.5) / 24
                d.line([x, ypx(T_.zs[0] - 0.05), x, y1], fill=dark(col, 0.8), width=3)
                hd.line([x, ypx(T_.zs[0] - 0.05), x, y1], fill=70, width=3)
        d.line([xc - W_ * 0.5 + 2, y0, xc - W_ * 0.5 + 2, ypx(T_.zs[0] - 0.2)], fill=dark(col, 0.8), width=2)
        return
    # placket + buttons (thobe, shirt, vest)
    nb = int(o.get("buttons") or 0)
    if o.get("placket") or o.get("kind") == "shirt":
        z_end = (Z["chest"] - 0.05) if o.get("placket") else T_.z_hem + 0.02
        pw = 0.034 * px_m
        d.rectangle([xc - pw / 2, ypx(zt), xc + pw / 2, ypx(z_end)], fill=dark(col, 0.985))
        for x in (xc - pw / 2, xc + pw / 2):
            d.line([x, ypx(zt), x, ypx(z_end)], fill=stitch, width=2)
            hd.line([x, ypx(zt), x, ypx(z_end)], fill=100, width=2)
        if nb:
            span = dist_to(z_end) - 0.06
            for i in range(nb):
                draw_button(d, hd, xc, ydown(0.03 + i * span / max(nb - 1, 1)), 0.0065 * px_m, col)
    if o.get("kind") == "vest" and nb:
        dv = cm("v_depth", 20)
        span = dist_to(T_.z_hem) - dv - 0.07
        for i in range(nb):
            draw_button(d, hd, xc + 0.012 * px_m, ydown(dv + 0.01 + i * span / max(nb - 1, 1)), 0.007 * px_m, dark(col, 0.6))
        d.line([xc - 0.002 * px_m, ydown(dv), xc - 0.002 * px_m, y1], fill=dark(col, 0.7), width=2)
    if o.get("zip"):
        if o.get("open"):
            gw = 0.012 * px_m
            for x in (xc - T_.gap(Z["chest"]) / 2 * px_m - gw, xc + T_.gap(Z["chest"]) / 2 * px_m):
                d.rectangle([x, ypx(zt), x + gw, y1], fill=(60, 60, 64))
        else:
            d.rectangle([xc - 0.006 * px_m, ypx(zt + 0.04), xc + 0.006 * px_m, y1], fill=(52, 52, 56))
            for y in range(int(ypx(zt + 0.04)), int(y1), 4):
                d.line([xc - 0.005 * px_m, y, xc + 0.005 * px_m, y], fill=(120, 120, 126), width=1)
                hd.line([xc - 0.005 * px_m, y, xc + 0.005 * px_m, y], fill=160, width=1)
            d.rectangle([xc - 0.01 * px_m, ypx(zt + 0.035), xc + 0.01 * px_m, ypx(zt + 0.005)], fill=(150, 150, 156))
    if o.get("kind") == "hoodie" and o.get("pocket"):
        zt_, zb_ = Z["pelvis"] + 0.12, Z["pelvis"] - 0.02
        w_top, w_bot = 0.17 * px_m, 0.30 * px_m
        poly = [(xc - w_top / 2, ypx(zt_)), (xc + w_top / 2, ypx(zt_)), (xc + w_bot / 2, ypx(zb_)), (xc - w_bot / 2, ypx(zb_))]
        d.polygon(poly, fill=dark(col, 0.97), outline=stitch)
        hd.polygon(poly, outline=95)
        d.line([poly[0], poly[3]], fill=dark(col, 0.75), width=3)
        d.line([poly[1], poly[2]], fill=dark(col, 0.75), width=3)
        d.rectangle([x0, ypx(T_.z_hem + 0.05), x1, y1], fill=dark(col, 0.95))
        for xx in range(x0, x1, 4):
            d.line([xx, ypx(T_.z_hem + 0.05), xx, y1], fill=dark(col, 0.88), width=1)
    if o.get("kind") == "shirt" and o.get("pocket"):
        pw, ph = 0.11 * px_m, 0.13
        x = xc + 0.07 * px_m
        d.rectangle([x, ypx(Z["chest"] + 0.07), x + pw, ypx(Z["chest"] - 0.06)], outline=stitch, width=2)
        hd.rectangle([x, ypx(Z["chest"] + 0.07), x + pw, ypx(Z["chest"] - 0.06)], outline=100, width=2)
        _ = ph
    if o.get("kind") == "jacket" and o.get("pocket"):
        for sx in (-1, 1):
            x = xc + sx * 0.11 * px_m
            d.line([x - 0.02 * px_m * sx, ypx(Z["pelvis"] + 0.08), x + 0.05 * px_m * sx, ypx(Z["pelvis"] - 0.04)],
                   fill=(40, 40, 44), width=4)
        d.rectangle([x0, ypx(T_.z_hem + 0.045), x1, y1], fill=dark(col, 0.9))
    if o.get("lapel"):
        # shawl collar: a wider band along the V opening
        dv = cm("v_depth", 24)
        for sx in (-1, 1):
            pts = [(xc + sx * 0.10 * px_m, ypx(zt + 0.01)), (xc + sx * 0.015 * px_m, ydown(dv)),
                   (xc + sx * 0.06 * px_m, ydown(dv)), (xc + sx * 0.17 * px_m, ypx(zt + 0.01))]
            d.polygon(pts, fill=dark(col, 0.94))
            d.line(pts[1:3], fill=stitch, width=2)
    if o.get("trim"):
        gold = (196, 160, 74) if o.get("gold") else tuple(min(255, int(x * 1.8 + 40)) for x in col)
        tw = 0.025 * px_m
        if o.get("open"):
            for sgn in (-1, 1):
                ga = T_.gap(Z["chest"])
                x = xc + sgn * (ga / 2 * px_m)
                d.rectangle([min(x, x + sgn * tw), y0, max(x, x + sgn * tw), y1], fill=gold)
                if o.get("gold"):
                    pt.mask.paste(0, (int(min(x, x + sgn * tw)), y0, int(max(x, x + sgn * tw)) + 1, y1))
                for y in range(y0, y1, 10):
                    d.line([x, y, x + sgn * tw, y + 5], fill=dark(gold, 0.7), width=1)
        d.rectangle([x0, y0, x1, ydown(0.02)], fill=gold)
        if o.get("gold"):
            pt.mask.paste(0, (x0, y0, x1, int(ydown(0.02))))
        d.rectangle([x0, ypx(T_.z_hem + 0.03), x1, y1], fill=gold if o.get("gold") else dark(col, 1.4))


def draw_button(d, hd, x, y, r, col):
    sh = dark(col, 0.82)
    d.ellipse([x - r - 1, y - r + 0.5, x + r + 1, y + r + 2.5], fill=sh)
    d.ellipse([x - r, y - r, x + r, y + r], fill=tuple(min(255, int(c * 1.02 + 4)) for c in col), outline=dark(col, 0.75))
    for hx in (-0.35, 0.35):
        d.ellipse([x + hx * r - 1, y - 1, x + hx * r + 1, y + 1], fill=dark(col, 0.6))
    hd.ellipse([x - r, y - r, x + r, y + r], fill=200)


def paint_trousers(pt, d, hd, legs, o, col, stitch):
    for pan in legs:
        x0, y0, x1, y1 = pt.rect(pan.name)
        zs = pan.meta["zs"]
        side = 1 if pan.name.endswith("L") else -1

        def ypx(z, zs=zs, y0=y0, y1=y1):
            return y0 + (1 - (z - zs[-1]) / max(zs[0] - zs[-1], 1e-6)) * (y1 - y0)

        def xpx(s, x0=x0, x1=x1, side=side):   # s: 0 inner, .25 front, .5 outer, .75 back (leg_tube's angle / 2pi)
            return x0 + (x1 - x0) * (s if side > 0 else 1 - s)
        # waistband + belt loops
        wb = ypx(zs[0] - 0.04)
        d.rectangle([x0, y0, x1, wb], fill=dark(col, 0.94))
        d.line([x0, wb, x1, wb], fill=stitch, width=2)
        hd.line([x0, wb, x1, wb], fill=95, width=2)
        for s in (0.2, 0.45, 0.62, 0.88):
            x = xpx(s)
            d.rectangle([x - 3, y0, x + 3, wb + 6], fill=dark(col, 0.85))
            hd.rectangle([x - 3, y0, x + 3, wb + 6], fill=170)
        # side seam (outer), inseam, hem
        for s in (0.5, 0.0, 1.0):
            d.line([xpx(s), wb, xpx(s), y1], fill=dark(col, 0.9), width=2)
        yh = ypx(zs[-1] + 0.025)
        d.line([x0, yh, x1, yh], fill=stitch, width=2)
        hd.line([x0, yh, x1, yh], fill=100, width=2)
        # front pocket (slant), fly on the left leg's inner front
        d.line([xpx(0.36), wb, xpx(0.47), ypx(zs[0] - 0.16)], fill=dark(col, 0.7), width=3)
        hd.line([xpx(0.36), wb, xpx(0.47), ypx(zs[0] - 0.16)], fill=80, width=3)
        if side > 0:
            d.line([xpx(0.12), wb, xpx(0.12), ypx(Z["crotch"] + 0.02)], fill=stitch, width=2)
            d.arc([xpx(0.0) - 12, ypx(Z["crotch"] + 0.08), xpx(0.12) + 1, ypx(Z["crotch"] + 0.02)], 0, 90, fill=stitch, width=2)
        # back pocket
        bx = xpx(0.72)
        d.rectangle([bx - 0.05 * (x1 - x0), ypx(zs[0] - 0.07), bx + 0.05 * (x1 - x0), ypx(zs[0] - 0.19)], outline=stitch, width=2)
        if o.get("fabric") == "denim":
            for s in (0.5, 0.0):
                d.line([xpx(s) + 3, wb, xpx(s) + 3, y1], fill=(196, 150, 70), width=1)


def paint_shemagh(pt, im, o):
    x0, y0, x1, y1 = pt.rect("shemagh")
    arr = np.asarray(im, np.float32).copy()
    white = np.array([238, 236, 230], np.float32)
    red = np.array([int(P["color"][i:i + 2], 16) for i in (1, 3, 5)], np.float32)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    if o.get("pattern") == "red":
        # houndstooth-like check: red lines on white, a border band near the edge
        cell = 14
        a = ((xx // cell) + (yy // cell)) % 2
        b = (((xx + cell // 2) // (cell // 2)) % 2) & (((yy + cell // 2) // (cell // 2)) % 2)
        m = ((a == 1) & (b == 1)) | ((xx % cell) < 2) | ((yy % cell) < 2)
        tile = np.where(m[..., None], red, white)
        edge = (yy > y1 - 70) & (yy < y1 - 40)
        tile[edge] = red
        arr[y0:y1, x0:x1] = tile
    else:
        arr[y0:y1, x0:x1] = white
        arr[y0:y1, x0:x1] += np.random.default_rng(3).normal(0, 1.5, (y1 - y0, x1 - x0, 1))
        for k in range(0, x1 - x0, 6):
            arr[y0:y1, x0 + k] -= 4
    arr[y1 - 12:y1, x0:x1] = (red if o.get("pattern") == "red" else white) * 0.9
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def normal_spec(im, pt, shiny=False):
    lumi = np.asarray(im.convert("L"), np.float32) / 255
    h = np.asarray(pt.height.filter(ImageFilter.GaussianBlur(1.2)), np.float32) / 255
    hh = lumi * 0.6 + h * 1.4
    gx = np.roll(hh, -1, 1) - np.roll(hh, 1, 1)
    gy = np.roll(hh, -1, 0) - np.roll(hh, 1, 0)
    k = 2.5
    nx, ny, nz = -gx * k, gy * k, np.ones_like(hh)
    nl = np.sqrt(nx * nx + ny * ny + nz * nz)
    nm = np.stack([(nx / nl) * 0.5 + 0.5, (ny / nl) * 0.5 + 0.5, (nz / nl) * 0.5 + 0.5], -1)
    nim = Image.fromarray((nm * 255).astype(np.uint8)).resize((512, 512), Image.LANCZOS)
    spec = Image.new("RGBA", (256, 256), (90, 90, 90, 255) if shiny else (40, 40, 40, 255))
    return nim, spec


# ===================================================================================================== Sollumz export
from Sollumz.tools.drawablehelper import convert_obj_to_model  # noqa: E402
from Sollumz.tools.blenderhelper import create_empty_object  # noqa: E402
from Sollumz.sollumz_properties import SollumType  # noqa: E402
from Sollumz.sollumz_preferences import get_export_settings  # noqa: E402

SCAF = {}
for comp, path in (job.get("scaffold") or {}).items():
    if path:
        SCAF[comp] = imp(path)


def template_material():
    m = None
    for objs in (body_objs.get("uppr"), SCAF.get("uppr")):
        if objs:
            o = mesh_of(objs)
            if o and o.data.materials:
                m = o.data.materials[0]
                break
    if m is None:
        raise RuntimeError("no ped material to copy")
    return m


def set_material(mat, diffuse, normal_dds, spec_dds, diffuse_dds):
    for nd in mat.node_tree.nodes:
        if nd.type != "TEX_IMAGE":
            continue
        if nd.name == "DiffuseSampler":
            nd.image = bpy.data.images.load(diffuse_dds)
            nd.texture_properties.embedded = False
        elif nd.name == "BumpSampler" and normal_dds:
            nd.image = bpy.data.images.load(normal_dds)
            nd.texture_properties.embedded = True
        elif nd.name == "SpecSampler" and spec_dds:
            nd.image = bpy.data.images.load(spec_dds)
            nd.texture_properties.embedded = True


def scaffold_for(comp):
    """(dictionary, drawable) objects to hang the new model under: an imported drawable of this component, else
    a fresh pair"""
    objs = SCAF.get(comp)
    if objs:
        dct = [o for o in objs if o.sollum_type == "sollumz_drawable_dictionary"]
        drw = [o for o in objs if o.sollum_type == "sollumz_drawable"]
        if dct and drw:
            for o in [o for o in objs if o.sollum_type == "sollumz_drawable_model"]:
                bpy.data.objects.remove(o, do_unlink=True)
            return dct[0], drw[0]
    dct = create_empty_object(SollumType.DRAWABLE_DICTIONARY)
    drw = create_empty_object(SollumType.DRAWABLE)
    drw.parent = dct
    return dct, drw


def export(dct, name):
    es = get_export_settings()
    es.target_formats = {"CWXML"}
    es.target_versions = {"GEN8"}
    es.limit_to_selected = True
    es.exclude_skeleton = True
    es.apply_transforms = False
    bpy.ops.object.select_all(action="DESELECT")
    dct.select_set(True)
    bpy.context.view_layer.objects.active = dct
    kw = {"directory": OUT + "/xml/"}
    if "direct_export" in bpy.ops.sollumz.export_assets.get_rna_type().properties:
        kw["direct_export"] = True
    r = bpy.ops.sollumz.export_assets(**kw)
    path = f"{OUT}/xml/{name}.ydd.xml"
    if "FINISHED" not in r or not os.path.exists(path):
        raise RuntimeError(f"Sollumz export of {name} failed ({list(r)})")
    return path


def main_piece(pc, results):
    build_template()
    verts, uvs, faces, srcs, masks, blends, twin = assemble()
    rigid = None
    local = None
    if pc.get("prop"):
        bname = pc.get("bone") or "SKEL_Head"
        rigid = bname if bname in ALL_NAMES else "SKEL_Head"
        bm_ = ARM.matrix_world @ ARM.data.bones[rigid].matrix_local
        local = bm_.inverted()
    elif pc["comp"] in ("berd",) and TPL["id"] in ("cap",):
        rigid = "SKEL_Head"
    W, C, _ = weights(verts, faces, srcs, blends, twin, rigid=rigid)
    im, pt = paint()
    dname = pc["diffuse"]
    im.save(f"{OUT}/tex/{dname}.png")
    pt.mask.save(f"{OUT}/tex/{dname}_mask.png")
    encode_dds(im.convert("RGBA"), "D3DFMT_DXT1", 11, f"{OUT}/tex/{dname}.dds")
    nim, sim = normal_spec(im, pt, shiny=TPL["id"] in ("jacket", "robe") and P.get("fabric") != "terry")
    encode_dds(nim.convert("RGBA"), "D3DFMT_DXT1", 10, f"{OUT}/emb/{pc['normal']}.dds")
    encode_dds(sim, "D3DFMT_DXT1", 9, f"{OUT}/emb/{pc['spec']}.dds")
    obj = make_object(pc["drawable"], verts, uvs, faces, None if pc.get("prop") else W, C, rigid_local=local)
    stats = mesh_stats(obj)
    mat = template_material().copy()
    mat.name = dname
    set_material(mat, dname, f"{OUT}/emb/{pc['normal']}.dds", f"{OUT}/emb/{pc['spec']}.dds", f"{OUT}/tex/{dname}.dds")
    obj.data.materials.append(mat)
    dct, drw = scaffold_for(pc["comp"] if not pc.get("prop") else "__prop__")
    obj.parent = drw
    obj.matrix_parent_inverse = drw.matrix_world.inverted()
    if not pc.get("prop"):
        mod = obj.modifiers.new("Armature", "ARMATURE")
        mod.object = dct if dct.type == "ARMATURE" else ARM
    convert_obj_to_model(obj)
    drw.name = pc["drawable"]
    dct.name = pc["dict"]
    xml = export(dct, pc["dict"])
    results.append({"role": "main", "xml": xml, "diffuse_png": f"{OUT}/tex/{dname}.png",
                    "mask_png": f"{OUT}/tex/{dname}_mask.png", **stats})


def mesh_stats(obj):
    me = obj.data
    cnt = {}
    for v in me.vertices:
        for g in v.groups:
            n = obj.vertex_groups[g.group].name
            cnt[n] = cnt.get(n, 0) + 1
    infl = [len(v.groups) for v in me.vertices]
    sums = [sum(g.weight for g in v.groups) for v in me.vertices]
    return {"verts": len(me.vertices), "faces": len(me.polygons), "groups": cnt,
            "max_influences": max(infl) if infl else 0,
            "weight_sum": [round(min(sums), 3), round(max(sums), 3)] if sums else [0, 0]}


def lower_piece(pc, results):
    """hide what is under a long garment: an empty lowr (one 1 cm triangle in the pelvis) or the base lowr cut below
    the hem (its own texture copied by clothes_variant)"""
    mode = pc.get("mode", "empty")
    dname = pc["diffuse"]
    dct, drw = scaffold_for("lowr")
    if mode == "cut" and lower_cut is not None:
        src = mesh_of(body_objs["lowr"])
        o = src.copy()
        o.data = src.data.copy()
        bpy.context.scene.collection.objects.link(o)
        import bmesh
        bm = bmesh.new()
        bm.from_mesh(o.data)
        mw = o.matrix_world
        kill = [f for f in bm.faces if max((mw @ v.co).z for v in f.verts) > lower_cut]
        bmesh.ops.delete(bm, geom=kill, context="FACES")
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
        bm.to_mesh(o.data)
        bm.free()
        if len(o.data.vertices) < 3:
            mode = "empty"
            bpy.data.objects.remove(o, do_unlink=True)
        else:
            m = o.data.materials[0].copy()
            m.name = dname
            for nd in m.node_tree.nodes:
                if nd.type == "TEX_IMAGE" and nd.name == "DiffuseSampler":
                    img = Image.new("RGBA", (8, 8), (128, 128, 128, 255))
                    encode_dds(img, "D3DFMT_DXT1", 1, f"{OUT}/tex/{dname}.dds")
                    nd.image = bpy.data.images.load(f"{OUT}/tex/{dname}.dds")
                    nd.texture_properties.embedded = False
            o.data.materials.clear()
            o.data.materials.append(m)
            o.parent = drw
            o.matrix_parent_inverse = drw.matrix_world.inverted()
            for md_ in list(o.modifiers):
                o.modifiers.remove(md_)
            mod = o.modifiers.new("Armature", "ARMATURE")
            mod.object = dct if dct.type == "ARMATURE" else ARM
            st = mesh_stats(o)
            if o.sollum_type != "sollumz_drawable_model":
                convert_obj_to_model(o)
    if mode == "empty":
        img = Image.new("RGBA", (64, 64), tuple(int(x) for x in (BASE if BASE is not None else (200, 200, 200))) + (255,))
        img.save(f"{OUT}/tex/{dname}.png")
        encode_dds(img, "D3DFMT_DXT1", 7, f"{OUT}/tex/{dname}.dds")
        tm = bpy.data.meshes.new("stub")
        pv = bone("SKEL_Pelvis")
        tm.from_pydata([tuple(pv + np.array(x)) for x in ((0, 0, 0), (0.01, 0, 0), (0, 0, 0.01))], [], [(0, 1, 2)])
        tm.uv_layers.new(name="UVMap 0")
        tm.uv_layers.new(name="UVMap 1")
        for k in ("Color 1", "Color 2"):
            tm.color_attributes.new(name=k, type="BYTE_COLOR", domain="CORNER")
        o = bpy.data.objects.new("stub", tm)
        bpy.context.scene.collection.objects.link(o)
        o.vertex_groups.new(name="SKEL_Pelvis").add([0, 1, 2], 1.0, "REPLACE")
        m = template_material().copy()
        m.name = dname
        set_material(m, dname, None, None, f"{OUT}/tex/{dname}.dds")
        o.data.materials.append(m)
        o.parent = drw
        mod = o.modifiers.new("Armature", "ARMATURE")
        mod.object = dct if dct.type == "ARMATURE" else ARM
        st = mesh_stats(o)
        convert_obj_to_model(o)
    drw.name = pc["drawable"]
    dct.name = pc["dict"]
    xml = export(dct, pc["dict"])
    results.append({"role": "lower", "mode": mode, "xml": xml,
                    "diffuse_png": f"{OUT}/tex/{dname}.png" if mode == "empty" else None, **st})


def run():
    results = []
    for pc in job["pieces"]:
        if pc["role"] == "main":
            main_piece(pc, results)
    for pc in job["pieces"]:
        if pc["role"] == "lower":
            lower_piece(pc, results)
    report["islands"] = {k: [round(x, 3) for x in v] for k, v in ISL.items()}
    report["panels"] = len(panels)
    return {"pieces": results, "report": report}


try:
    res = run()
except Exception as e:  # the caller turns it into a refusal / error
    res = {"error": str(e)[:500], "trace": traceback.format_exc()[-2500:]}
    log(res["trace"])
json.dump(res, open(result_path, "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
sys.stdout.flush()
os._exit(0)
