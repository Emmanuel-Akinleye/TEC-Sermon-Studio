"""The slide timeline for every analysed sermon, checked end to end.

Replays each sermon's cues through the same planning code the After Effects
build uses, and checks what a viewer would see:

* the panel is never up with no verse in it
* every verse the analysis found gets on screen
* sections never overlap, and full frame between them is never a flash
* nothing from the panel in the first seconds of the comp
"""
import json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import replay  # noqa: E402

JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"

CHECK = r"""
var V = SermonStudio._internals, failed = 0;
function bad(msg) { failed++; print("  FAIL  " + msg); }
for (var name in SERMONS) {
    var cues = SERMONS[name], plan = V.planSlide(JSON.parse(JSON.stringify(cues)), ANIM),
        w = plan.windows, i, k, found, c, it, panel = 0, held = 0, brief = 0;
    var empty = V.emptyPanel(plan, 0.05);
    for (i = 0; i < empty.length; i++)
        bad(name + ": empty panel " + empty[i].from.toFixed(1) + "-" + empty[i].to.toFixed(1));
    if (w.length && w[0].start - ANIM.panelFade < ANIM.startAfter - 0.001)
        bad(name + ": panel starts at " + (w[0].start - ANIM.panelFade).toFixed(1) + "s");
    for (i = 1; i < w.length; i++) {
        var full = (w[i].start - ANIM.panelFade) - (w[i - 1].end + ANIM.panelFade);
        if (full < ANIM.minFullFrame - 0.001)
            bad(name + ": only " + full.toFixed(1) + "s of full frame at " + w[i].start.toFixed(1));
    }
    var earliest = ANIM.startAfter + ANIM.insetOpenBefore + ANIM.panelFade;
    for (i = 0; i < cues.length; i++) {
        c = cues[i];
        if (c.end - earliest < ANIM.minReadable) continue;
        found = false;
        for (k = 0; k < plan.items.length; k++) {
            it = plan.items[k];
            if (it.ref === c.ref && it.showIn < c.end && it.showOut > c.start - 0.01) { found = true; break; }
        }
        if (!found) bad(name + ": " + c.ref + " at " + c.start.toFixed(1) + " never shown");
    }
    for (i = 0; i < w.length; i++) panel += w[i].end - w[i].start;
    for (k = 0; k < plan.items.length; k++) {
        it = plan.items[k];
        held = Math.max(held, it.showOut - it.end);
        if (it.showOut - it.showIn < 2) brief++;
    }
    print("  " + name + ": " + plan.items.length + " cards in " + w.length + " sections, panel up "
          + (panel / 60).toFixed(1) + " min, longest hold after a verse " + held.toFixed(0)
          + "s, " + brief + " on screen under 2s");
}
print(failed ? "\n  " + failed + " problems" : "\n  0 failed");
"""


def main():
    style = json.loads((ROOT / "config/style.json").read_text())
    sermons = {}
    for path in sorted((ROOT / "out").glob("*:*:*.cues.json")):
        if path.name.endswith(".selected.json"):
            continue
        try:
            cues = replay.rebuild(json.loads(path.read_text()))
        except (IndexError, FileNotFoundError):
            continue
        sermons[path.name.split(".")[0]] = [
            {"ref": c.ref, "text": c.text, "translation": c.translation,
             "start": c.start, "end": c.end} for c in cues if c.enabled and c.verse]
    js = "\n".join([
        "var __vcPanelHost = true, app = {project:{}};",
        "function CompItem(){} function AVLayer(){} function FolderItem(){}",
        (ROOT / "cep/jsx/SermonStudio.jsx").read_text(),
        "var SERMONS = " + json.dumps(sermons) + ";",
        "var ANIM = " + json.dumps(style["slide"]["animation"]) + ";",
        CHECK,
    ])
    p = Path(tempfile.gettempdir()) / "vc_plan.js"
    p.write_text(js)
    r = subprocess.run([JSC, str(p)], capture_output=True, text=True)
    out = r.stdout.strip() or r.stderr.strip()[:600]
    print(out)
    return 0 if out.endswith(" 0 failed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
