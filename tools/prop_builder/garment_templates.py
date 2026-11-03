"""Garment templates for the clothes designer's "new piece" mode (clothes_variant.py "mode": "garment").

Each template is a parametric garment that garment_bpy.py (Blender bpy + Sollumz) fits to the ped's OWN body: the ped's
skeleton (<ped>.yft) and its base drawables (uppr / lowr / hand / head / hair / feet _000) give the outline, every piece
is skinned by weight transfer from the body (at most 4 bones, normalised), gets clean UV islands and a generated fabric
texture (weave, seams, placket / buttons / zip drawn in), and is exported as a NEW drawable of its component (the next
free index) with the .ymt entries added. This file is plain Python (no bpy): the registry, the parameter schema and its
checks. The page (docs/clothes/) carries the same registry; `python garment_templates.py --json` prints it.

Registry entry:
  id, label (Arabic), group (long | upper | lower | extra), icon, slots (allowed components / props, the first is the
  default for an addon ped; "auto" picks p_head when the props .ymt can take it, else berd), params (schema:
  type range | select | bool | color, min / max / step / default / options with Arabic labels).
"""
import json
import re
import sys

GROUPS = {"long": "ثياب طويلة", "upper": "علوي", "lower": "سفلي", "extra": "إضافات"}

COLOR = lambda d: {"id": "color", "type": "color", "label": "لون القماش", "default": d}  # noqa: E731


def rng(pid, label, lo, hi, default, step=1, unit=""):
    return {"id": pid, "type": "range", "label": label, "min": lo, "max": hi, "step": step, "default": default, "unit": unit}


def sel(pid, label, options, default):
    return {"id": pid, "type": "select", "label": label, "options": [{"value": v, "label": lab} for v, lab in options],
            "default": default}


def flag(pid, label, default):
    return {"id": pid, "type": "bool", "label": label, "default": default}


HIDE_LOWER = lambda d: sel("hide_lower", "اللي تحت (البنطلون)", [  # noqa: E731
    ("empty", "يختفي (قطعة lowr فاضية)"), ("cut", "يبقى تحت الطرف بس"), ("keep", "يبقى مثل ما هو")], d)
EASE = lambda d, hi=8: rng("ease", "الوسع عن الجسم", 0.5, hi, d, 0.5, "cm")  # noqa: E731

