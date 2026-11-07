"""Clothes designer, TRANSFER: one existing piece of ped A moved onto ped B (Blender bpy + Sollumz).
Run by clothes_variant.py ("mode": "transfer") with the Python that has bpy (as garment_bpy.py):
    python transfer_bpy.py <job.json> <result.json>

job.json:
  {"sollumz": "<folder holding Sollumz/>",
   "src": {"skeleton": "<A>.yft.xml", "body": {"head": xml, "uppr": xml, ...}, "piece": "<A piece>.ydd.xml",
           "prop": false, "bone": "SKEL_Head" | "PH_L_Hand,SKEL_L_Hand" (props: their anchor bone)},
   "dst": {"skeleton": "<B>.yft.xml", "body": {...}, "scaffold": "<a B drawable>.ydd.xml" (components)},
   "names": {"dict": "b^berd_003_u", "drawable": "berd_003_u"}, "gap": 0.003, "out": "<dir>"}
result.json: {"xml": ".../<dict>.ydd.xml", "report": {...}} or {"error": "..."}

How a piece moves (every size comes from the two peds, nothing is fixed to one body):
1. skeleton: every point follows its own bones from A's rest pose to B's (linear blend of the bone changes with its
   own weights; a bone B does not have = its nearest ancestor that both have). Height, shoulders, arm and leg length.
2. girth: the point's nearest spot on A's body is moved the same way and looked up on B's body; the difference (how much
   fatter / thinner B is there) is added to the point, smoothed over the piece so it does not tear where the nearest
   body part changes (arm / torso), less for parts far from the body (backpacks, brims).
3. outside: a point that was outside A's body but ends up inside B's (or closer than `gap`) is put back outside at its
   old distance (at least `gap`).
4. normals: Blender keeps custom normals relative to the surface, so they turn with it; weights keep their bone names
   (missing bones -> the ancestor, merged).
Every LOD (high, medium, low, very low) is moved the same way. A prop (no weights) moves from A's anchor bone space to
B's: fitted in the world, written back in B's bone space.
"""
import json
import os
import sys
import traceback

import bpy  # noqa: I001  (bpy before addon_utils)
import addon_utils

job_path, result_path = sys.argv[1:3]
job = json.load(open(job_path))
sys.path.insert(0, job["sollumz"])
bpy.ops.wm.read_factory_settings(use_empty=True)
addon_utils.enable("Sollumz", default_set=True)
from Sollumz.known_paths import data_directory_path  # noqa: E402

os.makedirs(data_directory_path(), exist_ok=True)

import numpy as np  # noqa: E402
from mathutils import Vector  # noqa: E402
from mathutils.bvhtree import BVHTree  # noqa: E402
from Sollumz.sollumz_properties import LODLevel, SollumType  # noqa: E402
from Sollumz.tools.blenderhelper import create_empty_object  # noqa: E402
from Sollumz.sollumz_preferences import get_export_settings  # noqa: E402

OUT = job["out"]
os.makedirs(OUT + "/xml", exist_ok=True)
GAP = float(job.get("gap", 0.003))
report = {"notes": []}
BODY_PARTS = ("head", "uppr", "lowr", "hand", "feet")


def log(*a):
    print(*a, flush=True)


# ===================================================================================================== import
def imp(path, skeleton):
    kw = dict(directory=os.path.dirname(path), files=[{"name": os.path.basename(path)}], use_custom_settings=True,
              split_by_group=False)
    if skeleton:
        kw.update(dwd_import_external_skeleton="SAVED", dwd_import_external_skeleton_saved_path=skeleton)
    before = set(bpy.data.objects)
    r = bpy.ops.sollumz.import_assets(**kw)
    if "FINISHED" not in r:
        raise RuntimeError(f"Sollumz could not import {os.path.basename(path)}")
    return [o for o in bpy.data.objects if o not in before]


def models_of(objs):
    return [o for o in objs if o.type == "MESH" and o.sollum_type == "sollumz_drawable_model"]


