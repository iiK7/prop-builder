#!/usr/bin/env bash
# build_prop.sh <model.glb> <prop_name> <out_dir> [height_m]
# Produces <out_dir>/<prop_name>/ (FiveM resource), <out_dir>/<prop_name>.zip and <out_dir>/preview.png
#
# Environment:
#   PY    python with bpy, szio and pillow   (default: python3)
#   DEPS  folder holding Sollumz/ and the built cwconv (default: ./.deps)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PY:-python3}"
DEPS="$(realpath "${DEPS:-$HERE/.deps}")"
CWCONV="$DEPS/cwconv/cwconv"

GLB="$(realpath "$1")"; NAME="$2"; OUT="$(realpath -m "$3")"; HEIGHT="${4:-0}"
[[ "$NAME" =~ ^[a-z0-9_]{3,40}$ ]] || { echo "prop name must be 3-40 chars: a-z 0-9 _"; exit 1; }

WORK="$OUT/.work"; RES="$OUT/$NAME"
rm -rf "$WORK" "$RES"; mkdir -p "$WORK" "$RES/stream"

echo "::group::Blender (Sollumz) -> CodeWalker XML"
"$PY" -u "$HERE/glb_to_cwxml.py" "$GLB" "$WORK" "$NAME" "$DEPS" "$HEIGHT" 2>&1 | tee "$WORK/blender.log" | grep -vE "^(Freeing memory|\s*$)" || true
grep -q "^export: {'FINISHED'}" "$WORK/blender.log" || { echo "Blender export failed"; exit 1; }
echo "::endgroup::"

for png in "$WORK"/*.png; do
    [ -e "$png" ] && "$PY" "$HERE/png_to_dds.py" "$png" "${png%.png}.dds"
done

"$CWCONV" "$WORK/$NAME.ydr.xml" "$RES/stream/$NAME.ydr"
"$CWCONV" "$WORK/$NAME.ytyp.xml" "$RES/stream/$NAME.ytyp"
"$CWCONV" --check "$RES/stream/$NAME.ydr"
"$CWCONV" --check "$RES/stream/$NAME.ytyp"

"$PY" -u "$HERE/render_preview.py" "$GLB" "$OUT/preview.png" 2>&1 | grep "^preview:" || echo "preview render failed (not fatal)"

sed "s/__NAME__/$NAME/g" "$HERE/template/fxmanifest.lua" > "$RES/fxmanifest.lua"
sed "s/__NAME__/$NAME/g" "$HERE/template/client.lua" > "$RES/client.lua"
(cd "$OUT" && rm -f "$NAME.zip" && zip -qr "$NAME.zip" "$NAME")
grep "^size:" "$WORK/blender.log" || true
echo "done: $OUT/$NAME.zip"
