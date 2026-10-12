"""Room photo -> cut-out crops of the furniture in it + objects.json (YOLOv8 segmentation, CPU).
    python room_detect.py <photo> <out_dir> [max_objects]
"""
import json
import os
import sys

import numpy as np
from PIL import Image
from ultralytics import YOLO

src, out = sys.argv[1], sys.argv[2]
max_objects = int(sys.argv[3]) if len(sys.argv) > 3 else 6
os.makedirs(out, exist_ok=True)

# COCO classes worth turning into props, with a typical real height in metres
HEIGHTS = {
    "couch": 0.85, "chair": 0.9, "bed": 0.6, "dining table": 0.76, "tv": 0.6, "potted plant": 0.8,
    "refrigerator": 1.8, "oven": 0.9, "sink": 0.9, "toilet": 0.8, "bench": 0.5, "clock": 0.3,
    "vase": 0.3, "microwave": 0.3, "laptop": 0.25, "suitcase": 0.6, "bicycle": 1.0, "teddy bear": 0.4,
}

img = Image.open(src).convert("RGB")
if max(img.size) > 1280:
    img.thumbnail((1280, 1280))
W, H = img.size
res = YOLO("yolov8n-seg.pt").predict(img, conf=0.3, verbose=False)[0]
names = res.names
arr = np.asarray(img)
used = np.zeros((H, W), bool)
dets = []
if res.masks is not None:
    masks = res.masks.data.cpu().numpy()
    for k, box in enumerate(res.boxes):
        cls = names[int(box.cls)]
        if cls not in HEIGHTS:
            continue
        m = np.asarray(Image.fromarray((masks[k] * 255).astype(np.uint8)).resize((W, H))) > 127
        area = m.mean()
        if area < 0.012:
            continue
        dets.append((area, cls, float(box.conf), m))
    print("found:", ", ".join(f"{c} {conf:.2f}" for _, c, conf, _ in dets) or "nothing")

objects = []
for i, (area, cls, conf, m) in enumerate(sorted(dets, key=lambda d: -d[0])[:max_objects], 1):
    used |= m
    ys, xs = np.nonzero(m)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    pad = 12
    cx0, cy0, cx1, cy1 = max(x0 - pad, 0), max(y0 - pad, 0), min(x1 + pad, W), min(y1 + pad, H)
    rgba = np.dstack([arr, (m * 255).astype(np.uint8)])[cy0:cy1, cx0:cx1]
    crop = Image.fromarray(rgba, "RGBA")
    side = max(crop.size)
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 0))   # square, centred: what TRELLIS likes
    canvas.paste(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
    canvas.save(f"{out}/obj{i}.png")
    objects.append({"id": f"obj{i}", "class": cls, "conf": round(conf, 2), "height": HEIGHTS[cls],
                    "box": [round(x0 / W, 4), round(y0 / H, 4), round(x1 / W, 4), round(y1 / H, 4)]})

def median_colour(band):
    region = band & ~used
    if region.sum() < 50:
        return [0.8, 0.78, 0.74]
    return [round(float(c) / 255, 3) for c in np.median(arr[region], 0)]

rows = np.arange(H)[:, None] * np.ones((1, W))
info = {
    "size": [W, H],
    "wall": median_colour(rows < H * 0.3),
    "floor": median_colour(rows > H * 0.85),
    "objects": objects,
}
json.dump(info, open(f"{out}/objects.json", "w"), indent=1)
print(json.dumps(info, indent=1))