def lod_meshes(o):
    """[(level, mesh)] of every LOD this model has (the high one first)"""
    out = []
    for lv in (LODLevel.VERYHIGH, LODLevel.HIGH, LODLevel.MEDIUM, LODLevel.LOW, LODLevel.VERYLOW):
        try:
            lod = o.sz_lods.get_lod(lv)
        except Exception:  # noqa: BLE001  (an older Sollumz: only the object's own mesh)
            lod = None
        if lod is not None and lod.mesh is not None and len(lod.mesh.vertices):
            out.append((lv, lod.mesh))
    if not out and o.data is not None:
        out.append((None, o.data))
    return out


def set_lod(o, lv):
    if lv is not None:
        o.sz_lods.active_lod_level = lv


def mesh_weights(o, me):
    """(n, groups) weights of this LOD mesh, with the object's group names"""
    names = [g.name for g in o.vertex_groups]
    W = np.zeros((len(me.vertices), max(1, len(names))))
    for v in me.vertices:
        for g in v.groups:
            if g.group < len(names):
                W[v.index, g.group] = g.weight
    return W, names


def body(parts, skeleton):
    """merged body of a ped: world positions, weights, names, triangles, BVH (+ its armature)"""
    arm, pos, W, tris, off = None, [], [], [], 0
    got, nrms = [], []
    for part in BODY_PARTS:
        path = parts.get(part)
        if not path:
            continue
        objs = imp(path, skeleton)
        arm = arm or next((o for o in objs if o.type == "ARMATURE"), None)
        ms = models_of(objs)
        if not ms:
            continue
        o = max(ms, key=lambda m: len(m.data.vertices))
        me = o.data
        p = np.array([tuple(o.matrix_world @ v.co) for v in me.vertices])
        rot = o.matrix_world.to_3x3()
        nv = np.array([tuple((rot @ v.normal).normalized()) for v in me.vertices])
        w, nm = mesh_weights(o, me)
        me.calc_loop_triangles()
        t = np.array([tuple(x.vertices) for x in me.loop_triangles], int).reshape(-1, 3)
        pos.append(p)
        nrms.append(nv)
        W.append((w, nm))
        tris.append(t + off)
        off += len(p)
        got.append(part)
        for x in objs:   # the body is only a reference: hidden from the export
            x.hide_set(True)
    if not pos:
        raise RuntimeError("no body drawable could be read")
    all_names = sorted({n for _, nm in W for n in nm})
    Wm = np.zeros((off, len(all_names)))
    r0 = 0
    for w, nm in W:
        for j, n in enumerate(nm):
            Wm[r0:r0 + len(w), all_names.index(n)] = w[:, j]
        r0 += len(w)
    P = np.vstack(pos)
    T = np.vstack(tris)
    bvh = BVHTree.FromPolygons([tuple(x) for x in P], [tuple(x) for x in T])
    return {"pos": P, "nrm": np.vstack(nrms), "W": Wm, "names": all_names, "tris": T, "bvh": bvh, "arm": arm,
            "parts": got, "canon": canon_of(P), "kd": None}


def canon_of(P, digits=4):
    """one index per position (meshes split vertices at UV / normal seams): the first vertex at that spot"""
    seen = {}
    return np.array([seen.setdefault((round(p[0], digits), round(p[1], digits), round(p[2], digits)), i)
                     for i, p in enumerate(P)])


def welded_neighbours(tris, canon, n):
    """neighbours across the seams (by position), as lists of canonical indices"""
    nb = [set() for _ in range(n)]
    for t in tris:
        a, b, c = canon[t[0]], canon[t[1]], canon[t[2]]
        for x, y in ((a, b), (b, c), (c, a)):
            if x != y:
                nb[x].add(y)
                nb[y].add(x)
    return [np.array(sorted(s), int) for s in nb]


# ===================================================================================================== bones
def bone_mat(arm, name):
    return arm.matrix_world @ arm.data.bones[name].matrix_local


def first_bone(arm, names):
    for n in (names or "").split(","):
        if n and n in arm.data.bones:
            return n
    return None


