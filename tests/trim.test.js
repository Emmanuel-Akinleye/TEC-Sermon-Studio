// Layers must land exactly where they are put, whether the host trims or
// slides a layer when its in point is set. Text layers in After Effects have
// been seen to slide: a card meant for 4:30-4:42 ran from 4:30 to 9:12.
var V = SermonStudio._internals, comp = { duration: 5000 };
var pass = 0, fail = 0;
function ok(name, cond) { if (cond) { pass++; print("  ok   " + name); } else { fail++; print("  FAIL " + name); } }

function layer(slides) {
    var L = { startTime: 0, _in: 0, _out: comp.duration };
    Object.defineProperty(L, "inPoint", {
        get: function () { return this._in; },
        set: function (v) {
            if (slides) {                       // move the whole bar, keep its length
                var len = this._out - this._in;
                this.startTime += v - this._in; this._in = v; this._out = v + len;
            } else { this._in = v; }
        }
    });
    Object.defineProperty(L, "outPoint", {
        get: function () { return this._out; }, set: function (v) { this._out = v; }
    });
    return L;
}

function oldWay(L, a, b) { L.outPoint = b; L.inPoint = a; }       // what the build used to do

var bad = layer(true); oldWay(bad, 270, 282);
ok("the old order reproduces the bug (" + bad.inPoint + "-" + bad.outPoint + ")", bad.outPoint === 552);

[["a layer that trims", false], ["a layer that slides", true]].forEach(function (c) {
    var L = layer(c[1]);
    V.setSpan(L, 270, 282, comp);
    ok(c[0] + " ends up at 270-282 (got " + L.inPoint + "-" + L.outPoint + ")",
       Math.abs(L.inPoint - 270) < 0.01 && Math.abs(L.outPoint - 282) < 0.01);
    V.setSpan(L, 30, 41, comp);
    ok(c[0] + " can be moved earlier again (got " + L.inPoint + "-" + L.outPoint + ")",
       Math.abs(L.inPoint - 30) < 0.01 && Math.abs(L.outPoint - 41) < 0.01);
});
var edge = layer(true); V.setSpan(edge, 4990, 5100, comp);
ok("a span past the end of the comp is clipped to it", edge.outPoint === 5000);
print("\n  " + pass + " passed, " + fail + " failed");
