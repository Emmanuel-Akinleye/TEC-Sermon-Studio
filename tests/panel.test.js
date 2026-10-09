// The real cep/js/main.js, run against a pretend panel, file system and
// After Effects: restoring the right analysis, remembering settings, keeping
// review choices across a re-analysis, and the fix/add/in/out tools.

// ---- a pretend DOM ---------------------------------------------------------
function El(tag) {
    this.tagName = tag; this.children = []; this.listeners = {}; this.style = {};
    this.textContent = ""; this.value = ""; this.disabled = false; this.className = "";
    var self = this, classes = {};
    this.classList = {
        add: function (c) { classes[c] = true; }, remove: function (c) { delete classes[c]; },
        toggle: function (c, on) { if (on === undefined ? !classes[c] : on) { classes[c] = true; } else { delete classes[c]; } },
        contains: function (c) { return !!classes[c]; }
    };
}
El.prototype.addEventListener = function (ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); };
El.prototype.fire = function (ev, arg) { (this.listeners[ev] || []).forEach(function (fn) { fn(arg || {}); }); };
El.prototype.appendChild = function (c) { c.parent = this; this.children.push(c); return c; };
El.prototype.replaceWith = function (c) {
    var i = this.parent.children.indexOf(this); this.parent.children[i] = c; c.parent = this.parent;
};
El.prototype.focus = function () {}; El.prototype.select = function () {}; El.prototype.click = function () {};
El.prototype.play = function () { this.playing = true; PLAYED.push(this.src); };
El.prototype.pause = function () { this.playing = false; };
El.prototype.querySelector = function (q) {
    var m = /value='([^']*)'/.exec(q), i;
    for (i = 0; i < this.children.length; i++) if (m && this.children[i].value === m[1]) return this.children[i];
    return null;
};
Object.defineProperty(El.prototype, "innerHTML", { set: function () { this.children = []; }, get: function () { return ""; } });
var ELS = {}, PLAYED = [];
var document = {
    getElementById: function (id) { return ELS[id] || (ELS[id] = new El("div")); },
    createElement: function (t) { return new El(t); }
};
function option(sel, v) { var o = new El("option"); o.value = v; sel.appendChild(o); }
option(document.getElementById("layout"), "lower-third"); option(document.getElementById("layout"), "slide");
["ESV", "NLT", "KJV", "BSB"].forEach(function (v) { option(document.getElementById("translation"), v); });
document.getElementById("layout").value = "lower-third";
document.getElementById("translation").value = "ESV";

var window = { addEventListener: function () {}, setInterval: function () {}, setTimeout: function (f) { f(); },
               navigator: { platform: "Mac" }, process: { env: { HOME: "/Users/test" } } };

