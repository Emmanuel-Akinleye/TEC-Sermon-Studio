var pass = 0, fail = 0;
function ok(n, c) { if (c) { pass++; print("  ok   " + n); } else { fail++; print("  FAIL " + n); } }
fs = fakeFs; path = fakePath; childProcess = fakeChild; process = window.process;
projectRoot = "/proj"; extensionRoot = "/ext";

// Settings survive closing the panel.
document.getElementById("layout").value = "slide";
savePanelSettings();
document.getElementById("layout").value = "lower-third";
loadPanelSettings();
ok("the chosen layout is remembered", document.getElementById("layout").value === "slide");

// The camera reused a file name: last month's analysis must not load.
var MEDIA = "/Volumes/CARD/C0001.MP4";
DISK[MEDIA] = new Array(5001).join("x"); MTIME[MEDIA] = 2000;
function cue(ref, start, end, extra) {
    var c = { ref: ref, start: start, end: end, duration: end - start, enabled: true, text: ref + " text",
              translation: "ESV", source: "spoken+read", notes: [] };
    for (var k in extra || {}) c[k] = extra[k];
    return c;
}
DISK["/proj/out/C0001.cues.json"] = JSON.stringify({ media: MEDIA, media_identity: "C0001.MP4|999|1000",
    generated: "2026-08-30 10:00:00", cues: [cue("Romans 1:1", 10, 20)] });
DISK["/proj/out/C0001 (2).cues.json"] = JSON.stringify({ media: MEDIA, media_identity: identityOf(MEDIA),
    generated: "2026-09-21 10:00:00", cues: [cue("John 3:16", 10, 20)] });
var found = findAnalysis(MEDIA);
ok("the analysis of this recording is found, not the old one with its name",
   found && found.payload.cues[0].ref === "John 3:16");
DISK["/proj/out/C0001 (2).cues.selected.json"] = JSON.stringify({ media: MEDIA, generated: "2026-09-20 09:00:00",
    cues: [cue("John 3:16", 10, 20, { enabled: false })] });
ok("review ticks from an older analysis are not preferred",
   findAnalysis(MEDIA).payload.generated === "2026-09-21 10:00:00");
DISK["/proj/out/C0001 (2).cues.selected.json"] = JSON.stringify({ media: MEDIA, generated: "2026-09-21 10:00:00",
    cues: [cue("John 3:16", 10, 20, { enabled: false })] });
ok("review ticks from the same analysis are restored",
   findAnalysis(MEDIA).payload.cues[0].enabled === false);

// No mic, no analysis.
state.media = MEDIA; state.audio = null; state.payload = null;
updateReadiness();
ok("Analyze is off until a mic recording is chosen", document.getElementById("analyze").disabled === true);
setAudio("/Users/test/Downloads/service.WAV", true);
ok("...and on once one is", document.getElementById("analyze").disabled === false);

// Times in the list are edit times.
state.map = [{ offset: 5.8, scale: 1, tMin: 5.8, tMax: 1000 }];
ok("a verse shows at its time in the edit", Math.abs(compTimeOf(100) - 105.8) < 1e-9);
ok("a verse in a part cut from the edit says so", compTimeOf(2000) === null);

// Review choices survive a re-analysis.
var before = { media: MEDIA, cues: [
    cue("Romans 1:24", 50, 60, { enabled: true, userSet: true, origRef: "Romans 1:24" }),
    cue("Philippians 2:1", 100, 110, { enabled: false, userSet: true }),
    cue("Philippians 2:2", 130, 142, { userSet: true, userTimes: true, origStart: 128 }),
    cue("John 3:16", 300, 306, { source: "added" })
] };
before.cues[0].ref = "Genesis 2:24"; before.cues[0].book = "Genesis"; before.cues[0].chapter = 2; before.cues[0].verse = 24;
var after = { media: MEDIA, cues: [cue("Romans 1:24", 51, 59), cue("Philippians 2:1", 100.5, 111),
                                   cue("Philippians 2:2", 128.2, 140), cue("Hebrews 12:2", 400, 405)] };
carryOver(before, after);
var refs = after.cues.map(function (c) { return c.ref; });
ok("a verse you corrected stays corrected", refs.indexOf("Genesis 2:24") >= 0 && refs.indexOf("Romans 1:24") < 0);
ok("a verse you switched off stays off",
   after.cues.filter(function (c) { return c.ref === "Philippians 2:1"; })[0].enabled === false);
