/*
 * TEC Sermon Studio panel.
 *
 * The panel is a thin controller: it asks After Effects what is open, runs the
 * Python analysis as a child process, shows the cues for review, then hands a
 * filtered cue file back to ExtendScript to build.
 */
/*global CSInterface, SystemPath, nodeRequire, window, document*/
"use strict";

var cs = new CSInterface(),
    fs = null,
    path = null,
    childProcess = null,
    process = null,
    extensionRoot = "",
    projectRoot = "",
    state = {
        media: null,
        audio: null,
        alignment: null,
        comp: null,
        fps: 25,
        instances: 0,
        editedDuration: 0,
        audioSignature: null,
        payload: null,
        cuesPath: null,
        lastError: null,
        child: null,
        python: null,
        shownPct: 0,
        startedAt: 0,
        restoreTried: false,
        fontsLoaded: false,
        building: false,
        lastProbe: 0,
        /* How the recording maps into the edit, for showing edit timecodes. */
        map: [],
        snip: { from: null, to: null, moments: [], busy: false },
        clean: { busy: false, result: null, preview: null, notesFor: null },
        deck: { source: null, info: null, busy: false }
    };

/* ---------------------------------------------------------------- utils */
function $(id) { return document.getElementById(id); }

function setStatus(text, isError) {
    var el = $("status");
    el.textContent = text;
    el.className = isError ? "error" : "";
}

/* Progress only ever moves forward: stage boundaries would otherwise make it
   jump backwards, which reads as the run having gone wrong. */
function setProgress(pct) {
    var value = Math.max(0, Math.min(1, pct || 0)) * 100;
    if (value < state.shownPct) { value = state.shownPct; }
    state.shownPct = value;
    $("pctValue").textContent = Math.floor(value);

    if (value > 1 && state.startedAt) {
        var elapsed = (Date.now() - state.startedAt) / 1000,
            remaining = elapsed * (100 - value) / value;
        $("eta").textContent = remaining > 45
            ? "about " + Math.round(remaining / 60) + " min left"
            : "nearly done";
    } else {
        $("eta").textContent = "";
    }
}

function mins(seconds) {
    var m = Math.floor(seconds / 60), s = Math.round(seconds % 60);
    return m + "m " + (s < 10 ? "0" : "") + s + "s";
}

function tc(seconds, fps) {
    fps = fps || 25;
    var frames = Math.round(seconds * fps),
        f = frames % Math.round(fps),
        total = Math.floor(frames / Math.round(fps)),
        s = total % 60,
        m = Math.floor(total / 60) % 60,
        h = Math.floor(total / 3600);
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return p(h) + ":" + p(m) + ":" + p(s) + ":" + p(f);
}

/*
 * Always run against the script as it is on disk.
 *
 * ExtendScript stays resident once loaded, so editing SermonStudio.jsx used to
 * mean restarting After Effects. Re-reading the file first costs a millisecond
 * and removes that entirely.
 */
function freshScript() {
    return "$.evalFile(new File(" +
        JSON.stringify(path.join(extensionRoot, "jsx", "SermonStudio.jsx")) + ")); ";
}

function evalJSX(script, callback) {
    cs.evalScript(freshScript() + script, function (res) {
        var parsed = null;
        try { parsed = JSON.parse(res); } catch (e) { parsed = null; }
        if (callback) { callback(parsed, res); }
    });
}

/* ------------------------------------------------------------- bootstrap */
function resolveProjectRoot() {
    var marker = path.join(extensionRoot, "engine-path.txt"), recorded;
    if (fs.existsSync(marker)) {
        recorded = fs.readFileSync(marker, "utf8").trim();
        if (recorded && fs.existsSync(path.join(recorded, "engine", "cli.py"))) {
            return recorded;
        }
    }
    var guess = path.resolve(extensionRoot, "..");
    if (fs.existsSync(path.join(guess, "engine", "cli.py"))) { return guess; }
    return "";
}

function findPython() {
    var custom = $("python").value.trim(), candidates, i;
    if (custom) { return custom; }
    candidates = [
        "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
        "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
        "/usr/bin/python3"
    ];
    for (i = 0; i < candidates.length; i++) {
        if (fs.existsSync(candidates[i])) { return candidates[i]; }
    }
    return "python3";
}

function loadHostScript() {
    var jsx = path.join(extensionRoot, "jsx", "SermonStudio.jsx");
    cs.evalScript(
        "$.global.__vcPanelHost = true; $.evalFile(new File(" +
            JSON.stringify(jsx) + "));",
        function () { probe(); }
    );
}

/* ----------------------------------------------------------------- probe */
/*
 * Ask After Effects what is open - but not while it is busy.
 *
 * Every check re-reads this script inside After Effects, which costs it a
 * moment. That is nothing while you are working, and worth avoiding during a
 * render that runs for hours: the panel checks in far less often when you are
 * not looking at it, and not at all while a build or an analysis is running.
 */
function pollHost() {
    var now = Date.now(),
        hidden = (typeof document.hidden === "boolean") ? document.hidden : false,
        every = hidden ? 60000 : 4000;
    if (state.child || state.building || (state.snip && state.snip.busy)) { return; }
    if (now - (state.lastProbe || 0) < every) { return; }
    probe();
}

function probe() {
    state.lastProbe = Date.now();
    evalJSX("SermonStudio.probe()", function (info) {
        if (!info || !info.ok) {
            $("hostline").textContent = "After Effects did not respond";
            return;
        }
        $("hostline").textContent = "After Effects " + (info.hostVersion || "").split("x")[0];
        state.comp = info.comp;
        state.fps = info.fps || 25;
        state.instances = info.instances || 0;
        state.editedDuration = info.editedDuration || 0;
        state.projectPath = info.projectPath || null;
        state.compSize = [info.width || 0, info.height || 0];
        if (JSON.stringify(info.map || []) !== JSON.stringify(state.map)) {
            state.map = info.map || [];
            if (state.payload) { renderCues(); }
        }
        $("compName").textContent = info.comp || "no composition open";

        if (info.media) {
            state.media = info.media;
            $("mediaName").textContent = info.media.replace(/^.*[\/\\]/, "") +
                (info.fromSelection ? "  (selected layer)" : "");
        } else if (!state.media) {
            $("mediaName").textContent = info.comp
                ? "no video layer in this comp"
                : "open your sermon comp";
        }

        /* The recording is what gets analyzed; the comp only says where each
           moment ended up after trimming. */
        if (!info.media) {
            $("editInfo").textContent = "—";
        } else if (!state.instances) {
            $("editInfo").textContent = "not on this timeline — cards will use raw times";
        } else {
            $("editInfo").textContent = state.instances === 1
                ? "1 piece · " + mins(state.editedDuration) + " used"
                : state.instances + " pieces · " + mins(state.editedDuration) + " used";
        }
        reloadIfNewer();
        populateAudio(info);
        if (!state.fontsLoaded) {
            loadFontsFromEngine(function (list) {
                populateFonts(list && list.length ? list : info.fonts);
            });
        }
        restorePrevious();
        updateReadiness();
    });
}

/*
 * Reopening the panel should not cost another analysis. The cue file is on
 * disk, so load it back - preferring the version carrying the ticks from the
 * last build.
 */
function restorePrevious() {
    if (state.restoreTried || state.payload || !state.media || !projectRoot) { return; }
    state.restoreTried = true;

    var found = findAnalysis(state.media);
    if (!found) { return; }
    state.cuesPath = found.base;
    state.payload = found.payload;
    state.alignment = state.payload.alignment || null;
    showSync();
    renderCues();
    $("resultsPanel").classList.remove("hidden");
    $("footnote").textContent = "loaded previous analysis · " +
        (state.payload.generated || "");
}

/* Name, size and date of a file, as the engine records them. */
function identityOf(file) {
    try {
        var st = fs.statSync(file);
        return path.basename(file) + "|" + st.size + "|" + Math.floor(st.mtime.getTime() / 1000);
    } catch (e) { return null; }
}

/*
 * The saved analysis of this very recording.
 *
 * Matched on the file itself, not just its name: cameras reuse names, and
 * loading last month's verses for this week's C0001.MP4 would be worse than
 * loading nothing. Of the analysis and the copy carrying your review ticks,
 * the ticked copy wins only if it belongs to the same analysis.
 */
function findAnalysis(media) {
    var dir = path.join(projectRoot, "out"), names, i, base, head, ident = identityOf(media),
        best = null;
    try { names = fs.readdirSync(dir); } catch (e) { return null; }
    for (i = 0; i < names.length; i++) {
        if (!/\.cues\.json$/.test(names[i])) { continue; }
        base = path.join(dir, names[i]);
        try { head = JSON.parse(fs.readFileSync(base, "utf8")); } catch (e) { continue; }
        if (head.media !== media) { continue; }
        if (head.media_identity && ident && head.media_identity !== ident) { continue; }
        if (!best || String(head.generated) > String(best.payload.generated)) {
            best = { base: base, payload: head };
        }
    }
    if (!best) { return null; }
    var selected = best.base.replace(/\.json$/, ".selected.json");
    try {
        var ticked = JSON.parse(fs.readFileSync(selected, "utf8"));
        if (ticked.generated === best.payload.generated) { best.payload = ticked; }
    } catch (e) { /* no review saved yet */ }
    return best;
}

/* Settings that belong to the operator, not the design: kept between runs. */
function panelSettingsPath() { return path.join(projectRoot, "config", "panel.json"); }

function loadPanelSettings() {
    var saved = {};
    try { saved = JSON.parse(fs.readFileSync(panelSettingsPath(), "utf8")); } catch (e) { /* first run */ }
    if (saved.layout && $("layout").querySelector("option[value='" + saved.layout + "']")) {
        $("layout").value = saved.layout;
    }
    if (saved.translation && $("translation").querySelector("option[value='" + saved.translation + "']")) {
        $("translation").value = saved.translation;
    }
}

