#!/usr/bin/env bash
# SermonStudio installer. Safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_DIR="$HOME/Library/Application Support/Adobe/CEP/extensions"
EXT_NAME="com.tec.sermonstudio"
PY="${VERSECASTER_PYTHON:-python3}"

say()  { printf '\033[1m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[33m  ! %s\033[0m\n' "$1"; }

say "Checking prerequisites"
command -v "$PY" >/dev/null || { echo "python3 not found"; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' \
  || { echo "Python 3.10+ required (found $($PY -V))"; exit 1; }
command -v ffmpeg >/dev/null || warn "ffmpeg not found — install with: brew install ffmpeg"
echo "    python: $($PY -V)  at $(command -v "$PY")"

say "Installing Python packages"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r "$ROOT/requirements.txt"

say "Downloading offline Bible corpus (BSB)"
"$PY" - <<PYEOF
import sys
sys.path.insert(0, "$ROOT/engine")
import bible
if bible.have_corpus("BSB"):
    print("    already present")
else:
    print(f"    {bible.download_corpus('BSB'):,} verses")
PYEOF

say "Enabling unsigned CEP extensions"
for v in 9 10 11 12 13; do
  defaults write "com.adobe.CSXS.$v" PlayerDebugMode 1 2>/dev/null || true
done
killall cfprefsd 2>/dev/null || true

say "Installing the After Effects panel"
mkdir -p "$EXT_DIR"
echo "$ROOT" > "$ROOT/cep/engine-path.txt"
if [ -L "$EXT_DIR/$EXT_NAME" ] || [ -e "$EXT_DIR/$EXT_NAME" ]; then
  rm -rf "$EXT_DIR/$EXT_NAME"
fi
ln -s "$ROOT/cep" "$EXT_DIR/$EXT_NAME"
echo "    linked $EXT_DIR/$EXT_NAME"

say "Installing the fallback script (File > Scripts)"
for AE in /Applications/Adobe\ After\ Effects\ */Scripts; do
  if [ -d "$AE" ]; then
    ln -sf "$ROOT/cep/jsx/SermonStudio.jsx" "$AE/SermonStudio.jsx" 2>/dev/null \
      && echo "    linked into $AE" \
      || warn "could not write to $AE (needs admin) — copy cep/jsx/SermonStudio.jsx there manually"
  fi
done

if [ ! -f "$ROOT/config/keys.json" ]; then
  cp "$ROOT/config/keys.example.json" "$ROOT/config/keys.json"
  warn "Add your API key to config/keys.json for ESV/NIV/NLT text."
fi

cat <<'DONE'

Done.

  1. Restart After Effects.
  2. Window > Extensions > TEC Sermon Studio.
  3. Open your sermon comp, click Analyze, review, click Build.

First analysis downloads the Whisper model (~1.5 GB) once.
DONE