ok("a card you retimed keeps your times",
   after.cues.filter(function (c) { return c.ref === "Philippians 2:2"; })[0].start === 130);
ok("a verse you added is kept", refs.indexOf("John 3:16") >= 0);
ok("new finds are kept too", refs.indexOf("Hebrews 12:2") >= 0);
ok("still in time order", after.cues.every(function (c, i, a) { return !i || a[i - 1].start <= c.start; }));

// The list renders, and the tools work.
state.payload = { media: MEDIA, fps: 29.97, generated: "g", cues: [cue("Romans 1:24", 50, 60, { check: "look at this" }),
                                                                   cue("Philippians 2:1", 100, 110)] };
state.cuesPath = "/proj/out/C0001 (2).cues.json";
renderCues();
var list = document.getElementById("results");
ok("every verse is listed", list.children.length === 2);
ok("a flagged verse is marked", list.children[0].classList.contains("flag"));

LOOKUP["Genesis 2:24"] = { ok: true, ref: "Genesis 2:24", book: "Genesis", chapter: 2, verse: 24, verse_end: null,
                           text: "Therefore a man shall leave...", translation: "ESV" };
function findIn(el, test) {
    if (test(el)) return el;
    for (var i = 0; i < el.children.length; i++) { var f = findIn(el.children[i], test); if (f) return f; }
    return null;
}
var refEl = findIn(list.children[0], function (e) { return e.className === "ref"; });
editReference(state.payload.cues[0], refEl);
var input = findIn(list.children[0], function (e) { return e.className === "refedit"; });
input.value = "Genesis 2:24";
input.fire("keydown", { key: "Enter" });
flush();
var fixed = state.payload.cues[0];
ok("fixing a verse looks it up and replaces the card (" + fixed.ref + ")",
   fixed.ref === "Genesis 2:24" && fixed.text.indexOf("Therefore") === 0 && !fixed.check);
ok("...remembering what it was, for the next re-analysis", fixed.origRef === "Romans 1:24");
ok("...and is saved at once", JSON.parse(DISK["/proj/out/C0001 (2).cues.selected.json"]).cues[0].ref === "Genesis 2:24");

PLAYHEAD = { ok: true, compTime: 113.8, source: 108.0 };
setAtPlayhead(state.payload.cues[1], "end");
ok("'out' ends a card at the playhead", state.payload.cues[1].end === 108.0 && state.payload.cues[1].userTimes);
PLAYHEAD = { ok: true, compTime: 40, source: 150 };
setAtPlayhead(state.payload.cues[1], "start");
ok("'in' after the card's end is refused", state.payload.cues[1].start === 100);

LOOKUP["John 3:16"] = { ok: true, ref: "John 3:16", book: "John", chapter: 3, verse: 16, verse_end: null,
                        text: "For God so loved the world, that he gave his only Son", translation: "ESV" };
PLAYHEAD = { ok: true, compTime: 205.8, source: 200.0 };
document.getElementById("addRef").value = "John 3:16";
addVerse();
flush();
var added = state.payload.cues.filter(function (c) { return c.source === "added"; })[0];
ok("a missed verse can be added at the playhead", added && added.ref === "John 3:16" && added.start === 200);
ok("...on screen long enough to read", added && added.end - added.start >= 5);
ok("...and switched on", added && added.enabled === true);

LOOKUP["Hezekiah 3:1"] = undefined;
document.getElementById("addRef").value = "Hezekiah 3:1";
var count = state.payload.cues.length;
addVerse(); flush();
ok("a reference that does not exist adds nothing", state.payload.cues.length === count);
// Subtitles are written beside the project, timed to the edit.
state.projectPath = "/Users/test/Documents/TEC VIDEOS/Comfort In Unity.aep";
state.map = [{ offset: 5.8, scale: 1, tMin: 5.8, tMax: 1000 }];
SPAWNED.length = 0;
buildDone({ ok: true, built: 3, comp: "x", layout: "slide", stack: [] }, "");
flush();
var call = SPAWNED.filter(function (a) { return a[1] === "captions"; })[0] || [];
ok("a build writes the subtitles and transcript", call.length > 0);
ok("...beside the After Effects project",
   call[call.indexOf("--out-dir") + 1] === "/Users/test/Documents/TEC VIDEOS" &&
   call[call.indexOf("--name") + 1] === "Comfort In Unity");