function savePanelSettings() {
    try {
        fs.writeFileSync(panelSettingsPath(), JSON.stringify({
            layout: $("layout").value,
            translation: $("translation").value
        }, null, 2), "utf8");
    } catch (e) { /* not worth interrupting anyone over */ }
}

function updateReadiness() {
    $("analyze").disabled = !state.media || !projectRoot || !state.audio || !!state.child;
    $("analyze").title = state.audio ? "" : "Choose the mic recording first";
    $("build").disabled = !state.payload;
    if (!projectRoot) {
        $("footnote").textContent = "engine folder not found";
    } else if (!state.audio && !state.payload) {
        $("footnote").textContent = "choose the mic recording to analyze";
    }
}

/* --------------------------------------------------------------- analyze */
function analyze() {
    if (!state.media) { return; }
    if (!state.audio) {
        setStatus("Choose the mic recording first - the camera's sound is not used.", true);
        return;
    }
    var python = findPython(),
        args = [
            path.join(projectRoot, "engine", "cli.py"), "analyze", state.media,
            "--translation", $("translation").value,
            "--model", $("model").value,
            "--fps", String(state.fps),
            "--json"
        ];
    if (state.audio) {
        args.push("--audio", state.audio);
    }

    state.python = python;
    state.lastError = null;
    state.restoreTried = true;
    state.shownPct = 0;
    state.startedAt = Date.now();
    $("progress").classList.remove("hidden");
    $("analyze").disabled = true;
    $("resultsPanel").classList.add("hidden");
    setProgress(0);
    setStatus("Starting…");

    var child;
    try {
        child = childProcess.spawn(python, args, {
            cwd: projectRoot,
            env: childEnv()
        });
    } catch (e) {
        setStatus("Could not start Python: " + e.message, true);
        $("analyze").disabled = false;
        return;
    }
    state.child = child;

    var buffer = "", transcript = python + " " + args.join(" ") + "\n\n";
    child.stdout.on("data", function (chunk) {
        var text = chunk.toString();
        transcript += text;
        buffer += text;
        var lines = buffer.split("\n");
        buffer = lines.pop();
        lines.forEach(handleEvent);
    });

    var stderr = "";
    child.stderr.on("data", function (chunk) {
        stderr += chunk.toString();
        transcript += chunk.toString();
    });

    child.on("error", function (err) {
        state.lastError = "Could not run Python: " + err.message;
    });

    child.on("close", function (code) {
        state.child = null;
        $("analyze").disabled = false;
        writeLog(transcript + "\nexit code: " + code + "\n");
        if (code !== 0 && !state.payload) {
            setStatus(state.lastError
                || stderr.trim().split("\n").pop()
                || ("Analysis failed (exit " + code + ") — see out/last-run.log"), true);
        } else {
            $("progress").classList.add("hidden");
        }
        updateReadiness();
    });
}

/*
 * After Effects launches the panel with its own library paths injected and a
 * bare PATH. Both upset Python, so hand the child a clean environment.
 */
function childEnv() {
    var env = {}, key;
    for (key in process.env) {
        if (process.env.hasOwnProperty(key) && key.indexOf("DYLD_") !== 0) {
            env[key] = process.env[key];
        }
    }
    env.PATH = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
                "/usr/sbin", "/sbin", env.PATH || ""].join(":");
    return env;
}

var STAGE_WEIGHT = {
    corpus: [0.00, 0.03],
    sync: [0.03, 0.10],
    transcribe: [0.10, 0.78],
    detect: [0.78, 0.86],
    quotes: [0.86, 0.94],
    cues: [0.94, 1.00]
};

function handleEvent(line) {
    if (!line.trim()) { return; }
    var msg;
    try { msg = JSON.parse(line); } catch (e) { return; }

    if (msg.event === "progress") {
        /* Only a message that carries a real fraction may move the number.
           Guessing a stage is half done ("Using extracted audio") puts the
           percentage ahead of the work and it can never come back down. */
        if (msg.pct !== undefined && msg.pct !== null) {
            var span = STAGE_WEIGHT[msg.stage] || [0, 1];
            setProgress(span[0] + (span[1] - span[0]) * msg.pct);
        }
        if (msg.warning) {
            showWarning(msg.message);
        }
        setStatus(msg.message);
    } else if (msg.event === "done") {
        var previous = state.payload;
        state.cuesPath = msg.cues_path;
        state.alignment = msg.alignment || null;
        showSync();
        loadCues(msg.cues_path);
        carryOver(previous, state.payload);
        saveSelection();
        renderCues();
        writeSubtitles();
        state.shownPct = 100;
        setProgress(1);
        $("eta").textContent = "";
        setStatus("Found " + msg.cue_count + " verses");
    } else if (msg.event === "error") {
        state.lastError = msg.message;
        setStatus(msg.message, true);
    }
}

function cancel() {
    if (state.child) {
        state.child.kill();
        setStatus("Cancelled");
        $("progress").classList.add("hidden");
    }
}

/* ------------------------------------------------------------------- key */
function keysPath() {
    return path.join(projectRoot, "config", "keys.json");
}

function loadKey() {
    try {
        var keys = JSON.parse(fs.readFileSync(keysPath(), "utf8"));
        if (keys.esv && keys.esv.indexOf("paste ") < 0) { $("esvKey").value = keys.esv; }
    } catch (e) { /* no key yet */ }
}

function testKey() {
    var value = $("esvKey").value.trim();
    if (!value) { return; }
    $("testKey").disabled = true;
    $("testKey").textContent = "…";

    var child = childProcess.spawn(findPython(),
        [path.join(projectRoot, "engine", "cli.py"), "key", "esv", value],
        { cwd: projectRoot, env: childEnv() });
    var out = "", err = "";
    child.stdout.on("data", function (c) { out += c.toString(); });
    child.stderr.on("data", function (c) { err += c.toString(); });
    child.on("close", function (code) {
        $("testKey").disabled = false;
        $("testKey").textContent = "Test";
        if (code === 0) {
            showWarning("ESV key works. Re-analyze to replace the BSB text.", true);
        } else {
            showWarning(err.trim().split("\n").pop() || "The key did not work.");
        }
    });
}

function showWarning(text, good) {
    $("warnBanner").classList.remove("hidden");
    $("warnBanner").style.background = good ? "#1f2f22" : "";
    $("warnText").style.color = good ? "#9fd6a3" : "";
    $("warnText").textContent = text;
}

/* ---------------------------------------------------------------- layout */
function toHex(rgb) {
    function pair(n) {
        var h = Math.max(0, Math.min(255, Math.round(n))).toString(16);
        return h.length === 1 ? "0" + h : h;
    }
    return "#" + pair(rgb[0]) + pair(rgb[1]) + pair(rgb[2]);
}

function fromHex(hex) {
    var m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex).trim());
    return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : null;
}

/* Wire a colour input to a place in style.json. */
function bindColour(id, read, write) {
    var input = $(id);
    input.addEventListener("change", function () {
        var rgb = fromHex(input.value);
        if (rgb) { patchStyle(function (style) { write(style, rgb); }); }
    });
    return function (style) { input.value = toHex(read(style)); };
}

function patchStyle(change) {
    try {
        var stylePath = path.join(projectRoot, "config", "style.json"),
            style = readStyle();
        change(style);
        fs.writeFileSync(stylePath, JSON.stringify(style, null, 2), "utf8");
        return style;
    } catch (e) {
        $("footnote").textContent = "could not save: " + e.message;
        return null;
    }
}

function onLayoutChange() {
    var slide = $("layout").value === "slide";
    $("slideOptions").classList.toggle("hidden", !slide);
}

function loadSlideSettings() {
    try {
        var style = readStyle(), slide = style.slide;
        if (!slide) { return; }
        $("bgName").textContent = slide.background.image
            ? slide.background.image.replace(/^.*[\/\\]/, "") : "colour only";
        $("borderWidth").value = slide.camera.border.width;
        $("logoWidth").value = slide.logo.width;
        $("startAfter").value = slide.animation.startAfter;
        $("followPreacher").checked = slide.camera.follow !== false;
        for (var i = 0; i < (state.colourReaders || []).length; i++) {
            state.colourReaders[i](style);
        }
        $("logoName").textContent = slide.logo.image
            ? slide.logo.image.replace(/^.*[\/\\]/, "") : "none";
    } catch (e) { /* defaults are fine */ }
    onLayoutChange();
}

function setAsset(which, filePath) {
    patchStyle(function (style) {
        if (which === "background") { style.slide.background.image = filePath; }
        else { style.slide.logo.image = filePath; }
    });
    $(which === "background" ? "bgName" : "logoName").textContent =
        filePath ? filePath.replace(/^.*[\/\\]/, "")
                 : (which === "background" ? "colour only" : "none");
}

function pickAsset(which) {
    var dialog = document.createElement("input");
    dialog.type = "file";
    dialog.accept = "image/*";
    dialog.addEventListener("change", function () {
        if (dialog.files && dialog.files[0]) {
            setAsset(which, dialog.files[0].path);
        }
    });
    dialog.click();
}

/* ------------------------------------------------------------------ font */
/*
 * One family picker rather than three weight pickers: the sensible weights
 * are derivable, and nobody wants to choose a PostScript name three times.
 */
function pickWeights(styles) {
    function find(patterns) {
        var i, name;
        for (i = 0; i < patterns.length; i++) {
            for (name in styles) {
                if (styles.hasOwnProperty(name)
                        && new RegExp(patterns[i], "i").test(name)
                        && !/italic|oblique/i.test(name)) {
                    return styles[name];
                }
            }
        }
        return null;
    }
    var any = null, name;
    for (name in styles) {
        if (styles.hasOwnProperty(name) && !/italic|oblique/i.test(name)) {
            any = styles[name];
            break;
        }
    }
    var regular = find(["^regular$", "^book$", "^roman$", "^normal$", "^light$"]) || any,
        heavy = find(["^semi ?bold$", "^demi ?bold$", "^bold$", "^medium$"]) || regular,
        mid = find(["^medium$", "^semi ?bold$", "^regular$"]) || regular;
    return { reference: heavy, verse: regular, translation: mid };
}

