"""Build every analysed sermon's slide layout through the real SermonStudio.jsx
(against tests/ae.mock.js) and watch the result frame by frame:

* the panel is never up without verse text in it
* text never shows without the panel behind it
* backdrop, camera inset and logo fade exactly with the panel
* the camera is never missing: full frame wherever the panel is not fully up
* nothing from the panel in the first seconds of the comp
* every verse the analysis turned on is built
"""
import json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
import replay  # noqa: E402

JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
SHIFT = 5.8   # the recording starts this far into the sermon comp, as on 16/09

CHECK = r"""
var failed = 0;
function bad(msg) { failed++; if (failed < 40) print("  FAIL  " + msg); }
for (var name in PAYLOADS) {
    items.length = 0;
    var payload = PAYLOADS[name], path = "/tmp/" + name + ".json";
    FILES[path] = JSON.stringify(payload);
    FILES[payload.style.slide.background.image] = "";
    FILES[payload.style.slide.logo.image] = "";
    for (var fi = 0; fi < payload.cues.length; fi++)
        if (payload.cues[fi].image) FILES[payload.cues[fi].image] = "";
    var media = footage(payload.media, 3840, 2160, payload.duration);
    var sermon = comp(name, 3840, 2160, payload.duration + 30);
    var cam = sermon.layers.add(media);
    cam.startTime = SHIFT; cam._in = SHIFT; cam._out = SHIFT + payload.duration;
    app.project.activeItem = sermon; $.global = {};

    var start = JSON.parse(SermonStudio.buildStart(path, JSON.stringify({ layout: "slide" })));
    if (!start.ok) { bad(name + ": " + start.error); continue; }
    var step, guard = 0;
    do { step = JSON.parse(SermonStudio.buildStep(3)); } while (step.ok && step.done < step.total && guard++ < 1000);
    var res = JSON.parse(SermonStudio.buildFinish());
    if (!res.ok) { bad(name + ": " + res.error); continue; }
    if (res.misplaced) bad(name + ": " + res.misplaced + " layers not where intended");
    if (res.emptyPanel) bad(name + ": build reports " + res.emptyPanel + " empty-panel stretches");

    var out = items.filter(function (x) { return x instanceof CompItem && /layout:slide/.test(x.comment); })[0];
    var L = out._layers, panels = [], texts = [], furniture = [], full = [], pictures = [], k;
    for (k = 0; k < L.length; k++) {
        if (/^(Panel|Slide) \d+$/.test(L[k].name)) panels.push(L[k]);
        /* A presentation slide fills the panel with its own picture; it is
           content exactly as verse text is, and must count as such or every
           slide would look like an empty panel. */
        else if (/^VC \d+ Slide \d+/.test(L[k].name) && L[k].source) pictures.push(L[k]);
        else if (/ — text( \d+\/\d+)?$/.test(L[k].name)) texts.push(L[k]);
        else if (/^(Backdrop|Camera frame) \d+$|\(inset \d+\)$|^Logo$/.test(L[k].name)) furniture.push(L[k]);
        else if (/\(full frame \d+\)$/.test(L[k].name)) full.push(L[k]);
    }
    function maxAt(list, t) { var m = 0; for (var i = 0; i < list.length; i++) m = Math.max(m, list[i].opacityAt(t)); return m; }
    function sectionOf(t) {                       // the furniture that belongs to this moment
        for (var i = 0; i < panels.length; i++) if (t >= panels[i]._in && t < panels[i]._out) return panels[i].name.slice(6);
        return null;
    }
    var emptyRun = 0, worstEmpty = 0, at = 0, t, p, x, sec, f, fu, early = 0, noCam = 0, lone = 0, apart = 0,
        loud = 0, silent = 0, audible, a2;
    function hears(t) {                           // copies of the sermon's sound playing at t
        var n = 0;
        for (var i = 0; i < L.length; i++)
            if (L[i].source === sermon && L[i].audioEnabled !== false && t >= L[i]._in && t < L[i]._out) n++;
        return n;
    }
    for (t = 0; t < out.duration; t += 0.05) {
        p = maxAt(panels, t); x = Math.max(maxAt(texts, t), maxAt(pictures, t));
        if (p >= 50 && x < 50) { emptyRun += 0.05; if (emptyRun > worstEmpty) { worstEmpty = emptyRun; at = t; } }
        else emptyRun = 0;
        if (x > p + 5) lone++;
        if (p > 0 && t < payload.style.slide.animation.startAfter) early++;
        if (p < 99.5 && maxAt(full, t) < 100) noCam++;
        audible = hears(t);
        if (audible > 1) loud++;
        if (audible < 1) silent++;
        sec = sectionOf(t);
        for (f = 0; f < furniture.length; f++) {
            fu = furniture[f];
            if (fu.name !== "Logo" && sec && fu.name.indexOf(sec) < 0 && !/inset/.test(fu.name)) continue;
            if (fu.name === "Logo" || (sec && (fu.name.indexOf(" " + sec) >= 0))) {
                if (Math.abs(fu.opacityAt(t) - p) > 1) apart++;
            }
        }
    }
    if (worstEmpty > 0.8) bad(name + ": panel with no verse for " + worstEmpty.toFixed(1) + "s, ending at " + at.toFixed(1));
    if (lone) bad(name + ": verse text without the panel behind it (" + (lone * 0.05).toFixed(1) + "s)");
    if (early) bad(name + ": panel visible in the first seconds");
    if (noCam) bad(name + ": " + (noCam * 0.05).toFixed(1) + "s with neither full frame nor the panel fully up");
    if (loud) bad(name + ": sermon audio doubled for " + (loud * 0.05).toFixed(1) + "s");
    if (silent) bad(name + ": sermon audio missing for " + (silent * 0.05).toFixed(1) + "s");
    if (apart) bad(name + ": backdrop/camera/logo out of step with the panel (" + (apart * 0.05).toFixed(1) + "s)");

    var built = {}, missing = [];
    for (k = 0; k < texts.length; k++) built[texts[k].name.replace(/^VC \d+ /, "").replace(/ — text( \d+\/\d+)?$/, "")] = true;
    for (k = 0; k < pictures.length; k++) built[pictures[k].name.replace(/^VC \d+ /, "")] = true;
    var paged = texts.filter(function (x) { return / \d+\/\d+$/.test(x.name); }).length;
    var design = payload.style.slide.verse.fontSize * 2, smallest = 1e9, smallName = "";
    for (k = 0; k < texts.length; k++) {
        var sz = texts[k].props.property("ADBE Text Properties").property("ADBE Text Document").value.fontSize;
        if (sz < smallest) { smallest = sz; smallName = texts[k].name; }
    }
    if (texts.length && smallest < design * 0.72 - 0.5) bad(name + ": " + smallName + " shrunk to " + (100 * smallest / design).toFixed(0) + "% size");
    if (VERBOSE) for (k = 0; k < texts.length; k++) if (/ \d+\/\d+$/.test(texts[k].name)) print("      paged: " + texts[k].name);
    if (name === "long verse") {
        var pagesOf = texts.filter(function (x) { return /Esther 8:9 — text \d+\/\d+$/.test(x.name); });
        if (pagesOf.length < 2) bad("a 90-word verse was not split into pages");
        pagesOf.sort(function (a, b) { return a._in - b._in; });
        for (k = 1; k < pagesOf.length; k++)
            if (pagesOf[k]._in < pagesOf[k - 1]._in + 1.4) bad("pages of a long verse change too fast");
        if (pagesOf.length && (pagesOf[0]._in > 106.1 + SHIFT || pagesOf[pagesOf.length - 1]._out < 145.9 + SHIFT))
            bad("pages do not cover the reading of the verse");
    }
    // A verse only belongs on screen if enough of it falls after the wait the
    // operator set before the panel may appear at all.
    var anim = payload.style.slide.animation,
        earliest = anim.startAfter + anim.insetOpenBefore + anim.panelFade;
    for (k = 0; k < payload.cues.length; k++) {
        var c = payload.cues[k];
        if (c.enabled && c.end + SHIFT - earliest >= anim.minReadable && !built[c.ref])
            missing.push(c.ref + "@" + c.start.toFixed(0));
    }
    if (missing.length) bad(name + ": not built: " + missing.join(", "));
    if (pictures.length) {
        var box = null;
        for (k = 0; k < panels.length; k++) if (panels[k].name === "Panel 01") box = panels[k];
        for (k = 0; k < pictures.length; k++) {
            var pic = pictures[k], tg = pic.property("ADBE Transform Group"),
                sc = tg.property("ADBE Scale").value, pos = tg.property("ADBE Position").value,
                op = tg.property("ADBE Opacity"),
                pw = pic.source.width * sc[0] / 100, phh = pic.source.height * sc[1] / 100;
            if ((op.numKeys || 0) < 2) bad(name + ": " + pic.name + " does not fade");
            var ps = Math.min(out.width / payload.style.reference[0], out.height / payload.style.reference[1]),
                ox = (out.width - payload.style.reference[0] * ps) / 2,
                oy = (out.height - payload.style.reference[1] * ps) / 2,
                pbox = payload.style.slide.panel,
                boxL = ox + pbox.x * ps, boxT = oy + pbox.y * ps,
                boxW = pbox.width * ps, boxH = pbox.height * ps;
            if (pw > boxW + 1 || phh > boxH + 1)
                bad(name + ": " + pic.name + " is bigger than the panel (" + Math.round(pw) + "x"
                    + Math.round(phh) + " in " + Math.round(boxW) + "x" + Math.round(boxH) + ")");
            /* It should also be using the room it has: a slide fitted into a
               panel must come within a hair of one of its sides. The small
               inset that keeps it off the panel outline is all that is
               allowed for. */
            if (pw < boxW * 0.96 && phh < boxH * 0.96)
                bad(name + ": " + pic.name + " does not fill the panel in either direction ("
                    + Math.round(pw) + "x" + Math.round(phh) + " in " + Math.round(boxW) + "x"
                    + Math.round(boxH) + ")");
            if (pos[0] < boxL || pos[0] > boxL + boxW)
                bad(name + ": " + pic.name + " is not inside the panel");
            if (Math.abs(sc[0] - sc[1]) > 0.01)
                bad(name + ": " + pic.name + " is stretched out of shape");
        }
        var order = pictures.slice().sort(function (a, b) { return a._in - b._in; });
        for (k = 1; k < order.length; k++)
            if (order[k]._in < order[k - 1]._in) bad(name + ": slides out of order");
    }
    out.time = 120; app.project.activeItem = out;          // looking at the built comp
    var ph = JSON.parse(SermonStudio.playhead(payload.media));
    if (!ph.ok || Math.abs(ph.source - (120 - SHIFT)) > 1e-6) bad(name + ": playhead from the built comp read wrong");
    print("  " + name + ": " + texts.length + " text layers (" + paged + " as pages), "
          + (pictures.length ? pictures.length + " slide pictures, " : "") + panels.length
          + " sections, "
          + (texts.length ? "smallest text " + (100 * smallest / design).toFixed(0) + "% of design size, " : "")
          + "longest wait for text "
          + worstEmpty.toFixed(2) + "s");
}
// A logo that has been moved is reported, not silently left out.
(function () {
    var name = Object.keys(PAYLOADS)[0], payload = JSON.parse(JSON.stringify(PAYLOADS[name]));
    items.length = 0;
    payload.style.slide.logo.image = "/Users/nobody/moved-logo.png";
    FILES["/tmp/missing.json"] = JSON.stringify(payload);
    FILES[payload.style.slide.background.image] = "";
    var media = footage(payload.media, 3840, 2160, payload.duration), sermon = comp(name, 3840, 2160, payload.duration + 30);
    sermon.layers.add(media); app.project.activeItem = sermon; $.global = {};
    SermonStudio.buildStart("/tmp/missing.json", JSON.stringify({ layout: "slide" }));
    while (JSON.parse(SermonStudio.buildStep(20)).done < $.global.__vcJob.items.length) {}
    var res = JSON.parse(SermonStudio.buildFinish());
    if (!res.warnings || !/Logo image not found/.test(res.warnings.join(" "))) bad("a missing logo was not reported");
    else print("  a moved logo image is reported after the build");
})();

// The lower third builds every card, one at a time, never two at once.
(function () {
    var name = Object.keys(PAYLOADS)[0], payload = PAYLOADS[name];
    items.length = 0;
    FILES["/tmp/lt.json"] = JSON.stringify(payload);
    var media = footage(payload.media, 3840, 2160, payload.duration), sermon = comp(name, 3840, 2160, payload.duration + 30);
    var cam = sermon.layers.add(media); cam.startTime = SHIFT; cam._in = SHIFT; cam._out = SHIFT + payload.duration;
    app.project.activeItem = sermon; $.global = {};
    var st = JSON.parse(SermonStudio.buildStart("/tmp/lt.json", JSON.stringify({ layout: "lower-third" })));
    if (!st.ok) { bad("lower third: " + st.error); return; }
    while (JSON.parse(SermonStudio.buildStep(20)).done < st.total) {}
    var res = JSON.parse(SermonStudio.buildFinish());
    if (!res.ok) { bad("lower third: " + res.error); return; }
    var out = items.filter(function (x) { return x instanceof CompItem && /layout:captions/.test(x.comment); })[0];
    var texts = out._layers.filter(function (L) { return / — text$/.test(L.name); });
    var on = payload.cues.filter(function (c) { return c.enabled; }).length;
    if (texts.length !== on) bad("lower third built " + texts.length + " of " + on + " cards");
    var clash = 0;
    for (var t = 0; t < out.duration; t += 0.1) {
        var n = 0;
        for (var i = 0; i < texts.length; i++) if (texts[i].opacityAt(t) > 60) n++;
        if (n > 1) clash++;
    }
    if (clash) bad("lower third: two cards fully up at once for " + (clash * 0.1).toFixed(1) + "s");
    print("  lower third: " + texts.length + " cards built, none overlapping");
})();
// The same build with the still parts drawn into one picture: fewer layers,
// same result on screen.
(function () {
    var name = Object.keys(PAYLOADS)[0], payload = JSON.parse(JSON.stringify(PAYLOADS[name]));
    items.length = 0;
    payload.style.slide.furnitureImage = FURNITURE;
    FILES["/tmp/flat.json"] = JSON.stringify(payload);
    FILES[FURNITURE] = "";
    var media = footage(payload.media, 3840, 2160, payload.duration),
        sermon = comp(name, 3840, 2160, payload.duration + 30);
    var cam = sermon.layers.add(media); cam.startTime = SHIFT; cam._in = SHIFT; cam._out = SHIFT + payload.duration;
    app.project.activeItem = sermon; $.global = {};
    var st = JSON.parse(VerseCaster.buildStart("/tmp/flat.json", JSON.stringify({ layout: "slide" })));
    if (!st.ok) { bad("flat: " + st.error); return; }
    while (JSON.parse(VerseCaster.buildStep(20)).done < st.total) {}
    var res = JSON.parse(VerseCaster.buildFinish());
    if (!res.ok) { bad("flat: " + res.error); return; }
    var out = items.filter(function (x) { return x instanceof CompItem && /layout:slide/.test(x.comment); })[0];
    var L = out._layers, slides = [], insets = [], texts = [], old = [];
    for (var k = 0; k < L.length; k++) {
        if (/^Slide \d+$/.test(L[k].name)) slides.push(L[k]);
        else if (/\(inset \d+\)$/.test(L[k].name)) insets.push(L[k]);
        else if (/ — text( \d+\/\d+)?$/.test(L[k].name)) texts.push(L[k]);
        else if (/^(Backdrop|Camera frame) \d+$|^Panel \d+$|^Logo$/.test(L[k].name)) old.push(L[k].name);
    }
    if (old.length) bad("flat build still makes separate " + old.slice(0, 3).join(", "));
    if (!slides.length || slides.length !== insets.length) bad("one flat layer per section expected");
    if (res.sections !== slides.length) bad("flat: " + slides.length + " layers for " + res.sections + " sections");
    // Still never a panel without a verse, and the picture always covered.
    var run = 0, empty = 0, uncovered = 0, EMPTY_AT = 0;
    for (var t = 0; t < out.duration; t += 0.1) {
        var p = 0, x = 0, full = 0;
        for (k = 0; k < slides.length; k++) p = Math.max(p, slides[k].opacityAt(t));
        for (k = 0; k < texts.length; k++) x = Math.max(x, texts[k].opacityAt(t));
        for (k = 0; k < L.length; k++) if (/\(full frame \d+\)$/.test(L[k].name)) full = Math.max(full, L[k].opacityAt(t));
        if (p >= 50 && x < 50) { run += 0.1; if (run > empty) { empty = run; EMPTY_AT = t; } } else run = 0;
        if (p < 99.5 && full < 100) uncovered += 0.1;
    }
    if (empty > 0.8) bad("flat: panel with no verse for " + empty.toFixed(1) + "s in a row, around " + EMPTY_AT.toFixed(1) + "s");
    if (uncovered > 0.2) bad("flat: " + uncovered.toFixed(1) + "s with neither full frame nor panel");
    // The camera and its surround come and go together.
    var apart = 0;
    for (k = 0; k < slides.length; k++) {
        for (t = 0; t < out.duration; t += 0.25) {
            if (Math.abs(slides[k].opacityAt(t) - insets[k].opacityAt(t)) > 1) apart++;
        }
    }
    if (apart) bad("flat: camera and surround out of step");
    print("  flat build: " + slides.length + " section layers instead of " + (slides.length * 4 + 1));
})();

// Cuts in the edit: the panel leaves with the shot, and a lower third is not a cut.
(function () {
    items.length = 0;
    var payload = JSON.parse(JSON.stringify(PAYLOADS[Object.keys(PAYLOADS)[0]]));
    payload.style.slide.furnitureImage = FURNITURE;
    payload.style.slide.animation.startAfter = 0;
    // A reader finishing at 300, a fade to the preacher, and verses either side.
    payload.cues = [
        { ref: "Isaiah 46:12", text: "Listen to me, you stubborn of heart", translation: "ESV",
          start: 270 - SHIFT, end: 292 - SHIFT, enabled: true },
        { ref: "Isaiah 46:13", text: "I bring near my righteousness", translation: "ESV",
          start: 310 - SHIFT, end: 330 - SHIFT, enabled: true }];
    FILES["/tmp/cuts.json"] = JSON.stringify(payload);
    FILES[FURNITURE] = "";
    var media = footage(payload.media, 3840, 2160, 4000),
        lower = footage("PSAM LOWER THIRD.mov", 3840, 1200, 4000),
        fade = comp("Fade to White", 3840, 2160, 4);
    var sermon = comp("cuts test", 3840, 2160, 1000);
    var a = sermon.layers.add(media); a.startTime = SHIFT; a._in = SHIFT; a._out = 300;
    var b = sermon.layers.add(media); b.startTime = SHIFT; b._in = 300; b._out = 900;
    var f = sermon.layers.add(fade); f._in = 298; f._out = 302;
    var l = sermon.layers.add(lower); l._in = 100; l._out = 150;
    app.project.activeItem = sermon; $.global = {};

    var cuts = SermonStudio._internals.sceneCuts(sermon);
    if (cuts.indexOf(100) >= 0 || cuts.indexOf(150) >= 0) bad("a lower third was taken for a cut");
    if (!cuts.some(function (c) { return c >= 297 && c <= 303; }))
        bad("the cut at 300 was not found: " + cuts.join(", "));
    if (!cuts.some(function (c) { return Math.abs(c - SHIFT) < 0.2; }))
        bad("the start of the footage is not treated as a cut: " + cuts.join(", "));

    var st = JSON.parse(SermonStudio.buildStart("/tmp/cuts.json", JSON.stringify({ layout: "slide" })));
    if (!st.ok) { bad("cuts: " + st.error); return; }
    while (JSON.parse(SermonStudio.buildStep(20)).done < st.total) {}
    JSON.parse(SermonStudio.buildFinish());
    var spanning = st.windows.filter(function (w) { return w[0] < 298 && w[1] > 298; });
    if (spanning.length) bad("a panel section runs across the cut: " + JSON.stringify(spanning));
    if (st.windows.length !== 2) bad("expected a section either side of the cut, got " + st.windows.length);
    print("  cuts: " + cuts.length + " found, " + st.windows.length + " sections either side");
})();

// The slide camera follows the preacher: he stays in the middle of the inset.
(function () {
    var name = Object.keys(PAYLOADS)[0], payload = JSON.parse(JSON.stringify(PAYLOADS[name]));
    items.length = 0;
    payload.style.slide.furnitureImage = FURNITURE;
    FILES["/tmp/follow-build.json"] = JSON.stringify(payload);
    FILES[FURNITURE] = "";
    var media = footage(payload.media, 3840, 2160, payload.duration),
        sermon = comp(name, 3840, 2160, payload.duration + 30);
    var cam = sermon.layers.add(media); cam.startTime = SHIFT; cam._in = SHIFT; cam._out = SHIFT + payload.duration;
    app.project.activeItem = sermon; $.global = {};
    var st = JSON.parse(SermonStudio.buildStart("/tmp/follow-build.json", JSON.stringify({ layout: "slide" })));
    if (!st.ok) { bad("follow: " + st.error); return; }
    if (!st.windows || !st.windows.length) { bad("the build does not report its sections"); return; }
    if (!(st.cropWidth > 0.2 && st.cropWidth < 0.6)) bad("crop width looks wrong: " + st.cropWidth);

    // He starts centre, walks left, comes back.
    var w0 = st.windows[0];
    var followPath = {
        windows: [{ start: w0[0], end: w0[1], keys: [[w0[0], 0.50], [w0[0] + 4, 0.34],
                                                     [w0[0] + 9, 0.34], [w0[0] + 12, 0.62]] }]
    };
    FILES["/tmp/follow.json"] = JSON.stringify(followPath);
    var applied = JSON.parse(SermonStudio.applyCameraFollow("/tmp/follow.json"));
    if (!applied.ok) { bad("follow: " + applied.error); return; }
    if (applied.keys !== 4) bad("expected 4 framing keys, got " + applied.keys);

    var insetComp = items.filter(function (x) { return x instanceof CompItem && /layout:inset/.test(x.comment); })[0];
    if (!insetComp) { bad("no camera inset comp was made"); return; }
    var sn = payload.style.slide, ref = payload.style.reference,
        scale = Math.min(3840 / ref[0], 2160 / ref[1]);
    if (Math.abs(insetComp.width - sn.camera.width * scale) > 1.5 ||
        Math.abs(insetComp.height - sn.camera.height * scale) > 1.5)
        bad("the inset comp is not the size of the frame");
    var inner = insetComp._layers[0],
        pos = inner.property("ADBE Transform Group").property("ADBE Position"),
        factor = insetComp.height / 2160;
    // Wherever he is, that point of the picture sits in the middle of the frame.
    var worst = 0, edge = 0;
    followPath.windows[0].keys.forEach(function (k) {
        var px = pos.at(k[0])[0],
            shown = (insetComp.width / 2 - px) / factor + 1920,      // picture x at the centre
            want = k[1] * 3840,
            lo = insetComp.width - 3840 * factor / 2, hi = 3840 * factor / 2;
        if (px < lo - 0.5 || px > hi + 0.5) edge++;
        if (px > lo + 1 && px < hi - 1) worst = Math.max(worst, Math.abs(shown - want));
    });
    if (worst > 1) bad("the preacher is off centre by " + worst.toFixed(0) + "px");
    if (edge) bad("the inset shows past the edge of the picture");
    // Settled before the panel appears, and still when it leaves.
    if (Math.abs(pos.at(w0[0] - 0.5)[0] - pos.at(w0[0])[0]) > 0.5)
        bad("the framing is still moving as the panel appears");
    var insets = 0, L = items.filter(function (x) { return x instanceof CompItem && /layout:slide/.test(x.comment); })[0]._layers;
    for (var i = 0; i < L.length; i++) if (/\(inset \d+\)$/.test(L[i].name) && L[i].source === insetComp) insets++;
    if (insets !== st.windows.length) bad("sections do not all use the camera inset comp");
    print("  following: " + applied.keys + " framing keys, inset comp " + insetComp.width + "x" + insetComp.height);
})();

// A vertical snippet: portrait frame, pastor followed, captions rising.
(function () {
    var d = SNIPPET;
    items.length = 0;
    FILES["/tmp/snip.json"] = JSON.stringify(d);
    FILES[d.style.slide.background.image] = ""; FILES[d.style.slide.logo.image] = "";
    var media = footage(d.media, 3840, 2160, 3000), sermon = comp("16:09:2026", 3840, 2160, 3000);
    var cam = sermon.layers.add(media); cam.startTime = SHIFT; cam._in = SHIFT; cam._out = 2900;
    app.project.activeItem = sermon; $.global = {};
    var res = JSON.parse(SermonStudio.buildSnippet("/tmp/snip.json"));
    if (!res.ok) { bad("snippet: " + res.error); return; }
    var sn = d.style.snippet, k = sn.size[0] / sn.reference[0], dur = d.to - d.from;
    var out = items.filter(function (x) { return x instanceof CompItem && / layout:snippet ·/.test(x.comment); })[0];
    var camComp = items.filter(function (x) { return x instanceof CompItem && /layout:snippet-camera/.test(x.comment); })[0];
    if (!out || out.width !== 2160 || out.height !== 3840) { bad("snippet is not 2160x3840"); return; }
    if (Math.abs(out.duration - dur) > 0.01) bad("snippet length " + out.duration + " not " + dur);
    var names = out._layers.map(function (L) { return L.name; });
    ["Backdrop", "Camera", "Frame", "Logo"].forEach(function (n) { if (names.indexOf(n) < 0) bad("snippet has no " + n); });
    if (camComp.width !== Math.round(sn.frame.width * k) || camComp.height !== Math.round(sn.frame.height * k))
        bad("camera precomp is not the size of the frame");
    var inner = camComp._layers[0];
    if (Math.abs(inner.startTime + d.from) > 1e-6) bad("camera starts at the wrong moment of the sermon");
    // The pastor sits in the middle of the frame wherever the path says he is.
    var factor = camComp.height / sermon.height, pos = inner.property("ADBE Transform Group").property("ADBE Position");
    var worst = 0;
    d.follow.forEach(function (kf) {
        var px = pos.at(kf[0])[0], shown = (camComp.width / 2 - px) / factor + sermon.width / 2;   // sermon x at the frame centre
        var want = SHIFT_X + kf[1] * 3840;
        var lo = camComp.width - sermon.width * factor / 2, hi = sermon.width * factor / 2;
        if (px > lo + 1 && px < hi - 1) worst = Math.max(worst, Math.abs(shown - want));
    });
    if (worst > 1) bad("framing off the pastor by " + worst.toFixed(1) + "px");
    // Never shows past the edge of the picture.
    for (var t = 0; t < dur; t += 0.25) {
        var x = pos.at(t)[0];
        if (x < camComp.width - sermon.width * factor / 2 - 0.5 || x > sermon.width * factor / 2 + 0.5) { bad("frame shows past the picture at " + t); break; }
    }
    // Captions: rise in, float up, one at a time, all of them.
    var caps = out._layers.filter(function (L) { return /^Caption /.test(L.name); });
    if (caps.length !== d.captions.length) bad("snippet built " + caps.length + " of " + d.captions.length + " captions");
    var two = 0, rising = 0;
    for (t = 0; t < dur; t += 0.05) {
        var full = caps.filter(function (L) { return L.opacityAt(t) > 95; }).length;
        if (full > 1) two++;
    }
    caps.forEach(function (L) {
        var p = L.property("ADBE Transform Group").property("ADBE Position"), ks = p.keys;
        if (ks.length === 4 && ks[0].v[1] > ks[1].v[1] && ks[2].v[1] > ks[3].v[1]) rising++;
    });
    if (two) bad("two captions fully up at once for " + (two * 0.05).toFixed(1) + "s");
    if (rising !== caps.length) bad((caps.length - rising) + " captions do not rise in and float up");
    var capY = sn.frame.y * k + sn.frame.height * k * sn.caption.y;
    if (caps.some(function (L) { return Math.abs(L.property("ADBE Transform Group").property("ADBE Position").keys[1].v[1] - capY) > 0.5; }))
        bad("captions not over the lectern");
    print("  snippet: " + out.width + "x" + out.height + ", " + caps.length + " captions, " + d.follow.length + " framing keys");
})();
print(failed ? "\n  " + failed + " problems" : "\n  0 failed");
"""