class Retarget:
    """per-bone rest change A -> B (4x4 numpy), by bone name; a bone B lacks follows its nearest shared ancestor"""

    def __init__(self, arm_a, arm_b):
        self.a, self.b = arm_a, arm_b
        self.map, self.R = {}, {}
        root = next((b.name for b in arm_b.data.bones if b.parent is None), None)
        for bn in arm_a.data.bones:
            name = bn.name
            cur = bn
            while cur is not None and cur.name not in arm_b.data.bones:
                cur = cur.parent
            tgt = cur.name if cur is not None else root
            self.map[name] = tgt
            if tgt is None:
                self.R[name] = np.eye(4)
            else:
                ma = bone_mat(arm_a, tgt if tgt in arm_a.data.bones else name)
                mb = bone_mat(arm_b, tgt)
                self.R[name] = np.array(mb @ ma.inverted())
        self.missing = sorted(n for n, t in self.map.items() if t != n)

    def target(self, name):
        return self.map.get(name, name if name in self.b.data.bones else None)

    def matrices(self, names):
        return np.stack([self.R.get(n, np.eye(4)) for n in names])

    def points(self, P, W, names):
        """linear blend: sum_b w_b R_b p (rows without weights keep their place)"""
        if len(P) == 0:
            return P.copy()
        M = self.matrices(names)                       # (g, 4, 4)
        Ph = np.hstack([P, np.ones((len(P), 1))])      # (n, 4)
        moved = np.einsum("gij,nj->ngi", M, Ph)[:, :, :3]   # (n, g, 3)
        s = W.sum(1, keepdims=True)
        out = (W[:, :, None] * moved).sum(1) / np.maximum(s, 1e-9)
        out[s[:, 0] < 1e-9] = P[s[:, 0] < 1e-9]
        return out

    def rotations(self, W, names):
        """(n, 3, 3) blended rotation of every row (for the normals)"""
        M = self.matrices(names)[:, :3, :3]
        s = W.sum(1, keepdims=True)
        R = np.einsum("ng,gij->nij", W, M) / np.maximum(s, 1e-9)[:, :, None]
        R[s[:, 0] < 1e-9] = np.eye(3)
        return R


# ===================================================================================================== fitting
def nearest(bd, P):
    """nearest body spot of every point: location, normal, triangle, distance"""
    loc = np.zeros_like(P)
    nrm = np.zeros_like(P)
    tri = np.zeros(len(P), int)
    dist = np.zeros(len(P))
    for i, p in enumerate(P):
        lo, no, fi, d = bd["bvh"].find_nearest(Vector(p))
        if lo is None:
            loc[i], nrm[i], tri[i], dist[i] = p, (0, 0, 1), 0, 1e9
        else:
            loc[i], nrm[i], tri[i], dist[i] = tuple(lo), tuple(no), fi, d
    return loc, nrm, tri, dist


def bary_weights(bd, loc, tri):
    """the body's bone weights at these spots (barycentric in their triangles)"""
    out = np.zeros((len(loc), len(bd["names"])))
    for i, (p, fi) in enumerate(zip(loc, tri)):
        t = bd["tris"][fi]
        a, b, c = (bd["pos"][k] for k in t)
        v0, v1, v2 = b - a, c - a, p - a
        d00, d01, d11, d20, d21 = v0 @ v0, v0 @ v1, v1 @ v1, v2 @ v0, v2 @ v1
        den = d00 * d11 - d01 * d01 or 1e-12
        bv = (d11 * d20 - d01 * d21) / den
        bw = (d00 * d21 - d01 * d20) / den
        bc = np.clip(np.array([1 - bv - bw, bv, bw]), 0, 1)
        bc /= bc.sum() or 1
        out[i] = bc @ bd["W"][t]
    return out