/*
 * Fonts installed on this Mac, read straight off disk.
 *
 * After Effects exposes a font list, but its shape varies between versions and
 * it has come back unusable here. The files themselves are unambiguous, and a
 * TrueType file's name is its PostScript name in the overwhelming majority of
 * cases, which is what the build needs.
 */
function scanFontFiles() {
    var dirs = [
            path.join(process.env.HOME || "", "Library/Fonts"),
            "/Library/Fonts",
            "/System/Library/Fonts",
            "/System/Library/Fonts/Supplemental"
        ],
        families = {}, i, j, names, base, cut, family, style;

    for (i = 0; i < dirs.length; i++) {
        try {
            names = fs.readdirSync(dirs[i]);
        } catch (e) { continue; }
        for (j = 0; j < names.length; j++) {
            if (!/\.(ttf|otf)$/i.test(names[j])) { continue; }   /* .ttc hides its names */
            base = names[j].replace(/\.(ttf|otf)$/i, "");
            cut = base.indexOf("-");
            if (cut > 0) {
                family = base.substring(0, cut);
                style = base.substring(cut + 1);
            } else {
                family = base.replace(/\s+(Regular|Bold|Italic|Light|Medium|Black|Thin)$/i, "");
                style = base === family ? "Regular" : base.substring(family.length).trim();
            }
            if (!families[family]) { families[family] = {}; }
            families[family][style || "Regular"] = base;
        }
    }

    var out = [];
    for (family in families) {
        if (families.hasOwnProperty(family)) {
            out.push({ family: family, styles: families[family] });
        }
    }
    return out;
}

/* Ask the engine, which reads each file's real PostScript name. */
function loadFontsFromEngine(done) {
    var child, out = "";
    try {
        child = childProcess.spawn(findPython(),
            [path.join(projectRoot, "engine", "cli.py"), "fonts", "--json"],
            { cwd: projectRoot, env: childEnv() });
    } catch (e) {
        done(null);
        return;
    }
    child.stdout.on("data", function (c) { out += c.toString(); });
    child.on("error", function () { done(null); });
    child.on("close", function (code) {
        if (code !== 0) { done(null); return; }
        try { done(JSON.parse(out)); } catch (e) { done(null); }
    });
}

function populateFonts(fonts) {
    if (state.fontsLoaded) { return; }
    fonts = (fonts || []).filter(function (f) {
        return f && f.family && f.family !== "undefined" && f.styles;
    });
    if (!fonts.length) { fonts = scanFontFiles(); }
    if (!fonts.length) {
        $("fontFamily").innerHTML = "<option value=\"\">(no fonts found)</option>";
        state.fontsLoaded = true;
        return;
    }
    state.fontsLoaded = true;
    state.fonts = {};

    var select = $("fontFamily"), current = currentFamily(), i;
    fonts.sort(function (a, b) { return a.family.localeCompare(b.family); });
    select.innerHTML = "";
    for (i = 0; i < fonts.length; i++) {
        state.fonts[fonts[i].family] = fonts[i].styles;
        var option = document.createElement("option");
        option.value = fonts[i].family;
        option.textContent = fonts[i].family;
        if (fonts[i].family === current) { option.selected = true; }
        select.appendChild(option);
    }
    select.addEventListener("change", function () {
        applyFont(select.value);
    });
    $("fontFamily").title = fonts.length + " families found";
}

/* Which family the saved style is already using. */
function currentFamily() {
    try {
        var style = readStyle(), first = style.fonts.reference[0], family;
        for (family in state.fonts) {
            if (state.fonts.hasOwnProperty(family)) {
                for (var st in state.fonts[family]) {
                    if (state.fonts[family][st] === first) { return family; }
                }
            }
        }
    } catch (e) { /* fall through */ }
    return null;
}

function readStyle() {
    return JSON.parse(fs.readFileSync(path.join(projectRoot, "config", "style.json"), "utf8"));
}

function applyFont(family) {
    var styles = state.fonts[family];
    if (!styles) { return; }
    var picks = pickWeights(styles);
    try {
        var stylePath = path.join(projectRoot, "config", "style.json"),
            style = readStyle();
        style.fonts.reference = [picks.reference, "Arial-BoldMT"];
        style.fonts.verse = [picks.verse, "ArialMT"];
        style.fonts.translation = [picks.translation, "ArialMT"];
        fs.writeFileSync(stylePath, JSON.stringify(style, null, 2), "utf8");
        $("footnote").textContent = "font set to " + family + " — rebuild to apply";
    } catch (e) {
        $("footnote").textContent = "could not save font: " + e.message;
    }
}

/* ------------------------------------------------------------------ sync */
function showSync() {
    var a = state.alignment;
    if (!a) {
        $("syncPanel").classList.add("hidden");
        return;
    }
    $("syncPanel").classList.remove("hidden");
    $("syncOffset").textContent = (a.offset >= 0 ? "+" : "") + a.offset.toFixed(3) + "s";
    $("syncOffset").className = "syncbig" + (a.solid ? "" : " weak");

    var note = a.solid
        ? "mic started " + Math.abs(a.offset).toFixed(3) + "s " +
          (a.offset < 0 ? "before" : "after") + " the camera · confidence " +
          a.confidence.toFixed(0)
        : "weak match (confidence " + a.confidence.toFixed(0) +
          ") — check by ear before applying";
    if (Math.abs(a.drift_seconds) >= 0.08) {
        note += " · drifts " + a.drift_seconds.toFixed(2) + "s, stretch " +
            a.stretch_percent.toFixed(4) + "%";
    }
    $("syncNote").textContent = note;
    $("applySync").disabled = false;
}

function applySync() {
    if (!state.alignment || !state.audio) { return; }
    $("applySync").disabled = true;
    evalJSX(
        "SermonStudio.applySync(" + JSON.stringify(state.media) + ", " +
            JSON.stringify(state.audio) + ", " + state.alignment.offset + ", " +
            state.alignment.stretch_percent + ")",
        function (res, raw) {
            $("applySync").disabled = false;
            $("syncNote").textContent = (res && res.ok)
                ? "Moved " + res.layer + " to " + res.startTime.toFixed(3) + "s"
                : ((res && res.error) || raw || "Could not apply");
        }
    );
}

/* Everything a failed run printed, for when the one-line status is not enough. */
function writeLog(text, name) {
    try {
        fs.writeFileSync(path.join(projectRoot, "out", name || "last-run.log"), text, "utf8");
    } catch (e) { /* logging must never be the thing that breaks the run */ }
}

/* --------------------------------------------------------------- results */
function loadCues(cuesPath) {
    try {
        state.payload = JSON.parse(fs.readFileSync(cuesPath, "utf8"));
    } catch (e) {
        setStatus("Could not read results: " + e.message, true);
        return;
    }
    renderCues();
    $("resultsPanel").classList.remove("hidden");
    updateReadiness();
}

/* Where a moment of the recording sits in the edit, or null if it was cut. */
function compTimeOf(seconds) {
    var i, m, t;
    if (!state.map || !state.map.length) { return seconds; }
    for (i = 0; i < state.map.length; i++) {
        m = state.map[i];
        t = m.offset + seconds * m.scale;
        if (t >= m.tMin - 0.0005 && t <= m.tMax + 0.0005) { return t; }
    }
    return null;
}

