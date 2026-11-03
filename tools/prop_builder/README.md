# Prop builder

Turns an image or a `.glb` model into a FiveM prop resource (`.ydr` + `.ytyp` + `fxmanifest.lua`).
Runs on GitHub Actions (`.github/workflows/prop-builder.yml`), so nothing needs to be installed locally.

## Pipeline

1. **Image to 3D** (`image_to_glb.py`): sends the image to a TRELLIS Space on Hugging Face and gets a textured GLB.
   Needs the `HF_TOKEN` repository secret (free account, small daily GPU quota). Skipped when the input is already a `.glb`.
2. **GLB to CodeWalker XML** (`glb_to_cwxml.py`): Blender (`bpy`) with the Sollumz add-on. Scales the model to the
   requested height, puts the origin at the base, reduces it to 20k triangles, builds a 400-triangle collision mesh
   and exports `<name>.ydr.xml` and `<name>.ytyp.xml`.
   Words in a `.glb` material's name pick special shaders and options:
   `_glow` self-lit (emissivestrong), `_alpha` see-through with the texture's alpha (alpha / emissivestrong_alpha),
   `_noshadow` the prop casts no shadow, `_far` the prop is drawn up to 1500 m away (150 m otherwise).
3. **Textures** (`png_to_dds.py`): PNG to DXT1/DXT5 DDS with a full mip chain.
4. **Binary build** (`cwconv/`): CodeWalker.Core turns the XML into the game's `.ydr` / `.ytyp` and reads them back to check them.
5. **Package** (`build_prop.sh`): resource folder from `template/`, zip and a preview render.

## Ways to start a build

- **Issue**: New issue → "بروب جديد" form. Only the repository owner's issues start a build. The result is posted as a comment.
- **Upload**: add `name.png` (or `name@0.9.png` for a 0.9 m tall prop) to `prop_requests/`.
- **Manual**: Actions → Prop builder → Run workflow, with a link to the file.

Results are uploaded as a run artifact and copied to the `prop-builds` branch under `builds/<name>/`.

## Local run

```bash
pip install -r requirements.txt
# DEPS must contain Sollumz/ (pinned commit in the workflow) and the published cwconv/
PY=python3 DEPS=/path/to/deps ./build_prop.sh model.glb my_prop out 1.0
```

## Particle effects (.ypt)

`.github/workflows/ptfx-builder.yml` uses the same converter (`cwconv`) for particle files:

- add `ptfx_requests/<name>.ypt` (a particle file taken from the game) to get it as editable XML with its
  textures in `ptfx/<name>/xml/` on the `prop-builds` branch;
- add `ptfx_requests/<name>/<name>.ypt.xml` with its `.dds` textures next to it to build `ptfx/<name>/<name>.ypt`
  (read back once to check it).

## Clothes designer (new textures for existing clothes)

`.github/workflows/clothes-builder.yml` + `clothes_variant.py` + the site page `docs/clothes/` ("مصمم الملابس"):
a new colour (`tint`), logo (`logo`) or pattern from a description (`prompt`, via `text_to_image.py ... pattern`) for
one drawable of a freemode MP pack or an addon ped. The page uploads `clothes_requests/<name>.zip` (the pack, or only
this piece's files when the pack is over 40 MB) and `clothes_requests/<name>.json` (the design) in one commit.
The tool adds the first letter without a file (`_b` ... `_z`; a hole between the files first): a copy of the base `.ytd` with the diffuse recoloured and renamed
(same format, size and mip count; BC7 is written as DXT5/DXT1), or one more texture inside `<ped>.ytd` when every
texture lives there. Real packs often list more `aTexData` items than files (the game skips the missing ones): when the
`.ymt` already has a slot for that letter it stays as it is (only its texId is set to the base file's race, uni / whi /
bla ...); otherwise items are added up to the new letter and `numAvailTex` (= all textures of the component) grows by
the same number. Props of an addon ped follow its `PropsName` (meta file): their files are `<PropsName>^p_..._diff_...`
and `<PropsName>.ymt` must be in the pack. It refuses escrow (`.fxap`) / unreadable files, packs without a `.ymt`
(base-game items must be made an addon first) and 26 letters used.
Results: `prop-builds` branch `clothes/<name>/` (zip, `preview_before.png`, `preview_after.png`, `info.json`, also for a
refusal with its reason); an analysis (`"mode": "analyze"`) goes to `clothes_analysis/<name>/`.

cwconv commands used for it: `ytd-export <ytd> <dir>`, `ytd-build <dir|dds...> <out.ytd> [--rename a=b] [--strict]`,
`ymt-to-xml <ymt> <xml>`, `xml-to-ymt <xml> <ymt> [--pso]`.