TEMPLATES = [
    # ---------------------------------------------------------------- long
    {"id": "thobe", "label": "ثوب", "group": "long", "icon": "👘", "slots": ["uppr"],
     "note": "ثوب سعودي بياقة واقفة وجيب أزرار. يختفي البنطلون تحته (قطعة lowr فاضية جديدة).",
     "params": [COLOR("#f4f2ec"), rng("length", "الطول (ركبة ← الكعب)", 0, 100, 100, 1, "%"), EASE(2.2),
                rng("flare", "الاتساع لتحت", 0, 100, 30, 1, "%"), rng("sleeve", "طول الكم", 60, 100, 100, 1, "%"),
                rng("sleeve_width", "عرض الكم", 70, 160, 100, 5, "%"),
                sel("collar", "الياقة", [("band", "واقفة"), ("shirt", "ياقة قميص"), ("none", "بدون")], "band"),
                rng("buttons", "الأزرار", 0, 7, 5), HIDE_LOWER("empty")]},
    {"id": "kandura", "label": "كندورة", "group": "long", "icon": "🥻", "slots": ["uppr"],
     "note": "كندورة إماراتية بدون ياقة، رقبة مدورة وكركوشة (طرّة) معلقة على الصدر.",
     "params": [COLOR("#f6f5f1"), rng("length", "الطول (ركبة ← الكعب)", 0, 100, 100, 1, "%"), EASE(2.4),
                rng("flare", "الاتساع لتحت", 0, 100, 35, 1, "%"), rng("sleeve", "طول الكم", 60, 100, 100, 1, "%"),
                rng("sleeve_width", "عرض الكم", 70, 160, 105, 5, "%"),
                rng("tassel", "طول الكركوشة", 0, 40, 24, 1, "cm"), HIDE_LOWER("empty")]},
    {"id": "abaya", "label": "عباية", "group": "long", "icon": "🧕", "slots": ["uppr"],
     "note": "عباية واسعة بأكمام عريضة، مفتوحة من قدام (تحتها طبقة داخلية) أو مسكّرة.",
     "params": [COLOR("#141416"), rng("length", "الطول (ركبة ← الكعب)", 0, 100, 100, 1, "%"), EASE(3.5),
                rng("flare", "الاتساع لتحت", 0, 100, 60, 1, "%"), rng("sleeve", "طول الكم", 60, 100, 100, 1, "%"),
                rng("sleeve_width", "عرض الكم", 100, 220, 170, 5, "%"),
                sel("opening", "الفتحة من قدام", [("open", "مفتوحة"), ("closed", "مسكّرة")], "open"),
                rng("open_width", "عرض الفتحة", 2, 30, 12, 1, "cm"),
                flag("trim", "تطريز على الأطراف", True), HIDE_LOWER("empty")]},
    {"id": "robe", "label": "روب", "group": "long", "icon": "🛁", "slots": ["uppr"],
     "note": "روب حمام بفتحة V على الصدر وحزام مربوط. يبقى البنطلون تحت طرف الروب بس.",
     "params": [COLOR("#e9e4da"), rng("length", "الطول (ركبة ← الكعب)", 0, 100, 25, 1, "%"), EASE(3.0),
                rng("flare", "الاتساع لتحت", 0, 100, 20, 1, "%"), rng("sleeve", "طول الكم", 50, 100, 88, 1, "%"),
                rng("sleeve_width", "عرض الكم", 90, 180, 135, 5, "%"),
                rng("v_depth", "عمق فتحة الصدر", 5, 40, 24, 1, "cm"), flag("belt", "حزام", True),
                sel("fabric", "القماش", [("terry", "فوطة"), ("satin", "ساتان")], "terry"), HIDE_LOWER("cut")]},
    # ---------------------------------------------------------------- upper
    {"id": "tshirt", "label": "تيشيرت", "group": "upper", "icon": "👕", "slots": ["uppr", "jbib", "accs"],
     "note": "تيشيرت قطن بأكمام قصيرة ورقبة مدورة.",
     "params": [COLOR("#f2f2f2"), rng("length", "الطول (الخصر ← نص الفخذ)", 0, 100, 50, 1, "%"), EASE(1.5),
                rng("sleeve", "طول الكم", 25, 60, 45, 1, "%"), rng("sleeve_width", "عرض الكم", 80, 150, 110, 5, "%"),
                sel("neck", "الرقبة", [("round", "مدورة"), ("v", "V")], "round")]},
    {"id": "shirt", "label": "قميص بأكمام طويلة", "group": "upper", "icon": "👔", "slots": ["uppr", "jbib"],
     "note": "قميص بياقة وأزرار من فوق لتحت وأساور.",
     "params": [COLOR("#cfe0f2"), rng("length", "الطول (الخصر ← نص الفخذ)", 0, 100, 55, 1, "%"), EASE(1.6),
                rng("sleeve", "طول الكم", 60, 100, 100, 1, "%"), rng("buttons", "الأزرار", 3, 9, 7),
                flag("pocket", "جيب صدر", True)]},
    {"id": "hoodie", "label": "هودي", "group": "upper", "icon": "🧥", "slots": ["uppr", "jbib"],
     "note": "هودي بكابيشن (مرفوع أو نازل) وجيب كنغر وحبال.",
     "params": [COLOR("#7b8088"), rng("length", "الطول (الخصر ← نص الفخذ)", 0, 100, 45, 1, "%"), EASE(2.6),
                rng("sleeve", "طول الكم", 60, 100, 100, 1, "%"),
                sel("hood", "الكابيشن", [("down", "نازل على الظهر"), ("up", "على الراس"), ("none", "بدون")], "down"),
                flag("pocket", "جيب كنغر", True), flag("strings", "حبال", True)]},
    {"id": "jacket", "label": "جاكيت", "group": "upper", "icon": "🧥", "slots": ["jbib", "uppr"],
     "note": "جاكيت بسحاب: مفتوح (يبين اللي تحته) أو مسكّر، بياقة واقفة.",
     "params": [COLOR("#1f2a3a"), rng("length", "الطول (الخصر ← نص الفخذ)", 0, 100, 40, 1, "%"), EASE(3.2),
                rng("sleeve", "طول الكم", 60, 100, 100, 1, "%"),
                sel("opening", "السحاب", [("open", "مفتوح"), ("closed", "مسكّر")], "closed"),
                rng("open_width", "عرض الفتحة", 4, 30, 14, 1, "cm"),
                sel("collar", "الياقة", [("band", "واقفة"), ("shirt", "ياقة قميص"), ("none", "بدون")], "band"),
                flag("pockets", "جيوب", True)]},
    {"id": "vest", "label": "صدرية", "group": "upper", "icon": "🦺", "slots": ["task", "jbib", "accs"],
     "note": "صدرية بدون أكمام بفتحة V وأزرار، تنلبس فوق القميص.",
     "params": [COLOR("#3a3d42"), rng("length", "الطول (الخصر ← نص الفخذ)", 0, 100, 25, 1, "%"), EASE(2.6),
                rng("v_depth", "عمق فتحة الصدر", 5, 35, 20, 1, "cm"), rng("buttons", "الأزرار", 0, 6, 4)]},
    # ---------------------------------------------------------------- lower
    {"id": "trousers", "label": "بنطلون", "group": "lower", "icon": "👖", "slots": ["lowr"],
     "note": "بنطلون بحزام وسحاب وجيوب.",
     "params": [COLOR("#3b3a36"), rng("length", "الطول (ركبة ← الكعب)", 0, 100, 100, 1, "%"), EASE(1.0, 5),
                rng("width", "العرض", 80, 130, 100, 5, "%"),
                sel("fabric", "القماش", [("twill", "قماش"), ("denim", "جينز")], "twill")]},
    {"id": "shorts", "label": "شورت", "group": "lower", "icon": "🩳", "slots": ["lowr"],
     "note": "شورت لفوق الركبة، والساق تحته تنرسم بلون الجلد.",
     "params": [COLOR("#2e3f5c"), rng("length", "الطول (أعلى الفخذ ← الركبة)", 0, 100, 70, 1, "%"), EASE(1.5, 5),
                rng("width", "العرض", 80, 140, 110, 5, "%")]},
    {"id": "skirt", "label": "تنورة", "group": "lower", "icon": "👗", "slots": ["lowr"],
     "note": "تنورة من الخصر، والساق تحتها تنرسم بلون الجلد.",
     "params": [COLOR("#20201f"), rng("length", "الطول (أعلى الفخذ ← الكعب)", 0, 100, 40, 1, "%"), EASE(1.5, 6),
                rng("flare", "الاتساع لتحت", 0, 100, 40, 1, "%"), flag("pleats", "كسرات", False)]},
    # ---------------------------------------------------------------- extras
    {"id": "shemagh", "label": "شماغ وعقال", "group": "extra", "icon": "🧣", "slots": ["auto", "berd", "p_head"],
     "note": "شماغ نازل على الكتوف مع عقال. auto: إكسسوار راس (p_head) إذا الباك يقبل، وإلا قطعة berd (قناع).",
     "params": [sel("pattern", "النوع", [("red", "شماغ أحمر"), ("white", "غترة بيضاء")], "red"),
                COLOR("#b3202a"), flag("agal", "عقال", True), rng("drape", "النزول على الكتوف", 0, 100, 60, 1, "%"),
                EASE(1.0, 4)]},
    {"id": "bisht", "label": "بشت", "group": "extra", "icon": "🎽", "slots": ["jbib", "task", "accs"],
     "note": "بشت مفتوح من قدام بزري ذهبي، يتلبس فوق الثوب (قطعة منفصلة).",
     "params": [COLOR("#3a2a1c"), rng("length", "الطول (ركبة ← الكعب)", 0, 100, 85, 1, "%"), EASE(5.5, 10),
                rng("flare", "الاتساع لتحت", 0, 100, 70, 1, "%"), rng("sleeve", "طول الكم", 45, 100, 72, 1, "%"),
                rng("open_width", "عرض الفتحة", 6, 40, 20, 1, "cm"), flag("trim", "زري ذهبي", True)]},
    {"id": "cap", "label": "كاب", "group": "extra", "icon": "🧢", "slots": ["auto", "p_head", "berd"],
     "note": "كاب بحافة قدام. auto: إكسسوار راس (p_head) إذا الباك يقبل، وإلا قطعة berd.",
     "params": [COLOR("#1d1d1f"), rng("brim", "طول الحافة", 3, 10, 7, 0.5, "cm"),
                flag("backwards", "مقلوب لورا", False), EASE(0.8, 3)]},
]
# UV layout of each template's texture (u0, v0, u1, v1; v up), as garment_bpy.py makes it: the page draws it for logo
# placement (the front of the body is the middle of "trunk"). Checked against the builds by the tests.
_TOP = [("trunk", "الجسم (القدام بالنص)", (0.0, 0.0, 0.66, 1.0)), ("sleeve_L", "الكم اليسار", (0.67, 0.30, 0.83, 1.0)),
        ("sleeve_R", "الكم اليمين", (0.84, 0.30, 1.0, 1.0))]