// ---- a pretend file system and node modules ----------------------------------
var DISK = {}, MTIME = {};
var fakeFs = {
    existsSync: function (p) { return DISK.hasOwnProperty(p) || Object.keys(DISK).some(function (k) { return k.indexOf(p + "/") === 0; }); },
    readFileSync: function (p) { if (!DISK.hasOwnProperty(p)) throw new Error("ENOENT " + p); return DISK[p]; },
    writeFileSync: function (p, t) { DISK[p] = t; },
    readdirSync: function (d) {
        var out = {}; Object.keys(DISK).forEach(function (k) {
            if (k.indexOf(d + "/") === 0) out[k.slice(d.length + 1).split("/")[0]] = true; });
        return Object.keys(out);
    },
    statSync: function (p) { return { size: (DISK[p] || "").length || 1000, mtime: new Date((MTIME[p] || 1000) * 1000),
                                      isDirectory: function () { return false; } }; }
};
var fakePath = {
    join: function () { return Array.prototype.slice.call(arguments).join("/").replace(/\/+/g, "/"); },
    basename: function (p) { return String(p).replace(/^.*\//, ""); },
    dirname: function (p) { return String(p).replace(/\/[^\/]*$/, ""); },
    resolve: function (a, b) { return b === ".." ? a.replace(/\/[^\/]*$/, "") : a + "/" + b; }
};
var SPAWNED = [], LOOKUP = {};
function FakeStream() { this.h = {}; }
FakeStream.prototype.on = function (e, f) { this.h[e] = f; };
var fakeChild = {
    spawn: function (cmd, args) {
        SPAWNED.push(args);
        var proc = { stdout: new FakeStream(), stderr: new FakeStream(), h: {},
                     on: function (e, f) { this.h[e] = f; }, kill: function () {} };
        if (args[1] === "snippet-suggest") {
            var sug = { ok: true, moments: [
                { start: 797.8, end: 826.6, duration: 28.8, opening: "You will too.", why: ["repeats for effect"] },
                { start: 1775, end: 1806, duration: 31, opening: "You can demonstrate it.", why: [] }] };
            proc.finish = function () { proc.stdout.h.data(JSON.stringify(sug) + "\n"); proc.h.close(0); };
            PENDING.push(proc);
        }
        if (args[1] === "snippet") {
            var outPath = args[args.indexOf("--out") + 1];
            proc.finish = function () {
                proc.stdout.h.data(JSON.stringify({ event: "progress", message: "Finding the pastor in each frame" }) + "\n");
                proc.stdout.h.data(JSON.stringify({ event: "done", path: outPath, captions: 13, follow: 14 }) + "\n");
                proc.h.close(0);
            };
            PENDING.push(proc);
        }
        if (args[1] === "follow") {
            var fout = args[args.indexOf("--out") + 1];
            proc.finish = function () {
                if (FOLLOW_FAILS) {
                    proc.stdout.h.data(JSON.stringify({ event: "error", message: "No such file" }) + "\n");
                    proc.h.close(1);
                } else {
                    proc.stdout.h.data(JSON.stringify({ event: "progress", message: "Following the preacher, section 1 of 2" }) + "\n");
                    proc.stdout.h.data(JSON.stringify({ event: "done", path: fout, sections: 2, keys: 9 }) + "\n");
                    proc.h.close(0);
                }
            };
            PENDING.push(proc);
        }
        if (args[1] === "furniture") {
            proc.finish = function () { proc.stdout.h.data(JSON.stringify({ ok: true, path: "/x/furniture.png" }) + "\n"); proc.h.close(0); };
            PENDING.push(proc);
        }
        if (args[1] === "captions") {
            var out = { ok: true, srt: "/x/sub.srt", txt: "/x/t.txt", captions: 10 };
            proc.finish = function () { proc.stdout.h.data(JSON.stringify(out) + "\n"); proc.h.close(0); };
            PENDING.push(proc);
        }
        if (args[1] === "deck" && args[2] === "import") {
            var deckPath = args[3];
            proc.finish = function () {
                if (DECK_FAILS) {
                    proc.stdout.h.data(JSON.stringify({ ok: false, error:
                        "2 of 2 slides are built from text and shapes \u2014 export the deck as a PDF" }) + "\n");
                    proc.h.close(1);
                } else {
                    proc.stdout.h.data(JSON.stringify({ ok: true, id: "deck123", source: deckPath,
                        kind: ".pdf", count: 3, slides: [
                            { index: 1, image: "/d/slide-001.png", text: "The Reformation\nLuther", title: "The Reformation", read: "pdf" },
                            { index: 2, image: "/d/slide-002.png", text: "Council of Trent", title: "Council of Trent", read: "pdf" },
                            { index: 3, image: "/d/slide-003.png", text: "", title: "", read: "ocr" }] }) + "\n");
                    proc.h.close(0);
                }
            };
            PENDING.push(proc);
        }
        if (args[1] === "deck" && args[2] === "place") {
            DECK_PLACED = args.slice();
            proc.finish = function () {
                proc.stdout.h.data(JSON.stringify({ event: "progress", message: "Placing 3 slides" }) + "\n");
                proc.stdout.h.data(JSON.stringify({ event: "done", ok: true, slides: 3,
                    placed: 2, guessed: 1, verses: 4, deck: "deck123",
                    cues_path: args[args.indexOf("--cues") + 1] }) + "\n");
                proc.h.close(0);
            };
            PENDING.push(proc);
        }
        if (args[1] === "clean-profile") {
            proc.finish = function () {
                proc.stdout.h.data(JSON.stringify({ ok: true, notes: [
                    "Very noisy \u2014 voice sits 16 dB above the room",
                    "31% of the file is dead air (before and after the service)"],
                    profile: { snr_db: 16.2, reverb: 0.18 } }) + "\n");
                proc.h.close(0);
            };
            PENDING.push(proc);
        }
        if (args[1] === "clean-preview") {
            var dir = args[args.indexOf("--out-dir") + 1];
            proc.finish = function () {
                proc.stdout.h.data(JSON.stringify({ event: "progress", message: "Listening to the recording" }) + "\n");
                proc.stdout.h.data(JSON.stringify({ event: "done", ok: true, at: 1264.5, seconds: 20,
                    before: dir + "/service before.wav", after: dir + "/service after.wav",
                    notes: ["Very noisy \u2014 voice sits 16 dB above the room"], gain_db: 9.4 }) + "\n");
                proc.h.close(0);
            };
            PENDING.push(proc);
        }
        if (args[1] === "clean" && args[2] !== undefined) {
            var cleaned = args[args.indexOf("--out") + 1];
            proc.finish = function () {
                if (CLEAN_FAILS) {
                    proc.stdout.h.data(JSON.stringify({ event: "error", message: "ffmpeg is not installed" }) + "\n");
                    proc.h.close(1);
                } else {
                    proc.stdout.h.data(JSON.stringify({ event: "progress", message: "Cleaning the audio \u2014 3 of 75 min", pct: 0.04 }) + "\n");
                    proc.stdout.h.data(JSON.stringify({ event: "done", ok: true, path: cleaned,
                        gain_db: 9.4, latency_samples: 1439, measured_lufs: -25.4, target_lufs: -16,
                        notes: ["Very noisy \u2014 voice sits 16 dB above the room"] }) + "\n");
                    proc.h.close(0);
                }
            };
            PENDING.push(proc);
        }
        if (args[1] === "lookup") {
            var res = LOOKUP[args[2]] || { ok: false, error: "no such verse" };
            proc.finish = function () { proc.stdout.h.data(JSON.stringify(res) + "\n"); proc.h.close(res.ok ? 0 : 1); };
            PENDING.push(proc);
        }
        return proc;
    }
};
var PENDING = [], CLEAN_FAILS = false, DECK_FAILS = false, DECK_PLACED = null;
function flush() { while (PENDING.length) PENDING.shift().finish(); }

// ---- a pretend After Effects -------------------------------------------------
var PLAYHEAD = { ok: true, compTime: 105.8, source: 100.0 }, BUILT_SNIPPET = null, CLEAN_ADDED = null,
    FOLLOW_FAILS = false, FOLLOW_APPLIED = null;
function CSInterface() {}
CSInterface.prototype.evalScript = function (script, cb) {
    if (/SermonStudio\.playhead/.test(script)) { cb && cb(JSON.stringify(PLAYHEAD)); return; }
    if (/SermonStudio\.applyCameraFollow/.test(script)) {
        FOLLOW_APPLIED = /applyCameraFollow\("([^"]+)"\)/.exec(script)[1];
        cb && cb(JSON.stringify({ ok: true, keys: 9, sections: 2 }));
        return;
    }
    if (/SermonStudio\.buildStart/.test(script)) {
        cb && cb(JSON.stringify({ ok: true, total: 2, layout: "slide",
                                  windows: [[20, 60], [90, 120]], cropWidth: 0.36 }));
        return;
    }
    if (/SermonStudio\.buildStep/.test(script)) { cb && cb(JSON.stringify({ ok: true, done: 2, total: 2 })); return; }
    if (/SermonStudio\.buildFinish/.test(script)) {
        cb && cb(JSON.stringify({ ok: true, built: 2, comp: "Sermon — Slide Layout", layout: "slide", stack: [] }));
        return;
    }
    if (/SermonStudio\.addCleanAudio/.test(script)) {
        CLEAN_ADDED = /addCleanAudio\("([^"]+)", "([^"]*)"\)/.exec(script);
        cb && cb(JSON.stringify({ ok: true, layer: "Mic (cleaned)", startTime: 1.52,
                                  synced: true, muted: true, replaced: false, comp: "Sermon" }));
        return;
    }
    if (/SermonStudio\.buildSnippet/.test(script)) {
        BUILT_SNIPPET = /buildSnippet\("([^"]+)"\)/.exec(script)[1];
        cb && cb(JSON.stringify({ ok: true, comp: "16:09:2026 \u2014 Snippet 13.17-13.46", captions: 13 }));
        return;
    }
    cb && cb(JSON.stringify({ ok: true }));
};
CSInterface.prototype.getSystemPath = function () { return "/ext"; };
var SystemPath = { EXTENSION: "ext" };
function nodeRequire() { return {}; }