function renderCues() {
    var list = $("results"), cues = state.payload.cues;
    $("snippetPanel").classList.remove("hidden");
    list.innerHTML = "";
    $("count").textContent = cues.length;

    cues.forEach(function (cue) {
        var li = document.createElement("li"),
            box = document.createElement("input"),
            wrap = document.createElement("div"),
            head = document.createElement("div"),
            ref = document.createElement("span"),
            timeRow = document.createElement("div"),
            time = document.createElement("span"),
            setIn = document.createElement("button"),
            setOut = document.createElement("button"),
            badge = document.createElement("span"),
            body = document.createElement("div"),
            at = compTimeOf(cue.start);

        box.type = "checkbox";
        box.checked = cue.enabled !== false;
        box.addEventListener("change", function () {
            cue.enabled = box.checked;
            cue.userSet = true;
            li.className = box.checked ? "" : "off";
            updateFootnote();
            saveSelection();
        });

        ref.className = "ref";
        ref.textContent = cue.ref;
        if (cue.kind === "slide") {
            /* There is no reference to look up on a slide: its wording is
               whatever is printed on it. */
            ref.title = "Slide " + cue.slide + " of the deck";
        } else {
            ref.title = "Click to change the verse";
            ref.addEventListener("click", function () { editReference(cue, ref); });
        }

        time.className = "tc";
        time.textContent = (at === null ? "cut from the edit" : tc(at, state.fps)) + "  ·  " +
            (cue.end - cue.start).toFixed(1) + "s";
        time.title = "Move the playhead to this moment in the edit";
        time.addEventListener("click", function () {
            cs.evalScript(freshScript() + "SermonStudio.gotoSource(" + cue.start +
            ", " + JSON.stringify(state.media) + ")");
        });
        setIn.className = setOut.className = "ghost tiny rowtool";
        setIn.textContent = "in";
        setOut.textContent = "out";
        setIn.title = "Start this card at the playhead";
        setOut.title = "End this card at the playhead";
        setIn.addEventListener("click", function () { setAtPlayhead(cue, "start"); });
        setOut.addEventListener("click", function () { setAtPlayhead(cue, "end"); });
        timeRow.className = "timerow";
        timeRow.appendChild(time);
        timeRow.appendChild(setIn);
        timeRow.appendChild(setOut);

        var flagged = !!cue.check;
        badge.className = "badge" +
            (cue.source === "read" && !flagged ? " read" : "") +
            (flagged || cue.source === "chapter" ? " low" : "");
        if (flagged) {
            badge.textContent = cue.enabled === false ? "switched off" : "look";
        } else if (cue.kind === "slide") {
            badge.textContent = "slide";
        } else if (cue.kind === "document") {
            badge.textContent = "confession";
        } else if (cue.source === "added" || cue.origRef) {
            badge.textContent = "yours";
        } else if (cue.source === "chapter") {
            badge.textContent = "reference only";
        } else if ((cue.notes || []).indexOf("quoted again, not named") >= 0) {
            badge.textContent = "quoted";
        } else if ((cue.notes || []).indexOf("cited, not read aloud") >= 0) {
            badge.textContent = "cited";
        } else if (cue.source === "read") {
            badge.textContent = "read aloud";
        } else {
            badge.textContent = cue.translation;
        }

        body.className = "body";
        if (cue.kind === "slide") {
            /* Its heading is enough to recognise it; the rest is the words the
               engine matched against, which are not worth reading here. */
            body.textContent = (cue.text || "").split("\n")[0] || "picture only";
        } else if (cue.text) {
            body.textContent = cue.text;
        } else if (cue.source === "chapter") {
            body.textContent = "heard: \u201c" + cue.said + "\u201d \u2014 no verse number given";
        } else if (cue.kind === "document") {
            body.textContent = "heard: \u201c" + cue.said + "\u201d";
        } else {
            body.textContent = "no verse text \u2014 add an API key for " + cue.translation;
        }
        if (flagged) {
            var why = document.createElement("div");
            why.className = "reason";
            why.textContent = cue.check;
            wrap.appendChild(why);
            li.classList.add("flag");
        }
        if (state.onlyFlagged && !flagged) { li.style.display = "none"; }

        head.className = "cuehead";
        if (cue.kind === "slide" && cue.image) {
            var shot = document.createElement("img");
            shot.className = "thumb";
            shot.src = "file://" + encodeURI(cue.image).replace(/#/g, "%23");
            head.appendChild(shot);
        }
        head.appendChild(ref);
        head.appendChild(badge);
        wrap.className = "cue";
        wrap.appendChild(head);
        wrap.appendChild(timeRow);
        wrap.appendChild(body);

        li.className = box.checked ? "" : "off";
        if (flagged) { li.classList.add("flag"); }
        li.appendChild(box);
        li.appendChild(wrap);
        list.appendChild(li);
    });
    updateFootnote();
    deckReady();
}

/* Run an engine command that prints one JSON object. */
function runEngine(args, done) {
    var child, out = "", err = "";
    try {
        child = childProcess.spawn(findPython(),
            [path.join(projectRoot, "engine", "cli.py")].concat(args),
            { cwd: projectRoot, env: childEnv() });
    } catch (e) { done(null, e.message); return; }
    child.stdout.on("data", function (c) { out += c.toString(); });
    child.stderr.on("data", function (c) { err += c.toString(); });
    child.on("error", function (e) { done(null, e.message); });
    child.on("close", function () {
        var res = null;
        try { res = JSON.parse(out.trim().split("\n").pop()); } catch (e) { res = null; }
        done(res, err.trim().split("\n").pop());
    });
}

function lookup(text, done) {
    runEngine(["lookup", text, "--translation", $("translation").value], function (res, err) {
        if (!res || !res.ok) {
            $("footnote").textContent = (res && res.error) || err || "could not look that up";
            done(null);
            return;
        }
        done(res);
    });
}

function applyVerse(cue, found) {
    cue.kind = found.kind || "verse";
    cue.ref = found.ref;
    cue.book = found.book;
    cue.chapter = found.chapter;
    cue.verse = found.verse;
    cue.verse_end = found.verse_end;
    cue.text = found.text;
    cue.translation = found.translation;
    cue.check = "";
    cue.enabled = true;
    cue.userSet = true;
}

/* Wrong verse on a card: type the right one. */
function editReference(cue, el) {
    var input = document.createElement("input");
    input.type = "text";
    input.value = cue.ref;
    input.className = "refedit";
    el.replaceWith(input);
    input.focus();
    input.select();
    function finish(commit) {
        var typed = input.value.trim();
        if (!commit || !typed || typed === cue.ref) { renderCues(); return; }
        input.disabled = true;
        lookup(typed, function (found) {
            if (found) {
                if (!cue.origRef) { cue.origRef = cue.ref; cue.origStart = cue.start; }
                applyVerse(cue, found);
                saveSelection();
                $("footnote").textContent = "changed to " + found.ref + " \u2014 Build to apply";
            }
            renderCues();
        });
    }
    input.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { finish(true); }
        if (e.key === "Escape") { finish(false); }
    });
    input.addEventListener("blur", function () { if (!input.disabled) { finish(true); } });
}

function playhead(done) {
    evalJSX("SermonStudio.playhead(" + JSON.stringify(state.media) + ")", function (res, raw) {
        if (!res || !res.ok) {
            $("footnote").textContent = (res && res.error) || raw || "could not read the playhead";
            done(null);
            return;
        }
        done(res);
    });
}

function setAtPlayhead(cue, which) {
    playhead(function (at) {
        if (!at) { return; }
        if (cue.origStart === undefined) { cue.origStart = cue.start; }
        if (which === "start") {
            if (at.source >= cue.end - 0.5) { $("footnote").textContent = "that is after the card ends"; return; }
            cue.start = at.source;
        } else {
            if (at.source <= cue.start + 0.5) { $("footnote").textContent = "that is before the card starts"; return; }
            cue.end = at.source;
        }
        cue.duration = cue.end - cue.start;
        cue.userTimes = true;
        cue.userSet = true;
        state.payload.cues.sort(function (a, b) { return a.start - b.start; });
        saveSelection();
        renderCues();
        $("footnote").textContent = cue.ref + " now " + (which === "start" ? "starts" : "ends") +
            " at the playhead \u2014 Build to apply";
    });
}

/* A verse the analysis missed: typed in, starting at the playhead. */
function addVerse() {
    var typed = $("addRef").value.trim();
    if (!typed || !state.payload) { return; }
    $("addGo").disabled = true;
    playhead(function (at) {
        if (!at) { $("addGo").disabled = false; return; }
        lookup(typed, function (found) {
            $("addGo").disabled = false;
            if (!found) { return; }
            var words = String(found.text || "").split(/\s+/).length,
                cue = {
                    id: "added-" + Date.now(),
                    start: at.source,
                    end: at.source + Math.min(15, Math.max(5, words / 3.3)),
                    source: "added", said: "added by hand", notes: ["added by hand"],
                    confidence: 1, precise: false
                };
            applyVerse(cue, found);
            cue.duration = cue.end - cue.start;
            state.payload.cues.push(cue);
            state.payload.cues.sort(function (a, b) { return a.start - b.start; });
            saveSelection();
            $("addRef").value = "";
            $("addRow").classList.add("hidden");
            renderCues();
            $("footnote").textContent = "added " + found.ref + " at the playhead \u2014 Build to apply";
        });
    });
}

/*
 * Your review choices survive a re-analysis: ticks, fixed verses, moved
 * in/out points and verses you added are carried onto the new results.
 */
function carryOver(previous, current) {
    if (!previous || !current || previous.media !== current.media) { return; }
    (previous.cues || []).forEach(function (o) {
        if (o.source === "added") {
            var dup = current.cues.some(function (c) {
                return c.ref === o.ref && Math.abs(c.start - o.start) < 2;
            });
            if (!dup) { current.cues.push(o); }
            return;
        }
        if (!o.userSet) { return; }
        var ref = o.origRef || o.ref,
            start = o.origStart !== undefined ? o.origStart : o.start,
            until = o.end,
            match = null, best = 1e9;
        /* The same verse, nearest in time. A re-analysis can move a card by
           half a minute - that is often the point of re-analysing - so the
           nearest one wins unless it is somewhere else in the sermon. */
        current.cues.forEach(function (c) {
            var d = Math.abs(c.start - start),
                overlaps = c.start < until && c.end > start;
            if (c.ref === ref && (d < 90 || overlaps) && d < best) { match = c; best = d; }
        });
        if (!match) { return; }
        match.enabled = o.enabled;
        match.userSet = true;
        if (o.origRef) {
            ["ref", "book", "chapter", "verse", "verse_end", "text", "translation", "origRef"]
                .forEach(function (k) { match[k] = o[k]; });
            match.check = "";
        }
        if (o.userTimes) {
            match.start = o.start;
            match.end = o.end;
            match.duration = o.end - o.start;
            match.userTimes = true;
            match.origStart = start;
        }
    });
    current.cues.sort(function (a, b) { return a.start - b.start; });
}

/*
 * YouTube subtitles and a readable transcript, beside the After Effects
 * project, timed to the edit (so to the video you export and upload).
 * Written after every analysis and every build, so they follow the edit.
 */
function subtitleTarget() {
    var dir, name;
    if (state.projectPath) {
        dir = path.dirname(state.projectPath);
        name = path.basename(state.projectPath).replace(/\.aepx?$/i, "");
    } else {
        dir = path.join(projectRoot, "out");
        name = path.basename(state.media || "sermon").replace(/\.[^.]*$/, "");
    }
    return { dir: dir, name: name };
}

function writeSubtitles() {
    if (!state.payload || !state.cuesPath) { return; }
    var target = subtitleTarget(),
        args = ["captions", state.cuesPath, "--out-dir", target.dir, "--name", target.name];
    if (state.map && state.map.length) { args.push("--map", JSON.stringify(state.map)); }
    runEngine(args, function (res, err) {
        if (!res || !res.ok) {
            $("subsLine").classList.remove("hidden");
            $("subsText").textContent = "Subtitles not written: " + ((res && res.error) || err || "unknown error");
            $("subsShow").classList.add("hidden");
            return;
        }
        state.subtitles = res;
        $("subsLine").classList.remove("hidden");
        $("subsShow").classList.remove("hidden");
        $("subsText").textContent = "Subtitles for YouTube and a transcript saved beside your project";
    });
}