def girth_field(A, B, rt, reach=0.12, n_iter=4, k=0.5):
    """how far B's skin is from A's skin once A follows B's skeleton, per vertex of A's body: the moved A vertex looks
    along its own (moved) normal, both ways, for B's skin (nearest point when nothing is hit); smoothed over A's body
    across the seams, so the piece gets one smooth field and cannot tear where the nearest body part changes"""
    a1 = rt.points(A["pos"], A["W"], A["names"])
    n1 = np.einsum("nij,nj->ni", rt.rotations(A["W"], A["names"]), A["nrm"])
    n1 /= np.maximum(np.linalg.norm(n1, axis=1, keepdims=True), 1e-9)
    canon = A["canon"]
    D = np.zeros_like(a1)
    hit_rays = 0
    for i in np.unique(canon):
        p, d = Vector(a1[i]), Vector(n1[i])
        best = None
        for s in (1.0, -1.0):
            loc, _, _, dist = B["bvh"].ray_cast(p, d * s, reach)
            if loc is not None and (best is None or dist < best[1]):
                best = (loc, dist)
        if best is None:
            loc, _, _, _ = B["bvh"].find_nearest(p)
            best = (loc if loc is not None else p, 0)
        else:
            hit_rays += 1
        D[i] = np.array(best[0]) - a1[i]
    D = D[canon]
    nb = welded_neighbours(A["tris"], canon, len(canon))
    reps = np.unique(canon)
    for _ in range(n_iter):
        Dn = D.copy()
        for i in reps:
            if len(nb[i]):
                Dn[i] = (1 - k) * D[i] + k * D[nb[i]].mean(0)
        D = Dn[canon]
    report["girth_rays"] = f"{hit_rays}/{len(reps)}"
    A["moved"] = a1 + D
    A["kd"] = kd_of(A["pos"], reps)
    from mathutils.kdtree import KDTree
    kd = KDTree(len(reps))
    for i in reps:
        kd.insert(Vector(A["moved"][i]), int(i))
    kd.balance()
    A["moved_kd"] = kd
    return D


def kd_of(P, ids=None):
    from mathutils.kdtree import KDTree
    ids = range(len(P)) if ids is None else ids
    ids = list(ids)
    kd = KDTree(len(ids))
    for i in ids:
        kd.insert(Vector(P[i]), int(i))
    kd.balance()
    return kd


def shepard(P, kd, field, sigma):
    """field (on the points of kd) at P: gaussian-weighted average within 2.5 sigma (sigma per point), else nearest"""
    out = np.zeros((len(P), field.shape[1]))
    for i, p in enumerate(P):
        sg = float(sigma[i])
        found = kd.find_range(Vector(p), 2.5 * sg)
        if found:
            idx = np.array([f[1] for f in found])
            w = np.exp(-(np.array([f[2] for f in found]) / sg) ** 2)
            out[i] = (w[:, None] * field[idx]).sum(0) / max(w.sum(), 1e-9)
        else:
            _, j, _ = kd.find(Vector(p))
            out[i] = field[j]
    return out


def bumps(P, centres, vectors, sigma):
    """smooth push of the points P by pushes at `centres`: per point the strongest push felt (gaussian falloff with
    the distance), in the weighted mean direction of the pushes around it: a smooth bump, no spikes or crumples"""
    if not len(centres):
        return np.zeros_like(P)
    C = np.asarray(centres)
    V = np.asarray(vectors)
    amt = np.linalg.norm(V, axis=1)
    kd = kd_of(C)
    out = np.zeros_like(P)
    for i, p in enumerate(P):
        found = kd.find_range(Vector(p), 2.5 * sigma)
        if not found:
            continue
        idx = np.array([f[1] for f in found])
        w = np.exp(-(np.array([f[2] for f in found]) / sigma) ** 2)
        mag = float((w * amt[idx]).max())
        d = (w[:, None] * V[idx]).sum(0)
        nd = np.linalg.norm(d)
        if nd > 1e-9 and mag > 1e-5:
            out[i] = d / nd * mag
    return out