ok("...timed to the edit", JSON.parse(call[call.indexOf("--map") + 1])[0].offset === 5.8);
ok("...and says where they are", !document.getElementById("subsLine").classList.contains("hidden") &&
   /saved/.test(document.getElementById("subsText").textContent));
// Snippets: choose the stretch at the playhead, or pick a suggestion.
state.snip = { from: null, to: null, moments: [], busy: false };
showSnipRange();
ok("Make snippet waits for a start and an end", document.getElementById("snipMake").disabled === true);
PLAYHEAD = { ok: true, compTime: 826.6, source: 820.8 };
snipAt("to");
PLAYHEAD = { ok: true, compTime: 797.8, source: 792.0 };
snipAt("from");
ok("start and end come from the playhead", state.snip.from === 797.8 && state.snip.to === 826.6);
ok("...and the snippet can then be made", document.getElementById("snipMake").disabled === false);
PLAYHEAD = { ok: true, compTime: 700, source: 694.2 };
snipAt("to");
ok("an end set before the start swaps them", state.snip.from === 700 && state.snip.to === 797.8);
PLAYHEAD = { ok: true, compTime: 701, source: 695.2 };
snipAt("to");
ok("a snippet under 3 seconds is not offered", document.getElementById("snipMake").disabled === true);

suggestMoments(); flush();
var snipList = document.getElementById("snipList");
ok("suggested moments are listed", snipList.children.length === 2);
snipList.children[0].fire("click");
ok("clicking a suggestion uses its start and end", state.snip.from === 797.8 && state.snip.to === 826.6);

SPAWNED.length = 0;
makeSnippet(); flush();
var snipCall = SPAWNED.filter(function (a) { return a[1] === "snippet"; })[0] || [];
ok("making it prepares that exact stretch",
   snipCall[snipCall.indexOf("--from") + 1] === "797.8" && snipCall[snipCall.indexOf("--to") + 1] === "826.6");
ok("...timed to the edit", snipCall.indexOf("--map") > 0);
ok("...then builds it in After Effects", BUILT_SNIPPET === snipCall[snipCall.indexOf("--out") + 1]);
ok("...and says so", /Built/.test(document.getElementById("snipStatus").textContent) && !state.snip.busy);
// The wait before the panel may appear is the operator's to set.
DISK["/proj/config/style.json"] = JSON.stringify({ slide: { animation: { startAfter: 10 },
    camera: { border: { width: 5 } }, panel: {}, logo: { width: 170 }, background: {} }, fonts: {} });
document.getElementById("startAfter").value = "45";
setPanelDelay();
ok("the panel's wait is saved", JSON.parse(DISK["/proj/config/style.json"]).slide.animation.startAfter === 45);
ok("...and said so", /45s/.test(document.getElementById("footnote").textContent));
document.getElementById("startAfter").value = "-5";
setPanelDelay();
ok("a negative wait is treated as none",
   JSON.parse(DISK["/proj/config/style.json"]).slide.animation.startAfter === 0);

// Building the slide layout has the preacher followed first.
DISK["/proj/config/style.json"] = JSON.stringify({ reference: [1920, 1080], slide: {
    animation: { startAfter: 20 }, camera: { border: { width: 5 }, follow: true },
    panel: {}, logo: { width: 170 }, background: {} }, fonts: {}, build: {} });
document.getElementById("layout").value = "slide";
state.compSize = [3840, 2160];
state.media = "/Volumes/Extreme Pro/church/raw/16:09:2026.MP4";
state.map = [{ offset: 5.8, scale: 1, tMin: 5.8, tMax: 2496 }];
state.payload = { media: state.media, fps: 29.97, generated: "g", cues: [cue("John 3:16", 50, 60)] };
state.cuesPath = "/proj/out/16:09:2026.cues.json";
SPAWNED.length = 0; FOLLOW_APPLIED = null;
build(); flush(); flush();
var followCall = SPAWNED.filter(function (a) { return a[1] === "follow"; })[0] || [];
ok("the build has the preacher followed", followCall.length > 0);
ok("...through the sections it is about to build",
   JSON.parse(followCall[followCall.indexOf("--windows") + 1]).length === 2);