function showSubtitles() {
    if (!state.subtitles) { return; }
    try { childProcess.spawn("open", ["-R", state.subtitles.srt]); } catch (e) { /* nothing to show */ }
}

/*
 * How long the full frame is held before the panel may appear at all.
 * The intro runs at the head of the comp, and coming straight out of it into
 * the slide layout is jarring: this is the breathing space.
 */
function setPanelDelay() {
    var seconds = Math.max(0, Number($("startAfter").value) || 0);
    $("startAfter").value = seconds;
    patchStyle(function (style) { style.slide.animation.startAfter = seconds; });
    $("footnote").textContent = seconds
        ? "the panel now waits " + seconds + "s into the comp \u2014 Build to apply"
        : "the panel may start straight away \u2014 Build to apply";
}

/* ------------------------------------------------------- audio cleanup */
/*
 * Noise, hum, room and level, done on this machine.
 *
 * This is the local answer to Adobe Podcast: the engine measures the
 * recording and builds a filter chain from what it finds, so nothing is
 * uploaded anywhere. Check reports what it heard, Preview plays twenty
 * seconds before and after, and the cleaned WAV goes into the comp in sync
 * with the original muted.
 */
function cleanArgs(extra) {
    var args = extra.slice(),
        room = $("cleanDereverb").value;
    args.push("--strength", $("cleanStrength").value);
    if (room === "on") { args.push("--dereverb"); }
    if (room === "off") { args.push("--no-dereverb"); }
    return args;
}

function cleanNotes(notes) {
    var list = $("cleanNotes");
    while (list.firstChild) { list.removeChild(list.firstChild); }
    (notes || []).forEach(function (note) {
        var li = document.createElement("li");
        li.textContent = note;
        list.appendChild(li);
    });
}

function cleanBusy(busy, message) {
    state.clean.busy = busy;
    ["cleanRun", "cleanPreview", "cleanCheck"].forEach(function (id) {
        $(id).disabled = busy;
    });
    if (message) { $("cleanStatus").textContent = message; }
}

/* Which recording cleanup works on: the mic file, never the camera's sound. */
function cleanSource() {
    if (!state.audio) {
        $("cleanStatus").textContent = "Pick the mic recording first \u2014 cleanup works on the "
            + "external mic, not the camera's sound.";
        return null;
    }
    return state.audio;
}

function cleanTarget() {
    var target = subtitleTarget(),
        base = path.basename(state.audio).replace(/\.[^.]*$/, "");
    return path.join(target.dir, base + " (cleaned).wav");
}

function cleanCheck() {
    var source = cleanSource();
    if (!source || state.clean.busy) { return; }
    cleanBusy(true, "Listening to the recording\u2026");
    runEngine(cleanArgs(["clean-profile", source, "--json"]), function (res, err) {
        cleanBusy(false);
        if (!res || !res.ok) {
            $("cleanStatus").textContent = (res && res.error) || err || "Could not read that recording";
            return;
        }
        state.clean.notesFor = source;
        cleanNotes(res.notes);
        $("cleanStatus").textContent = "Nothing has been changed yet \u2014 Preview to hear it, "
            + "or Clean up audio to write the file.";
    });
}

function cleanPreview() {
    var source = cleanSource();
    if (!source || state.clean.busy) { return; }
    cleanBusy(true, "Preparing twenty seconds before and after\u2026");
    var out = path.join(projectRoot, "out", "cleanup");
    runEngineStream(cleanArgs(["clean-preview", source, "--out-dir", out]), function (msg) {
        if (msg.event === "progress") { $("cleanStatus").textContent = msg.message + "\u2026"; }
    }, function (res, err) {
        cleanBusy(false);
        if (!res || res.event !== "done" || !res.ok) {
            $("cleanStatus").textContent = (res && res.message) || err || "Could not make a preview";
            return;
        }
        state.clean.preview = res;
        cleanNotes(res.notes);
        $("cleanPlayBefore").classList.remove("hidden");
        $("cleanPlayAfter").classList.remove("hidden");
        $("cleanStatus").textContent = "Twenty seconds from " + mins(res.at)
            + " \u2014 play Before and After, then change the strength if you want it harder.";
    });
}

