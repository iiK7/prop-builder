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
- **Clothes**: the site's clothes page (`docs/clothes/`); builds run in the private `claude-private` repository, see below.
- **Game**: the estate script starts builds this way without engines; they come from `settings.json` (`game`), which the
  site's "pieces from the game" card saves.

Results are uploaded as a run artifact and copied to the `prop-builds` branch under `builds/<name>/`
(zip, `preview.png`, `info.json` and the game files in `stream/`).
After every run the workflow also updates `status.json` at the top of `prop-builds`: the last known Hugging Face
state per engine (last success, last "quota used up", and the seconds left / retry time when the quota message
gives them) and the last 30 failed builds with their request, so the site can show them and rebuild them.
It never holds the token or error text.

## Local run

```bash
pip install -r requirements.txt
# DEPS must contain Sollumz/ (pinned commit in the workflow) and the published cwconv/
PY=python3 DEPS=/path/to/deps ./build_prop.sh model.glb my_prop out 1.0
```

## Clothes designer (new textures for existing clothes)

The **code** is here (`clothes_variant.py`, `garment_templates.py` + `garment_bpy.py`, `transfer_bpy.py`, `clothes_preview.py` +
`clothes_preview_bpy.py`, `cwconv`) and the site page
`docs/clothes/` ("مصمم الملابس"); the **workflow, the uploaded packs and the results live in the private repository
`iiK7/claude-private`** (`.github/workflows/clothes-builder.yml` there checks this repository out for the tools). Nothing
clothes-related is processed or stored in this public repository: clothing packs are often paid, so they must never be
uploaded here (no `clothes_requests/` folder, no clothes results on `prop-builds`).

The page uploads `clothes_requests/<name>.zip` (the pack, or only this piece's files + the ped's `.yft` when the pack is
over 40 MB) and `clothes_requests/<name>.json` (the design) to `claude-private` in one commit, and reads the results from
its `clothes-builds` branch through the API with the owner's key (a fine-grained token with access to **both**
prop-builder and claude-private: Contents read/write, Actions read).

**Moving a piece to another ped** (`"mode": "transfer"`, the page's "🔁 نقل قطعة"): an existing component or prop of
ped A goes to ped B as B's next drawable, with all its textures and B's `.ymt` entry (A's own flags). The request zip is
ped B's pack with ped A's under `__source__/` (A only with its skeleton, body `_000` and the piece). `transfer_bpy.py`
(Blender + Sollumz) moves every LOD: each point follows its own bones from A's rest pose to B's (a bone B lacks: its
nearest shared ancestor), then B's girth (A's skin looked up on B's along its normals, a smooth gaussian average
around the point); what ends up inside B's skin, or lets B's skin through between its points (a nose tip), goes out as
smooth bumps, only where it is deeper than it was on A. Props move from A's anchor bone space to B's. Both peds need
their `.yft` and body `_000` drawables (freemode: the game's body files).

`clothes_variant.py`: a new colour (`tint`), logo (`logo`) or pattern from a description (`prompt`, via `text_to_image.py
... pattern`) for one drawable of a freemode MP pack or an addon ped.
The tool adds the first letter without a file (`_b` ... `_z`; a hole between the files first): a copy of the base `.ytd` with the diffuse recoloured and renamed
(same format, size and mip count; BC7 is written as DXT5/DXT1), or one more texture inside `<ped>.ytd` when every
texture lives there. Real packs often list more `aTexData` items than files (the game skips the missing ones): when the
`.ymt` already has a slot for that letter it stays as it is (only its texId is set to the base file's race, uni / whi /
bla ...); otherwise items are added up to the new letter and `numAvailTex` (= all textures of the component) grows by
the same number. Props of an addon ped follow its `PropsName` (meta file): their files are `<PropsName>^p_..._diff_...`
and `<PropsName>.ymt` must be in the pack. It refuses escrow (`.fxap`) / unreadable files, packs without a `.ymt`
(base-game items must be made an addon first) and 26 letters used.

`clothes_preview.py` (3D preview, runs with every request and alone with `"mode": "preview"`): the ped body `.yft`
(skeleton) and every component / prop `.ydd` -> CodeWalker XML (`cwconv res-to-xml`) -> Blender `bpy` + Sollumz (same
pinned versions as the props) -> one `glb/<comp>_<nnn>.glb` per drawable in the skeleton's rest pose (props moved to their
anchor bone), textures as `tex/<name>.png` (at most 1024 px), `parts.json` listing drawables, texture letters and which
texture each material uses. A built design's zip is laid over the pack so the new letter is in the list. The page shows
it with three.js.
Results (private `clothes-builds` branch): `clothes/<name>/` (zip, `preview_before.png`, `preview_after.png`,
`info.json`, also for a refusal with its reason), `clothes_analysis/<name>/` for `"mode": "analyze"`,
`clothes_preview/<name>/` for the 3D preview.

**New piece ("قطعة جديدة", `"mode": "garment"`)**: `garment_templates.py` is the registry (15 templates: ثوب، كندورة،
عباية، روب، تيشيرت، قميص بأكمام طويلة، هودي، جاكيت، صدرية، بنطلون، شورت، تنورة، شماغ وعقال، بشت، كاب; Arabic labels,
allowed components, parameter schema, UV layout; `--json` prints it, `--sync-page docs/clothes/index.html` copies it into
the page). `garment_bpy.py` (Blender bpy + Sollumz) fits the template to the ped's OWN body: landmarks from its skeleton,
the outline from its base drawables (trunk rings from the torso / legs, sleeves along the arm bones, one tube per trouser
leg, head shells by rays at the head + hair), inner lining, skin weights by transfer from the body (4 bones max,
normalised; skirts smoothed around the ring), UV islands with the front of the body in the middle, a generated fabric
texture (weave / knit / twill / denim / terry ..., seams, placket, buttons, zip, pockets, trims) + normal / spec maps, and
exports CodeWalker XML. `clothes_variant.py` builds the `.ydd` / `.ytd` with cwconv, adds the drawable (+ compInfos) or
the head prop (+ aAnchors) to the `.ymt` at the next free number, checks the round trips and zips the resource. Long
garments also add a `lowr` that hides the trousers (empty, or the base trousers cut below the hem); shorts and skirts draw
the legs below them in the ped's skin tone. The designer's colour / logo / pattern can go on top (never on skin, gold trim
or the agal). It needs the ped's `.yft` and `uppr_000` / `lowr_000` (a freemode pack therefore needs the base body files
uploaded with it); `p_head` needs the props `.ymt` (`auto` falls back to `berd`).

cwconv commands used for it: `ytd-export <ytd> <dir>`, `ytd-build <dir|dds...> <out.ytd> [--rename a=b] [--strict]`,
`ymt-to-xml <ymt> <xml>`, `xml-to-ymt <xml> <ymt> [--pso]`, `res-to-xml <yft|ydd|ydr> <dir>`, and `<x.ydd.xml> <out.ydd>`
(drawable dictionary from CodeWalker XML, embedded textures from the folder next to it).
