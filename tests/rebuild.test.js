// Build twice, from the sermon or from the built comp: one comp, reused.

var V = SermonStudio._internals, style = { build: { slideCompSuffix: " — Slide Layout", compSuffix: " — Verse Captions" } };
var sermon = comp("23:08:2026");
var pass = 0, fail = 0;
function ok(name, cond){ if (cond) { pass++; print("  ok   " + name); } else { fail++; print("  FAIL " + name); } }
function slides(){ return items.filter(function(x){ return x instanceof CompItem && /layout:slide/.test(x.comment); }).length; }

app.project.activeItem = sermon;
var first = V.slideComp(V.resolveComp(), style);
ok("first build makes one slide comp", slides() === 1 && first.name === "23:08:2026 — Slide Layout");

app.project.activeItem = first;                       // user is now looking at the built comp
ok("Build while viewing the built comp still targets the sermon", V.resolveComp() === sermon);
var second = V.slideComp(V.resolveComp(), style);
ok("rebuild reuses it instead of making another", second === first && slides() === 1);
ok("reused comp is emptied before rebuilding", second.numLayers === 0);

first.name = "Sunday final";                          // editor renames it
var third = V.slideComp(V.resolveComp(), style);
ok("still reused after being renamed", third === first && slides() === 1);

var cap1 = V.captionsComp(sermon, {}, style), cap2 = V.captionsComp(sermon, {}, style);
ok("lower-third captions comp is reused too", cap1 === cap2);
ok("captions and slide comps are kept separate", cap1 !== first);

var other = comp("30:08:2026"); app.project.activeItem = other;
var otherSlide = V.slideComp(V.resolveComp(), style);
ok("a different sermon gets its own comp", otherSlide !== first && slides() === 2);

var legacy = comp("Old — Slide Layout"); var old = comp("Old");
var reusedLegacy = V.slideComp(old, style);
ok("a comp built before tagging is reused by name", reusedLegacy === legacy);

// Built before the rename: still recognised, still overwritten.
var older = comp("07:09:2026");
var oldBuilt = comp("07:09:2026 — Slide Layout");
oldBuilt.comment = "TEC Verse Caster \u00b7 source:" + older.id + " \u00b7 layout:slide";
app.project.activeItem = older;
var again = V.slideComp(V.resolveComp(), style);
ok("a comp built under the old name is reused, not duplicated", again === oldBuilt);
ok("...and carries the new name from then on", /^TEC Sermon Studio/.test(again.comment));
app.project.activeItem = oldBuilt;
ok("Build while viewing it still targets its sermon", V.resolveComp() === older);

print("\n  " + pass + " passed, " + fail + " failed");
