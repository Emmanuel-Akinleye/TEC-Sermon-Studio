/*
 * TEC Sermon Studio — builds verse lower-thirds in After Effects from a cue file.
 *
 * Everything it creates is ordinary native layers: shape plates, box text and
 * a controlling null per cue. Nothing is baked, so any card can be restyled,
 * retimed or deleted by hand afterwards without going back to the analysis.
 *
 * Entry points:
 *   SermonStudio.buildFromFile(path, optionsJson)   called by the CEP panel
 *   SermonStudio.probe()                            comp + media info for the panel
 * Running the file directly from File > Scripts prompts for a cue file.
 */

/*global app, File, Folder, CompItem, AVLayer, TextLayer, ShapeLayer,
  ParagraphJustification, KeyframeEase, PREMULTIPLIED_COLOR, BlendingMode */

(function (global) {
    "use strict";

    var VC = {};
    VC.version = "1.0.0";

    /* ------------------------------------------------------------------ */
    /* JSON — ExtendScript has no parser of its own.                       */
    /* ------------------------------------------------------------------ */
    var JSONLite = (function () {
        function Parser(text) { this.s = text; this.i = 0; }
        Parser.prototype.error = function (msg) {
            throw new Error("Cue file is not valid JSON: " + msg + " at character " + this.i);
        };
        Parser.prototype.ws = function () {
            while (this.i < this.s.length && " \t\n\r".indexOf(this.s.charAt(this.i)) >= 0) { this.i++; }
        };
        Parser.prototype.value = function () {
            this.ws();
            var c = this.s.charAt(this.i);
            if (c === "{") { return this.object(); }
            if (c === "[") { return this.array(); }
            if (c === '"') { return this.string(); }
            if (c === "t" && this.s.substr(this.i, 4) === "true") { this.i += 4; return true; }
            if (c === "f" && this.s.substr(this.i, 5) === "false") { this.i += 5; return false; }
            if (c === "n" && this.s.substr(this.i, 4) === "null") { this.i += 4; return null; }
            return this.number();
        };
        Parser.prototype.object = function () {
            var obj = {}, c;
            this.i++;
            this.ws();
            if (this.s.charAt(this.i) === "}") { this.i++; return obj; }
            while (true) {
                this.ws();
                if (this.s.charAt(this.i) !== '"') { this.error("expected key"); }
                var key = this.string();
                this.ws();
                if (this.s.charAt(this.i) !== ":") { this.error("expected ':'"); }
                this.i++;
                obj[key] = this.value();
                this.ws();
                c = this.s.charAt(this.i);
                if (c === ",") { this.i++; continue; }
                if (c === "}") { this.i++; return obj; }
                this.error("expected ',' or '}'");
            }
        };
        Parser.prototype.array = function () {
            var arr = [], c;
            this.i++;
            this.ws();
            if (this.s.charAt(this.i) === "]") { this.i++; return arr; }
            while (true) {
                arr.push(this.value());
                this.ws();
                c = this.s.charAt(this.i);
                if (c === ",") { this.i++; continue; }
                if (c === "]") { this.i++; return arr; }
                this.error("expected ',' or ']'");
            }
        };
        Parser.prototype.string = function () {
            var out = "", c, code;
            this.i++;
            while (this.i < this.s.length) {
                c = this.s.charAt(this.i++);
                if (c === '"') { return out; }
                if (c === "\\") {
                    c = this.s.charAt(this.i++);
                    if (c === "n") { out += "\n"; }
                    else if (c === "t") { out += "\t"; }
                    else if (c === "r") { out += "\r"; }
                    else if (c === "b") { out += "\b"; }
                    else if (c === "f") { out += "\f"; }
                    else if (c === "u") {
                        code = parseInt(this.s.substr(this.i, 4), 16);
                        this.i += 4;
                        out += String.fromCharCode(code);
                    } else { out += c; }
                } else { out += c; }
            }
            this.error("unterminated string");
        };
        Parser.prototype.number = function () {
            var start = this.i;
            if (this.s.charAt(this.i) === "-") { this.i++; }
            while (this.i < this.s.length && "0123456789+-.eE".indexOf(this.s.charAt(this.i)) >= 0) { this.i++; }
            var n = parseFloat(this.s.substring(start, this.i));
            if (isNaN(n)) { this.error("bad number"); }
            return n;
        };

        function stringify(v) {
            var t = typeof v, i, parts, k;
            if (v === null || t === "undefined") { return "null"; }
            if (t === "number") { return isFinite(v) ? String(v) : "null"; }
            if (t === "boolean") { return v ? "true" : "false"; }
            if (t === "string") {
                return '"' + v.replace(/\\/g, "\\\\").replace(/"/g, '\\"')
                    .replace(/\n/g, "\\n").replace(/\r/g, "\\r").replace(/\t/g, "\\t") + '"';
            }
            if (v instanceof Array) {
                parts = [];
                for (i = 0; i < v.length; i++) { parts.push(stringify(v[i])); }
                return "[" + parts.join(",") + "]";
            }
            parts = [];
            for (k in v) {
                if (v.hasOwnProperty(k) && typeof v[k] !== "function") {
                    parts.push(stringify(String(k)) + ":" + stringify(v[k]));
                }
            }
            return "{" + parts.join(",") + "}";
        }

        return {
            parse: function (text) { return new Parser(String(text)).value(); },
            stringify: stringify
        };
    }());
    VC.JSON = JSONLite;

    /* ------------------------------------------------------------------ */
    /* small helpers                                                       */
    /* ------------------------------------------------------------------ */
    function rgb(arr) {
        return [arr[0] / 255, arr[1] / 255, arr[2] / 255];
    }

    function readFile(path) {
        var f = new File(path);
        if (!f.exists) { throw new Error("Cue file not found: " + path); }
        f.encoding = "UTF-8";
        f.open("r");
        var text = f.read();
        f.close();
        return text;
    }

    function pad2(n) { return (n < 10 ? "0" : "") + n; }

    function resolveFont(candidates) {
        var have = {}, i, all, sawList = false;
        try {
            if (app.fonts && app.fonts.allFonts) {
                all = app.fonts.allFonts;
                for (i = 0; i < all.length; i++) {
                    have[all[i].postScriptName] = true;
                }
                sawList = all.length > 0;
            }
        } catch (e) { /* this host does not expose its fonts */ }

        if (sawList) {
            for (i = 0; i < candidates.length; i++) {
                if (have[candidates[i]]) { return candidates[i]; }
            }
        }
        /* The panel writes a PostScript name read out of an installed font
           file, so the first candidate is the one that was actually chosen.
           Preferring the last would silently substitute the safety net. */
        return candidates[0];
    }

    /* Split a PostScript name such as "Montserrat-SemiBold" into its parts. */
    function describeFont(font) {
        var ps = "", family = "", style = "", cut;
        try {
            ps = String(font.postScriptName || "");
            family = String(font.familyName || font.fontFamily || font.family || "");
            style = String(font.styleName || font.fontStyle || font.style || "");
        } catch (e) { /* read what we can */ }
        if (!ps) { return null; }
        if (!family || family === "undefined") {
            cut = ps.indexOf("-");
            if (cut > 0) {
                family = ps.substring(0, cut);
                if (!style || style === "undefined") { style = ps.substring(cut + 1); }
            } else {
                family = ps.replace(/(?:MT|PS|Std|Pro)+$/, "");
            }
        }
        if (!style || style === "undefined") { style = "Regular"; }
        /* Foundry suffixes ride along on the style too: "BoldMT" is "Bold". */
        style = style.replace(/(?:MT|PS)+$/, "") || "Regular";
        /* "SemiBold" reads better as "Semi Bold" when matching weights. */
        return { ps: ps, family: family, style: style };
    }

    function sameMedia(layer, mediaPath) {
        var src = layer.source, name;
        if (!src) { return false; }
        name = decodeURIComponent(String(mediaPath).replace(/^.*[\/\\]/, ""));
        if (src.mainSource && src.mainSource.file) {
            if (src.mainSource.file.fsName === mediaPath) { return true; }
            return decodeURIComponent(src.mainSource.file.name) === name;
        }
        return src.name === name;
    }

    /*
     * Every place the recorded video appears in the edit.
     *
     * The sermon file is one continuous recording, but in the comp it is cut,
     * trimmed, sometimes split into several pieces and sometimes buried in a
     * precomp. Each piece maps source time to comp time differently, so one
     * global offset cannot work. An instance says: comp time =
     * offset + sourceTime * scale, valid while comp time is inside [tMin, tMax].
     */
    function collectInstances(comp, mediaPath, depth, offset, scale, winMin, winMax, out) {
        var i, layer, lScale, lOffset, aMin, aMax, inner;
        if (depth > 2) { return out; }
        for (i = 1; i <= comp.numLayers; i++) {
            layer = comp.layer(i);
            if (!(layer instanceof AVLayer) || !layer.source) { continue; }

            lScale = (layer.stretch || 100) / 100;
            /* This layer's own mapping, lifted into the outer comp. */
            lOffset = offset + layer.startTime * scale;
            aMin = Math.max(winMin, offset + layer.inPoint * scale);
            aMax = Math.min(winMax, offset + layer.outPoint * scale);
            if (aMax <= aMin) { continue; }

            if (sameMedia(layer, mediaPath)) {
                out.push({
                    offset: lOffset,
                    scale: lScale * scale,
                    tMin: aMin,
                    tMax: aMax,
                    name: layer.name
                });
            } else if (layer.source instanceof CompItem) {
                collectInstances(layer.source, mediaPath, depth + 1,
                    lOffset, lScale * scale, aMin, aMax, out);
            }
        }
        return out;
    }

    function mediaInstances(comp, mediaPath) {
        if (!mediaPath) { return []; }
        var found = collectInstances(comp, mediaPath, 0, 0, 1, 0, comp.duration, []);
        found.sort(function (a, b) { return a.tMin - b.tMin; });
        return found;
    }

    /* Where a moment in the recording lands in the edit, or null if it was cut. */
    function sourceToComp(instances, seconds) {
        var i, inst, t;
        for (i = 0; i < instances.length; i++) {
            inst = instances[i];
            t = inst.offset + seconds * inst.scale;
            if (t >= inst.tMin - 0.0005 && t <= inst.tMax + 0.0005) {
                return { time: t, instance: inst };
            }
        }
        return null;
    }

    function folder(name) {
        var i, item, legacy = null;
        for (i = 1; i <= app.project.numItems; i++) {
            item = app.project.item(i);
            if (item instanceof FolderItem && item.name === name) { return item; }
            if (item instanceof FolderItem && item.name === OLD_TAG) { legacy = item; }
        }
        /* The folder this project already has, under its old name. */
        if (legacy) { legacy.name = name; return legacy; }
        return app.project.items.addFolder(name);
    }

    /*
     * A cue's window in comp time. Returns null when that moment is not in
     * the edit at all, which is the desired answer: if you cut the section
     * where he read the verse, you do not want a card for it.
     */
    function mapCue(instances, cue) {
        var a, b, inst, inTime, outTime;
        if (!instances.length) {
            return { inTime: cue.start, outTime: cue.end, trimmed: false };
        }
        a = sourceToComp(instances, cue.start);
        b = sourceToComp(instances, cue.end);
        if (!a && !b) { return null; }
        inst = (a || b).instance;
        inTime = a ? a.time : inst.tMin;
        outTime = b ? b.time : inst.tMax;
        if (a && b && a.instance !== b.instance) { outTime = a.instance.tMax; }
        if (outTime - inTime < 0.4) { return null; }
        return { inTime: inTime, outTime: outTime, trimmed: !(a && b) };
    }

    /*
     * Every comp Sermon Studio builds is tagged in its comment with the sermon
     * comp it was built from and which layout it holds. That is how a rebuild
     * finds and reuses it — even after it has been renamed — and how clicking
     * Build while *looking at* a built comp still means the sermon behind it.
     */
    var TAG = "TEC Sermon Studio";
    /* Built before the rename: still ours, still overwritten by a rebuild. */
    var OLD_TAG = "TEC Sermon Studio";

    function tagFor(target, kind) {
        return TAG + " · source:" + target.id + " · layout:" + kind;
    }

    function builtFrom(comp) {
        var text = String(comp.comment || ""), m = /source:(\d+)/.exec(text);
        return (m && (text.indexOf(TAG) === 0 || text.indexOf(OLD_TAG) === 0)) ? Number(m[1]) : null;
    }

    function compById(id) {
        var i, it;
        for (i = 1; i <= app.project.numItems; i++) {
            it = app.project.item(i);
            if (it instanceof CompItem && it.id === id) { return it; }
        }
        return null;
    }

    /* The comp Sermon Studio built from `target` for this layout, if any. */
    function findBuilt(target, kind, legacyName) {
        var i, it, byName = null;
        for (i = 1; i <= app.project.numItems; i++) {
            it = app.project.item(i);
            if (!(it instanceof CompItem)) { continue; }
            if (builtFrom(it) === target.id && String(it.comment).indexOf("layout:" + kind) >= 0) {
                return it;
            }
            /* Built before comps were tagged: fall back to the old name. */
            if (!byName && it.name === legacyName && builtFrom(it) === null) { byName = it; }
        }
        return byName;
    }

    /*
     * The composition being worked on.
     *
     * Clicking anything in the Project panel — a WAV, say — makes that the
     * active item, and the comp disappears from app.project.activeItem. So
     * remember the last comp seen, and failing that take the longest comp
     * that holds video, which on a sermon project is the edit.
     */
    function resolveComp() {
        var item = app.project.activeItem, i, it, best = null, j, hasVideo, source;
        if (item instanceof CompItem) {
            /* Looking at something Sermon Studio built: work on its sermon. */
            source = builtFrom(item);
            if (source !== null && compById(source)) { item = compById(source); }
            $.global.__vcLastCompId = item.id;
            return item;
        }
        if ($.global.__vcLastCompId) {
            for (i = 1; i <= app.project.numItems; i++) {
                it = app.project.item(i);
                if (it instanceof CompItem && it.id === $.global.__vcLastCompId) { return it; }
            }
        }
        /* Never pick a comp Sermon Studio built as the sermon. */
        for (i = 1; i <= app.project.numItems; i++) {
            it = app.project.item(i);
            if (!(it instanceof CompItem)) { continue; }
            if (builtFrom(it) !== null || /Verse Captions|Slide Layout/.test(it.name)) { continue; }
            hasVideo = false;
            for (j = 1; j <= it.numLayers && !hasVideo; j++) {
                if (it.layer(j) instanceof AVLayer && it.layer(j).source
                        && it.layer(j).source.mainSource
                        && it.layer(j).source.mainSource.file
                        && it.layer(j).hasVideo) { hasVideo = true; }
            }
            if (hasVideo && (!best || it.duration > best.duration)) { best = it; }
        }
        return best;
    }

    /* ------------------------------------------------------------------ */
    /* drawing                                                             */
    /* ------------------------------------------------------------------ */
    /*
     * Put a layer on the timeline from `from` to `to`, and make sure it stays.
     *
     * For layers without footage — text and shape layers — setting inPoint
     * can slide the whole layer rather than trim it, so a card that should
     * end at 4:42 ends at 4:30 + 4:42. Setting the in point first and the out
     * point second gives the right result either way, and the values are read
     * back and corrected in case the host still disagrees.
     *
     * Call this before adding any keyframes: sliding a layer moves them too.
     */
    /*
     * Progress for the panel. A build is one long call into After Effects,
     * so the panel cannot be told as it goes; instead a small file is
     * rewritten as each verse is laid down and the panel reads it.
     */
    function progress(fraction, label) {
        var f, now = new Date().getTime();
        if (!progress.file) { return; }
        if (fraction < 1 && progress.last && now - progress.last < 150) { return; }
        progress.last = now;
        try {
            f = new File(progress.file);
            f.encoding = "UTF-8";
            f.open("w");
            f.write(JSONLite.stringify({ pct: fraction, label: label }));
            f.close();
        } catch (e) { /* progress is a courtesy; never let it break a build */ }
    }

    function setSpan(layer, from, to, comp) {
        var a = Math.max(0, from), b = Math.min(comp.duration, to), tries;
        if (b - a < 0.04) { b = Math.min(comp.duration, a + 0.04); }
        for (tries = 0; tries < 3; tries++) {
            layer.inPoint = a;
            layer.outPoint = b;
            if (Math.abs(layer.inPoint - a) < 0.002 && Math.abs(layer.outPoint - b) < 0.002) {
                return true;
            }
        }
        layer.startTime += a - layer.inPoint;
        layer.outPoint = b;
        setSpan.failures = (setSpan.failures || 0) +
            ((Math.abs(layer.inPoint - a) < 0.002 && Math.abs(layer.outPoint - b) < 0.002) ? 0 : 1);
        return !setSpan.failures;
    }

    function addRect(shapeLayer, name, left, top, width, height, color, radius) {
        var root = shapeLayer.property("ADBE Root Vectors Group"),
            group = root.addProperty("ADBE Vector Group"),
            vectors = group.property("ADBE Vectors Group"),
            rect = vectors.addProperty("ADBE Vector Shape - Rect"),
            fill;
        group.name = name;
        rect.property("ADBE Vector Rect Size").setValue([width, height]);
        rect.property("ADBE Vector Rect Position").setValue([left + width / 2, top + height / 2]);
        if (radius) { rect.property("ADBE Vector Rect Roundness").setValue(radius); }
        fill = vectors.addProperty("ADBE Vector Graphic - Fill");
        fill.property("ADBE Vector Fill Color").setValue(color);
        return group;
    }

    function strokeGroup(group, color, width) {
        var stroke = group.property("ADBE Vectors Group")
            .addProperty("ADBE Vector Graphic - Stroke");
        stroke.property("ADBE Vector Stroke Color").setValue(color);
        stroke.property("ADBE Vector Stroke Width").setValue(width);
        return stroke;
    }

    function addRectOutline(shapeLayer, name, left, top, width, height,
                            color, strokeWidth, radius) {
        var root = shapeLayer.property("ADBE Root Vectors Group"),
            group = root.addProperty("ADBE Vector Group"),
            vectors = group.property("ADBE Vectors Group"),
            rect = vectors.addProperty("ADBE Vector Shape - Rect"),
            stroke;
        group.name = name;
        rect.property("ADBE Vector Rect Size").setValue([width, height]);
        rect.property("ADBE Vector Rect Position").setValue([left + width / 2, top + height / 2]);
        if (radius) { rect.property("ADBE Vector Rect Roundness").setValue(radius); }
        stroke = vectors.addProperty("ADBE Vector Graphic - Stroke");
        stroke.property("ADBE Vector Stroke Color").setValue(color);
        stroke.property("ADBE Vector Stroke Width").setValue(strokeWidth);
        return group;
    }

    function styleText(layer, text, font, size, leading, color, justification) {
        var prop = layer.property("ADBE Text Properties").property("ADBE Text Document"),
            doc = prop.value;
        doc.resetCharStyle();
        doc.text = text;
        doc.font = font;
        doc.fontSize = size;
        doc.applyFill = true;
        doc.fillColor = color;
        doc.applyStroke = false;
        doc.justification = justification || ParagraphJustification.LEFT_JUSTIFY;
        if (leading) {
            doc.autoLeading = false;
            doc.leading = leading;
        }
        prop.setValue(doc);
        return prop;
    }

    /* Place a layer so its rendered top-left sits exactly at (left, top). */
    function placeAt(layer, left, top, atTime) {
        var r;
        layer.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([0, 0]);
        layer.property("ADBE Transform Group").property("ADBE Position").setValue([0, 0]);
        r = layer.sourceRectAtTime(atTime || 0, false);
        layer.property("ADBE Transform Group").property("ADBE Position")
            .setValue([left - r.left, top - r.top]);
        return r;
    }

    function measure(layer, atTime) {
        return layer.sourceRectAtTime(atTime || 0, false);
    }

    /* A paragraph text box that never clips. After Effects hides whatever
       overflows a box and leaves it out of the measured size, so a box sized
       to the space available would report a long verse as fitting while its
       last lines silently vanish. Measure in a box far taller than any text,
       and let fitText decide the size. */
    function tallBox(comp, width) {
        return comp.layers.addBoxText([width, Math.max(comp.height * 3, 4000)]);
    }

    /* Shrink until the wrapped text fits the allowed height. */
    function fitText(layer, prop, maxHeight, minSize) {
        var doc = prop.value, size = doc.fontSize, r = measure(layer), guard = 0;
        while (r.height > maxHeight && size > minSize && guard < 40) {
            size = Math.max(minSize, size - Math.max(1, Math.round(size * 0.06)));
            doc = prop.value;
            doc.leading = doc.leading * (size / doc.fontSize);
            doc.fontSize = size;
            prop.setValue(doc);
            r = measure(layer);
            guard++;
        }
        return r;
    }

    /* ------------------------------------------------------------------ */
    /* animation                                                           */
    /* ------------------------------------------------------------------ */
    function easeKeys(prop, influence) {
        var i, inEase, outEase, dims;
        influence = influence || 70;
        for (i = 1; i <= prop.numKeys; i++) {
            dims = (prop.value instanceof Array) ? prop.value.length : 1;
            inEase = [];
            outEase = [];
            while (inEase.length < dims) {
                inEase.push(new KeyframeEase(0, influence));
                outEase.push(new KeyframeEase(0, influence));
            }
            try {
                prop.setTemporalEaseAtKey(i, inEase, outEase);
            } catch (e) { /* single-dimension properties reject arrays on some builds */ }
        }
    }

    function fade(layer, tIn, tOut, anim, delay) {
        /* A short card must still be readable, so the fades shrink with it
           rather than eating the whole thing. */
        var span = Math.max(0.1, tOut - tIn),
            rise = Math.min(anim.inDuration, span * 0.3),
            fall = Math.min(anim.outDuration, span * 0.3),
            op = layer.property("ADBE Transform Group").property("ADBE Opacity"),
            a = tIn + Math.min(delay || 0, span * 0.1),
            b = a + rise,
            c = tOut - fall,
            d = tOut;
        if (c <= b) { c = (b + d) / 2; }
        op.setValueAtTime(a, 0);
        op.setValueAtTime(b, 100);
        op.setValueAtTime(c, 100);
        op.setValueAtTime(d, 0);
        easeKeys(op, anim.easeInfluence);
    }

    function slide(nullLayer, tIn, tOut, anim, scale) {
        var pos = nullLayer.property("ADBE Transform Group").property("ADBE Position"),
            rest = pos.value,
            drop = [rest[0], rest[1] + anim.slideY * scale],
            b = tIn + anim.inDuration,
            c = tOut - anim.outDuration;
        if (c <= b) { c = (b + tOut) / 2; }
        pos.setValueAtTime(tIn, drop);
        pos.setValueAtTime(b, rest);
        pos.setValueAtTime(c, rest);
        pos.setValueAtTime(tOut, drop);
        easeKeys(pos, anim.easeInfluence);
    }

    /* ------------------------------------------------------------------ */
    /* one cue                                                             */
    /* ------------------------------------------------------------------ */
    function buildCue(comp, cue, index, style, fonts, scale, tIn, tOut) {
        var anim = style.animation,
            barCfg = style.bar,
            chipCfg = style.chip,
            tag = "VC " + pad2(index) + " " + cue.ref,
            barWidth = barCfg.fullWidth ? comp.width : barCfg.width * scale,
            barLeft = barCfg.fullWidth ? 0 : barCfg.paddingX * scale,
            innerWidth = barWidth - barCfg.paddingX * 2 * scale,
            padTop = barCfg.paddingTop * scale,
            padBottom = barCfg.paddingBottom * scale,
            maxTextHeight = barCfg.maxHeight * scale - padTop - padBottom,
            verse, verseProp, verseRect, barHeight, barTop,
            refLayer, refProp, refRect, transLayer, transProp, transRect,
            chipHeight, chipWidth, chipTop, chipLeft, plate, ctrl, layers, i,
            hasBody = !!(cue.text && cue.text.length);

        /* Verse body first: its wrapped height decides how tall the bar is.
           A chapter reference with no verse gets the chip on its own. */
        verse = null;
        verseRect = { width: 0, height: 0 };
        if (hasBody) {
            verse = tallBox(comp, innerWidth);
            verse.name = tag + " — text";
            verseProp = styleText(verse, cue.text, fonts.verse,
                barCfg.fontSize * scale, barCfg.leading * scale, rgb(barCfg.textColor));
            verseRect = fitText(verse, verseProp, maxTextHeight, 16);
            barHeight = Math.max(barCfg.minHeight * scale, verseRect.height + padTop + padBottom);
        } else {
            barHeight = 0;
        }
        barTop = comp.height - barCfg.bottomOffset * scale - barHeight;

        /* Reference chip text. */
        refLayer = comp.layers.addText(cue.ref);
        refLayer.name = tag + " — ref";
        refProp = styleText(refLayer, cue.ref, fonts.reference,
            chipCfg.fontSize * scale, 0, rgb(chipCfg.textColor));
        refRect = measure(refLayer);

        transLayer = null;
        transRect = { width: 0, height: 0 };
        if (chipCfg.showTranslation && cue.translation) {
            transLayer = comp.layers.addText("(" + cue.translation + ")");
            transLayer.name = tag + " — translation";
            transProp = styleText(transLayer, "(" + cue.translation + ")", fonts.translation,
                chipCfg.translationFontSize * scale, 0, rgb(chipCfg.translationColor));
            transRect = measure(transLayer);
        }

        chipHeight = refRect.height + chipCfg.paddingY * 2 * scale;
        chipWidth = refRect.width + transRect.width + chipCfg.paddingX * 2 * scale
            + (transLayer ? chipCfg.paddingX * 0.4 * scale : 0);
        chipTop = hasBody
            ? barTop - chipHeight - chipCfg.gapBelow * scale
            : comp.height - barCfg.bottomOffset * scale - chipHeight
                - barCfg.paddingBottom * scale;
        chipLeft = chipCfg.offsetX * scale;

        /* Plate: cream bar plus red chip in one shape layer. */
        plate = comp.layers.addShape();
        plate.name = tag + " — plate";
        if (hasBody) {
            addRect(plate, "Bar", barLeft, barTop, barWidth, barHeight, rgb(barCfg.fill), 0);
        }
        addRect(plate, "Chip", chipLeft, chipTop, chipWidth, chipHeight,
            rgb(chipCfg.fill), chipCfg.cornerRadius * scale);
        plate.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([0, 0]);
        plate.property("ADBE Transform Group").property("ADBE Position").setValue([0, 0]);
        /* Every new layer lands on top of the stack, so the plate would cover
           the text it is supposed to sit behind. `verse` was created first and
           is therefore the lowest of the text layers. */
        plate.moveAfter(hasBody ? verse : refLayer);

        /* Position the text now that the plate geometry is known. */
        if (hasBody) {
            placeAt(verse, barLeft + barCfg.paddingX * scale, barTop + padTop);
        }
        placeAt(refLayer, chipLeft + chipCfg.paddingX * scale,
            chipTop + chipCfg.paddingY * scale);
        if (transLayer) {
            placeAt(transLayer,
                chipLeft + chipCfg.paddingX * scale + refRect.width + chipCfg.paddingX * 0.4 * scale,
                chipTop + chipCfg.paddingY * scale + (refRect.height - transRect.height));
        }

        /* Controller null drives the slide for the whole group. */
        ctrl = comp.layers.addNull(comp.duration);
        ctrl.name = tag;
        ctrl.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([0, 0]);
        ctrl.property("ADBE Transform Group").property("ADBE Position").setValue([0, 0]);
        ctrl.enabled = false;
        ctrl.moveToBeginning();

        layers = hasBody ? [plate, verse, refLayer] : [plate, refLayer];
        if (transLayer) { layers.push(transLayer); }
        for (i = 0; i < layers.length; i++) {
            layers[i].parent = ctrl;
            layers[i].label = style.build.labelColor;
            setSpan(layers[i], tIn - 0.02, tOut + 0.02, comp);
            fade(layers[i], tIn, tOut, anim,
                (layers[i] === refLayer || layers[i] === transLayer) ? anim.chipDelay : 0);
        }

        ctrl.label = style.build.labelColor;
        setSpan(ctrl, tIn - 0.02, tOut + 0.02, comp);
        slide(ctrl, tIn, tOut, anim, scale);
        if (style.build.shyGroups) { ctrl.shy = true; }

        return ctrl;
    }


    /* ------------------------------------------------------------------ */
    /* slide layout                                                        */
    /* ------------------------------------------------------------------ */
    /*
     * A full-frame treatment rather than an overlay: the sermon comp is
     * scaled into a portrait inset on the left while a panel on the right
     * carries the series title and the verse.
     *
     * Because the camera has to move, this cannot sit on top of the edit the
     * way the lower third does. It builds a wrapper comp that *contains* the
     * sermon comp, which leaves the edit itself untouched — you render the
     * wrapper instead.
     */

    /*
     * Where the edit cuts.
     *
     * The panel must not ride over a cut: when a reader finishes and the edit
     * fades to the preacher, the verse has to leave with him, not sit beside
     * a new shot. A cut is where a full-frame layer starts or ends - the
     * clips themselves and the transitions laid over them. Overlays like a
     * lower third are not full frame, so they are left out of it.
     */
    function sceneCuts(target) {
        var times = [], i, layer, src, out = [], t;
        for (i = 1; i <= target.numLayers; i++) {
            layer = target.layer(i);
            if (!(layer instanceof AVLayer) || !layer.enabled || !layer.hasVideo) { continue; }
            if (layer.adjustmentLayer) { continue; }
            src = layer.source;
            if (!src || !src.width || !src.height) { continue; }
            if (src.width < target.width * 0.9 || src.height < target.height * 0.9) { continue; }
            if (src instanceof CompItem && builtFrom(src) !== null) { continue; }
            times.push(layer.inPoint);
            times.push(layer.outPoint);
        }
        times.sort(function (a, b) { return a - b; });
        for (i = 0; i < times.length; i++) {
            t = times[i];
            if (t <= 0.05 || t >= target.duration - 0.05) { continue; }
            if (out.length && t - out[out.length - 1] < 0.15) { continue; }
            out.push(t);
        }
        return out;
    }

    /*
     * The whole slide timeline, decided before anything is built.
     *
     * In plain words:
     *  - the panel is never up without a verse in it. A verse stays until the
     *    next one replaces it, or until the panel closes;
     *  - the panel closes only when nothing is due for `holdGap` seconds, and
     *    never drops to full frame for less than `minFullFrame`;
     *  - a verse stays up long enough to be read (by its word count);
     *  - the same verse twice in a row is one card, not a fade to itself;
     *  - nothing in the first `startAfter` seconds.
     *
     * Returns the panel sections and, per verse, when it shows and whether it
     * opens or closes its section (those enter and leave with the panel).
     */
    function wordCount(text) {
        var m = String(text || "").match(/\S+/g);
        return m ? m.length : 0;
    }

    function planSlide(cues, anim, cuts) {
        var holdGap = anim.holdGap !== undefined ? anim.holdGap : 20,
            openBefore = anim.insetOpenBefore || 0,
            closeAfter = anim.insetCloseAfter || 0,
            fade = anim.panelFade || 0,
            minFull = anim.minFullFrame !== undefined ? anim.minFullFrame : 4,
            minWindow = anim.insetMinWindow || 0,
            rate = anim.readingRate || 3.3,
            minRead = anim.minReadable || 2.5,
            maxRead = anim.maxReadable || 12,
            items = [], merged = [], windows = [], out = [],
            i, k, c, prev, win, next, open;

        for (i = 0; i < cues.length; i++) {
            c = cues[i];
            if (!(c.end > c.start)) { continue; }
            items.push({ ref: c.ref, text: c.text, translation: c.translation,
                         start: c.start, end: c.end,
                         /* A presentation slide carries its picture instead of
                            words: see slidePicture. */
                         kind: c.kind, image: c.image, slide: c.slide });
        }
        items.sort(function (a, b) { return a.start - b.start; });
        items = holdOff(items, anim);

        for (i = 0; i < items.length; i++) {
            c = items[i];
            prev = merged[merged.length - 1];
            if (prev && prev.ref === c.ref && prev.translation === c.translation
                    && c.start - prev.end <= holdGap) {
                prev.end = Math.max(prev.end, c.end);
                continue;
            }
            /* Two verses starting together: the later-listed one wins. */
            if (prev && c.start - prev.start < 0.5) { merged.pop(); }
            merged.push(c);
        }

        for (i = 0; i < merged.length; i++) {
            c = merged[i];
            c.until = Math.max(c.end, c.start + Math.min(maxRead,
                Math.max(minRead, wordCount(c.text) / rate)));
            open = c.start - openBefore;
            c.shot = shotOf(cuts, c.start);
            if (win && win.shot === c.shot
                    && (c.start - win.last <= holdGap
                        || (open - fade) - (win.last + closeAfter + fade) < minFull)) {
                win.items.push(c);
                win.last = Math.max(win.last, c.until);
            } else {
                win = { start: Math.max(0, open), items: [c], shot: c.shot };
                win.last = Math.max(c.until, win.start + minWindow - closeAfter);
                windows.push(win);
            }
            /* Never earlier than the shot it belongs to. */
            if (cuts && cuts.length && win.items.length === 1) {
                win.start = Math.max(win.start, shotStart(cuts, c.start) + 0.05);
            }
        }

        for (i = 0; i < windows.length; i++) {
            win = windows[i];
            win.end = win.last + closeAfter;
            /* Gone, dissolve and all, before the edit cuts away. */
            var until = shotEnd(cuts, win.start);
            if (until !== null) { win.end = Math.min(win.end, until - fade - 0.05); }
            if (win.end <= win.start + 0.2) { win.end = win.start + 0.2; }
            for (k = 0; k < win.items.length; k++) {
                c = win.items[k];
                next = win.items[k + 1];
                /* The first verse is in the panel from the moment it opens. */
                c.showIn = k === 0 ? win.start : c.start;
                c.showOut = next ? next.start : win.end;
                c.opening = k === 0 ? win : null;
                c.closing = next ? null : win;
                out.push(c);
            }
        }
        return { windows: windows, items: out };
    }

    /* Sound on or off for a layer; a source without audio has no switch. */
    function muted(layer, off) {
        try { layer.audioEnabled = !off; } catch (e) { /* nothing to mute */ }
    }

    /* Which shot of the edit a moment belongs to, and where that shot runs. */
    function shotOf(cuts, t) {
        var i, n = 0;
        for (i = 0; i < (cuts || []).length; i++) { if (cuts[i] <= t) { n = i + 1; } }
        return n;
    }

    function shotStart(cuts, t) {
        var i, at = 0;
        for (i = 0; i < (cuts || []).length; i++) { if (cuts[i] <= t) { at = cuts[i]; } }
        return at;
    }

    function shotEnd(cuts, t) {
        var i;
        for (i = 0; i < (cuts || []).length; i++) { if (cuts[i] > t) { return cuts[i]; } }
        return null;
    }

    /* Stretches where the panel is up with no verse in it: none, by
       construction. Checked anyway, so a regression cannot ship quietly. */
    function emptyPanel(plan, tolerance) {
        var found = [], i, k, win, cursor, c;
        for (i = 0; i < plan.windows.length; i++) {
            win = plan.windows[i];
            cursor = win.start;
            for (k = 0; k < win.items.length; k++) {
                c = win.items[k];
                if (c.showIn - cursor > tolerance) { found.push({ from: cursor, to: c.showIn }); }
                cursor = Math.max(cursor, c.showOut);
            }
            if (win.end - cursor > tolerance) { found.push({ from: cursor, to: win.end }); }
        }
        return found;
    }

    function maskShape(box, scale, position, sourceWidth, sourceHeight) {
        function toLayer(x, y) {
            return [
                (x - position[0]) / scale + sourceWidth / 2,
                (y - position[1]) / scale + sourceHeight / 2
            ];
        }
        var shape = new Shape();
        shape.vertices = [
            toLayer(box.left, box.top),
            toLayer(box.left + box.width, box.top),
            toLayer(box.left + box.width, box.top + box.height),
            toLayer(box.left, box.top + box.height)
        ];
        shape.inTangents = [[0, 0], [0, 0], [0, 0], [0, 0]];
        shape.outTangents = [[0, 0], [0, 0], [0, 0], [0, 0]];
        shape.closed = true;
        return shape;
    }

    function importAsset(path) {
        var file, options, i, item;
        if (!path) { return null; }
        file = new File(path);
        if (!file.exists) { return null; }
        for (i = 1; i <= app.project.numItems; i++) {
            item = app.project.item(i);
            if (item.mainSource && item.mainSource.file
                    && item.mainSource.file.fsName === file.fsName) {
                return item;
            }
        }
        try {
            options = new ImportOptions(file);
            return app.project.importFile(options);
        } catch (e) {
            return null;
        }
    }

    function slideComp(target, style) {
        return reuseOrCreate(target, "slide", target.name + style.build.slideCompSuffix);
    }

    /* Rebuilding overwrites what was built before, rather than adding another. */
    function reuseOrCreate(target, kind, name) {
        var comp = findBuilt(target, kind, name);
        if (comp) {
            while (comp.numLayers > 0) { comp.layer(1).remove(); }
            comp.duration = target.duration;
        } else {
            comp = app.project.items.addComp(name, target.width, target.height,
                target.pixelAspect, target.duration, target.frameRate);
            comp.parentFolder = folder("TEC Sermon Studio");
        }
        comp.comment = tagFor(target, kind);
        return comp;
    }

    /* The stretches where the panel is NOT covering the frame. */
    function gapsBetween(windows, duration) {
        var gaps = [], cursor = 0, i;
        for (i = 0; i < windows.length; i++) {
            if (windows[i].start > cursor) {
                gaps.push({ start: cursor, end: Math.min(windows[i].start, duration) });
            }
            cursor = Math.max(cursor, windows[i].end);
        }
        if (cursor < duration) { gaps.push({ start: cursor, end: duration }); }
        return gaps;
    }

    function trim(layer, from, to, comp) {
        return setSpan(layer, from, to, comp);
    }

    /*
     * The camera inset lives in a comp the size of its frame.
     *
     * Cropping with a mask locks the picture in place: the mask travels with
     * the layer, so the framing cannot move. A comp the size of the frame
     * crops by its own edges instead, which leaves the camera free to pan and
     * keep the preacher centred as he moves about the lectern.
     */
    function insetComp(target, box, fps) {
        var name = target.name + " \u2014 Camera inset",
            tag = tagFor(target, "inset"), i, it, comp = null, layer, factor;
        for (i = 1; i <= app.project.numItems; i++) {
            it = app.project.item(i);
            if (it instanceof CompItem && String(it.comment) === tag) { comp = it; break; }
        }
        if (comp) {
            while (comp.numLayers > 0) { comp.layer(1).remove(); }
            comp.duration = target.duration;
        } else {
            comp = app.project.items.addComp(name, Math.max(2, Math.round(box.width)),
                Math.max(2, Math.round(box.height)), 1, target.duration, fps);
            comp.parentFolder = folder(TAG);
        }
        comp.comment = tag;
        layer = comp.layers.add(target);
        layer.name = target.name;
        muted(layer, true);
        return { comp: comp, layer: layer };
    }

    /* Where the camera layer must sit for a point across the picture (0-1) to
       land in the middle of the frame, without showing past its edges. */
    function insetPosition(cam, target, factor, focusX, focusY) {
        var overflowY = Math.max(0, target.height * factor - cam.height),
            x = cam.width / 2 - (focusX * target.width - target.width / 2) * factor,
            lo = cam.width - target.width * factor / 2,
            hi = target.width * factor / 2;
        return [Math.max(lo, Math.min(hi, x)),
                cam.height / 2 + (0.5 - focusY) * overflowY];
    }

    function buildSlide(target, payload, style, windows, scale) {
        var slide = style.slide,
            anim = slide.animation,
            fade = anim.panelFade,
            comp = slideComp(target, style),
            originX, originY, insetBox, panelBox, asset, gaps,
            base, backdrop, inset, frame, plate, fading, i, j, win;

        originX = (comp.width - style.reference[0] * scale) / 2;
        originY = (comp.height - style.reference[1] * scale) / 2;
        insetBox = {
            left: originX + slide.camera.x * scale,
            top: originY + slide.camera.y * scale,
            width: slide.camera.width * scale,
            height: slide.camera.height * scale
        };
        panelBox = {
            left: originX + slide.panel.x * scale,
            top: originY + slide.panel.y * scale,
            width: slide.panel.width * scale,
            height: slide.panel.height * scale
        };

        /*
         * Two complete states, cross-dissolved — the reference dissolves from
         * the full-frame camera to the whole slide composition rather than
         * physically moving the camera.
         *
         * Everything here is trimmed to the moments it is actually visible.
         * That matters more than it looks: the inset is a second instance of
         * the edit, so an untrimmed layer would make After Effects render the
         * whole sermon twice on every frame. Trimmed, the edit is composited
         * exactly once at any point on the timeline, and the extra cost is a
         * few shapes and text layers during the panel sections only.
         */

        /* 0. The sermon's sound, once, unbroken. The full-frame and inset
              copies below overlap through every dissolve; if they carried
              audio too, it would double - a jump in level at each change. */
        var sound = comp.layers.add(target);
        sound.name = target.name + " (sound)";
        sound.enabled = false;
        muted(sound, false);
        sound.label = style.build.labelColor;

        /* The camera the insets all share, framed on the middle until the
           preacher is followed (see applyCameraFollow). */
        var cam = insetComp(target, insetBox, comp.frameRate),
            camFactor = cam.comp.height / target.height * (slide.camera.zoom || 1);
        cam.layer.property("ADBE Transform Group").property("ADBE Scale")
            .setValue([camFactor * 100, camFactor * 100]);
        cam.layer.property("ADBE Transform Group").property("ADBE Position")
            .setValue(insetPosition(cam.comp, target, camFactor,
                                    slide.camera.focusX, slide.camera.focusY));

        /* 1. Full-frame camera, only where the panel is not covering it. */
        gaps = gapsBetween(windows, comp.duration);
        for (i = 0; i < gaps.length; i++) {
            base = comp.layers.add(target);
            base.name = target.name + " (full frame " + pad2(i + 1) + ")";
            muted(base, true);
            fitToComp(base, comp);
            trim(base, gaps[i].start - fade, gaps[i].end + fade, comp);
        }

        /* 2-5. One set of slide furniture per panel section.
         *
         * Everything that never moves - backdrop, panel, frame, logo - is
         * drawn once into a single picture the size of the comp, with a hole
         * where the camera goes. One flat layer instead of four that have to
         * be scaled and drawn on every frame, which is what made panel
         * stretches render slower than full frame. */
        var ready = importAsset(slide.furnitureImage);
        if (slide.furnitureImage && !ready) {
            buildWarnings.push("The slide backdrop could not be prepared, so it was built the "
                               + "slower way. Rebuild to try again.");
        }
        asset = ready ? null : importAsset(slide.background.image);
        if (!ready && slide.background.image && !asset) {
            buildWarnings.push("Backdrop image not found (" + String(slide.background.image)
                .replace(/^.*[\/\\]/, "") + ") - used the plain colour. Choose it again in Settings.");
        }
        for (j = 0; j < windows.length; j++) {
            win = windows[j];
            fading = [];

            if (ready) {
                inset = comp.layers.add(cam.comp);
                inset.name = target.name + " (inset " + pad2(j + 1) + ")";
                inset.property("ADBE Transform Group").property("ADBE Position")
                    .setValue([insetBox.left + insetBox.width / 2,
                               insetBox.top + insetBox.height / 2]);
                fading.push(inset);

                plate = comp.layers.add(ready);
                plate.name = "Slide " + pad2(j + 1);
                fitToComp(plate, comp);
                fading.push(plate);

                for (i = 0; i < fading.length; i++) {
                    trim(fading[i], win.start - fade, win.end + fade, comp);
                    fadeWindow(fading[i], win, anim);
                    fading[i].label = style.build.labelColor;
                }
                continue;
            }

            if (asset) {
                backdrop = comp.layers.add(asset);
                backdrop.name = "Backdrop " + pad2(j + 1);
                fitToComp(backdrop, comp);
            } else {
                backdrop = comp.layers.addShape();
                backdrop.name = "Backdrop " + pad2(j + 1);
                addRect(backdrop, "Fill", 0, 0, comp.width, comp.height,
                    rgb(slide.background.fill), 0);
                backdrop.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([0, 0]);
                backdrop.property("ADBE Transform Group").property("ADBE Position").setValue([0, 0]);
            }
            fading.push(backdrop);

            inset = comp.layers.add(cam.comp);
            inset.name = target.name + " (inset " + pad2(j + 1) + ")";
            inset.property("ADBE Transform Group").property("ADBE Position")
                .setValue([insetBox.left + insetBox.width / 2,
                           insetBox.top + insetBox.height / 2]);
            fading.push(inset);

            if (slide.camera.border.width > 0) {
                frame = comp.layers.addShape();
                frame.name = "Camera frame " + pad2(j + 1);
                addRectOutline(frame, "Frame",
                    insetBox.left, insetBox.top, insetBox.width, insetBox.height,
                    rgb(slide.camera.border.color),
                    slide.camera.border.width * scale,
                    (slide.camera.border.radius || 0) * scale);
                frame.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([0, 0]);
                frame.property("ADBE Transform Group").property("ADBE Position").setValue([0, 0]);
                fading.push(frame);
            }

            plate = comp.layers.addShape();
            plate.name = "Panel " + pad2(j + 1);
            var panelGroup = addRect(plate, "Panel", panelBox.left, panelBox.top,
                panelBox.width, panelBox.height, rgb(slide.panel.fill), 0);
            if (slide.panel.strokeWidth > 0) {
                strokeGroup(panelGroup, rgb(slide.panel.strokeColor),
                            slide.panel.strokeWidth * scale);
            }
            /* A new shape layer sits at the comp centre, so its contents draw
               half a comp away from the coordinates they were given. */
            plate.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([0, 0]);
            plate.property("ADBE Transform Group").property("ADBE Position").setValue([0, 0]);
            fading.push(plate);

            for (i = 0; i < fading.length; i++) {
                trim(fading[i], win.start - fade, win.end + fade, comp);
                fadeWindow(fading[i], win, anim);
                fading[i].label = style.build.labelColor;
            }
        }

        return { comp: comp, panel: panelBox, windows: windows, plate: plate, flat: !!ready,
                 camera: cam, cameraFactor: camFactor, insetBox: insetBox };
    }

    /* Fill a box with a layer, cropping the overflow rather than squashing it.
     *
     * Focus shifts the image within whatever overflows the box. Measuring that
     * shift against the full scaled size instead of the overflow pushes the
     * picture off the edge and leaves a gap on the opposite side — which is
     * exactly what a portrait box does to landscape footage, where the height
     * fits exactly and nothing overflows vertically at all.
     */
    function cropInto(layer, box, sourceWidth, sourceHeight, focusX, focusY, zoom) {
        var factor = Math.max(box.width / sourceWidth, box.height / sourceHeight)
                * (zoom || 1),
            overflowX = Math.max(0, sourceWidth * factor - box.width),
            overflowY = Math.max(0, sourceHeight * factor - box.height),
            position = [
                box.left + box.width / 2 + (0.5 - focusX) * overflowX,
                box.top + box.height / 2 + (0.5 - focusY) * overflowY
            ],
            mask;
        layer.property("ADBE Transform Group").property("ADBE Scale")
            .setValue([factor * 100, factor * 100]);
        layer.property("ADBE Transform Group").property("ADBE Position").setValue(position);
        mask = layer.property("ADBE Mask Parade")
            .addProperty("ADBE Mask Atom").property("ADBE Mask Shape");
        mask.setValue(maskShape(box, factor, position, sourceWidth, sourceHeight));
    }

    function fitToComp(layer, comp) {
        var rect = layer.sourceRectAtTime(0, false),
            factor = Math.max(comp.width / rect.width, comp.height / rect.height);
        layer.property("ADBE Transform Group").property("ADBE Scale")
            .setValue([factor * 100, factor * 100]);
        layer.property("ADBE Transform Group").property("ADBE Position")
            .setValue([comp.width / 2, comp.height / 2]);
    }

    function windowFade(win, anim) {
        return Math.min(anim.panelFade, (win.end - win.start) * 0.25);
    }

    function fadeWindow(layer, win, anim) {
        var op = layer.property("ADBE Transform Group").property("ADBE Opacity"),
            fade = windowFade(win, anim);
        op.setValueAtTime(Math.max(0, win.start - fade), 0);
        op.setValueAtTime(win.start, 100);
        op.setValueAtTime(win.end, 100);
        op.setValueAtTime(win.end + fade, 0);
        easeKeys(op, anim.easeInfluence);
    }


    /* Series title, rule and logo: present whenever the panel is. */
    function slideFurniture(built, style, fonts, scale, cues) {
        var slide = style.slide, comp = built.comp, panel = built.panel,
            i, title, titleProp, titleRect, rule, logoAsset, logo, bottom;

        if (slide.series.text) {
            title = comp.layers.addBoxText([
                panel.width - slide.panel.paddingX * 2 * scale,
                slide.series.leading * 2.4 * scale
            ]);
            title.name = "Series title";
            titleProp = styleText(title, slide.series.text, fonts.reference,
                slide.series.fontSize * scale, slide.series.leading * scale,
                rgb(slide.series.color));
            titleRect = placeAt(title,
                panel.left + slide.panel.paddingX * scale,
                panel.top + slide.panel.paddingTop * scale);

            if (slide.series.rule) {
                rule = comp.layers.addShape();
                rule.name = "Series rule";
                addRect(rule, "Rule",
                    panel.left + slide.panel.paddingX * scale,
                    panel.top + slide.panel.paddingTop * scale + titleRect.height
                        + slide.series.gapBelow * 0.35 * scale,
                    panel.width - slide.panel.paddingX * 2 * scale,
                    Math.max(1, slide.series.ruleWidth * scale),
                    rgb(slide.series.ruleColor), 0);
                rule.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([0, 0]);
                rule.property("ADBE Transform Group").property("ADBE Position").setValue([0, 0]);
            }
            built.titleBottom = panel.top + slide.panel.paddingTop * scale
                + titleRect.height + slide.series.gapBelow * scale;
        } else {
            built.titleBottom = panel.top + slide.panel.paddingTop * scale;
        }

        logoAsset = (style.slide.furnitureImage && built.flat) ? null : importAsset(slide.logo.image);
        if (!built.flat && slide.logo.image && !logoAsset) {
            buildWarnings.push("Logo image not found (" + String(slide.logo.image)
                .replace(/^.*[\/\\]/, "") + ") - built without it. Choose it again in Settings.");
        }
        if (logoAsset) {
            logo = comp.layers.add(logoAsset);
            logo.name = "Logo";

            /* Measure the source itself rather than the laid-out layer: a
               freshly added layer can report a rect that has already been
               transformed, which then feeds back into the scale and produces
               a logo many times the intended size. */
            var srcW = logoAsset.width || logo.sourceRectAtTime(0, false).width,
                srcH = logoAsset.height || logo.sourceRectAtTime(0, false).height,
                targetW = slide.logo.width * scale,
                logoScale = srcW > 0 ? targetW / srcW : 1,
                drawnW, drawnH;

            /* Never larger than a quarter of the frame, whatever the source. */
            if (logoScale * srcW > comp.width * 0.25) {
                logoScale = (comp.width * 0.25) / srcW;
            }
            drawnW = srcW * logoScale;
            drawnH = srcH * logoScale;

            logo.property("ADBE Transform Group").property("ADBE Anchor Point")
                .setValue([srcW / 2, srcH / 2]);
            logo.property("ADBE Transform Group").property("ADBE Scale")
                .setValue([logoScale * 100, logoScale * 100]);
            logo.property("ADBE Transform Group").property("ADBE Position").setValue([
                comp.width - slide.logo.marginRight * scale - drawnW / 2,
                comp.height - slide.logo.marginBottom * scale - drawnH / 2
            ]);
            built.logo = logo;
        }

        /* Furniture lives and dies with the panel. */
        for (i = 0; i < built.windows.length; i++) {
            if (title) { fadeWindow(title, built.windows[i], slide.animation); }
            if (rule) { fadeWindow(rule, built.windows[i], slide.animation); }
            if (built.logo) { fadeWindow(built.logo, built.windows[i], slide.animation); }
        }
        return built;
    }

    /* One verse inside the panel, with its reference beneath.

       With no header above it, the verse and its reference are treated as a
       single block and centred in the panel, rather than pinned to the top
       with dead space underneath. */
    /* Split a verse into `n` pages of similar length, breaking after a
       sentence or clause where one is near, and never mid-word. */
    function splitPages(text, n) {
        var words = String(text).split(/\s+/), pages = [], per, i, cut, best, k, score, page;
        if (n <= 1 || words.length < n * 4) { return [String(text)]; }
        per = words.length / n;
        for (i = 0, k = 0; i < n - 1; i++) {
            cut = Math.round(per * (i + 1));
            best = cut;
            /* Prefer a break just after punctuation, within a few words. */
            for (score = 0; score <= Math.max(2, Math.round(per * 0.3)); score++) {
                if (/[.;:!?]["\u201d\u2019)]?$/.test(words[cut - 1 + score] || "")) { best = cut + score; break; }
                if (/[.;:!?]["\u201d\u2019)]?$/.test(words[cut - 1 - score] || "")) { best = cut - score; break; }
                if (/,["\u201d\u2019)]?$/.test(words[cut - 1 + score] || "") && best === cut) { best = cut + score; }
            }
            best = Math.max(k + 2, Math.min(words.length - 2, best));
            page = words.slice(k, best).join(" ");
            pages.push(page);
            k = best;
        }
        pages.push(words.slice(k).join(" "));
        return pages;
    }

    /* Fade one layer in and out; `opening`/`closing` sections share the
       panel's own keys so text and panel move as one. */
    function verseKeys(layer, anim, tIn, tOut, opening, closing, comp) {
        var f, a, b, c, d, op;
        if (closing || opening) {
            f = windowFade(closing || opening, anim);
            a = opening ? opening.start - windowFade(opening, anim) : tIn;
            b = opening ? opening.start : Math.min(tIn + anim.verseIn, tOut);
            c = closing ? closing.end : Math.max(b, tOut - anim.verseOut);
            d = closing ? closing.end + f : tOut;
            setSpan(layer, a - 0.02, d + 0.02, comp);
            op = layer.property("ADBE Transform Group").property("ADBE Opacity");
            op.setValueAtTime(Math.max(0, a), 0);
            op.setValueAtTime(b, 100);
            op.setValueAtTime(c, 100);
            op.setValueAtTime(d, 0);
            easeKeys(op, anim.easeInfluence);
        } else {
            setSpan(layer, tIn - 0.02, tOut + 0.02, comp);
            fade(layer, tIn, tOut, { inDuration: anim.verseIn, outDuration: anim.verseOut,
                                     easeInfluence: anim.easeInfluence }, 0);
        }
    }

    /* One verse inside the panel, with its reference beneath.

       With no header above it, the verse and its reference are treated as a
       single block and centred in the panel, rather than pinned to the top
       with dead space underneath.

       A verse that would need shrinking below a comfortable size is shown as
       two or more pages instead, timed across his reading of it. */
    function slideCue(built, cue, index, style, fonts, scale, tIn, tOut, closing, opening) {
        var slide = style.slide, comp = built.comp, panel = built.panel,
            anim = slide.animation,
            tag = "VC " + pad2(index) + " " + cue.ref,
            innerWidth = panel.width - slide.panel.paddingX * 2 * scale,
            innerTop = built.titleBottom,
            innerBottom = panel.top + panel.height - slide.panel.paddingBottom * scale,
            available = innerBottom - innerTop,
            gap = slide.reference.gapAbove * scale,
            justify = slide.verse.align === "center"
                ? ParagraphJustification.CENTER_JUSTIFY
                : ParagraphJustification.LEFT_JUSTIFY,
            design = slide.verse.fontSize * scale,
            comfortable = Math.round(design * (slide.verse.minScale || 0.72)),
            text = cue.text || cue.ref,
            refLayer, refRect, refHeight, maxBody, pages, n, k, body, prop, rect,
            size, tallest, blockTop, bodies = [], sizes, times, total, words, done, i, t;

        refLayer = comp.layers.addText(cue.ref);
        refLayer.name = tag + " \u2014 ref";
        styleText(refLayer, cue.ref + (cue.translation ? "  (" + cue.translation + ")" : ""),
            fonts.reference, slide.reference.fontSize * scale, 0, rgb(slide.reference.color));
        refRect = measure(refLayer);
        refHeight = refRect.height;
        maxBody = available - refHeight - gap;

        /* The fewest pages that fit at a comfortable size. */
        body = tallBox(comp, innerWidth);
        prop = styleText(body, text, fonts.verse, design, slide.verse.leading * scale,
                         rgb(slide.verse.color), justify);
        for (n = 1; n <= 4; n++) {
            pages = splitPages(text, n);
            sizes = [];
            for (k = 0; k < pages.length; k++) {
                styleText(body, pages[k], fonts.verse, design, slide.verse.leading * scale,
                          rgb(slide.verse.color), justify);
                rect = fitText(body, prop, maxBody, n < 4 ? comfortable : 18);
                sizes.push(prop.value.fontSize);
                if (rect.height > maxBody + 1) { sizes = null; break; }
            }
            if (sizes) { break; }
        }
        if (!sizes) { pages = splitPages(text, 4); sizes = [18, 18, 18, 18]; }
        size = Math.min.apply(Math, sizes);

        /* Every page at the same size, so the text does not jump between them. */
        tallest = 0;
        for (k = 0; k < pages.length; k++) {
            if (k > 0) { body = tallBox(comp, innerWidth); }
            body.name = tag + " \u2014 text" + (pages.length > 1 ? " " + (k + 1) + "/" + pages.length : "");
            styleText(body, pages[k], fonts.verse, size,
                      slide.verse.leading * scale * (size / design),
                      rgb(slide.verse.color), justify);
            rect = measure(body);
            tallest = Math.max(tallest, rect.height);
            bodies.push(body);
        }
        blockTop = innerTop + (available - (tallest + gap + refHeight)) / 2;
        for (k = 0; k < bodies.length; k++) {
            placeAt(bodies[k], panel.left + slide.panel.paddingX * scale, blockTop);
        }
        placeAt(refLayer,
            slide.reference.align === "left"
                ? panel.left + slide.panel.paddingX * scale
                : panel.left + panel.width - slide.panel.paddingX * scale - refRect.width,
            blockTop + tallest + gap);

        /* Page changes follow the reading: split his reading time by words. */
        times = [tIn];
        if (bodies.length > 1) {
            total = 0;
            words = [];
            for (k = 0; k < pages.length; k++) {
                words.push(pages[k].split(/\s+/).length);
                total += words[k];
            }
            var from = Math.max(tIn, cue.start), to = Math.min(tOut, Math.max(cue.end, from + 1));
            done = 0;
            for (k = 0; k < pages.length - 1; k++) {
                done += words[k];
                t = from + (to - from) * done / total;
                times.push(Math.max(times[times.length - 1] + 1.5, Math.min(tOut - 1.5, t)));
            }
        }
        times.push(tOut);

        for (k = 0; k < bodies.length; k++) {
            bodies[k].label = style.build.labelColor;
            verseKeys(bodies[k], anim, times[k], times[k + 1],
                      k === 0 ? opening : null, k === bodies.length - 1 ? closing : null, comp);
        }
        refLayer.label = style.build.labelColor;
        verseKeys(refLayer, anim, tIn, tOut, opening, closing, comp);
        return bodies[0];
    }

    /*
     * One presentation slide in the panel, where a verse would go.
     *
     * The deck is the point of a session like church history, so the slide
     * takes the panel the same way a verse does: the same box, the same fade
     * in and out, and it changes when he moves on - the engine works out when
     * that is from the transcript. Fitted inside the panel rather than filling
     * it, because a slide is a fixed shape and cropping it would cut words off
     * its edges.
     */
    function slidePicture(built, cue, index, style, fonts, scale, tIn, tOut, closing, opening) {
        var slide = style.slide, comp = built.comp, panel = built.panel,
            item = importAsset(cue.image),
            tag = "VC " + pad2(index) + " " + cue.ref,
            /* A picture takes the whole panel, not the box the text sits in.
               The paddings exist to keep words off the panel edge; a slide is
               artwork, and holding it inside them threw away a third of the
               room it had - on a deck whose own content is small to begin
               with, that is the difference between readable and not. A thin
               inset keeps it off the outline. */
            inset = Math.max(4, slide.panel.paddingX * 0.12) * scale,
            titled = built.titleBottom > panel.top + slide.panel.paddingTop * scale + 1,
            innerLeft = panel.left + inset,
            innerWidth = panel.width - inset * 2,
            innerTop = titled ? built.titleBottom : panel.top + inset,
            innerBottom = panel.top + panel.height - inset,
            available = innerBottom - innerTop,
            layer, factor, width, height;

        if (!item) {
            /* The picture has moved or been cleared out: show its heading so
               the moment is not silently blank. */
            return slideCue(built, cue, index, style, fonts, scale, tIn, tOut, closing, opening);
        }
        layer = comp.layers.add(item);
        layer.name = tag;
        factor = Math.min(innerWidth / item.width, available / item.height);
        width = item.width * factor;
        height = item.height * factor;
        layer.property("ADBE Transform Group").property("ADBE Scale")
            .setValue([factor * 100, factor * 100]);
        layer.property("ADBE Transform Group").property("ADBE Anchor Point")
            .setValue([item.width / 2, item.height / 2]);
        layer.property("ADBE Transform Group").property("ADBE Position")
            .setValue([innerLeft + innerWidth / 2, innerTop + available / 2]);
        layer.label = style.build.labelColor;
        verseKeys(layer, slide.animation, tIn, tOut, opening, closing, comp);
        return layer;
    }

    /* ------------------------------------------------------------------ */
    /* build                                                               */
    /* ------------------------------------------------------------------ */
    function captionsComp(target, payload, style) {
        return reuseOrCreate(target, "captions", target.name + style.build.compSuffix);
    }

    function ensureCaptionLayer(target, comp) {
        var i, layer;
        for (i = 1; i <= target.numLayers; i++) {
            layer = target.layer(i);
            if (layer.source === comp) { return layer; }
        }
        layer = target.layers.add(comp);
        layer.moveToBeginning();
        layer.name = comp.name;
        return layer;
    }

    /*
     * Building runs in steps: set up, a few verses at a time, then finish.
     *
     * After Effects cannot redraw the panel while a script is running, so a
     * build done in one call only ever showed its progress at the end. Run
     * in steps, After Effects is free for a moment between them, the panel
     * repaints, and the percentage moves. The job waits in $.global.
     */
    var buildWarnings = [];

    function startJob(payload, options) {
        options = options || {};
        setSpan.failures = 0;
        buildWarnings = [];
        var project = app.project,
            target = options.compName ? null : resolveComp(),
            style = payload.style, i, cue, window, enabledOnly, job;

        if (options.compName) {
            for (i = 1; i <= project.numItems; i++) {
                if (project.item(i) instanceof CompItem && project.item(i).name === options.compName) {
                    target = project.item(i);
                    break;
                }
            }
        }
        if (!(target instanceof CompItem)) {
            throw new Error("Open the sermon composition first, then build.");
        }

        job = {
            payload: payload, options: options, target: target, style: style,
            /* Uniform so nothing distorts; centred so a comp that is not
               16:9 keeps the design intact with margin. */
            scale: Math.min(target.width / style.reference[0],
                            target.height / style.reference[1]),
            /* Cue times are relative to the recording, not the comp: find
               every piece of the recording in the edit. */
            instances: mediaInstances(target, payload.media),
            fonts: {
                reference: resolveFont(style.fonts.reference),
                verse: resolveFont(style.fonts.verse),
                translation: resolveFont(style.fonts.translation)
            },
            layout: options.layout === "slide" ? "slide" : "captions",
            items: [], next: 0, built: 0, skipped: 0, trimmed: 0, misplaced: 0
        };

        app.beginUndoGroup("TEC Sermon Studio — build (set up)");
        try {
            if (job.layout === "slide") {
                for (i = 0; i < payload.cues.length; i++) {
                    cue = payload.cues[i];
                    if (cue.enabled === false) { continue; }
                    window = mapCue(job.instances, cue);
                    if (window === null) { job.skipped++; continue; }
                    job.items.push({
                        ref: cue.ref, text: cue.text, translation: cue.translation,
                        start: window.inTime, end: Math.min(window.outTime, target.duration),
                        /* A presentation slide carries its picture instead of
                           words: see slidePicture. */
                        kind: cue.kind, image: cue.image, slide: cue.slide
                    });
                }
                if (!job.items.length) { throw new Error("No cues land inside this comp."); }
                job.cuts = sceneCuts(target);
                job.plan = planSlide(job.items, style.slide.animation, job.cuts);
                job.items = job.plan.items;
                if (!job.items.length) {
                    throw new Error("No verses after the first " +
                        style.slide.animation.startAfter + " seconds of this comp.");
                }
                job.slide = buildSlide(target, payload, style, job.plan.windows, job.scale);
                slideFurniture(job.slide, style, job.fonts, job.scale, job.items);
                job.comp = job.slide.comp;
            } else {
                job.comp = (style.build.mode === "in-place")
                    ? target : captionsComp(target, payload, style);
                enabledOnly = (options.onlyRefs && options.onlyRefs.length) ? options.onlyRefs : null;
                for (i = 0; i < payload.cues.length; i++) {
                    cue = payload.cues[i];
                    if (enabledOnly) {
                        if (indexOfStr(enabledOnly, cue.id || (cue.ref + "@" + cue.start)) < 0) { continue; }
                    } else if (cue.enabled === false) {
                        continue;
                    }
                    window = mapCue(job.instances, cue);
                    if (window === null || window.inTime >= job.comp.duration) { job.skipped++; continue; }
                    if (window.trimmed) { job.trimmed++; }
                    job.items.push({ cue: cue, inTime: window.inTime, outTime: window.outTime });
                }
            }
        } finally {
            app.endUndoGroup();
        }
        job.misplaced += setSpan.failures || 0;
        $.global.__vcJob = job;
        return job;
    }

    function stepJob(count) {
        var job = $.global.__vcJob, k, it;
        if (!job) { throw new Error("No build in progress."); }
        setSpan.failures = 0;
        app.beginUndoGroup("TEC Sermon Studio — build (verses)");
        try {
            for (k = 0; k < count && job.next < job.items.length; k++) {
                it = job.items[job.next];
                if (job.layout === "slide") {
                    (it.kind === "slide" && it.image ? slidePicture : slideCue)(
                        job.slide, it, job.next + 1, job.style, job.fonts, job.scale,
                        it.showIn, it.showOut, it.closing, it.opening);
                } else if (it.cue && it.cue.kind === "slide") {
                    /* A deck needs the panel beside him; there is nowhere for a
                       slide to go in a lower third. Counted and reported, so a
                       build that quietly left the deck out cannot be mistaken
                       for one that worked. */
                    job.skippedSlides = (job.skippedSlides || 0) + 1;
                } else {
                    buildCue(job.comp, it.cue, job.next + 1, job.style, job.fonts, job.scale,
                             it.inTime, Math.min(it.outTime, job.comp.duration));
                }
                job.next++;
                job.built++;
            }
        } finally {
            app.endUndoGroup();
        }
        job.misplaced += setSpan.failures || 0;
        return { done: job.next, total: job.items.length };
    }

    function finishJob() {
        var job = $.global.__vcJob, report = [], li, comp;
        if (!job) { throw new Error("No build in progress."); }
        app.beginUndoGroup("TEC Sermon Studio — build (finish)");
        try {
            if (job.layout === "slide") {
                /* The lower-third captions would otherwise show in the inset. */
                disableOverlayCaptions(job.target, job.style);
            } else if (job.style.build.mode !== "in-place") {
                ensureCaptionLayer(job.target, job.comp);
            }
        } finally {
            app.endUndoGroup();
        }
        comp = job.comp;
        for (li = 1; li <= comp.numLayers; li++) {
            report.push(comp.layer(li).name + " [" + comp.layer(li).inPoint.toFixed(1) +
                        "-" + comp.layer(li).outPoint.toFixed(1) + "]");
        }
        $.global.__vcJob = null;
        return {
            ok: true, built: job.built, comp: comp.name, layout: job.layout,
            sections: job.plan ? job.plan.windows.length : 0,
            cuts: job.cuts ? job.cuts.length : 0,
            warnings: (job.skippedSlides
                ? buildWarnings.concat([job.skippedSlides + " slides were left out: a deck "
                    + "needs the slide panel layout, not a lower third"])
                : buildWarnings),
            emptyPanel: job.plan ? emptyPanel(job.plan, 0.8).length : 0,
            misplaced: job.misplaced, skipped: job.skipped, trimmed: job.trimmed,
            instances: job.instances.length, fonts: job.fonts,
            layers: comp.numLayers, stack: report
        };
    }

    /* The whole build in one call, for File > Scripts. */
    VC.build = function (payload, options) {
        startJob(payload, options);
        while (stepJob(50).done < $.global.__vcJob.items.length) { /* keep going */ }
        return JSONLite.stringify(finishJob());
    };

    VC.buildStart = function (path, optionsJson) {
        try {
            var job = startJob(JSONLite.parse(readFile(path)),
                               optionsJson ? JSONLite.parse(optionsJson) : {});
            var report = { ok: true, total: job.items.length, layout: job.layout };
            if (job.layout === "slide" && job.plan) {
                report.windows = [];
                for (var w = 0; w < job.plan.windows.length; w++) {
                    report.windows.push([job.plan.windows[w].start, job.plan.windows[w].end]);
                }
                /* How much of the picture's width the inset shows, which is
                   what "centred" is measured against. */
                report.cropWidth = Math.min(1, (job.slide.insetBox.width / job.slide.insetBox.height)
                    * (job.target.height / job.target.width) / (job.style.slide.camera.zoom || 1));
            }
            return JSONLite.stringify(report);
        } catch (e) {
            $.global.__vcJob = null;
            return JSONLite.stringify({ ok: false, error: String(e.message || e) });
        }
    };

    /*
     * Keep the preacher in the middle of the inset.
     *
     * The engine watches the recording through each panel section and says
     * where he is; this turns that into keyframes on the camera inside its
     * comp. They are ordinary position keys, so any of it can be adjusted by
     * hand afterwards.
     */
    VC.applyCameraFollow = function (path) {
        try {
            var job = $.global.__vcJob;
            if (!job || !job.slide || !job.slide.camera) {
                throw new Error("Build the slide layout first.");
            }
            var data = JSONLite.parse(readFile(path)),
                cam = job.slide.camera, target = job.target,
                factor = job.slide.cameraFactor,
                pos = cam.layer.property("ADBE Transform Group").property("ADBE Position"),
                focusY = job.style.slide.camera.focusY,
                w, k, win, keys, value, first, last, used = 0;
            app.beginUndoGroup("TEC Sermon Studio \u2014 follow the preacher");
            try {
                for (w = 0; w < (data.windows || []).length; w++) {
                    win = data.windows[w];
                    keys = win.keys || [];
                    if (!keys.length) { continue; }
                    /* Settled before the panel dissolves in, and held after it
                       leaves, so nothing drifts while it is on screen. */
                    first = insetPosition(cam.comp, target, factor, keys[0][1], focusY);
                    pos.setValueAtTime(Math.max(0, win.start - 0.6), first);
                    for (k = 0; k < keys.length; k++) {
                        value = insetPosition(cam.comp, target, factor, keys[k][1], focusY);
                        pos.setValueAtTime(Math.max(0, keys[k][0]), value);
                        used++;
                    }
                    last = insetPosition(cam.comp, target, factor, keys[keys.length - 1][1], focusY);
                    pos.setValueAtTime(win.end + 0.6, last);
                }
                if (used) { easeKeys(pos, 45); }
            } finally {
                app.endUndoGroup();
            }
            return JSONLite.stringify({ ok: true, keys: used,
                                        sections: (data.windows || []).length });
        } catch (e) {
            return JSONLite.stringify({ ok: false, error: String(e.message || e) });
        }
    };

    VC.buildStep = function (count) {
        try {
            var r = stepJob(Number(count) || 3);
            return JSONLite.stringify({ ok: true, done: r.done, total: r.total });
        } catch (e) {
            $.global.__vcJob = null;
            return JSONLite.stringify({ ok: false, error: String(e.message || e) });
        }
    };

    VC.buildFinish = function () {
        try {
            return JSONLite.stringify(finishJob());
        } catch (e) {
            $.global.__vcJob = null;
            return JSONLite.stringify({ ok: false, error: String(e.message || e) });
        }
    };

    /*
     * Nothing from the panel before `startAfter` seconds into the comp.
     *
     * The earliest the panel may start dissolving in is startAfter; a verse
     * card follows it by the usual open-before and fade time. A verse that
     * begins earlier starts at that point if enough of it is left to read,
     * and is left out otherwise.
     */
    function holdOff(cues, anim) {
        var after = anim.startAfter || 0, earliest, kept = [], i, c;
        if (!after) { return cues; }
        earliest = after + (anim.insetOpenBefore || 0) + (anim.panelFade || 0);
        for (i = 0; i < cues.length; i++) {
            c = cues[i];
            if (c.start < earliest) {
                if (c.end - earliest < (anim.minReadable || 2.5)) { continue; }
                c.start = earliest;
            }
            kept.push(c);
        }
        return kept;
    }

    function disableOverlayCaptions(target, style) {
        var captions = findBuilt(target, "captions", target.name + style.build.compSuffix),
            i, layer;
        if (!captions) { return; }
        for (i = 1; i <= target.numLayers; i++) {
            layer = target.layer(i);
            if (layer.source === captions) { layer.enabled = false; }
        }
    }

    function indexOfStr(arr, value) {
        var i;
        for (i = 0; i < arr.length; i++) { if (String(arr[i]) === String(value)) { return i; } }
        return -1;
    }

    VC.buildFromFile = function (path, optionsJson) {
        try {
            var payload = JSONLite.parse(readFile(path)),
                options = optionsJson ? JSONLite.parse(optionsJson) : {};
            return VC.build(payload, options);
        } catch (e) {
            return JSONLite.stringify({ ok: false, error: String(e.message || e) });
        }
    };

    /* What the panel needs to know about the current project. */
    VC.probe = function () {
        var item = resolveComp(), info = { ok: true, version: VC.version }, layer, i, src;
        info.hostVersion = app.version;
        /* Subtitles and the transcript are saved beside the project. */
        try {
            info.projectPath = app.project.file ? app.project.file.fsName : null;
        } catch (e) { info.projectPath = null; }

        /* Audio sitting in the Project panel: the mic recording is usually
           imported already, so offering it beats a file browser. */
        info.projectAudio = [];
        try {
            /* Deliberately distinct names: `item` above holds the active
               composition, and `var` is function-scoped, so reusing it here
               would quietly replace the comp with the last project item. */
            var picked = {}, chosen = app.project.selection, ci, pi, asset, file;
            for (ci = 0; ci < chosen.length; ci++) {
                if (chosen[ci] && chosen[ci].id) { picked[chosen[ci].id] = true; }
            }
            for (pi = 1; pi <= app.project.numItems; pi++) {
                asset = app.project.item(pi);
                if (!asset || !asset.mainSource) { continue; }
                if (!asset.hasAudio || asset.hasVideo) { continue; }
                file = asset.mainSource.file;
                if (!file) { continue; }
                info.projectAudio.push({
                    name: asset.name,
                    path: file.fsName,
                    duration: asset.duration || 0,
                    selected: !!picked[asset.id]
                });
            }
        } catch (e) {
            info.projectAudioError = String(e.message || e);
        }

        /* Font families, so the panel can offer a real choice rather than a
           hard-coded list that may not be installed.

           Only postScriptName can be relied on across hosts, so family and
           style are derived from it when the host does not supply them. */
        info.fonts = [];
        try {
            var all = app.fonts.allFonts, families = {}, name, k, meta;
            for (i = 0; i < all.length; i++) {
                meta = describeFont(all[i]);
                if (!meta) { continue; }
                if (!families[meta.family]) { families[meta.family] = {}; }
                families[meta.family][meta.style] = meta.ps;
            }
            for (name in families) {
                if (families.hasOwnProperty(name)) {
                    info.fonts.push({ family: name, styles: families[name] });
                }
            }
        } catch (e) {
            info.fontError = String(e.message || e);
        }
        if (item instanceof CompItem) {
            info.comp = item.name;
            info.width = item.width;
            info.height = item.height;
            info.fps = item.frameRate;
            info.duration = item.duration;
            info.media = null;
            info.mediaDuration = 0;

            /* A selected footage layer wins: it is the clearest statement of
               which recording the editor means. */
            try {
                var sel = item.selectedLayers;
                for (i = 0; i < sel.length; i++) {
                    if (sel[i] instanceof AVLayer && sel[i].source
                            && sel[i].source.mainSource
                            && sel[i].source.mainSource.file) {
                        info.media = sel[i].source.mainSource.file.fsName;
                        info.mediaDuration = sel[i].source.duration;
                        info.fromSelection = true;
                        break;
                    }
                }
            } catch (e) { /* no selection API on this host */ }

            if (!info.media) {
                info.media = longestFootage(item, 0);
                if (info.media) {
                    info.mediaDuration = longestFootage.duration;
                }
            }
            /* Audio-only layers are candidate mic recordings. */
            info.audioLayers = [];
            for (i = 1; i <= item.numLayers; i++) {
                layer = item.layer(i);
                if (layer instanceof AVLayer && layer.source
                        && layer.source.mainSource && layer.source.mainSource.file
                        && layer.hasAudio && !layer.hasVideo) {
                    info.audioLayers.push({
                        name: layer.name,
                        path: layer.source.mainSource.file.fsName
                    });
                }
            }

            info.width = item.width;
            info.height = item.height;
            if (info.media) {
                var inst = mediaInstances(item, info.media);
                info.instances = inst.length;
                info.editedDuration = 0;
                /* So the panel can show each verse at its time in the edit. */
                info.map = [];
                for (i = 0; i < inst.length; i++) {
                    info.editedDuration += inst[i].tMax - inst[i].tMin;
                    info.map.push({ offset: inst[i].offset, scale: inst[i].scale,
                                    tMin: inst[i].tMin, tMax: inst[i].tMax });
                }
            }
        } else {
            info.comp = null;
        }
        return JSONLite.stringify(info);
    };

    /* The longest piece of footage in the comp, precomps included: on a
       sermon timeline that is the camera recording, not a sting or a logo. */
    function longestFootage(comp, depth) {
        var i, layer, best = null, bestDur = 0, nested;
        longestFootage.duration = longestFootage.duration || 0;
        if (depth === 0) { longestFootage.duration = 0; }
        if (depth > 2) { return null; }
        for (i = 1; i <= comp.numLayers; i++) {
            layer = comp.layer(i);
            if (!(layer instanceof AVLayer) || !layer.source) { continue; }
            if (layer.source instanceof CompItem) {
                nested = longestFootage(layer.source, depth + 1);
                if (nested && longestFootage.duration > bestDur) {
                    best = nested;
                    bestDur = longestFootage.duration;
                }
            } else if (layer.source.mainSource && layer.source.mainSource.file
                    && layer.hasVideo && layer.source.duration > bestDur) {
                best = layer.source.mainSource.file.fsName;
                bestDur = layer.source.duration;
            }
        }
        longestFootage.duration = bestDur;
        return best;
    }

    /*
     * Position the mic layer against the camera layer using a measured offset.
     *
     * video_time = audio_time + offset, and each layer maps its own file time
     * to comp time by its startTime, so the audio layer simply wants to sit
     * `offset` seconds from the video layer.
     */
    VC.applySync = function (videoPath, audioPath, offset, stretchPercent) {
        var comp = resolveComp(), i, layer, videoLayer = null,
            audioLayer = null, result;
        if (!(comp instanceof CompItem)) {
            return JSONLite.stringify({ ok: false, error: "Open the sermon comp first." });
        }
        for (i = 1; i <= comp.numLayers; i++) {
            layer = comp.layer(i);
            if (!(layer instanceof AVLayer) || !layer.source) { continue; }
            if (!videoLayer && sameMedia(layer, videoPath)) { videoLayer = layer; }
            if (!audioLayer && sameMedia(layer, audioPath)) { audioLayer = layer; }
        }
        if (!videoLayer) {
            return JSONLite.stringify({ ok: false, error: "The camera layer is not in this comp." });
        }
        if (!audioLayer) {
            return JSONLite.stringify({
                ok: false,
                error: "Import the mic recording into this comp first, then apply."
            });
        }

        app.beginUndoGroup("TEC Sermon Studio — sync mic to camera");
        try {
            if (stretchPercent && Math.abs(stretchPercent - 100) > 0.0001) {
                audioLayer.stretch = stretchPercent;
            }
            audioLayer.startTime = videoLayer.startTime + Number(offset);
            result = {
                ok: true, layer: audioLayer.name,
                startTime: audioLayer.startTime,
                stretch: audioLayer.stretch
            };
        } finally {
            app.endUndoGroup();
        }
        return JSONLite.stringify(result);
    };

    /*
     * Put the cleaned mic recording into the comp, in sync, and mute the
     * original.
     *
     * The cleaned file is the same length as the one it came from and lines
     * up with it sample for sample - the engine measures the filters' own
     * delay and takes it back off - so it can simply inherit the original
     * layer's position, stretch and trims. That matters: the cues were timed
     * against the original, and a cleaned file sitting even a frame out would
     * move every one of them.
     *
     * Rebuilt rather than stacked: cleaning twice replaces the layer instead
     * of leaving two copies playing together.
     */
    VC.addCleanAudio = function (cleanedPath, originalPath) {
        var comp = resolveComp(), i, layer, original = null, existing = null,
            item, added, result, muted = false;
        if (!(comp instanceof CompItem)) {
            return JSONLite.stringify({ ok: false, error: "Open the sermon comp first." });
        }
        for (i = comp.numLayers; i >= 1; i--) {
            layer = comp.layer(i);
            if (!(layer instanceof AVLayer) || !layer.source) { continue; }
            if (sameMedia(layer, cleanedPath)) { existing = layer; continue; }
            if (originalPath && !original && sameMedia(layer, originalPath)) { original = layer; }
        }
        item = importAsset(cleanedPath);
        if (!item) {
            return JSONLite.stringify({ ok: false, error: "After Effects would not import the cleaned file." });
        }

        app.beginUndoGroup("TEC Sermon Studio \u2014 cleaned audio");
        try {
            if (existing) { existing.remove(); }
            added = comp.layers.add(item);
            added.name = "Mic (cleaned)";
            if (original) {
                added.startTime = original.startTime;
                try { added.stretch = original.stretch; } catch (e) { /* not stretchable */ }
                try {
                    added.inPoint = original.inPoint;
                    added.outPoint = original.outPoint;
                } catch (e) { /* shorter than the original: leave it full length */ }
                try {
                    original.audioEnabled = false;
                    muted = true;
                } catch (e) { /* nothing to mute */ }
                try { added.moveBefore(original); } catch (e) { /* order does not matter */ }
            }
            result = {
                ok: true, layer: added.name, startTime: added.startTime,
                synced: !!original, muted: muted, replaced: !!existing,
                comp: comp.name
            };
        } finally {
            app.endUndoGroup();
        }
        return JSONLite.stringify(result);
    };

    /* Move the playhead to a moment in the recording, wherever the edit put it. */
    VC.gotoSource = function (seconds, mediaPath) {
        var item = resolveComp(), hit;
        if (!(item instanceof CompItem)) { return "no comp"; }
        hit = sourceToComp(mediaInstances(item, mediaPath), Number(seconds));
        item.time = hit ? hit.time : Number(seconds);
        return hit ? "ok" : "cut";
    };

    /* Where the playhead is, in the edit and in the recording. The viewer may
       be on a comp Sermon Studio built; those play the sermon comp from 0. */
    VC.playhead = function (mediaPath) {
        var item = app.project.activeItem, target = resolveComp(), t, inst, i, source = null;
        if (!(target instanceof CompItem)) {
            return JSONLite.stringify({ ok: false, error: "Open the sermon comp first." });
        }
        t = (item instanceof CompItem) ? item.time : target.time;
        var range = /range:([\d.]+)-/.exec(String(item && item.comment || ""));
        if (range && item instanceof CompItem && item !== target) { t += Number(range[1]); }
        inst = mediaInstances(target, mediaPath);
        if (!inst.length) { source = t; }
        for (i = 0; i < inst.length; i++) {
            if (t >= inst[i].tMin - 0.0005 && t <= inst[i].tMax + 0.0005) {
                source = (t - inst[i].offset) / inst[i].scale;
                break;
            }
        }
        if (source === null) {
            return JSONLite.stringify({ ok: false,
                error: "The playhead is on a part of the edit without the sermon recording." });
        }
        return JSONLite.stringify({ ok: true, compTime: t, source: source });
    };

    /* ------------------------------------------------------------------ */
    /* snippets                                                            */
    /* ------------------------------------------------------------------ */
    /*
     * A vertical clip for social media: the camera in a portrait frame that
     * keeps the pastor centred, the logo above it, and captions a few words at
     * a time over the lectern that rise in and float up as they leave.
     *
     * Built as its own comp containing the sermon comp, so the edit is left
     * alone. The camera sits in a small precomp the size of the frame, which
     * crops it cleanly however far the framing pans.
     */
    function clock(t) {
        var m = Math.floor(t / 60), sec = Math.floor(t % 60);
        return m + "." + pad2(sec);
    }

    function snippetTag(target, kind, from, to) {
        return tagFor(target, kind) + " · range:" + from.toFixed(2) + "-" + to.toFixed(2);
    }

    /* The comp built for this stretch before, reused so a rebuild overwrites it. */
    function snippetComp(target, kind, name, from, to, w, h, fps) {
        var tag = snippetTag(target, kind, from, to), i, it, comp = null;
        for (i = 1; i <= app.project.numItems; i++) {
            it = app.project.item(i);
            if (it instanceof CompItem && String(it.comment) === tag) { comp = it; break; }
        }
        if (comp) {
            while (comp.numLayers > 0) { comp.layer(1).remove(); }
        } else {
            comp = app.project.items.addComp(name, w, h, 1, to - from, fps);
            comp.parentFolder = folder("TEC Sermon Studio");
        }
        comp.duration = to - from;
        comp.comment = tag;
        return comp;
    }

    /* Where a point across the camera picture (0-1) lands across the sermon
       comp: the camera layer may have been moved or scaled in the edit. */
    function cameraToComp(target, mediaPath, t, nx) {
        var i, layer, tr, pos, anc, sc;
        for (i = 1; i <= target.numLayers; i++) {
            layer = target.layer(i);
            if (layer instanceof AVLayer && layer.source && sameMedia(layer, mediaPath)
                    && t >= layer.inPoint - 0.01 && t <= layer.outPoint + 0.01) {
                tr = layer.property("ADBE Transform Group");
                pos = tr.property("ADBE Position").valueAtTime(t, false);
                anc = tr.property("ADBE Anchor Point").valueAtTime(t, false);
                sc = tr.property("ADBE Scale").valueAtTime(t, false);
                return pos[0] + (nx * layer.source.width - anc[0]) * sc[0] / 100;
            }
        }
        return nx * target.width;
    }

    function captionLayer(comp, text, fonts, cap, k) {
        var layer = comp.layers.addText(text),
            prop = layer.property("ADBE Text Properties").property("ADBE Text Document"),
            doc = prop.value, r;
        doc.resetCharStyle();
        doc.text = String(text).replace(/\n/g, "\r");
        doc.font = fonts.reference;
        doc.fontSize = cap.fontSize * k;
        doc.applyFill = true;
        doc.fillColor = rgb(cap.color);
        doc.applyStroke = cap.strokeWidth > 0;
        if (doc.applyStroke) {
            doc.strokeColor = rgb(cap.strokeColor);
            doc.strokeWidth = cap.strokeWidth * k;
            doc.strokeOverFill = false;
        }
        doc.justification = ParagraphJustification.CENTER_JUSTIFY;
        doc.autoLeading = false;
        doc.leading = cap.leading * k;
        prop.setValue(doc);
        r = layer.sourceRectAtTime(0, false);
        layer.property("ADBE Transform Group").property("ADBE Anchor Point")
            .setValue([r.left + r.width / 2, r.top + r.height / 2]);
        return layer;
    }

    function buildSnippet(data) {
        var style = data.style, sn = style.snippet, slide = style.slide,
            target = resolveComp(), W = sn.size[0], H = sn.size[1], k = W / sn.reference[0],
            from = data.from, to = data.to, dur = to - from, fps, name, comp, cam, inner,
            box, factor, zoom, overflowY, y, keys, pos, i, t, sx, px, minX, maxX, holdAt,
            camLayer, asset, backdrop, frame, logoAsset, logo, fonts, cap, capY, c, layer,
            a, b, rise, tin, tout, op, ps, levels, fa, built = 0;
        if (!(target instanceof CompItem)) { throw new Error("Open the sermon comp first."); }
        if (!(dur > 1)) { throw new Error("Pick a start and an end at least a second apart."); }
        fps = target.frameRate;
        name = target.name + " — Snippet " + clock(from) + "-" + clock(to);
        box = { x: sn.frame.x * k, y: sn.frame.y * k, w: sn.frame.width * k, h: sn.frame.height * k };
        fonts = { reference: resolveFont(style.fonts.reference) };

        app.beginUndoGroup("TEC Sermon Studio — snippet");
        try {
            comp = snippetComp(target, "snippet", name, from, to, W, H, fps);

            /* Backdrop, as on the slide layout. */
            asset = importAsset(slide.background.image);
            if (asset) {
                backdrop = comp.layers.add(asset);
                fitToComp(backdrop, comp);
            } else {
                backdrop = comp.layers.addShape();
                addRect(backdrop, "Fill", 0, 0, W, H, rgb(slide.background.fill), 0);
                backdrop.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([0, 0]);
                backdrop.property("ADBE Transform Group").property("ADBE Position").setValue([0, 0]);
            }
            backdrop.name = "Backdrop";

            /* The camera, following the pastor inside a frame-sized precomp. */
            cam = snippetComp(target, "snippet-camera", name + " (camera)", from, to,
                              Math.round(box.w), Math.round(box.h), fps);
            inner = cam.layers.add(target);
            inner.name = target.name;
            inner.startTime = -from;
            zoom = (sn.camera && sn.camera.zoom) || 1;
            factor = cam.height / target.height * zoom;
            inner.property("ADBE Transform Group").property("ADBE Scale").setValue([factor * 100, factor * 100]);
            overflowY = Math.max(0, target.height * factor - cam.height);
            y = cam.height / 2 + (0.5 - ((sn.camera && sn.camera.focusY) || 0.5)) * overflowY;
            minX = cam.width - target.width * factor / 2;
            maxX = target.width * factor / 2;
            keys = (data.follow && data.follow.length) ? data.follow : [[0, 0.5]];
            pos = inner.property("ADBE Transform Group").property("ADBE Position");
            if (keys.length === 1) {
                sx = cameraToComp(target, data.media, from + keys[0][0], keys[0][1]);
                pos.setValue([Math.max(minX, Math.min(maxX, cam.width / 2 - (sx - target.width / 2) * factor)), y]);
            } else {
                for (i = 0; i < keys.length; i++) {
                    t = Math.max(0, Math.min(dur, keys[i][0]));
                    sx = cameraToComp(target, data.media, from + t, keys[i][1]);
                    px = cam.width / 2 - (sx - target.width / 2) * factor;
                    pos.setValueAtTime(t, [Math.max(minX, Math.min(maxX, px)), y]);
                }
                easeKeys(pos, 50);
                /* Where the edit cuts, the framing jumps with it rather than panning. */
                for (i = 0; i < (data.cuts || []).length; i++) {
                    holdAt = pos.nearestKeyIndex(data.cuts[i]);
                    if (holdAt > 1) {
                        try {
                            pos.setInterpolationTypeAtKey(holdAt - 1,
                                KeyframeInterpolationType.HOLD, KeyframeInterpolationType.HOLD);
                        } catch (e) { /* leave it eased */ }
                    }
                }
            }
            camLayer = comp.layers.add(cam);
            camLayer.name = "Camera";
            camLayer.property("ADBE Transform Group").property("ADBE Position")
                .setValue([box.x + box.w / 2, box.y + box.h / 2]);
            /* No click where the clip starts and stops mid-sentence. */
            fa = sn.fadeAudio || 0;
            if (fa > 0) {
                try {
                    levels = camLayer.property("ADBE Audio Group").property("ADBE Audio Levels");
                    levels.setValueAtTime(0, [-48, -48]);
                    levels.setValueAtTime(fa, [0, 0]);
                    levels.setValueAtTime(Math.max(fa, dur - fa), [0, 0]);
                    levels.setValueAtTime(dur, [-48, -48]);
                } catch (e) { /* no audio to fade */ }
            }

            /* Frame, in the slide layout's border colour. */
            if (slide.camera.border.width > 0) {
                frame = comp.layers.addShape();
                frame.name = "Frame";
                addRectOutline(frame, "Frame", box.x, box.y, box.w, box.h,
                    rgb(slide.camera.border.color), (sn.borderWidth || 6) * k, 0);
                frame.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([0, 0]);
                frame.property("ADBE Transform Group").property("ADBE Position").setValue([0, 0]);
            }

            /* Logo above the frame. */
            logoAsset = importAsset(slide.logo.image);
            if (logoAsset) {
                logo = comp.layers.add(logoAsset);
                logo.name = "Logo";
                var lw = logoAsset.width || 1, lh = logoAsset.height || 1,
                    ls = Math.min(sn.logo.width * k / lw, (sn.logo.maxHeight || 1e9) * k / lh);
                logo.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([lw / 2, lh / 2]);
                logo.property("ADBE Transform Group").property("ADBE Scale").setValue([ls * 100, ls * 100]);
                logo.property("ADBE Transform Group").property("ADBE Position")
                    .setValue([W / 2, sn.logo.top * k + lh * ls / 2]);
            }

            /* Captions over the lectern: rise in, float up and away. */
            cap = sn.caption;
            capY = box.y + box.h * cap.y;
            rise = cap.rise * k;
            for (i = 0; i < data.captions.length; i++) {
                c = data.captions[i];
                a = Math.max(0, c[0]);
                b = Math.min(dur, c[1]);
                if (b - a < 0.1) { continue; }
                tin = Math.min(cap["in"], (b - a) * 0.4);
                tout = cap.out;
                layer = captionLayer(comp, c[2], fonts, cap, k);
                layer.name = "Caption " + pad2(i + 1);
                setSpan(layer, Math.max(0, a - 0.06), Math.min(dur, b + tout), comp);
                ps = layer.property("ADBE Transform Group").property("ADBE Position");
                op = layer.property("ADBE Transform Group").property("ADBE Opacity");
                ps.setValueAtTime(Math.max(0, a - 0.06), [W / 2, capY + rise]);
                ps.setValueAtTime(a + tin, [W / 2, capY]);
                ps.setValueAtTime(Math.max(a + tin, b - 0.05), [W / 2, capY]);
                ps.setValueAtTime(Math.min(dur, b + tout), [W / 2, capY - rise]);
                op.setValueAtTime(Math.max(0, a - 0.06), 0);
                op.setValueAtTime(a + tin, 100);
                op.setValueAtTime(Math.max(a + tin, b - 0.05), 100);
                op.setValueAtTime(Math.min(dur, b + tout), 0);
                easeKeys(ps, 70);
                easeKeys(op, 70);
                built++;
            }
        } finally {
            app.endUndoGroup();
        }
        try { comp.openInViewer(); } catch (e) { /* the panel says where it is */ }
        return { ok: true, comp: comp.name, captions: built, follow: keys.length,
                 width: W, height: H, duration: dur };
    }

    VC.buildSnippet = function (path) {
        try {
            return JSONLite.stringify(buildSnippet(JSONLite.parse(readFile(path))));
        } catch (e) {
            return JSONLite.stringify({ ok: false, error: String(e.message || e) });
        }
    };

    VC.gotoComp = function (seconds) {
        var item = resolveComp();
        if (!(item instanceof CompItem)) { return "no comp"; }
        item.time = Math.max(0, Math.min(item.duration, Number(seconds)));
        try { item.openInViewer(); } catch (e) { /* already open */ }
        return "ok";
    };

    /* Exposed for the offline test harness in tests/; not used by the panel. */
    VC._internals = {
        holdOff: holdOff,
        planSlide: planSlide,
        sceneCuts: sceneCuts,
        emptyPanel: emptyPanel,
        setSpan: setSpan,
        resolveComp: resolveComp,
        slideComp: slideComp,
        captionsComp: captionsComp,
        mediaInstances: mediaInstances,
        sourceToComp: sourceToComp,
        mapCue: mapCue,
        collectInstances: collectInstances
    };

    global.SermonStudio = VC;
    /* The old name still works, for anything saved before the rename. */
    global.VerseCaster = VC;

    /* Direct run from File > Scripts. */
    if (typeof global.__vcPanelHost === "undefined" && app.project) {
        (function () {
            var f;
            try {
                f = File.openDialog("Choose a TEC Sermon Studio cue file", "*.json");
                if (f) {
                    var out = JSONLite.parse(VC.buildFromFile(f.fsName, null));
                    alert(out.ok
                        ? ("TEC Sermon Studio built " + out.built + " verse cards in \"" + out.comp + "\".")
                        : ("Sermon Studio: " + out.error));
                }
            } catch (e) {
                alert("TEC Sermon Studio: " + e.message);
            }
        }());
    }
}(this));