def fit(P, W, names, rt, A, B, D, stats, tris=None):
    """world points of ped A -> world points on ped B (steps 1-3 of the docstring); tris = the piece's triangles (the
    check for B's skin coming through the piece between its points)"""
    P1 = rt.points(P, W, names)
    loc, nrm, tri, dist = nearest(A, P)
    side_a = np.einsum("ij,ij->i", P - loc, nrm)
    # the girth change: a smooth (gaussian) average of A's body change around the point, wider the farther the
    # point is from the body (a drape under the chin follows the chin and neck together, smoothly)
    sigma = 0.02 + 0.6 * np.minimum(dist, 0.4)
    delta = shepard(P, A["kd"], D, sigma)
    far = np.clip(1.0 - (dist - 0.12) / 0.30, 0.3, 1.0)       # 1 near the body, 0.3 from 42 cm away
    P2 = P1 + delta * far[:, None]
    P_girth = P2.copy()
    # outside stays outside: where B's skin came closer than the point was to A's (and closer than the gap), the
    # piece goes out as a smooth bump around those points
    keep = np.maximum(side_a, GAP)
    want = (side_a > 0) & (dist < 0.08)
    pushed = np.zeros(len(P2), bool)
    for _ in range(3):
        q2, n2, _, _ = nearest(B, P2)
        side_b = np.einsum("ij,ij->i", P2 - q2, n2)
        push = want & (side_b < GAP) & (side_b < side_a - 0.001)
        if not push.any():
            break
        pushed |= push
        need = n2[push] * (keep[push] - side_b[push])[:, None]
        P2 = P2 + bumps(P2, P2[push], need, 0.012)
    if tris is not None and len(tris):
        # B's skin through the piece between its points (a nose tip): only what is deeper than on A (made so)
        before = {int(A["canon"][h[0]]): h[3] for h in piece_under_skin(P, tris, A, depth=0.08)}
        P2, n_skin = skin_through(P2, tris, B, stats, A, before)
        stats["skin_fixed"] = stats.get("skin_fixed", 0) + n_skin
    # pushes + bumps: smoothed over the piece (across its seams), never more than 4 cm past where the girth put it
    extra = P2 - P_girth
    if tris is not None and len(tris):
        canon = canon_of(P_girth)
        nb = welded_neighbours(tris, canon, len(P2))
        reps = np.unique(canon)
        extra = extra[canon]
        for _ in range(6):
            en = extra.copy()
            for i in reps:
                if len(nb[i]):
                    en[i] = 0.5 * extra[i] + 0.5 * extra[nb[i]].mean(0)
            extra = en[canon]
        P2 = P_girth + extra
        # what the smoothing let back through (a nose tip): once more, as smooth bumps
        P2, n2_skin = skin_through(P2, tris, B, stats, A, before, sigma=0.018, passes=3)
        stats["skin_fixed"] = stats.get("skin_fixed", 0) + n2_skin
        extra = P2 - P_girth
    n_ex = np.linalg.norm(extra, axis=1)
    big = n_ex > 0.04
    P2[big] = P_girth[big] + extra[big] / n_ex[big, None] * 0.04
    # the last of it: a point still inside B (it was outside A) goes out by what is left (small after the smoothing)
    q2, n2, _, _ = nearest(B, P2)
    side_b = np.einsum("ij,ij->i", P2 - q2, n2)
    rest = want & (side_b < np.minimum(keep, side_a) - 0.0005) & (side_b < GAP)
    amount = np.clip(np.minimum(keep, GAP) - side_b, 0, 0.01)
    P2[rest] += n2[rest] * amount[rest, None]
    stats["last_pass"] = stats.get("last_pass", 0) + int(rest.sum())
    q2, n2, _, _ = nearest(B, P2)
    side_b = np.einsum("ij,ij->i", P2 - q2, n2)
    stats["inside_after"] = stats.get("inside_after", 0) + int((want & (side_b < 0)).sum())
    stats["points"] += len(P)
    stats["pushed"] += int(pushed.sum())
    stats["max_move_cm"] = max(stats["max_move_cm"], float(np.linalg.norm(P2 - P, axis=1).max() * 100) if len(P) else 0)
    stats["girth_cm"] = max(stats["girth_cm"], float(np.linalg.norm(delta, axis=1).max() * 100) if len(P) else 0)
    return P2


def piece_under_skin(P, tris, B, depth=0.035):
    """B's skin points with the piece UNDER them (looking from the skin inward, the piece is hit within `depth`):
    [(skin vertex index, skin point, its normal, how deep the piece is)]"""
    bvh = BVHTree.FromPolygons([tuple(x) for x in P], [tuple(t) for t in tris])
    lo, hi = P.min(0) - 0.05, P.max(0) + 0.05
    near = np.nonzero(((B["pos"] >= lo) & (B["pos"] <= hi)).all(1))[0]
    out = []
    for i in np.unique(B["canon"][near]):
        p, n = Vector(B["pos"][i]), Vector(B["nrm"][i])
        loc, _, _, d = bvh.ray_cast(p - n * 0.0005, -n, depth)
        if loc is not None:
            out.append((int(i), B["pos"][i], B["nrm"][i], d))
    return out