def main():
    payloads = {}
    for path in sorted((ROOT / "out").glob("*:*:*.cues.json")):
        if path.name.endswith(".selected.json"):
            continue
        payload = json.loads(path.read_text())
        try:
            cues = replay.rebuild(payload)
        except (IndexError, FileNotFoundError):
            continue
        payload["style"] = json.loads((ROOT / "config/style.json").read_text())
        payload["cues"] = [c.to_dict() for c in cues]
        payload.pop("mentions", None)
        payloads[path.name.split(".")[0]] = payload
    if payloads:
        # A verse far too long for one panel (Esther 8:9 is 90 words), read
        # over 40 seconds between two ordinary ones: it must page, not shrink.
        long_text = ("And the king's scribes were summoned at that time, and an edict was "
                     "written, according to all that Mordecai commanded, to the Jews and to the "
                     "satraps and the governors and the officials of the provinces from India to "
                     "Ethiopia, a hundred and twenty-seven provinces, to each province in its own "
                     "script and to each people in its own language, and also to the Jews in their "
                     "script and their language, all of it sealed with the ring of the king")
        base = dict(next(iter(payloads.values())))
        base["cues"] = [
            {"ref": "Esther 8:8", "text": "Now write in the king's name as you please.",
             "translation": "ESV", "start": 100.0, "end": 106.0, "enabled": True},
            {"ref": "Esther 8:9", "text": long_text, "translation": "ESV",
             "start": 106.0, "end": 146.0, "enabled": True},
            {"ref": "Esther 8:10", "text": "And he wrote in the name of King Ahasuerus.",
             "translation": "ESV", "start": 146.0, "end": 152.0, "enabled": True},
        ]
        payloads["long verse"] = base

        # A session carried by a presentation: slides in the panel, back to
        # back, each with its own picture.
        deck = dict(next(iter(payloads.values())))
        deck["cues"] = [
            {"ref": f"Slide {n} \u2014 Heading {n}", "text": f"Heading {n}\nsome words",
             "translation": "", "kind": "slide", "slide": n,
             "image": f"/decks/test/slide-{n:03d}.png",
             "start": 100.0 + (n - 1) * 90, "end": 100.0 + n * 90, "enabled": True}
            for n in range(1, 5)]
        # A deck session uses its own arrangement, exactly as the panel sets
        # it before building: the panel takes the frame's full height.
        deck["style"] = json.loads((ROOT / "config/style.json").read_text())
        if deck["style"]["slide"].get("deck"):
            deck["style"]["slide"]["camera"] = deck["style"]["slide"]["deck"]["camera"]
            deck["style"]["slide"]["panel"] = deck["style"]["slide"]["deck"]["panel"]
        payloads["deck"] = deck
    # A snippet, with a made-up path for the pastor so no video is needed.
    sys.path.insert(0, str(ROOT / "engine"))
    import snippets
    first = json.loads((ROOT / "out/16:09:2026.cues.json").read_text()) \
        if (ROOT / "out/16:09:2026.cues.json").exists() else None
    snippet = None
    if first:
        style = json.loads((ROOT / "config/style.json").read_text())
        emap = [{"offset": SHIFT, "scale": 1, "tMin": SHIFT, "tMax": 2900}]
        snippet = snippets.prepare(first, emap, 797.8, 826.6, style, follow=False)
        snippet["follow"] = [[0, 0.5], [8, 0.5], [9.5, 0.40], [18, 0.40], [19, 0.12], [25, 0.62]]
        snippet["style"] = style
    import furniture
    style_now = json.loads((ROOT / "config/style.json").read_text())
    flat = furniture.make(style_now, 3840, 2160)
    js = "\n".join([
        "var SNIPPET = " + json.dumps(snippet) + ", SHIFT_X = 0;",
        "var FURNITURE = " + json.dumps(flat) + ";",
        (ROOT / "tests/ae.mock.js").read_text(),
        "var FILES = {}, SHIFT = %s, VERBOSE = %s;" % (SHIFT, "true" if "-v" in sys.argv else "false"),
        (ROOT / "cep/jsx/SermonStudio.jsx").read_text(),
        "var PAYLOADS = " + json.dumps(payloads) + ";",
        CHECK,
    ])
    p = Path(tempfile.gettempdir()) / "vc_build.js"
    p.write_text(js)
    r = subprocess.run([JSC, str(p)], capture_output=True, text=True)
    out = (r.stdout.strip() + "\n" + r.stderr.strip()[:800]).strip()
    print(out)
    return 0 if out.endswith(" 0 failed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