ATLAS = {
    "thobe": _TOP, "kandura": _TOP + [("tassel", "الكركوشة", (0.67, 0.0, 0.83, 0.12))],
    "abaya": _TOP + [("inner", "الطبقة الداخلية", (0.84, 0.0, 1.0, 0.29))],
    "robe": _TOP + [("belt", "الحزام", (0.67, 0.0, 0.83, 0.29)), ("inner", "الصدر", (0.84, 0.0, 1.0, 0.29))],
    "tshirt": _TOP, "shirt": _TOP + [("flap", "الياقة", (0.67, 0.0, 0.83, 0.29))],
    "hoodie": _TOP + [("hood", "الكابيشن", (0.67, 0.0, 0.83, 0.29)), ("strings", "الحبال", (0.84, 0.0, 1.0, 0.12))],
    "jacket": _TOP, "vest": _TOP[:1], "bisht": _TOP,
    "trousers": [("leg_L", "الرجل اليسار", (0.0, 0.0, 0.42, 1.0)), ("leg_R", "الرجل اليمين", (0.42, 0.0, 0.84, 1.0))],
    "shorts": [("leg_L", "الرجل اليسار", (0.0, 0.0, 0.42, 1.0)), ("leg_R", "الرجل اليمين", (0.42, 0.0, 0.84, 1.0)),
               ("skin_L", "جلد", (0.84, 0.0, 0.92, 1.0)), ("skin_R", "جلد", (0.92, 0.0, 1.0, 1.0))],
    "skirt": [("trunk", "التنورة (القدام بالنص)", (0.0, 0.0, 0.70, 1.0)), ("skin_L", "جلد", (0.71, 0.0, 0.855, 1.0)),
              ("skin_R", "جلد", (0.855, 0.0, 1.0, 1.0))],
    "shemagh": [("shemagh", "الشماغ", (0.0, 0.0, 1.0, 0.80)), ("agal", "العقال", (0.0, 0.81, 1.0, 1.0))],
    "cap": [("crown", "الكاب (القدام بالنص)", (0.0, 0.0, 0.70, 1.0)), ("brim", "الحافة", (0.71, 0.0, 1.0, 1.0))],
}
for _t in TEMPLATES:
    _t["atlas"] = [{"id": k, "label": lab, "rect": list(r)} for k, lab, r in ATLAS[_t["id"]]]