ok("...measured against how much of the picture the inset shows",
   Number(followCall[followCall.indexOf("--crop-width") + 1]) === 0.36);
ok("...and the framing is applied in After Effects", FOLLOW_APPLIED === followCall[followCall.indexOf("--out") + 1]);
ok("...before the verses are built", /Built/.test(document.getElementById("footnote").textContent));

// With the recording unplugged, the build carries on with fixed framing.
FOLLOW_FAILS = true; SPAWNED.length = 0; FOLLOW_APPLIED = null;
build(); flush(); flush();
ok("an unreachable recording does not stop the build", FOLLOW_APPLIED === null &&
   /Built/.test(document.getElementById("footnote").textContent));
ok("...and says why the framing is fixed",
   /left fixed/.test(document.getElementById("warnText").textContent));
FOLLOW_FAILS = false;

// And it can be switched off.
document.getElementById("followPreacher").checked = false;
setFollowPreacher();
ok("following can be turned off", JSON.parse(DISK["/proj/config/style.json"]).slide.camera.follow === false);

// An analysis re-run behind the panel's back is picked up, not built over.
state.cuesPath = "/proj/out/sermon.cues.json";
state.payload = { media: MEDIA, fps: 29.97, generated: "2026-09-27 14:07:55",
                  cues: [cue("Exodus 20:1", 1390, 1447), cue("Exodus 20:2", 1447, 1459, { enabled: false, userSet: true })] };
DISK["/proj/out/sermon.cues.json"] = JSON.stringify({ media: MEDIA, fps: 29.97,
    generated: "2026-09-27 14:52:05",
    cues: [cue("Exodus 20:1", 1390, 1417), cue("Exodus 20:2", 1417, 1459)] });
reloadIfNewer();
ok("a newer analysis on disk is loaded", state.payload.generated === "2026-09-27 14:52:05");
ok("...with the corrected timings", state.payload.cues[0].end === 1417);
ok("...keeping what you had switched off", state.payload.cues[1].enabled === false);
ok("...and saved, so Build uses it",
   JSON.parse(DISK["/proj/out/sermon.cues.selected.json"]).cues[0].end === 1417);
reloadIfNewer();
ok("it does not keep reloading the same one", state.payload.cues[0].end === 1417);
state.building = true;
DISK["/proj/out/sermon.cues.json"] = JSON.stringify({ media: MEDIA, generated: "2026-09-27 15:00:00", cues: [] });
reloadIfNewer();
ok("and never mid-build", state.payload.generated === "2026-09-27 14:52:05");
state.building = false;

// The panel leaves After Effects alone while it is busy.
state.child = null; state.building = false; state.snip.busy = false; state.lastProbe = 0;
var asked = 0, realProbe = probe;
probe = function () { asked++; state.lastProbe = Date.now(); };
pollHost();
ok("it checks in when nothing is happening", asked === 1);
pollHost();
ok("...but not again straight away", asked === 1);
state.lastProbe = 0; state.building = true;
pollHost();
ok("...and not at all during a build", asked === 1);
state.building = false; state.child = {};
pollHost();
ok("...nor during an analysis", asked === 1);
state.child = null; state.lastProbe = 0;
pollHost();
ok("...resuming afterwards", asked === 2);
probe = realProbe;


// ---- audio cleanup -------------------------------------------------------
// The local stand-in for Adobe Podcast: it must work on the mic recording,
// never the camera sound, and what it writes has to stay in sync.
state.clean = { busy: false, result: null, preview: null, notesFor: null };
state.audio = null;
state.projectPath = "/Users/test/Edits/Sunday.aep";
document.getElementById("cleanStrength").value = "medium";
document.getElementById("cleanDereverb").value = "auto";
cleanRun();
ok("cleanup asks for the mic recording before anything else",
   /mic/.test(document.getElementById("cleanStatus").textContent) && !PENDING.length);

setAudio("/Users/test/Downloads/service.WAV", true);
SPAWNED.length = 0;
cleanCheck();
flush();
var checked = SPAWNED[SPAWNED.length - 1];
ok("Check listens to the recording without changing it",
   checked[1] === "clean-profile" && checked[2] === "/Users/test/Downloads/service.WAV");
