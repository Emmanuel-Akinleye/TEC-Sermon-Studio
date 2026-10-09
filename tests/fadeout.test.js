// The slide timeline: the panel, backdrop, camera and logo come and go
// together, and the panel is never up without a verse in it.
var V = SermonStudio._internals, pass = 0, fail = 0;
function ok(n, c) { if (c) { pass++; print("  ok   " + n); } else { fail++; print("  FAIL " + n); } }
function near(a, b) { return Math.abs(a - b) < 0.01; }

var anim = { startAfter: 10, insetOpenBefore: 0.5, panelFade: 0.6, minReadable: 2.5,
             holdGap: 20, minFullFrame: 4, insetCloseAfter: 1.5, insetMinWindow: 4,
             readingRate: 3.3, maxReadable: 12 };
function v(ref, start, end, words) {
    return { ref: ref, start: start, end: end, translation: "ESV",
             text: new Array((words || 10) + 1).join("word ") };
}

// A reading, then preaching, then the verse again unnamed, then a new verse.
var plan = V.planSlide([
    v("Phil 2:1", 132, 150), v("Phil 2:2", 150, 160),       // read back to back
    v("Phil 2:2", 177, 186),                                 // quoted again 17s later
    v("Phil 2:1", 187, 192),                                 // "verse 1 stands as..."
    v("Phil 1:12", 240, 248),                                // 48s later: its own section
    v("Acts 10:38", 400, 403, 60)                            // long verse, said briefly
], anim);
ok("three panel sections (" + plan.windows.length + ")", plan.windows.length === 3);
ok("no stretch of empty panel", V.emptyPanel(plan, 0.05).length === 0);
ok("the same verse again within the hold gap is one card",
   plan.items[1].ref === "Phil 2:2" && plan.items[2].ref === "Phil 2:1");
ok("a verse holds until the next one replaces it (" + plan.items[1].showOut + ")",
   near(plan.items[1].showOut, 187));
ok("the first verse of a section opens it", plan.items[0].opening === plan.windows[0]);
ok("verses after the first do not reopen it",
   plan.items[1].opening === null && plan.items[2].opening === null);
ok("the last verse of a section closes it and holds to its end",
   plan.items[2].closing === plan.windows[0] && near(plan.items[2].showOut, plan.windows[0].end));
ok("verses that hand over do not close the section",
   plan.items[0].closing === null && plan.items[1].closing === null);
ok("a long verse stays up long enough to read (" +
   (plan.windows[2].end - plan.windows[2].start).toFixed(1) + "s)",
   plan.windows[2].end - plan.windows[2].start >= 12);

// Close verses never drop to full frame for a moment in between.
var close = V.planSlide([v("A", 50, 55), v("B", 58.5, 64)], anim);
ok("no flash of full frame between close sections", close.windows.length === 1);
var apart = V.planSlide([v("A", 50, 55), v("B", 90, 96)], anim);
var gap = (apart.windows[1].start - 0.6) - (apart.windows[0].end + 0.6);
ok("sections far apart leave the camera full frame in between (" + gap.toFixed(1) + "s)",
   apart.windows.length === 2 && gap >= 4);

// Nothing from the panel in the first 10 seconds.
var early = V.planSlide([v("A", 0, 10.5), v("B", 3, 25), v("C", 30, 40)], anim);
ok("a verse over before there is time to read it is left out",
   early.items.length === 2 && early.items[0].ref === "B");
ok("the panel's dissolve starts no earlier than 10s (" +
   (early.windows[0].start - 0.6).toFixed(2) + "s)", early.windows[0].start - 0.6 >= 10 - 0.001);
ok("...and the text dissolves in with it",
   early.items[0].opening === early.windows[0]);

// A longer wait keeps the panel away until after the intro.
var late = V.planSlide([v("A", 12, 40), v("B", 50, 62), v("C", 120, 130)],
                       { startAfter: 45, insetOpenBefore: 0.5, panelFade: 0.6, minReadable: 2.5,
                         holdGap: 20, minFullFrame: 4, insetCloseAfter: 1.5, insetMinWindow: 4,
                         readingRate: 3.3, maxReadable: 12 });
ok("nothing from the panel before the wait is over (" + (late.windows[0].start - 0.6).toFixed(1) + "s)",
   late.windows[0].start - 0.6 >= 45 - 0.001);
ok("a verse still being read when the wait ends comes in then",
   late.items[0].ref === "B" || Math.abs(late.items[0].showIn - late.windows[0].start) < 0.01);
ok("verses over before then are left out", !late.items.some(function (i) { return i.ref === "A" && i.showOut < 45; }));

// The edit cuts away: the panel goes with it, and comes back after.
var reading = [v("Isaiah 46:11", 100, 108), v("Isaiah 46:12", 108, 116),
               v("Isaiah 46:13", 130, 138)];
var cutAt = 120;                                  // reader out, preacher in
var split = V.planSlide(reading, anim, [cutAt]);
ok("the panel closes at the cut (" + split.windows[0].end.toFixed(1) + "s)",
   split.windows[0].end + anim.panelFade <= cutAt + 0.001);
ok("...and opens again for what follows", split.windows.length === 2 &&
   split.windows[1].start >= cutAt);
ok("no verse is left on screen over the cut",
   split.items.every(function (i) { return i.showOut <= cutAt || i.showIn >= cutAt; }));
ok("without a cut they would have been one section",
   V.planSlide(reading, anim, []).windows.length === 1);

// A verse that starts right after a cut does not dissolve in over it.
var after = V.planSlide([v("Isaiah 46:13", 121, 130)], anim, [cutAt]);
ok("the dissolve starts after the cut, not before",
   after.windows[0].start >= cutAt && after.windows[0].start - 0.001 <= 121);

// A card too short to see still gets a proper section.
var brief = V.planSlide([v("A", 100, 101, 2)], anim);
ok("a very short section is lengthened, not dropped",
   brief.windows.length === 1 && brief.windows[0].end - brief.windows[0].start >= 4);
print("\n  " + pass + " passed, " + fail + " failed");
