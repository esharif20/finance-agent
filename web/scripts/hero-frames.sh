#!/usr/bin/env bash
# Re-render the phone hero's card sequence from the film (dev machine only; the frames are committed).
# 36 frames at 30 fps, 440 px wide WebP. Usage: web/scripts/hero-frames.sh
set -euo pipefail
cd "$(dirname "$0")/../public/hero"
tmp=$(mktemp -d)
ffmpeg -v error -y -i cards.mp4 -vf "fps=30,scale=440:-2:flags=lanczos" "$tmp/p_%02d.png"
mkdir -p frames
for f in "$tmp"/p_*.png; do
  n=$(basename "$f" .png)
  cwebp -quiet -q 70 -m 6 "$f" -o "frames/${n#p_}.webp"
done
rm -rf "$tmp"
ls frames | wc -l