ok("...at the chosen strength", checked.join(" ").indexOf("--strength medium") >= 0);
ok("...and says nothing has been changed yet",
   /Nothing has been changed/.test(document.getElementById("cleanStatus").textContent));
ok("what it heard is listed for you", document.getElementById("cleanNotes").children.length === 2);

document.getElementById("cleanDereverb").value = "on";
SPAWNED.length = 0;
cleanCheck();
flush();
ok("the room can be forced down", SPAWNED[SPAWNED.length - 1].indexOf("--dereverb") >= 0);
document.getElementById("cleanDereverb").value = "off";
SPAWNED.length = 0;
cleanCheck();
flush();
ok("...or left alone", SPAWNED[SPAWNED.length - 1].indexOf("--no-dereverb") >= 0);
document.getElementById("cleanDereverb").value = "auto";
SPAWNED.length = 0;
cleanCheck();
flush();
var autoArgs = SPAWNED[SPAWNED.length - 1].join(" ");
ok("...or decided from the recording",
   autoArgs.indexOf("--dereverb") < 0 && autoArgs.indexOf("--no-dereverb") < 0);

// Preview: twenty seconds, played in the panel itself.
PLAYED.length = 0;
cleanPreview();
flush();
ok("Preview offers both halves",
   !document.getElementById("cleanPlayBefore").classList.contains("hidden")
   && !document.getElementById("cleanPlayAfter").classList.contains("hidden"));