def skin_through(P, tris, B, stats, A=None, before=None, sigma=0.015, passes=3):
    """the skin of B coming through the piece between its points (a nose tip, lips): the piece goes out along the
    skin's normal there as a smooth bump (gaussian, `sigma`). Spots where the piece was already under A's skin
    (`before`: A's skin vertex -> depth; matched through where A's skin lands on B) only get what is deeper than on A
    (+2 mm), so the piece stays as it was made."""
    P = P.copy()
    fixed = 0
    for _ in range(passes):
        hits = piece_under_skin(P, tris, B)
        cs, vs = [], []
        for h in hits:
            d = h[3]
            if A is not None and before:
                _, j, _ = A["moved_kd"].find(Vector(h[1]))
                d -= before.get(int(A["canon"][j]), 0.0) if j is not None else 0.0
            if d > 0.002:
                cs.append(h[1])
                vs.append(h[2] * (d + GAP))
        if not cs:
            break
        fixed += len(cs)
        P = P + bumps(P, cs, vs, sigma)
    return P, fixed


def remap_groups(o, levels, rt):
    """vertex groups of bones B does not have -> their ancestor's group (weights added), on every LOD"""
    moves = {g.name: rt.target(g.name) for g in o.vertex_groups if rt.target(g.name) and rt.target(g.name) != g.name}
    if not moves:
        return []
    for tgt in set(moves.values()):
        if o.vertex_groups.get(tgt) is None:
            o.vertex_groups.new(name=tgt)
    idx = {g.name: g.index for g in o.vertex_groups}
    for lv, _ in levels:
        set_lod(o, lv)
        me = o.data
        for v in me.vertices:
            add = {}
            for g in v.groups:
                nm = o.vertex_groups[g.group].name
                if nm in moves and g.weight > 0:
                    add[moves[nm]] = add.get(moves[nm], 0) + g.weight
            for tgt, w in add.items():
                o.vertex_groups[idx[tgt]].add([v.index], w, "ADD")
    for nm in moves:
        g = o.vertex_groups.get(nm)
        if g is not None:
            o.vertex_groups.remove(g)
    return sorted(moves)


# ===================================================================================================== export
def export(dct, name):
    es = get_export_settings()
    es.target_formats = {"CWXML"}
    es.target_versions = {"GEN8"}
    es.limit_to_selected = True
    es.exclude_skeleton = True
    es.apply_transforms = False
    bpy.ops.object.select_all(action="DESELECT")
    for o in dct.children_recursive:
        o.hide_set(False)
    dct.hide_set(False)
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


