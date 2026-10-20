# Prop builder

Turns an image or a `.glb` model into a FiveM prop resource (`.ydr` + `.ytyp` + `fxmanifest.lua`).
Runs on GitHub Actions (`.github/workflows/prop-builder.yml`), so nothing needs to be installed locally.

## Pipeline

1. **Image to 3D** (`to3d.sh` + `image_to_glb.py`) with the engine **you pick** (nothing switches on its own):
   `trellis2` (microsoft/TRELLIS.2, best), `hunyuan` (tencent/Hunyuan3D-2.1), `trellis` (the older TRELLIS) — free
   Hugging Face Spaces that share the `HF_TOKEN` daily GPU quota — or `triposr` (GitHub's CPU, no limit, lower quality).
   When the quota is used up the build fails with a message: pick another engine. Skipped for a `.glb`.
   A description first becomes a picture (`text_to_image.py`, `pollinations` free with no limit, or `flux`).
2. **GLB to CodeWalker XML** (`glb_to_cwxml.py`): Blender (`bpy`) with the Sollumz add-on. Scales the model to the
   requested height, puts the origin at the base, reduces it to 20k triangles, builds a 400-triangle collision mesh
   and exports `<name>.ydr.xml` and `<name>.ytyp.xml`.
3. **Textures** (`png_to_dds.py`): PNG to DXT1/DXT5 DDS with a full mip chain.
4. **Binary build** (`cwconv/`): CodeWalker.Core turns the XML into the game's `.ydr` / `.ytyp` and reads them back to check them.
5. **Package** (`build_prop.sh`): resource folder from `template/`, zip and a preview render.

## Ways to start a build

- **Issue**: New issue → "بروب جديد" form. Only the repository owner's issues start a build. The result is posted as a comment.
- **Upload**: add `name.png` (or `name@0.9.png` for a 0.9 m tall prop) to `prop_requests/`.
- **Manual**: Actions → Prop builder → Run workflow, with a link to the file.
- **Site**: photo, `.glb` or a description, with the 3D engine and the picture source picked on the page.
  It uploads `prop_requests/<name>@<height>[@<mode>]@<engine>[@<picture>].<ext>` (`.txt` = a description).
- **Description**: Run workflow with `prompt` instead of a link (any language); `engine` and `image` pick the engines.
- **Game**: the estate script starts builds this way without engines; they come from `settings.json` (`game`), which the
  site's "pieces from the game" card saves.

Results are uploaded as a run artifact and copied to the `prop-builds` branch under `builds/<name>/`
(zip, `preview.png`, `info.json` and the game files in `stream/`).

## Local run

```bash
pip install -r requirements.txt
# DEPS must contain Sollumz/ (pinned commit in the workflow) and the published cwconv/
PY=python3 DEPS=/path/to/deps ./build_prop.sh model.glb my_prop out 1.0
```
