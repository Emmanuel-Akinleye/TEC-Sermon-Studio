// A small After Effects object model: enough to run a whole build through the
// real SermonStudio.jsx (buildStart -> buildStep -> buildFinish) and then look
// at what it made - every layer's span and opacity keyframes.

function CompItem() {} function AVLayer() {} function FolderItem() {} function FootageItem() {}
function TextLayer() {} function ShapeLayer() {}
var ParagraphJustification = { LEFT_JUSTIFY: 1, CENTER_JUSTIFY: 2, RIGHT_JUSTIFY: 3 };
function KeyframeEase(speed, influence) { this.speed = speed; this.influence = influence; }
function Shape() {}
function ImportOptions(file) { this.file = file; }
function TextDocument(text) { this.text = text; this.fontSize = 40; this.leading = 50; }
TextDocument.prototype.resetCharStyle = function () {};

function File(path) {
    this.fsName = path; this.name = String(path).replace(/^.*\//, "");
    this.exists = typeof FILES !== "undefined" && FILES.hasOwnProperty(path);
}
File.prototype.open = function () { return true; };
File.prototype.read = function () { return FILES[this.fsName]; };
File.prototype.write = function () {};
File.prototype.close = function () {};

function Prop(name) {
    this.name = name; this.value = null; this.keys = []; this.children = {};
}
Prop.prototype.property = function (name) {
    if (!this.children[name]) {
        this.children[name] = new Prop(name);
        if (name === "ADBE Text Document") { this.children[name].value = new TextDocument(""); }
        if (name === "ADBE Position" || name === "ADBE Anchor Point") { this.children[name].value = [0, 0]; }
    }
    return this.children[name];
};
Prop.prototype.addProperty = function (name) { return new Prop(name); };
Prop.prototype.setValue = function (v) { this.value = v; };
Prop.prototype.setValueAtTime = function (t, v) {
    for (var i = 0; i < this.keys.length; i++) {
        if (Math.abs(this.keys[i].t - t) < 1e-6) { this.keys[i].v = v; return; }
    }
    this.keys.push({ t: t, v: v });
    this.keys.sort(function (a, b) { return a.t - b.t; });
};
Object.defineProperty(Prop.prototype, "numKeys", { get: function () { return this.keys.length; } });
Prop.prototype.setTemporalEaseAtKey = function () {};
Prop.prototype.valueAtTime = function (t) { return this.keys.length ? this.at(t) : this.value; };
Prop.prototype.nearestKeyIndex = function (t) {
    var best = 1, d = 1e9;
    for (var i = 0; i < this.keys.length; i++) if (Math.abs(this.keys[i].t - t) < d) { d = Math.abs(this.keys[i].t - t); best = i + 1; }
    return best;
};
Prop.prototype.setInterpolationTypeAtKey = function (i, a) { this.keys[i - 1].hold = a === 1; };
var KeyframeInterpolationType = { HOLD: 1, LINEAR: 2, BEZIER: 3 };
Prop.prototype.keyTime = function (i) { return this.keys[i - 1].t; };
Prop.prototype.keyValue = function (i) { return this.keys[i - 1].v; };
Prop.prototype.at = function (t) {                       // linear, which is enough here
    var k = this.keys, i;
    if (!k.length) { return this.name === "ADBE Opacity" ? 100 : this.value; }
    if (t <= k[0].t) { return k[0].v; }
    for (i = 1; i < k.length; i++) {
        if (t <= k[i].t) {
            var f = (t - k[i - 1].t) / (k[i].t - k[i - 1].t);
            if (k[i - 1].hold) f = 0;
            if (k[i].v instanceof Array)
                return k[i].v.map(function (v, n) { return k[i - 1].v[n] + (v - k[i - 1].v[n]) * f; });
            return k[i - 1].v + (k[i].v - k[i - 1].v) * f;
        }
    }
    return k[k.length - 1].v;
};

var nextId = 1, items = [];

function makeLayer(kind, comp, source) {
    var L = kind === "text" ? new TextLayer() : kind === "shape" ? new ShapeLayer() : new AVLayer();
    L.kind = kind; L.source = source || null; L.name = source ? source.name : kind;
    L.startTime = 0; L._in = 0; L._out = comp.duration; L.enabled = true; L.comp = comp;
    L.props = new Prop("root");
    L.hasVideo = !!source;
    Object.defineProperty(L, "inPoint", { get: function () { return this._in; },
                                          set: function (v) { this._in = v; } });
    Object.defineProperty(L, "outPoint", { get: function () { return this._out; },
                                           set: function (v) { this._out = v; } });
    L.property = function (n) { return this.props.property(n); };
    L.sourceRectAtTime = function () {
        if (this.kind === "text") {
            // Word-wrapped like real text: average glyph ~0.52em wide, so a
            // smaller size fits more on a line as well as shorter lines.
            var doc = this.props.property("ADBE Text Properties").property("ADBE Text Document").value;
            var width = this.boxWidth || 1e9, perLine = Math.max(1, width / (doc.fontSize * 0.52));
            var words = String(doc.text || "").split(/\s+/), lines = 1, used = 0, w;
            for (var i = 0; i < words.length; i++) {
                w = words[i].length + (used ? 1 : 0);
                if (used + w > perLine && used) { lines++; used = words[i].length; } else { used += w; }
            }
            var chars = String(doc.text || "").length;
            return { left: 0, top: -doc.fontSize,
                     width: Math.min(width, chars * doc.fontSize * 0.52),
                     height: lines * (doc.leading || doc.fontSize * 1.2) };
        }
        return { left: 0, top: 0, width: (source && source.width) || 3840,
                 height: (source && source.height) || 2160 };
    };
    L.moveAfter = function () {}; L.moveBefore = function () {}; L.moveToEnd = function () {};
    L.moveToBeginning = function () {};
    L.remove = function () { var a = comp._layers; a.splice(a.indexOf(this), 1); };
    L.opacityAt = function (t) {
        if (t < this._in || t >= this._out || !this.enabled) { return 0; }
        return this.props.property("ADBE Transform Group").property("ADBE Opacity").at(t);
    };
    return L;
}

function comp(name, w, h, duration) {
    var c = new CompItem();
    c.id = nextId++; c.name = name; c.comment = ""; c.width = w || 3840; c.height = h || 2160;
    c.pixelAspect = 1; c.duration = duration || 3000; c.frameRate = 29.97; c._layers = [];
    Object.defineProperty(c, "numLayers", { get: function () { return this._layers.length; } });
    c.openInViewer = function () {};
    c.layer = function (i) { return this._layers[i - 1]; };
    c.layers = {
        add: function (src) {
            // As After Effects does: centred, anchored at the middle, full size.
            var L = makeLayer("av", c, src), tr = L.property("ADBE Transform Group");
            tr.property("ADBE Anchor Point").value = [(src.width || c.width) / 2, (src.height || c.height) / 2];
            tr.property("ADBE Position").value = [c.width / 2, c.height / 2];
            tr.property("ADBE Scale").value = [100, 100];
            c._layers.unshift(L); return L;
        },
        addShape: function () { var L = makeLayer("shape", c); c._layers.unshift(L); return L; },
        addText: function (t) { var L = makeLayer("text", c); L.property("ADBE Text Properties")
            .property("ADBE Text Document").value.text = t; c._layers.unshift(L); return L; },
        addBoxText: function (size) { var L = makeLayer("text", c); L.boxWidth = size[0]; c._layers.unshift(L); return L; },
        addNull: function () { var L = makeLayer("null", c); c._layers.unshift(L); return L; }
    };
    items.push(c);
    return c;
}

function footage(path, w, h, duration) {
    var f = new FootageItem();
    f.id = nextId++; f.name = path.replace(/^.*\//, ""); f.width = w; f.height = h; f.duration = duration;
    f.mainSource = { file: { fsName: path, name: f.name } };
    items.push(f);
    return f;
}

var app = {
    project: {
        activeItem: null,
        get numItems() { return items.length; },
        item: function (i) { return items[i - 1]; },
        items: {
            addComp: function (n, w, h, pa, d) { return comp(n, w, h, d); },
            addFolder: function (n) { var f = new FolderItem(); f.name = n; items.push(f); return f; }
        },
        importFile: function (o) { return footage(o.file.fsName, 1920, 1080, 0); }
    },
    beginUndoGroup: function () {}, endUndoGroup: function () {}
};
var __vcPanelHost = true, $ = { global: {} };
