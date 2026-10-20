"""Image -> textured 3D model (GLB) on a free Hugging Face Space, with the engine you pick.
    python image_to_glb.py <image> <out.glb> <engine>
engine: trellis2 (microsoft/TRELLIS.2), hunyuan (tencent/Hunyuan3D-2.1) or trellis (trellis-community/TRELLIS).
Only the picked engine is tried. Needs HF_TOKEN (a free account gives a small daily GPU quota).
"""
import os
import shutil
import sys

from gradio_client import Client, handle_file

image, out, engine = sys.argv[1:4]
SPACES = {
    "trellis2": "microsoft/TRELLIS.2",
    "hunyuan": "tencent/Hunyuan3D-2.1",
    "trellis": "trellis-community/TRELLIS",
}
if engine not in SPACES:
    sys.exit(f"unknown 3D engine {engine!r} (pick one of {', '.join(SPACES)} or triposr)")
space = SPACES[engine]
token = os.environ.get("HF_TOKEN") or None
print("HF_TOKEN:", "set" if token else "NOT SET (anonymous quota)")


def path_of(result):
    if isinstance(result, (list, tuple)):
        result = result[0]
    if isinstance(result, dict):
        result = result.get("path") or result.get("value")
    return result


def trellis2(client):
    try:
        client.predict(api_name="/start_session")
    except Exception:
        pass
    img = handle_file(image)
    try:
        img = handle_file(path_of(client.predict(input=img, api_name="/preprocess_image")))
    except Exception as e:
        print("preprocess skipped:", e)
    print("generating 3D with TRELLIS.2 (this uses the GPU quota)...")
    client.predict(image=img, seed=0, resolution="1024", api_name="/image_to_3d")
    # the build reduces the mesh to 20k triangles anyway; a smaller download is enough
    return path_of(client.predict(decimation_target=100000, texture_size=1024, api_name="/extract_glb"))


def hunyuan(client):
    print("generating 3D with Hunyuan3D-2.1 (this uses the GPU quota)...")
    result = client.predict(image=handle_file(image), steps=30, guidance_scale=5.0, seed=1234, octree_resolution=256,
                            check_box_rembg=True, num_chunks=8000, randomize_seed=True, api_name="/generation_all")
    shape, textured = path_of(result[0]), path_of(result[1])
    if textured and str(textured).lower().endswith(".glb"):
        return textured
    # textured mesh in another format: let the Space export it as GLB with its texture
    exported = client.predict(file_out=handle_file(shape), file_out2=handle_file(textured), file_type="glb",
                              reduce_face=False, export_texture=True, target_face_num=10000, api_name="/on_export_click")
    return path_of(exported[1] if isinstance(exported, (list, tuple)) and len(exported) > 1 else exported)


def trellis(client):
    try:
        client.predict(api_name="/start_session")
    except Exception:
        pass
    img = handle_file(image)
    try:
        img = handle_file(client.predict(image=img, api_name="/preprocess_image"))
    except Exception as e:
        print("preprocess skipped:", e)
    print("generating 3D with TRELLIS (this uses the GPU quota)...")
    params = dict(seed=0, ss_guidance_strength=7.5, ss_sampling_steps=12,
                  slat_guidance_strength=3.0, slat_sampling_steps=12, multiimage_algo="stochastic")
    try:
        # current Space API: one call that generates and extracts the GLB
        result = client.predict(image=img, multiimages=[], mesh_simplify=0.95, texture_size=1024,
                                api_name="/generate_and_extract_glb", **params)
        return path_of(result[1])  # (video, glb, download_glb)
    except ValueError as e:
        if "api_name" not in str(e):
            raise
    # older Space API: generate, then extract
    client.predict(image=img, multiimages=[], api_name="/image_to_3d", **params)
    return path_of(client.predict(mesh_simplify=0.95, texture_size=1024, api_name="/extract_glb"))


client = None
try:
    print(f"connecting to {space}")
    client = Client(space, token=token, verbose=False)
    path = {"trellis2": trellis2, "hunyuan": hunyuan, "trellis": trellis}[engine](client)
    shutil.copy(path, out)
    print("glb:", out, os.path.getsize(out), "bytes")
except Exception as e:
    print(f"{space} failed: {e}")
    if client:
        try:
            client.view_api(print_info=True)
        except Exception:
            pass
    if "quota" in str(e).lower():
        print(f"::error title=Daily GPU limit reached ({engine})::The free Hugging Face GPU quota is used up for today. "
              "Pick another engine (TripoSR has no limit) or try again tomorrow.")
    sys.exit(f"image to 3D failed on {space}: {e}")