/* The panel plays the preview itself, so judging it needs no trip to Finder. */
function cleanPlay(which) {
    var player = $("cleanAudio"), file = state.clean.preview && state.clean.preview[which];
    if (!file) { return; }
    try {
        player.pause();
        player.src = "file://" + encodeURI(file).replace(/#/g, "%23");
        player.currentTime = 0;
        player.play();
        $("cleanStatus").textContent = (which === "before" ? "Playing the original\u2026"
                                                           : "Playing the cleaned version\u2026");
    } catch (e) {
        $("cleanStatus").textContent = "Could not play that here \u2014 the files are in out/cleanup.";
    }
}

function cleanRun() {
    var source = cleanSource();
    if (!source || state.clean.busy) { return; }
    var out = cleanTarget();
    cleanBusy(true, "Cleaning the audio\u2026");
    ["cleanAdd", "cleanShow", "cleanUseForAnalysis"].forEach(function (id) {
        $(id).classList.add("hidden");
    });
    runEngineStream(cleanArgs(["clean", source, "--out", out, "--json"]), function (msg) {
        if (msg.event === "progress") {
            $("cleanStatus").textContent = msg.message
                + (msg.pct ? " (" + Math.round(msg.pct * 100) + "%)" : "\u2026");
        }
    }, function (res, err) {
        cleanBusy(false);
        if (!res || res.event !== "done" || !res.ok) {
            $("cleanStatus").textContent = (res && res.message) || err || "Could not clean that file";
            return;
        }
        state.clean.result = res;
        cleanNotes(res.notes);
        $("cleanAdd").classList.remove("hidden");
        $("cleanShow").classList.remove("hidden");
        $("cleanUseForAnalysis").classList.remove("hidden");
        $("cleanStatus").textContent = "Cleaned, level set " + (res.gain_db > 0 ? "+" : "")
            + res.gain_db.toFixed(1) + " dB to broadcast loudness \u2014 saved as "
            + path.basename(res.path);
    });
}

function cleanAdd() {
    if (!state.clean.result) { return; }
    $("cleanStatus").textContent = "Putting it into the comp\u2026";
    evalJSX("SermonStudio.addCleanAudio(" + JSON.stringify(state.clean.result.path) + ", "
            + JSON.stringify(state.audio || "") + ")", function (res, raw) {
        if (!res || !res.ok) {
            $("cleanStatus").textContent = (res && res.error) || raw || "After Effects would not add it";
            return;
        }
        $("cleanStatus").textContent = res.synced
            ? "Added in sync with your mic layer" + (res.muted ? ", and the original is muted" : "")
            : "Added \u2014 your mic layer is not in this comp, so check its position";
    });
}

function cleanShow() {
    if (!state.clean.result) { return; }
    try { childProcess.spawn("open", ["-R", state.clean.result.path]); } catch (e) { /* nothing to show */ }
}

/*
 * Transcribe the cleaned file instead of the original.
 *
 * Safe to do because the cleaned file is the same length and lines up with
 * the original sample for sample, so a sync offset already measured still
 * holds - but the analysis has to be run again for the words to change.
 */
function cleanUseForAnalysis() {
    if (!state.clean.result) { return; }
    setAudio(state.clean.result.path);
    $("cleanStatus").textContent = "Analysis will now transcribe the cleaned audio \u2014 "
        + "press Analyze sermon.";
}

/*
 * A preview belongs to the settings it was made with: once the strength or
 * the room setting changes it no longer represents what Clean up audio would
 * produce, so it stops being offered rather than playing something stale.
 */
function cleanStrengthChanged() {
    state.clean.preview = null;
    $("cleanPlayBefore").classList.add("hidden");
    $("cleanPlayAfter").classList.add("hidden");
    try { $("cleanAudio").pause(); } catch (e) { /* nothing playing */ }
    if (!state.clean.busy) {
        $("cleanStatus").textContent = "Preview to hear this setting, or Clean up audio to write it.";
    }
}

/* ---------------------------------------------------------------- slides */
/*
 * A presentation shown beside the speaker, the way a verse is.
 *
 * A session on church history leans on its deck rather than on Scripture, so
 * the deck takes the panel: one slide at a time, fading in and out, changing
 * when he moves on. Which moment that is comes from the transcript - the
 * engine reads the words on every slide when it imports the deck and finds
 * where each one is being talked about. Any deck will do: a PDF, a .pptx, or
 * a folder of exported pictures.
 */
function deckReady() {
    $("deckPlace").disabled = !(state.deck.source && state.payload && state.cuesPath
                                && !state.deck.busy);
    // A deck read in but nothing to place it against: say which way round to
    // do it, rather than leaving a disabled button with no explanation.
    if (state.deck.info && !state.deck.busy && !(state.payload && state.cuesPath)) {
        $("deckStatus").textContent = "Analyze the session first, then place the slides";
    }
}

function deckNotes(lines) {
    var list = $("deckNotes");
    while (list.firstChild) { list.removeChild(list.firstChild); }
    (lines || []).forEach(function (line) {
        var li = document.createElement("li");
        li.textContent = line;
        list.appendChild(li);
    });
}

function setDeck(source) {
    state.deck.source = source || null;
    state.deck.info = null;
    deckNotes([]);
    $("deckClear").classList.toggle("hidden", !source);
    if (!source) {
        $("deckName").textContent = "For a session carried by a presentation rather than by "
            + "verses. Export a PDF or PNGs from Canva, Keynote, PowerPoint or Google Slides.";
        $("deckStatus").textContent = "";
        deckReady();
        return;
    }
    $("deckName").textContent = path.basename(source);
    $("deckStatus").textContent = "Reading the deck\u2026";
    state.deck.busy = true;
    deckReady();
    runEngine(["deck", "import", source, "--json"], function (res, err) {
        state.deck.busy = false;
        deckReady();
        if (!res || !res.ok) {
            // The engine explains what to export instead when it cannot read
            // a deck, which is more use than "failed".
            $("deckStatus").textContent = (res && (res.error || res.message)) || err
                || "Could not read that deck";
            return;
        }
        state.deck.info = res;
        var titled = (res.slides || []).filter(function (s) { return s.title; }).length;
        deckNotes([
            res.count + " slides",
            titled + " of " + res.count + " have words on them to match against the talk"
        ]);
        $("deckStatus").textContent = state.payload
            ? "Ready \u2014 Place from transcript"
            : "Analyze the session first, then place the slides";
        deckReady();
    });
}

function chooseDeck() {
    var dialog = document.createElement("input");
    dialog.type = "file";
    dialog.accept = ".pdf,.pptx,.png,.jpg,.jpeg,.tif,.tiff,.heic,.webp";
    dialog.addEventListener("change", function () {
        if (dialog.files && dialog.files[0]) { setDeck(dialog.files[0].path); }
    });
    dialog.click();
}

/* A folder of exported pictures: the picker hands back the files, and the
   folder they share is the deck. */
function chooseDeckFolder() {
    var dialog = document.createElement("input");
    dialog.type = "file";
    dialog.webkitdirectory = true;
    dialog.addEventListener("change", function () {
        if (dialog.files && dialog.files.length) {
            setDeck(path.dirname(dialog.files[0].path));
        }
    });
    dialog.click();
}

function placeDeck() {
    if (!state.deck.source || !state.cuesPath || state.deck.busy) { return; }
    var which = (state.deck.info && state.deck.info.id) || state.deck.source;
    state.deck.busy = true;
    deckReady();
    $("deckStatus").textContent = "Finding where each slide belongs\u2026";
    runEngineStream(["deck", "place", which, "--cues", state.cuesPath, "--json"], function (msg) {
        if (msg.event === "progress") { $("deckStatus").textContent = msg.message + "\u2026"; }
    }, function (res, err) {
        state.deck.busy = false;
        deckReady();
        if (!res || res.event !== "done" || !res.ok) {
            $("deckStatus").textContent = (res && res.message) || err || "Could not place the slides";
            return;
        }
        $("deckStatus").textContent = res.slides + " slides placed"
            + (res.guessed ? ", " + res.guessed + " to check" : "")
            + " \u2014 Build to apply";
        // The cue file on disk now holds the slides as well; show them.
        loadCues(state.cuesPath);
    });
}

/* ------------------------------------------------------------- snippets */
/*
 * Vertical clips for social media. You choose the stretch - start and end at
 * the playhead - or pick one of the suggested moments; the engine prepares
 * the captions and follows the pastor, and After Effects builds the comp.
 */
function showSnipRange() {
    var s = state.snip, both = s.from !== null && s.to !== null;
    $("snipRange").textContent =
        (s.from === null ? "start ?" : tc(s.from, state.fps)) + "  \u2192  " +
        (s.to === null ? "end ?" : tc(s.to, state.fps)) +
        (both ? "  \u00b7  " + Math.round(s.to - s.from) + "s" : "");
    $("snipMake").disabled = s.busy || !both || s.to - s.from < 3;
    $("snipMake").title = both && s.to - s.from < 3 ? "A snippet needs at least 3 seconds" : "";
}

function snipAt(which) {
    playhead(function (at) {
        if (!at) { return; }
        state.snip[which] = at.compTime;
        var s = state.snip;
        if (s.from !== null && s.to !== null && s.to < s.from) {
            var swap = s.from; s.from = s.to; s.to = swap;
        }
        markPicked(null);
        showSnipRange();
    });
}

function markPicked(index) {
    var items = $("snipList").children, i;
    for (i = 0; i < items.length; i++) {
        items[i].classList.toggle("picked", i === index);
    }
}

function renderMoments() {
    var list = $("snipList");
    list.innerHTML = "";
    state.snip.moments.forEach(function (m, i) {
        var li = document.createElement("li"),
            wrap = document.createElement("div"),
            head = document.createElement("div"),
            when = document.createElement("span"),
            body = document.createElement("div"),
            why = document.createElement("div");
        when.className = "tc";
        when.textContent = tc(m.start, state.fps) + " \u2013 " + tc(m.end, state.fps) +
            "  \u00b7  " + Math.round(m.duration) + "s";
        head.className = "cuehead";
        head.appendChild(when);
        body.className = "body";
        body.textContent = "\u201c" + m.opening + "\u2026\u201d";
        why.className = "snipwhy";
        why.textContent = (m.why || []).join(" \u00b7 ");
        wrap.className = "cue";
        wrap.appendChild(head);
        wrap.appendChild(body);
        wrap.appendChild(why);
        li.appendChild(wrap);
        li.title = "Use this stretch - you can still move the start and end";
        li.addEventListener("click", function () {
            state.snip.from = m.start;
            state.snip.to = m.end;
            markPicked(i);
            showSnipRange();
            cs.evalScript(freshScript() + "SermonStudio.gotoComp(" + m.start + ")");
        });
        list.appendChild(li);
    });
}

function suggestMoments() {
    if (!state.payload || !state.cuesPath) { return; }
    $("snipStatus").textContent = "Looking for moments\u2026";
    var args = ["snippet-suggest", state.cuesPath];
    if (state.map && state.map.length) { args.push("--map", JSON.stringify(state.map)); }
    runEngine(args, function (res, err) {
        if (!res || !res.ok) {
            $("snipStatus").textContent = (res && res.error) || err || "Could not suggest moments";
            return;
        }
        state.snip.moments = res.moments || [];
        renderMoments();
        $("snipStatus").textContent = state.snip.moments.length
            ? "Click one to use it, or choose your own start and end"
            : "No clear moments found - choose your own start and end";
    });
}

/* Like runEngine, but passes on the progress lines as they come. */
function runEngineStream(args, onEvent, done) {
    var child, buffer = "", last = null, err = "";
    try {
        child = childProcess.spawn(findPython(),
            [path.join(projectRoot, "engine", "cli.py")].concat(args),
            { cwd: projectRoot, env: childEnv() });
    } catch (e) { done(null, e.message); return; }
    child.stdout.on("data", function (chunk) {
        buffer += chunk.toString();
        var lines = buffer.split("\n");
        buffer = lines.pop();
        lines.forEach(function (line) {
            var msg = null;
            try { msg = JSON.parse(line); } catch (e) { return; }
            last = msg;
            onEvent(msg);
        });
    });
    child.stderr.on("data", function (c) { err += c.toString(); });
    child.on("error", function (e) { done(null, e.message); });
    child.on("close", function () {
        if (buffer.trim()) { try { last = JSON.parse(buffer); onEvent(last); } catch (e) { /* partial */ } }
        done(last, err.trim().split("\n").pop());
    });
}

function makeSnippet() {
    var s = state.snip;
    if (s.busy || s.from === null || s.to === null || !state.cuesPath) { return; }
    var stem = path.basename(state.cuesPath).replace(/\.cues\.json$/, ""),
        out = path.join(projectRoot, "out", "snippets", stem + "-" + Math.round(s.from) + ".json"),
        args = ["snippet", state.cuesPath, "--from", String(s.from), "--to", String(s.to), "--out", out];
    if (state.map && state.map.length) { args.push("--map", JSON.stringify(state.map)); }
    s.busy = true;
    showSnipRange();
    $("snipStatus").textContent = "Preparing captions\u2026";
    runEngineStream(args, function (msg) {
        if (msg.event === "progress") { $("snipStatus").textContent = msg.message + "\u2026"; }
    }, function (res, err) {
        if (!res || res.event !== "done") {
            s.busy = false;
            showSnipRange();
            $("snipStatus").textContent = (res && res.message) || err || "Could not prepare the snippet";
            return;
        }
        $("snipStatus").textContent = "Building in After Effects\u2026";
        evalJSX("SermonStudio.buildSnippet(" + JSON.stringify(res.path) + ")", function (built, raw) {
            s.busy = false;
            showSnipRange();
            $("snipStatus").textContent = (built && built.ok)
                ? "Built \u201c" + built.comp + "\u201d \u2014 it is open in the viewer"
                : ((built && built.error) || raw || "Build failed");
        });
    });
}

/*
 * Pick up an analysis that has been re-run since this panel loaded.
 *
 * Build works from what the panel holds, so a panel left open through a
 * re-analysis would quietly build the old timings over the new ones. Your
 * review choices are carried across, as they are after any re-analysis.
 */
function reloadIfNewer() {
    if (!state.payload || !state.cuesPath || state.building || state.child) { return; }
    var fresh;
    try {
        fresh = JSON.parse(fs.readFileSync(state.cuesPath, "utf8"));
    } catch (e) { return; }
    if (!fresh.generated || fresh.generated === state.payload.generated) { return; }
    var previous = state.payload;
    state.payload = fresh;
    carryOver(previous, state.payload);
    saveSelection();
    renderCues();
    $("footnote").textContent = "loaded the newer analysis of " + fresh.generated +
        " \u2014 Build to apply";
}

/* Every review change is saved at once, so closing the panel loses nothing. */
function saveSelection() {
    if (!state.payload || !state.cuesPath) { return; }
    try {
        fs.writeFileSync(state.cuesPath.replace(/\.json$/, ".selected.json"),
                         JSON.stringify(state.payload, null, 2), "utf8");
    } catch (e) { /* the build writes it again anyway */ }
}

function updateFootnote() {
    if (!state.payload) { return; }
    var on = state.payload.cues.filter(function (c) { return c.enabled !== false; }).length,
        look = state.payload.cues.filter(function (c) { return c.check && c.enabled !== false; }).length;
    $("footnote").textContent = on + " of " + state.payload.cues.length + " selected" +
        (look ? " · " + look + " to look at" : " · nothing flagged");
    $("onlyFlagged").textContent = "Flagged" + (look ? " (" + look + ")" : "");
    $("build").disabled = on === 0;
}

function setAll(value) {
    if (!state.payload) { return; }
    state.payload.cues.forEach(function (c) { c.enabled = value; c.userSet = true; });
    saveSelection();
    renderCues();
}

/* ----------------------------------------------------------------- build */
/* Whether this session is carried by a deck rather than by verses. */
function hasSlides() {
    var cues = (state.payload && state.payload.cues) || [], i;
    for (i = 0; i < cues.length; i++) {
        if (cues[i].kind === "slide" && cues[i].enabled !== false) { return true; }
    }
    return false;
}

function build() {
    if (!state.payload) { return; }
    /* The slide layout's still parts are drawn once, at this comp's size, so
       After Effects does not rebuild them on every frame. */
    if ($("layout").value === "slide" && state.compSize && state.compSize[0]) {
        $("footnote").textContent = "Preparing the backdrop\u2026";
        var args = ["furniture", "--width", String(state.compSize[0]),
                    "--height", String(state.compSize[1])];
        if (hasSlides()) { args.push("--deck"); }
        runEngine(args, function (res) {
            state.furniture = (res && res.ok) ? res.path : null;
            buildNow();
        });
        return;
    }
    buildNow();
}

function buildNow() {
    if (!state.payload) { return; }
    var outPath = state.cuesPath.replace(/\.json$/, ".selected.json");
    try {
        /* Style is read fresh so a font change does not need a re-analysis. */
        state.payload.style = readStyle();
        /*
         * A session built on a presentation gets its own arrangement: the
         * panel takes the frame's full height and the camera a narrower
         * column, because a slide full of text cannot be read at the size a
         * verse needs. Sermons are left exactly as they were - the backdrop
         * drawn above matches whichever of the two this is.
         */
        if (hasSlides() && state.payload.style.slide.deck) {
            state.payload.style.slide.camera = state.payload.style.slide.deck.camera;
            state.payload.style.slide.panel = state.payload.style.slide.deck.panel;
        }
        state.payload.style.slide.furnitureImage = state.furniture || null;
    } catch (e) { /* keep whatever the analysis captured */ }
    try {
        fs.writeFileSync(outPath, JSON.stringify(state.payload, null, 2), "utf8");
    } catch (e) {
        $("footnote").textContent = "Could not write cue file: " + e.message;
        return;
    }
    $("build").disabled = true;
    state.building = true;
    $("footnote").textContent = "Building… 0%";
    state.shownPct = 0;
    state.startedAt = Date.now();
    $("progress").classList.remove("hidden");
    $("cancel").classList.add("hidden");
    setProgress(0);
    setStatus("Setting up in After Effects");

    /*
     * After Effects cannot repaint the panel while a script runs, so the
     * build is driven in small steps: set up, a few verses at a time, then
     * finish. Between steps the panel redraws and the percentage moves.
     */
    var layout = $("layout").value,
        noun = layout === "slide" ? "Verse " : "Card ",
        perStep = 3;

    function show(fraction, label) {
        setProgress(fraction);
        setStatus(label);
        $("footnote").textContent = "Building… " + Math.floor(state.shownPct) + "%";
    }

    function failed(res, raw) {
        state.building = false;
        $("build").disabled = false;
        $("cancel").classList.remove("hidden");
        $("progress").classList.add("hidden");
        $("footnote").textContent = (res && res.error) || raw || "Build failed";
    }

    function step(total) {
        evalJSX("SermonStudio.buildStep(" + perStep + ")", function (r, raw) {
            if (!r || !r.ok) { failed(r, raw); return; }
            show(0.12 + 0.85 * (total ? r.done / total : 1), noun + r.done + " of " + total);
            if (r.done < r.total) {
                window.setTimeout(function () { step(total); }, 0);
            } else {
                show(0.98, "Finishing");
                evalJSX("SermonStudio.buildFinish()", buildDone);
            }
        });
    }

    evalJSX(
        "SermonStudio.buildStart(" + JSON.stringify(outPath) + ", " +
            JSON.stringify(JSON.stringify({ layout: layout })) + ")",
        function (res, raw) {
            if (!res || !res.ok) { failed(res, raw); return; }
            show(0.12, noun + "0 of " + res.total);
            followPreacher(res, function () { step(res.total); });
        }
    );
}

/*
 * Keep the preacher in the middle of the camera inset.
 *
 * The engine looks through the recording at each panel section and works out
 * where he is; After Effects then keyframes the camera to match. If the
 * recording is not to hand (an archived sermon, the drive unplugged), the
 * build carries on with the framing fixed, and says so.
 */
function followPreacher(res, done) {
    var wants = $("layout").value === "slide" && res.windows && res.windows.length
        && state.media && readStyleSafe("slide.camera.follow") !== false;
    if (!wants) { done(); return; }
    var stem = path.basename(state.cuesPath).replace(/\.cues\.selected\.json$/, ""),
        out = path.join(projectRoot, "out", "follow", stem + ".json"),
        args = ["follow", "--media", state.media, "--windows", JSON.stringify(res.windows),
                "--crop-width", String(res.cropWidth), "--out", out];
    if (state.map && state.map.length) { args.push("--map", JSON.stringify(state.map)); }
    setStatus("Following the preacher\u2026");
    runEngineStream(args, function (msg) {
        if (msg.event === "progress" && msg.message) { setStatus(msg.message); }
    }, function (result, err) {
        if (!result || result.event !== "done") {
            showWarning("The camera framing was left fixed: " +
                ((result && result.message) || err || "the recording could not be read") +
                " Connect the drive with the sermon footage and Build again to have the " +
                "preacher followed.");
            done();
            return;
        }
        evalJSX("SermonStudio.applyCameraFollow(" + JSON.stringify(out) + ")", function () {
            done();
        });
    });
}

/* Whether the camera inset should keep the preacher centred. */
function setFollowPreacher() {
    var on = $("followPreacher").checked;
    patchStyle(function (style) { style.slide.camera.follow = on; });
    $("footnote").textContent = on
        ? "the camera will follow the preacher \u2014 Build to apply"
        : "the camera will stay put \u2014 Build to apply";
}

/* One setting out of the saved style, without throwing if it is not there. */
function readStyleSafe(dotted) {
    try {
        var value = readStyle(), parts = dotted.split("."), i;
        for (i = 0; i < parts.length; i++) { value = value[parts[i]]; }
        return value;
    } catch (e) { return undefined; }
}

function buildDone(res, raw) {
    state.building = false;
    $("build").disabled = false;
    $("cancel").classList.remove("hidden");
    if (res && res.ok) {
        state.shownPct = 100;
        setProgress(1);
        setStatus("Built");
        $("eta").textContent = "";
        window.setTimeout(function () { $("progress").classList.add("hidden"); }, 2500);
    } else {
        $("progress").classList.add("hidden");
    }
    if (res && res.ok && res.stack) {
        writeLog("layers in " + res.comp + " (" + res.layers + "), " +
                 (res.misplaced || 0) + " not placed where intended, " +
                 (res.emptyPanel || 0) + " empty-panel spots:\n  " +
                 res.stack.join("\n  ") + "\n", "last-build.log");
    }
    if (res && res.ok) {
        var note = "Built " + res.built +
            (res.layout === "slide" ? " slides in " : " cards in ") + res.comp;
        if (res.skipped) {
            note += " · " + res.skipped + " skipped (cut from the edit)";
        }
        if (res.emptyPanel) {
            note += " · " + res.emptyPanel + " spot(s) where the panel has no verse - see last-build.log";
        }
        if (res.warnings && res.warnings.length) {
            showWarning(res.warnings.join(" "));
        }
        $("footnote").textContent = note;
        writeSubtitles();
    } else {
        $("footnote").textContent = (res && res.error) || raw || "Build failed";
    }
}

/* ---------------------------------------------------------------- browse */
function browse() {
    var dialog = document.createElement("input");
    dialog.type = "file";
    dialog.accept = "video/*,audio/*";
    dialog.addEventListener("change", function () {
        if (dialog.files && dialog.files[0]) {
            state.media = dialog.files[0].path;
            $("mediaName").textContent = dialog.files[0].name;
            updateReadiness();
        }
    });
    dialog.click();
}

var AUDIO_EXT = /\.(wav|mp3|m4a|aac|aif|aiff|flac|caf)$/i;

/*
 * Look for the mic recording on disk.
 *
 * The sound desk hands over a WAV named after the service, and it usually
 * lands in Downloads or beside the footage long before anyone imports it into
 * After Effects. Matching on the filename finds it without being told.
 */
function findAudioNearby(videoPath) {
    if (!videoPath) { return []; }

    var videoDir = path.dirname(videoPath),
        stem = path.basename(videoPath).replace(/\.[^.]*$/, ""),
        home = process.env.HOME || "",
        dirs = [videoDir, path.dirname(videoDir),
                path.join(home, "Downloads"), path.join(home, "Desktop"),
                path.join(home, "Movies"), path.join(home, "Music")],
        wanted = tokensOf(stem),
        found = [], seen = {}, i, j, names, full;

    /* One level below the footage folder catches an "audio" subfolder. */
    try {
        fs.readdirSync(videoDir).forEach(function (entry) {
            var sub = path.join(videoDir, entry);
            try {
                if (fs.statSync(sub).isDirectory() && dirs.length < 12) { dirs.push(sub); }
            } catch (e) { /* unreadable */ }
        });
    } catch (e) { /* unreadable */ }

    for (i = 0; i < dirs.length; i++) {
        try { names = fs.readdirSync(dirs[i]); } catch (e) { continue; }
        for (j = 0; j < names.length && j < 400; j++) {
            if (!AUDIO_EXT.test(names[j]) || names[j].indexOf("._") === 0) { continue; }
            full = path.join(dirs[i], names[j]);
            if (seen[full] || full === videoPath) { continue; }
            seen[full] = true;
            var score = nameOverlap(wanted, tokensOf(names[j].replace(/\.[^.]*$/, "")));
            if (score > 0) { found.push({ path: full, name: names[j], score: score }); }
        }
    }

    found.sort(function (a, b) { return b.score - a.score; });
    return found.slice(0, 6);
}

function tokensOf(text) {
    var parts = String(text).toLowerCase().split(/[^a-z0-9]+/), out = [], i;
    for (i = 0; i < parts.length; i++) {
        if (parts[i].length > 1) { out.push(parts[i]); }
    }
    return out;
}

/* How much of the video's name the audio file shares. 1 means identical. */
function nameOverlap(a, b) {
    if (!a.length || !b.length) { return 0; }
    var hits = 0, i, j;
    for (i = 0; i < a.length; i++) {
        for (j = 0; j < b.length; j++) {
            if (a[i] === b[j]) { hits++; break; }
        }
    }
    return hits / a.length;
}

/*
 * Offer whatever audio is already in the project, since the mic recording is
 * usually imported before anyone opens this panel. A layer sitting in the comp
 * or an item selected in the Project panel is picked automatically.
 */
function populateAudio(info) {
    var select = $("audioPick"),
        seen = {}, items = [], i, chosen = state.audio, auto = null;

    (info.audioLayers || []).forEach(function (a) {
        if (!seen[a.path]) { seen[a.path] = true; items.push({ name: a.name + "  (in comp)", path: a.path }); }
    });
    (info.projectAudio || []).forEach(function (a) {
        if (!seen[a.path]) {
            seen[a.path] = true;
            items.push({ name: a.name + (a.selected ? "  (selected)" : ""), path: a.path });
        }
        if (a.selected && !auto) { auto = a.path; }
    });
    findAudioNearby(state.media).forEach(function (a) {
        if (!seen[a.path]) {
            seen[a.path] = true;
            items.push({
                name: a.name + (a.score >= 0.999 ? "  (matches the video)" : "  (found on disk)"),
                path: a.path,
                score: a.score
            });
            if (!auto && a.score >= 0.999) { auto = a.path; }
        }
    });
    if (state.audio && !seen[state.audio]) {
        items.unshift({ name: state.audio.replace(/^.*[\/\\]/, "") + "  (file)", path: state.audio });
    }

    /* Only one candidate, or one explicitly selected: use it without asking. */
    if (!chosen) {
        if (auto) { chosen = auto; }
        else if (items.length === 1) { chosen = items[0].path; }
    }

    var signature = items.map(function (a) { return a.path; }).join("|") + "::" + (chosen || "");
    if (signature === state.audioSignature) { return; }
    state.audioSignature = signature;

    select.innerHTML = "";
    var none = document.createElement("option");
    none.value = "";
    none.textContent = "choose the mic recording…";
    select.appendChild(none);
    for (i = 0; i < items.length; i++) {
        var option = document.createElement("option");
        option.value = items[i].path;
        option.textContent = items[i].name;
        select.appendChild(option);
    }
    select.value = chosen || "";
    setAudio(chosen || null, true);
}

function setAudio(filePath, quiet) {
    state.audio = filePath || null;
    if (!quiet) { state.alignment = null; $("syncPanel").classList.add("hidden"); }
    $("clearAudio").classList.toggle("hidden", !filePath);
    $("audioName").textContent = filePath
        ? "This is transcribed and lined up with the video automatically."
        : "Pick the external mic recording for this service. Anything imported " +
          "into your project is listed here.";
    updateReadiness();
}

function browseAudio() {
    var dialog = document.createElement("input");
    dialog.type = "file";
    dialog.accept = "audio/*";
    dialog.addEventListener("change", function () {
        if (dialog.files && dialog.files[0]) {
            state.audioSignature = null;
            setAudio(dialog.files[0].path);
        }
    });
    dialog.click();
}

function clearAudio() {
    state.audioSignature = null;
    $("audioPick").value = "";
    setAudio(null);
}

/* ------------------------------------------------------------------ init */
function init() {
    try {
        fs = nodeRequire("fs");
        path = nodeRequire("path");
        childProcess = nodeRequire("child_process");
        process = window.process || nodeRequire("process");
    } catch (e) {
        $("hostline").textContent = e.message;
        return;
    }

    extensionRoot = cs.getSystemPath(SystemPath.EXTENSION);
    projectRoot = resolveProjectRoot();

    $("settingsToggle").addEventListener("click", function () {
        $("settings").classList.toggle("hidden");
    });
    $("refresh").addEventListener("click", probe);
    $("browse").addEventListener("click", browse);
    $("browseAudio").addEventListener("click", browseAudio);
    $("audioPick").addEventListener("change", function () {
        setAudio($("audioPick").value || null);
    });
    $("clearAudio").addEventListener("click", clearAudio);
    $("applySync").addEventListener("click", applySync);
    $("testKey").addEventListener("click", testKey);
    loadPanelSettings();
    $("layout").addEventListener("change", function () { onLayoutChange(); savePanelSettings(); });
    $("translation").addEventListener("change", savePanelSettings);
    state.colourReaders = [
        bindColour("bgColor",
            function (s) { return s.slide.background.fill; },
            function (s, c) { s.slide.background.fill = c; }),
        bindColour("borderColor",
            function (s) { return s.slide.camera.border.color; },
            function (s, c) {
                /* The camera frame and the panel outline are meant to read as
                   the same line, so one control sets both. */
                s.slide.camera.border.color = c;
                s.slide.panel.strokeColor = c;
            }),
        bindColour("panelColor",
            function (s) { return s.slide.panel.fill; },
            function (s, c) { s.slide.panel.fill = c; }),
        bindColour("verseColor",
            function (s) { return s.slide.verse.color; },
            function (s, c) {
                s.slide.verse.color = c;
                s.slide.reference.color = c;
            })
    ];
    $("logoWidth").addEventListener("change", function () {
        patchStyle(function (style) {
            style.slide.logo.width = Number($("logoWidth").value) || 180;
        });
    });
    /*
     * How long the full frame is held before the panel may appear at all.
     * The intro runs at the head of the comp, and coming straight out of it
     * into the slide layout is jarring: this is the breathing space.
     */
    $("startAfter").addEventListener("change", setPanelDelay);
    $("followPreacher").addEventListener("change", setFollowPreacher);
    $("borderWidth").addEventListener("change", function () {
        patchStyle(function (style) {
            var width = Number($("borderWidth").value) || 0;
            style.slide.camera.border.width = width;
            style.slide.panel.strokeWidth = width;
        });
    });
    $("chooseBg").addEventListener("click", function () { pickAsset("background"); });
    $("clearBg").addEventListener("click", function () { setAsset("background", null); });
    $("chooseLogo").addEventListener("click", function () { pickAsset("logo"); });
    $("clearLogo").addEventListener("click", function () { setAsset("logo", null); });
    loadSlideSettings();
    $("esvLink").addEventListener("click", function (e) {
        e.preventDefault();
        cs.openURLInDefaultBrowser("https://api.esv.org/account/create-application/");
    });
    loadKey();
    $("analyze").addEventListener("click", analyze);
    $("cancel").addEventListener("click", cancel);
    $("build").addEventListener("click", build);
    $("selectAll").addEventListener("click", function () { setAll(true); });
    $("onlyFlagged").addEventListener("click", function () {
        state.onlyFlagged = !state.onlyFlagged;
        $("onlyFlagged").classList.toggle("on", state.onlyFlagged);
        renderCues();
    });
    $("selectNone").addEventListener("click", function () { setAll(false); });
    $("addVerse").addEventListener("click", function () {
        $("addRow").classList.toggle("hidden");
        if (!$("addRow").classList.contains("hidden")) { $("addRef").focus(); }
    });
    $("addGo").addEventListener("click", addVerse);
    $("subsShow").addEventListener("click", function (e) { e.preventDefault(); showSubtitles(); });
    $("deckChoose").addEventListener("click", chooseDeck);
    $("deckFolder").addEventListener("click", chooseDeckFolder);
    $("deckClear").addEventListener("click", function () { setDeck(null); });
    $("deckPlace").addEventListener("click", placeDeck);
    $("cleanCheck").addEventListener("click", cleanCheck);
    $("cleanPreview").addEventListener("click", cleanPreview);
    $("cleanRun").addEventListener("click", cleanRun);
    $("cleanAdd").addEventListener("click", cleanAdd);
    $("cleanShow").addEventListener("click", cleanShow);
    $("cleanUseForAnalysis").addEventListener("click", cleanUseForAnalysis);
    $("cleanPlayBefore").addEventListener("click", function () { cleanPlay("before"); });
    $("cleanPlayAfter").addEventListener("click", function () { cleanPlay("after"); });
    $("cleanStrength").addEventListener("change", cleanStrengthChanged);
    $("cleanDereverb").addEventListener("change", cleanStrengthChanged);
    $("snipIn").addEventListener("click", function () { snipAt("from"); });
    $("snipOut").addEventListener("click", function () { snipAt("to"); });
    $("snipSuggest").addEventListener("click", suggestMoments);
    $("snipMake").addEventListener("click", makeSnippet);
    showSnipRange();
    $("addRef").addEventListener("keydown", function (e) {
        if (e.key === "Enter") { addVerse(); }
        if (e.key === "Escape") { $("addRow").classList.add("hidden"); }
    });

    loadHostScript();
    window.setInterval(pollHost, 1000);
}

window.addEventListener("load", init);
