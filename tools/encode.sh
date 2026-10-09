#!/usr/bin/env bash
# Hardware-encode After Effects masters for delivery.
#
# After Effects encodes H.264 in software, which measured about 8x slower than
# the Apple silicon media engine at 4K. Rendering a ProRes master from AE's own
# queue and encoding here is far quicker — but a 4K ProRes master runs to
# hundreds of gigabytes for a sermon.
#
# So this also takes several masters at once. Render the comp in chunks, point
# this at them, and it encodes each, joins the results losslessly, and (with
# --clean) deletes each chunk as soon as it is encoded. Peak disk use is one
# chunk rather than the whole sermon.
#
#   encode.sh master.mov                          one master, 1080p
#   encode.sh part1.mov part2.mov part3.mov --4k  chunks, joined, 4K
#   encode.sh part*.mov --4k --clean              delete chunks as it goes
#   encode.sh master.mov --4k --hevc              smaller file, same quality
#   encode.sh master.mov --4k --bitrate 20M       pick your own
set -euo pipefail

FF="$(command -v ffmpeg || echo /opt/homebrew/bin/ffmpeg)"
[ -x "$FF" ] || { echo "ffmpeg not found (brew install ffmpeg)"; exit 1; }

# Defaults sized to land near 8-10 GB for an 85-minute sermon, which is what
# this workflow has always produced. Override with --bitrate.
SCALE="scale=1920:-2"; BITRATE="8M"; CODEC="h264_videotoolbox"; TAG="avc1"
SUFFIX="1080p"; CLEAN=0; USER_BITRATE=""; INPUTS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --4k)      SCALE="scale=3840:-2"; BITRATE="16M"; SUFFIX="2160p" ;;
    --hevc)    CODEC="hevc_videotoolbox"; TAG="hvc1"
               [ "$SUFFIX" = "2160p" ] && BITRATE="12M" || BITRATE="6M" ;;
    --bitrate) shift; USER_BITRATE="$1" ;;
    --clean)   CLEAN=1 ;;
    -*)        echo "unknown option: $1"; exit 1 ;;
    *)         INPUTS+=("$1") ;;
  esac
  shift
done
[ -n "$USER_BITRATE" ] && BITRATE="$USER_BITRATE"
[ ${#INPUTS[@]} -gt 0 ] || { echo "usage: encode.sh <master.mov> [more.mov ...] [--4k] [--hevc] [--clean]"; exit 1; }

FIRST="${INPUTS[0]}"
OUT="${FIRST%.*} ${SUFFIX}.mp4"
[ ${#INPUTS[@]} -gt 1 ] && OUT="$(dirname "$FIRST")/$(basename "${FIRST%.*}" | sed 's/[ _-]*[0-9]*$//') ${SUFFIX}.mp4"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
LIST="$WORK/list.txt"; : > "$LIST"

n=0
for src in "${INPUTS[@]}"; do
  [ -f "$src" ] || { echo "missing: $src"; exit 1; }
  n=$((n+1))
  part="$WORK/part_$(printf '%03d' "$n").mp4"
  echo "[$n/${#INPUTS[@]}] encoding $(basename "$src")  ($SUFFIX, $CODEC, $BITRATE)"
  "$FF" -nostdin -y -v error -stats -i "$src" \
    -vf "$SCALE" -c:v "$CODEC" -b:v "$BITRATE" -tag:v "$TAG" \
    -c:a aac -b:a 256k "$part"
  printf "file '%s'\n" "$part" >> "$LIST"
  if [ "$CLEAN" -eq 1 ]; then
    echo "      removing $(basename "$src")"
    rm -f "$src"
  fi
done

if [ "$n" -eq 1 ]; then
  mv "$WORK/part_001.mp4" "$OUT"
else
  echo "joining $n parts"
  "$FF" -nostdin -y -v error -f concat -safe 0 -i "$LIST" -c copy -movflags +faststart "$OUT"
fi

echo "done: $OUT"
ls -la "$OUT" | awk '{printf "      %.1f GB\n", $5/1073741824}'
