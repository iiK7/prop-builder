"""Image -> textured 3D model (GLB) using a free TRELLIS Space on Hugging Face.
    python image_to_glb.py <image> <out.glb>
Needs HF_TOKEN in the environment (a free account gives a small daily GPU quota).
"""
import os
import shutil
import sys

from gradio_client import Client, handle_file

image, out = sys.argv[1:3]
SPACES = [s for s in os.environ.get("TRELLIS_SPACES", "trellis-community/TRELLIS").split(",") if s]
token = os.environ.get("HF_TOKEN") or None
print("HF_TOKEN:", "set" if token else "NOT SET (anonymous quota)")

last_error = None
for space in SPACES:
    try:
        print(f"connecting to {space}")
        client = Client(space, token=token, verbose=False)
        try:
            client.predict(api_name="/start_session")
        except Exception:
            pass
        img = handle_file(image)
        try:
            img = handle_file(client.predict(image=img, api_name="/preprocess_image"))
        except Exception as e:
            print("preprocess skipped:", e)
        print("generating 3D (this uses the GPU quota)...")
        params = dict(seed=0, ss_guidance_strength=7.5, ss_sampling_steps=12,
                      slat_guidance_strength=3.0, slat_sampling_steps=12,
                      multiimage_algo="stochastic")
        try:
            # current Space API: one call that generates and extracts the GLB
            result = client.predict(image=img, multiimages=[], mesh_simplify=0.95, texture_size=1024,
                                    api_name="/generate_and_extract_glb", **params)
            result = result[1]  # (video, glb, download_glb)
        except ValueError as e:
            if "api_name" not in str(e):
                raise
            # older Space API: generate, then extract
            client.predict(image=img, multiimages=[], api_name="/image_to_3d", **params)
            result = client.predict(mesh_simplify=0.95, texture_size=1024, api_name="/extract_glb")
        path = result[0] if isinstance(result, (list, tuple)) else result
        if isinstance(path, dict):
            path = path.get("path") or path.get("value")
        shutil.copy(path, out)
        print("glb:", out, os.path.getsize(out), "bytes")
        sys.exit(0)
    except Exception as e:  # try the next Space, keep the API listing for debugging
        last_error = e
        print(f"{space} failed: {e}")
        try:
            client.view_api(print_info=True)
        except Exception:
            pass

if "quota" in str(last_error).lower():
    print("::error title=Daily image-to-3D limit reached::The free Hugging Face GPU quota is used up for today. "
          "Try again later, or use a flat product type (bag, box, board, can), which does not need it.")
sys.exit(f"image to 3D failed on every Space: {last_error}")
