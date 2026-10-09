/*
 * Offline check of the source-time -> comp-time mapping.
 *
 * This is the logic that decides where each verse card lands once the sermon
 * footage has been trimmed, split and nested in the edit, so it is worth
 * testing without needing After Effects open.
 */
var MEDIA = "/Volumes/Media/sermon.mp4";

function CompItem() {}
function AVLayer() {}
function FolderItem() {}

function footage(path, duration) {
    return { name: path.replace(/^.*\//, ""), duration: duration,
             mainSource: { file: { fsName: path, name: path.replace(/^.*\//, "") } } };
}

function layer(source, startTime, inPoint, outPoint, stretch) {
    var l = new AVLayer();
    l.source = source;
    l.startTime = startTime;
    l.inPoint = inPoint;
    l.outPoint = outPoint;
    l.stretch = stretch || 100;
    l.hasVideo = true;
    l.name = "layer";
    return l;
}

function comp(duration, layers) {
    var c = new CompItem();
    c.duration = duration;
    c.numLayers = layers.length;
    c.layer = function (i) { return layers[i - 1]; };
    return c;
}

var passed = 0, failed = 0;

function check(name, actual, expected) {
    var ok;
    if (expected === null) {
        ok = (actual === null);
    } else {
        ok = actual !== null && Math.abs(actual - expected) < 0.01;
    }
    if (ok) { passed++; print("  ok   " + name); }
    else { failed++; print("  FAIL " + name + "  got " + actual + " want " + expected); }
}

var VC = SermonStudio._internals;

function mapAt(target, seconds) {
    var inst = VC.mediaInstances(target, MEDIA),
        hit = VC.sourceToComp(inst, seconds);
    return hit ? hit.time : null;
}

/* 1. Whole recording dropped in untouched. */
var src = footage(MEDIA, 3600);
var plain = comp(3600, [layer(src, 0, 0, 3600)]);
print("\nuntrimmed footage at comp start");
check("source 0 -> 0", mapAt(plain, 0), 0);
check("source 1800 -> 1800", mapAt(plain, 1800), 1800);

/* 2. Head trimmed: first 12 minutes of pre-service cut off. */
var trimmed = comp(2880, [layer(src, -720, 0, 2880)]);
print("\nhead trimmed (first 720s removed)");
check("source 600 was cut", mapAt(trimmed, 600), null);
check("source 720 -> 0", mapAt(trimmed, 720), 0);
check("source 1000 -> 280", mapAt(trimmed, 1000), 280);

/* 3. Split in two with a chunk removed from the middle. */
var split = comp(1200, [
    layer(src, 0, 0, 600),        /* source 0-600   -> comp 0-600   */
    layer(src, -300, 600, 1200)   /* source 900-1500 -> comp 600-1200 */
]);
print("\nsplit with the middle removed");
check("source 300 -> 300", mapAt(split, 300), 300);
check("source 750 was cut", mapAt(split, 750), null);
check("source 900 -> 600", mapAt(split, 900), 600);
check("source 1100 -> 800", mapAt(split, 1100), 800);

/* 4. Footage inside a precomp that is itself offset. */
var inner = comp(1800, [layer(src, 0, 0, 1800)]);
var outerLayer = layer(inner, 60, 60, 1860);
var outer = comp(1920, [outerLayer]);
print("\nfootage nested in an offset precomp");
check("source 0 -> 60", mapAt(outer, 0), 60);
check("source 500 -> 560", mapAt(outer, 500), 560);

/* 5. A cue that straddles a cut keeps its start and ends at the cut. */
print("\ncue straddling a cut");
var inst = VC.mediaInstances(split, MEDIA);
var straddle = VC.mapCue(inst, { start: 580, end: 640 });
check("in stays at 580", straddle.inTime, 580);
check("out clamps to the cut at 600", straddle.outTime, 600);

var goneEntirely = VC.mapCue(inst, { start: 700, end: 760 });
check("cue inside the removed section is dropped",
      goneEntirely === null ? null : goneEntirely.inTime, null);

/* 6. No matching footage: build straight onto the comp timeline. */
print("\nmedia not present in the comp");
var other = comp(600, [layer(footage("/x/other.mp4", 600), 0, 0, 600)]);
var fallback = VC.mapCue(VC.mediaInstances(other, MEDIA), { start: 30, end: 40 });
check("falls back to raw cue time", fallback.inTime, 30);

print("\n" + passed + " passed, " + failed + " failed");
if (failed > 0) { throw new Error(failed + " mapping tests failed"); }
