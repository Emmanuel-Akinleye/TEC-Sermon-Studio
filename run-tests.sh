#!/usr/bin/env bash
# Everything that can be checked without opening After Effects.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
PY="${VERSECASTER_PYTHON:-python3}"
JSC=/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc
fail=0

echo "== spoken citation detection =="
"$PY" tests/test_detection.py || fail=1

echo
echo "== cue timing rules =="
"$PY" tests/test_timing.py || fail=1

echo
echo "== real sermons: things that went wrong once =="
"$PY" tests/test_known.py || fail=1

echo
echo "== confessions and catechisms =="
"$PY" tests/test_documents.py || fail=1

echo
echo "== subtitles and transcript =="
"$PY" tests/test_captions.py || fail=1

echo
echo "== presentation slides: converting a deck =="
"$PY" tests/test_deck.py || fail=1

echo
echo "== audio cleanup: measurement, chain and the sound it makes =="
"$PY" tests/test_cleanup.py || fail=1

echo
echo "== After Effects time mapping =="
if [ -x "$JSC" ]; then
  "$PY" - <<'PYEOF' || fail=1
import subprocess, pathlib, tempfile, sys
jsx = pathlib.Path('cep/jsx/SermonStudio.jsx').read_text()
test = pathlib.Path('tests/map_times.js').read_text()
head, tail = test.split('var MEDIA', 1)
harness = "var __vcPanelHost=true, app={project:{}};\n" + head + "\n" + jsx + "\nvar MEDIA" + tail
p = pathlib.Path(tempfile.gettempdir()) / 'vc_suite.js'
p.write_text(harness)
r = subprocess.run(['/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc', str(p)],
                   capture_output=True, text=True)
print(r.stdout.strip() or r.stderr.strip()[:400])
sys.exit(0 if '0 failed' in r.stdout else 1)
PYEOF
else
  echo "  (jsc unavailable — skipped)"
fi

echo
echo "== rebuilding overwrites instead of adding comps =="
"$PY" - <<'PYEOF' || fail=1
import subprocess, pathlib, tempfile, sys
parts = [pathlib.Path(f).read_text() for f in
         ("tests/rebuild.mock.js", "cep/jsx/SermonStudio.jsx", "tests/rebuild.test.js")]
p = pathlib.Path(tempfile.gettempdir()) / "vc_rebuild.js"
p.write_text("\n".join(parts))
r = subprocess.run(["/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc", str(p)],
                   capture_output=True, text=True)
out = r.stdout.strip() or r.stderr.strip()[:400]
print(out)
sys.exit(0 if " 0 failed" in out else 1)
PYEOF

echo
echo "== layers land where they are put =="
"$PY" - <<'PYEOF' || fail=1
import subprocess, pathlib, tempfile, sys
parts = ["var __vcPanelHost = true, app = {project:{}};",
         "function CompItem(){} function AVLayer(){} function FolderItem(){}",
         pathlib.Path("cep/jsx/SermonStudio.jsx").read_text(),
         pathlib.Path("tests/trim.test.js").read_text(),
         pathlib.Path("tests/fadeout.test.js").read_text()]
p = pathlib.Path(tempfile.gettempdir()) / "vc_trim.js"
p.write_text("\n".join(parts))
r = subprocess.run(["/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc", str(p)],
                   capture_output=True, text=True)
out = r.stdout.strip() or r.stderr.strip()[:400]
print(out)
sys.exit(0 if out.count(" 0 failed") == 2 else 1)
PYEOF

echo
echo "== real sermons: what the slide layout would show =="
"$PY" tests/test_plan.py || fail=1

echo
echo "== real sermons: the slide build, frame by frame =="
"$PY" tests/test_build.py || fail=1

echo
echo "== the panel: restore, settings, review tools =="
"$PY" - <<'PYEOF' || fail=1
import subprocess, pathlib, tempfile, sys
parts = [pathlib.Path(f).read_text() for f in ("tests/panel.test.js", "cep/js/main.js", "tests/panel.checks.js")]
p = pathlib.Path(tempfile.gettempdir()) / "vc_panel.js"
p.write_text("\n".join(parts))
r = subprocess.run(["/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc", str(p)],
                   capture_output=True, text=True)
out = (r.stdout.strip() + "\n" + r.stderr.strip()[:400]).strip()
print(out)
sys.exit(0 if out.endswith(" 0 failed") else 1)
PYEOF

echo
echo "== scripts parse =="
for f in cep/jsx/SermonStudio.jsx cep/js/main.js cep/js/CSInterface.js; do
  "$PY" - "$f" <<'PYEOF' || fail=1
import subprocess, pathlib, sys, tempfile
src = pathlib.Path(sys.argv[1]).read_text()
mocks = ("var window={navigator:{platform:'Mac'},addEventListener:function(){},setInterval:function(){}},"
         "document={},CSInterface=function(){},SystemPath={},nodeRequire=function(){},Date={now:function(){}};")
p = pathlib.Path(tempfile.gettempdir()) / 'vc_parse.js'
p.write_text(mocks + "\nfunction c(){ " + src + " }\nif(typeof c==='function') print('ok');")
r = subprocess.run(['/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc', str(p)],
                   capture_output=True, text=True)
ok = 'ok' in r.stdout
print(f"  {'ok  ' if ok else 'FAIL'} {sys.argv[1]}")
sys.exit(0 if ok else 1)
PYEOF
done

echo
echo "== config files are valid =="
for f in config/*.json; do
  "$PY" -c "import json,sys; json.load(open(sys.argv[1])); print('  ok   ' + sys.argv[1])" "$f" || fail=1
done

echo
[ "$fail" -eq 0 ] && echo "ALL PASSED" || echo "SOMETHING FAILED"
exit $fail