BY_ID = {t["id"]: t for t in TEMPLATES}

# freemode MP packs: tops go on jbib (uppr is the arms there), extras on the same slots
MP_SLOT = {"long": "jbib", "upper": "jbib"}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class ParamError(ValueError):
    def __init__(self, ar, en):
        super().__init__(en)
        self.ar, self.en = ar, en


def clean_params(tid, params):
    """the template's params with defaults filled, numbers clamped to the schema, options checked"""
    t = BY_ID.get(str(tid or ""))
    if t is None:
        raise ParamError("القالب غلط.", f"unknown template {tid!r}")
    params = params if isinstance(params, dict) else {}
    out = {}
    for p in t["params"]:
        v = params.get(p["id"], p["default"])
        if p["type"] == "range":
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ParamError(f"قيمة {p['label']} لازم رقم.", f"{p['id']} is not a number")
            if v != v:
                v = p["default"]
            v = min(max(v, p["min"]), p["max"])
        elif p["type"] == "select":
            if v not in [o["value"] for o in p["options"]]:
                raise ParamError(f"اختيار {p['label']} غلط.", f"{p['id']}={v!r} is not an option")
        elif p["type"] == "bool":
            v = bool(v)
        elif p["type"] == "color":
            if not isinstance(v, str) or not HEX.match(v):
                raise ParamError("اللون لازم مثل ‎#aa3322.", f"bad colour {v!r}")
            v = v.lower()
        out[p["id"]] = v
    return out


def slot_for(tid, slot, kind):
    """the component / prop the piece goes on: the asked one when the template allows it, else the default.
    kind: 'mp' (freemode MP pack) or 'ped' (addon ped)"""
    t = BY_ID[tid]
    if slot and slot not in t["slots"]:
        raise ParamError(f"{t['label']} ما ينحط على {slot}. المسموح: {', '.join(t['slots'])}.",
                         f"{tid} can't go on {slot} ({t['slots']})")
    if slot:
        return slot
    if kind == "mp" and t["group"] in MP_SLOT and MP_SLOT[t["group"]] in t["slots"] + ["jbib"]:
        return MP_SLOT[t["group"]]
    return t["slots"][0]


def registry():
    return {"groups": GROUPS, "templates": TEMPLATES}


def page_json():
    """the registry as the page embeds it (one line, between /* GARMENTS:BEGIN */ and /* GARMENTS:END */)"""
    return json.dumps(registry(), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def sync_page(path):
    """write the registry into the clothes page (docs/clothes/index.html)"""
    text = open(path, encoding="utf-8").read()
    new = re.sub(r"/\* GARMENTS:BEGIN \*/.*?/\* GARMENTS:END \*/", lambda m: f"/* GARMENTS:BEGIN */{page_json()}/* GARMENTS:END */",
                 text, count=1, flags=re.S)
    if new != text:
        open(path, "w", encoding="utf-8").write(new)
    return new != text


if __name__ == "__main__":
    if "--json" in sys.argv:
        print(json.dumps(registry(), ensure_ascii=False, indent=1))
    elif "--sync-page" in sys.argv:
        print("updated" if sync_page(sys.argv[sys.argv.index("--sync-page") + 1]) else "already current")
    else:
        for t in TEMPLATES:
            print(f"{t['id']:9} {t['group']:6} {'/'.join(t['slots']):16} {t['label']}")
