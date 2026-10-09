#!/usr/bin/env bash
# How long After Effects takes per frame, for a stretch of a comp.
#
#   tools/bench.sh "/path/project.aep" "20:09:2026" 30000 60
#
# Renders `frames` frames starting at `start`, to a scratch folder that is
# deleted afterwards, and prints milliseconds per frame. Run it with nothing
# else rendering, or the numbers mean nothing.
set -uo pipefail
AERENDER="/Applications/Adobe After Effects 2026/aerender"
PROJECT="$1"; COMP="$2"; START="${3:-0}"; FRAMES="${4:-60}"
END=$((START + FRAMES - 1))
OUT=$(mktemp -d /tmp/ss-bench-XXXX)
trap 'rm -rf "$OUT"' EXIT

echo "rendering $FRAMES frames of \"$COMP\" from frame $START"
BEGAN=$(date +%s.%N)
"$AERENDER" -project "$PROJECT" -comp "$COMP" -s "$START" -e "$END" \
    -RStemplate "Best Settings" -OMtemplate "Lossless" \
    -output "$OUT/[#####].tif" 2>&1 | tail -3
ENDED=$(date +%s.%N)

python3 - "$BEGAN" "$ENDED" "$FRAMES" <<'PY'
import sys
began, ended, frames = float(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3])
took = ended - began
per = took / frames
print(f"\n  {took:.1f}s for {frames} frames = {per*1000:.0f} ms/frame = {1/per:.1f} fps")
print(f"  a 95-minute sermon at this rate: {per * 95 * 60 * 29.97 / 3600:.1f} hours")
PY