# ===================================================================================================== run
def run():
    src, dst, names = job["src"], job["dst"], job["names"]
    A = body(src["body"], src["skeleton"])
    B = body(dst["body"], dst["skeleton"])
    if A["arm"] is None or B["arm"] is None:
        raise RuntimeError("a ped skeleton could not be read")
    rt = Retarget(A["arm"], B["arm"])
    D = girth_field(A, B, rt)
    report["body_a"], report["body_b"] = A["parts"], B["parts"]
    report["bones_mapped"] = {n: rt.map[n] for n in rt.missing[:40]}
    is_prop = bool(src.get("prop"))
    objs = imp(src["piece"], None if is_prop else src["skeleton"])
    models = models_of(objs)
    if not models:
        raise RuntimeError("the piece has no model")
    stats = {"points": 0, "pushed": 0, "max_move_cm": 0.0, "girth_cm": 0.0, "lods": 0, "models": len(models)}
    remapped = set()
    if is_prop:
        ba = first_bone(A["arm"], src.get("bone") or "SKEL_Head")
        bb = first_bone(B["arm"], src.get("bone") or "SKEL_Head")
        if not ba or not bb:
            raise RuntimeError(f"the anchor bone {src.get('bone')} is not in both skeletons")
        Ma, Mb_inv = bone_mat(A["arm"], ba), bone_mat(B["arm"], bb).inverted()
        report["anchor"] = [ba, bb]
    for o in models:
        levels = lod_meshes(o)
        stats["lods"] += len(levels)
        for lv, me in levels:
            n = len(me.vertices)
            co = np.zeros(n * 3)
            me.vertices.foreach_get("co", co)
            co = co.reshape(-1, 3)
            if is_prop:
                world = np.array([tuple(Ma @ o.matrix_world @ Vector(c)) for c in co])
                W = np.ones((n, 1))
                gnames = [ba]
            else:
                set_lod(o, lv)
                world = np.array([tuple(o.matrix_world @ Vector(c)) for c in co])
                W, gnames = mesh_weights(o, me)
                if not o.vertex_groups:
                    W, gnames = np.ones((n, 1)), [next(b.name for b in A["arm"].data.bones if b.parent is None)]
            me.calc_loop_triangles()
            ptris = np.array([tuple(t.vertices) for t in me.loop_triangles], int).reshape(-1, 3)
            moved = fit(world, W, gnames, rt, A, B, D, stats, ptris)
            back = (o.matrix_world.inverted() @ Mb_inv) if is_prop else o.matrix_world.inverted()
            new = np.array([tuple(back @ Vector(p)) for p in moved])
            # custom normals are kept relative to the surface by Blender: they turn with it, nothing to do
            me.vertices.foreach_set("co", new.ravel())
            me.update()
        if not is_prop:
            remapped |= set(remap_groups(o, levels, rt))
            set_lod(o, levels[0][0])
    report["groups_merged"] = sorted(remapped)
    report["fit"] = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in stats.items()}

    # ---- the new drawable: under B's skeleton (components) or as it is (props), with the new names
    dct_src = next((o for o in objs if o.sollum_type == "sollumz_drawable_dictionary"), None)
    drw_src = next((o for o in objs if o.sollum_type == "sollumz_drawable"), None)
    if is_prop:
        dct, drw = dct_src, drw_src
        if dct is None or drw is None:
            dct = create_empty_object(SollumType.DRAWABLE_DICTIONARY)
            drw = drw or create_empty_object(SollumType.DRAWABLE)
            drw.parent = dct
    else:
        sc = imp(dst["scaffold"], dst["skeleton"])
        dct = next(o for o in sc if o.sollum_type == "sollumz_drawable_dictionary")
        drw = next(o for o in sc if o.sollum_type == "sollumz_drawable")
        for m in models_of(sc):
            bpy.data.objects.remove(m, do_unlink=True)
        if drw_src is not None:   # the piece's own drawable settings (LOD distances, flags) go along
            for key in ("lod_dist_high", "lod_dist_med", "lod_dist_low", "lod_dist_vlow"):
                if hasattr(drw_src.drawable_properties, key) and hasattr(drw.drawable_properties, key):
                    setattr(drw.drawable_properties, key, getattr(drw_src.drawable_properties, key))
        for o in models:
            mw = o.matrix_world.copy()
            o.parent = drw
            o.matrix_world = mw
            for md in list(o.modifiers):
                if md.type == "ARMATURE":
                    md.object = dct
            if not any(md.type == "ARMATURE" for md in o.modifiers):
                o.modifiers.new("Armature", "ARMATURE").object = dct
        for x in (drw_src, dct_src):
            if x is not None and x not in (dct, drw):
                bpy.data.objects.remove(x, do_unlink=True)
    drw.name = names["drawable"]
    dct.name = names["dict"]
    xml = export(dct, names["dict"])
    verts = sum(len(me.vertices) for o in models for _, me in lod_meshes(o))
    return {"xml": xml, "verts": verts, "report": report}


try:
    res = run()
except Exception as e:  # the caller turns it into a refusal / error
    res = {"error": str(e)[:500], "trace": traceback.format_exc()[-2500:]}
    log(res["trace"])
json.dump(res, open(result_path, "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
sys.stdout.flush()
os._exit(0)