cleanPlay("after");
ok("the cleaned half plays in the panel",
   PLAYED.length === 1 && /^file:\/\//.test(PLAYED[0]) && /service(%20| )after\.wav/.test(PLAYED[0]));
cleanPlay("before");
ok("so does the original",
   PLAYED.length === 2 && /service(%20| )before\.wav/.test(PLAYED[1]));

// A preview belongs to the settings it was made with.
document.getElementById("cleanStrength").value = "voice";
cleanStrengthChanged();
ok("changing the strength withdraws the old preview",
   document.getElementById("cleanPlayAfter").classList.contains("hidden")
       && state.clean.preview === null);
PLAYED.length = 0;
cleanPlay("after");
ok("...and it cannot be played by accident", PLAYED.length === 0);

// Cleaning for real.
document.getElementById("cleanStrength").value = "medium";
SPAWNED.length = 0;
cleanRun();
var running = SPAWNED[SPAWNED.length - 1];
ok("the cleaned file is written beside the project, named after the recording",
   running[running.indexOf("--out") + 1] === "/Users/test/Edits/service (cleaned).wav");
flush();
ok("progress is reported while it runs or afterwards",
   document.getElementById("cleanStatus").textContent.length > 0);
ok("the level it set is reported",
   /\+9\.4 dB/.test(document.getElementById("cleanStatus").textContent));
ok("and it can then be added, shown or analysed",
   !document.getElementById("cleanAdd").classList.contains("hidden")
   && !document.getElementById("cleanShow").classList.contains("hidden")
   && !document.getElementById("cleanUseForAnalysis").classList.contains("hidden"));

CLEAN_ADDED = null;
cleanAdd();
ok("After Effects is given the cleaned file and the original it replaces",
   CLEAN_ADDED && CLEAN_ADDED[1] === "/Users/test/Edits/service (cleaned).wav"
   && CLEAN_ADDED[2] === "/Users/test/Downloads/service.WAV");
ok("...and the result says it landed in sync with the original muted",
   /in sync/.test(document.getElementById("cleanStatus").textContent)
   && /muted/.test(document.getElementById("cleanStatus").textContent));

cleanUseForAnalysis();
ok("the cleaned audio can be the one that gets transcribed",
   state.audio === "/Users/test/Edits/service (cleaned).wav");
ok("...and it says the analysis has to be run again",
   /Analyze sermon/.test(document.getElementById("cleanStatus").textContent));

// A failure is reported, not swallowed.
setAudio("/Users/test/Downloads/service.WAV", true);
CLEAN_FAILS = true;
state.clean.busy = false;
cleanRun();
flush();
ok("a cleanup that fails says why",
   /ffmpeg is not installed/.test(document.getElementById("cleanStatus").textContent));
ok("...and does not offer a file that was never written",
   document.getElementById("cleanAdd").classList.contains("hidden"));
CLEAN_FAILS = false;
ok("the buttons come back after a failure",
   document.getElementById("cleanRun").disabled === false);


// ---- a presentation shown beside the speaker ----------------------------
// Any deck the user picks, placed from the transcript.
state.deck = { source: null, info: null, busy: false };
setDeck(null);
ok("with no deck there is nothing to place", document.getElementById("deckPlace").disabled === true);

SPAWNED.length = 0;
setDeck("/Users/test/Decks/Reformation.pdf");
var asked = SPAWNED[SPAWNED.length - 1];
ok("choosing a deck reads it", asked[1] === "deck" && asked[2] === "import"
   && asked[3] === "/Users/test/Decks/Reformation.pdf");
flush();
ok("the deck's name is shown", /Reformation\.pdf/.test(document.getElementById("deckName").textContent));
ok("what it found is listed", document.getElementById("deckNotes").children.length === 2);
ok("it says how many slides have words to match on",
   /2 of 3/.test(document.getElementById("deckNotes").children[1].textContent),
   document.getElementById("deckNotes").children[1].textContent);

// Without an analysis there is no transcript to place against.
var keepPayload = state.payload, keepPath = state.cuesPath;
state.payload = null; state.cuesPath = null;
deckReady();
ok("a deck cannot be placed before the session is analysed",
   document.getElementById("deckPlace").disabled === true);
ok("...and it says so", /Analyze the session first/.test(document.getElementById("deckStatus").textContent),
   document.getElementById("deckStatus").textContent);

state.payload = keepPayload; state.cuesPath = keepPath;
deckReady();
ok("with an analysis it can be placed", document.getElementById("deckPlace").disabled === false);

DECK_PLACED = null;
placeDeck();
flush();
ok("placing passes the deck and the sermon to the engine",
   DECK_PLACED && DECK_PLACED[2] === "place" && DECK_PLACED[3] === "deck123"
   && DECK_PLACED[DECK_PLACED.indexOf("--cues") + 1] === keepPath, DECK_PLACED);
ok("...and reports what it did",
   /3 slides placed/.test(document.getElementById("deckStatus").textContent)
   && /1 to check/.test(document.getElementById("deckStatus").textContent),
   document.getElementById("deckStatus").textContent);

// A deck it cannot read says what to export instead.
DECK_FAILS = true;
setDeck("/Users/test/Decks/Talk.pptx");
flush();
ok("a deck that cannot be read says what to export instead",
   /export the deck as a PDF/i.test(document.getElementById("deckStatus").textContent),
   document.getElementById("deckStatus").textContent);
DECK_FAILS = false;

setDeck(null);
ok("a deck can be cleared", state.deck.source === null
   && document.getElementById("deckPlace").disabled === true);

// Slides in the review list are slides, not verses.
state.payload = { cues: [
    { ref: "Slide 2 \u2014 Council of Trent", kind: "slide", slide: 2,
      image: "/d/slide-002.png", text: "Council of Trent\nRome's answer 1545",
      start: 300, end: 420, enabled: true, notes: [], check: "" },
    { ref: "Romans 1:17", kind: "verse", text: "The righteous shall live by faith.",
      translation: "ESV", start: 500, end: 510, enabled: true, notes: [], check: "" }
] };
state.map = [{ offset: 0, scale: 1, tMin: 0, tMax: 4800 }];
renderCues();
var rows = document.getElementById("results").children;
ok("a slide is listed with the verses", rows.length === 2, rows.length);
var slideRow = rows[0].children[1], headRow = slideRow.children[0];
ok("a slide is badged as a slide",
   headRow.children[headRow.children.length - 1].textContent === "slide",
   headRow.children[headRow.children.length - 1].textContent);
ok("...and shows its own picture",
   headRow.children[0].tagName === "img" && /slide-002\.png/.test(headRow.children[0].src),
   headRow.children[0].tagName);
ok("...with its heading, not everything read off it",
   slideRow.children[2].textContent === "Council of Trent", slideRow.children[2].textContent);
ok("...and is not offered a verse to look up",
   /Slide 2 of the deck/.test(headRow.children[1].title), headRow.children[1].title);
ok("a verse beside it is still a verse",
   rows[1].children[1].children[0].children[1].textContent === "ESV",
   rows[1].children[1].children[0].children[1].textContent);

print("\n  " + pass + " passed, " + fail + " failed");
