#!/bin/bash
# Render a build with Blender (clay views, exploded, x-ray, close-ups), then make the contact
# sheet. Blender runs here, or over ssh on RENDER_HOST (user@host) in RENDER_DIR there.
# Usage: ./render.sh out/<design>-NN [--samples N] [--size PX] [--closeups]
# --closeups renders only the close-ups (closeups.png).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
build="$(cd "$1" && pwd)"
shift
name="$(basename "$build")"
if [ -z "${RENDER_HOST:-}" ]; then
  blender -b --factory-startup -t 0 --python "$here/lib/blender_render.py" -- "$build" "$@" \
    | grep -E '^render:|Error|error'
else
  dir="${RENDER_DIR:-c100-case-render}"
  ssh_opts=(-o BatchMode=yes -o ConnectTimeout=10)
  ssh "${ssh_opts[@]}" "$RENDER_HOST" "mkdir -p $dir/out/$name"
  rsync -a -e "ssh ${ssh_opts[*]}" "$here/lib/blender_render.py" "$RENDER_HOST:$dir/"
  rsync -a -e "ssh ${ssh_opts[*]}" "$build/assembly.glb" "$build/render.json" \
    "$RENDER_HOST:$dir/out/$name/"
  ssh "${ssh_opts[@]}" "$RENDER_HOST" \
    "cd $dir && flock render.lock nice -n 10 blender -b --factory-startup -t 0 --python blender_render.py -- out/$name $(printf '%q ' "$@")" \
    | grep -E '^render:|Error|error'
  rsync -a -e "ssh ${ssh_opts[*]}" "$RENDER_HOST:$dir/out/$name/render/" "$build/render/"
fi
cd "$here"
uv run python -m lib.sheet "$build"
