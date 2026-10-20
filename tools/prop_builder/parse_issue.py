"""Read a 'New prop' issue form body and print shell assignments:
NAME=..., HEIGHT=..., MODE=..., ENGINE=..., FILE_URL=...
"""
import os
import re
import sys

body = os.environ.get("ISSUE_BODY", "")


def field(title):
    m = re.search(r"###\s*" + re.escape(title) + r"\s*\n+(.*?)(?=\n###|\Z)", body, re.S)
    v = (m.group(1).strip() if m else "")
    return "" if v == "_No response_" else v


name = re.sub(r"[^a-z0-9_]", "_", field("Prop name").lower()).strip("_")[:40]
height = re.sub(r"[^0-9.]", "", field("Height (metres)")) or "0"
file_block = field("Image or 3D model")
MODES = {"bag": "bag", "box": "box", "board": "board", "can": "can", "room": "room"}
mode = next((v for k, v in MODES.items() if k in field("Type").lower()), "ai")
ENGINES = {"trellis.2": "trellis2", "hunyuan": "hunyuan", "trellis": "trellis", "triposr": "triposr"}
engine = next((v for k, v in ENGINES.items() if k in field("3D engine").lower()), "")
urls = re.findall(r"https://[^\s)\"'>\]]+", file_block)
if not name or not urls:
    sys.exit("missing prop name or file")
print(f"NAME={name}")
if mode == "room":
    m = re.search(r"(\d+(?:\.\d+)?)\s*[x×*]\s*(\d+(?:\.\d+)?)", field("Height (metres)"))
    height = f"{m.group(1)}x{m.group(2)}" if m else "4x4"
print(f"HEIGHT={height}")
print(f"MODE={mode}")
print(f"ENGINE={engine}")
print(f"FILE_URL={urls[0]}")
