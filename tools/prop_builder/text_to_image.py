"""Description -> product photo (PNG) for the image to 3D step.
    python text_to_image.py "<description>" <out.png> [<info.json>]
The description can be in any language: anything that is not plain English is translated first.
Tries the free Pollinations API (no key, no GPU quota), then the FLUX.1-schnell Space on Hugging Face
(uses HF_TOKEN and the daily GPU quota, which the 3D step also needs).
Writes {"prompt": ..., "english": ..., "engine": ...} to <info.json> when given.
"""
import json
import os
import random
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request

from PIL import Image

text, out = sys.argv[1].strip(), sys.argv[2]
info_path = sys.argv[3] if len(sys.argv) > 3 else None
if not text:
    sys.exit("empty description")

UA = {"User-Agent": "prop-builder/1.0 (https://github.com/iiK7/prop-builder)"}


def fetch(url, timeout):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def translate(s):
    """English stays as it is; anything else goes through the free text API, else stays as typed"""
    if re.fullmatch(r"[\x20-\x7e]+", s):
        return s
    ask = f"Translate this furniture description to English. Reply with the translation only, no quotes: {s}"
    for attempt in range(3):
        try:
            reply = fetch("https://text.pollinations.ai/" + urllib.parse.quote(ask), 60).decode("utf-8", "replace").strip()
            reply = reply.strip('"\'').splitlines()[0].strip() if reply else ""
            if reply and re.search(r"[A-Za-z]", reply) and len(reply) < 300:
                return reply
        except Exception as e:
            print(f"translate attempt {attempt + 1} failed: {e}")
        time.sleep(3)
    print("::warning::Could not translate the description, using it as typed")
    return s


english = translate(text)
prompt = (f"e-commerce catalog cutout photo of {english}, one single object isolated on a pure white seamless background, "
          "the whole object in view, three-quarter front view, centered, soft even studio lighting, "
          "empty background, no room, no other furniture, no text, no people")
print("description:", text)
print("english:", english)


def clean_watermark(path):
    """Pollinations stamps a small logo in the bottom-right corner: paint it with the background colour"""
    img = Image.open(path).convert("RGB")
    w, h = img.size
    corner = img.crop((0, int(h * 0.94), int(w * 0.06), h)).resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    img.paste(corner, (int(w * 0.76), int(h * 0.95), w, h))
    img.save(out, "PNG")


def pollinations():
    seed = random.randint(1, 2_000_000_000)
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
           + f"?width=1024&height=1024&nologo=true&model=flux&seed={seed}")
    raw = out + ".download"
    with open(raw, "wb") as f:
        f.write(fetch(url, 240))
    Image.open(raw).verify()  # not an image -> raises
    clean_watermark(raw)
    os.remove(raw)


def flux_space():
    from gradio_client import Client
    token = os.environ.get("HF_TOKEN") or None
    client = Client("black-forest-labs/FLUX.1-schnell", token=token, verbose=False)
    result, _ = client.predict(prompt=prompt, seed=0, randomize_seed=True, width=1024, height=1024,
                               num_inference_steps=4, api_name="/infer")
    path = result.get("path") if isinstance(result, dict) else result
    tmp = out + ".download"
    shutil.copy(path, tmp)
    Image.open(tmp).convert("RGB").save(out, "PNG")
    os.remove(tmp)


engine, last_error = None, None
for name, make, tries in (("pollinations", pollinations, 3), ("flux", flux_space, 1)):
    for attempt in range(tries):
        try:
            print(f"{name}: attempt {attempt + 1}")
            make()
            engine = name
            break
        except Exception as e:
            last_error = e
            print(f"{name} failed: {e}")
            time.sleep(5)
    if engine:
        break

if not engine:
    sys.exit(f"description to image failed: {last_error}")

print("image:", out, os.path.getsize(out), "bytes", f"({engine})")
if info_path:
    with open(info_path, "w") as f:
        json.dump({"prompt": text, "english": english, "engine": engine}, f, ensure_ascii=False)
