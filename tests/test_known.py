"""Facts about real sermons that must stay true.

Each entry is something that went wrong on a real service and was fixed. If a
later change breaks one, this says so before a sermon goes out wrong again.
Sermons whose files are no longer on this machine are skipped.
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "engine"))
import replay  # noqa: E402

FACTS = {
    "16:09:2026": [
        ("shown", "Philippians 1:28"),
        ("shown", "Philippians 1:29"),         # "…their own salvation. And verse 29 says"
        ("shown_at", "Philippians 2:2", 2.85), # "having the same love, being in full accord…"
                                               # quoted again, unnamed - the panel sat empty
        ("absent_at", "Hebrews 12:2", 10.4),   # "seated at the right hand of the Father":
        ("absent_at", "Hebrews 12:2", 10.75),  # stock wording, not a quote of 12:2
        ("shown_at", "Philippians 2:5", 32.23), # "In verse 5, it says, Have this mind…" with
        ("absent", "1 Peter 4:5"),              # 1 Peter 4 still the last chapter named
        ("shown_at", "Philippians 2:2", 7.25),  # "that verse 2 that I read" - not 1:2
        ("absent", "Philippians 1:2"),
        ("absent", "Hebrews 12:2"),             # "that exhortation in verse 2" - Phil 2,
        ("shown_at", "Philippians 2:2", 15.28), # Hebrews 12 was only named in passing
    ],
    "23:08:2026": [
        ("shown", "1 Corinthians 13:9"),     # "from verse 8" read on into 9
        ("shown", "1 Corinthians 13:10"),    # ...and 10
        ("shown", "1 Corinthians 13:11"),
        ("short_read", "1 Corinthians 13:12", 15),# ends when the reading ends...
        ("shown_at", "1 Corinthians 13:12", 13.67),# ...and comes back when he quotes it in full
        ("shown", "Genesis 2:25"),
        ("short", "Genesis 2:25", 15),
        ("shown", "Romans 6:20"),             # "let's read from verse 20" after citing 21
        ("readable", "Romans 6:20"),
        ("absent", "Romans 6:22"),            # his comment after 21, not verse 22
        ("absent", "Romans 1:15"),
        ("absent", "Romans 1:24"),            # "from" misread as "Rom" moved the context
        ("shown", "1 Chronicles 29:10"),      # heard as "1 Corinthians chapter 29. Verse 10"
        ("shown", "1 Chronicles 29:12"),      # ...and read on from there
        ("absent_at", "1 Corinthians 13:10", 70.65),  # not 1 Cor 13, an hour after it was read
        ("shown", "Genesis 2:24"),            # "in verse 24, it says, Therefore a man…"            # "read from verse 8" — then preached on Genesis
    ],
    "C0001": [
        # "Again, I'll read from verse 19 to 22" - heard as Romans 12:19-22,
        # a range Romans 12 cannot have (it ends at 21). The whole range was
        # thrown out as impossible instead of the book being corrected, and a
        # guessed passage then put verses 2 to 22 on one card.
        ("shown_at", "Philippians 2:19", 16.6),
        ("shown_at", "Philippians 2:20", 17.4),
        ("shown_at", "Philippians 2:21", 17.6),
        ("shown_at", "Philippians 2:22", 17.7),
        ("absent", "Philippians 2:14"),          # never read; it was swept in by the block
        ("narrow", "Philippians 2:19", 3),       # no card covering half the chapter
    ],
    "27:09:2026": [
        # "Exodus chapter 20, verse 1 to 6": he reads verse 1, is on verse 2
        # eight seconds later ("I am the LORD your God"), and preaches it for
        # half a minute before reading it out in full. Verse 1 used to sit
        # there for 57 seconds, and the whole split was nearly thrown away
        # because the alignment skipped verse 1 altogether.
        ("shown", "Exodus 20:1"),
        ("short", "Exodus 20:1", 30),
        ("shown_at", "Exodus 20:2", 23.6),
        ("shown", "Exodus 20:3"),
    ],
    "23:09:2026": [
        # He reads the ESV; the corpus is the BSB. "Do all things without
        # grumbling or disputing" and "Do everything without complaining or
        # arguing" share no distinctive word, and verse 14 fell out of the
        # middle of a continuous reading.
        ("shown_at", "Philippians 2:14", 0.49),
        ("shown_at", "Philippians 2:18", 0.97),
        ("shown_at", "Philippians 2:13", 0.41),
    ],
    "20:09:2026": [
        ("absent", "Colossians 2:20"),        # skipped; he went to verse 21
        ("shown", "Colossians 2:21"),
        ("off_at", "Judges 16:28", 28.6),     # named, then taken back - kept off screen
        ("shown_at", "Judges 16:28", 29.5),   # ...and read for real a minute later
        ("off_at", "Judges 16:15", 28.8),     # "let's finish verse 15" - then went to 20
        ("atleast", "2 Timothy 2:20", 2),     # "verse 20 again" after preaching on it
        ("shown", "Judges 13:5"),             # "read from verse 1 to 5"
        ("absent", "Judges 13:7"),            # shares 13:5's wording; never read
        ("short", "Judges 13:4", 20),         # must not swallow the Numbers reading
        ("shown", "Numbers 6:1"),             # "Numbers chapter, I was, 6. Verse 1"
        ("shown", "Numbers 6:5"),
        ("shown", "Numbers 6:8"),
        ("short", "Hosea 2:13", 20),
        ("unflagged", "1 Timothy 6:17"),
        ("shown", "1 Timothy 6:6"),           # heard as "in 1966" — book fixed by the reading
        ("absent", "Jeremiah 6:10"),          # ...which was carried over wrongly before
        ("absent", "1 Timothy 6:9"),          # he skipped 9 on the way to 10
        ("short", "1 Timothy 6:8", 15),       # must not stretch over the sermon on it
        ("shown", "Colossians 2:19"),
        ("shown", "Hosea 2:1"),               # every verse of 2:1-13 its own card
        ("shown", "Hosea 2:7"),
        ("shown_at", "Hosea 2:7", 4.3),
        ("absent_at", "Hosea 2:9", 5.3),      # "I lavished on her food" is his comment on 8       # second pass: "verse 5 to verse 18", 7 read after an aside
        ("short", "2 Timothy 2:20", 45),      # ends when the reading does
        ("short", "Philippians 3:3", 60),     # two quotations, not one 5-minute card
    ],
}


def main():
    failed = checked = 0
    for stem, facts in FACTS.items():
        path = ROOT / "out" / f"{stem}.cues.json"
        if not path.exists():
            print(f"  skip  {stem} (no analysis on this machine)")
            continue
        try:
            cues = replay.rebuild(json.loads(path.read_text()))
        except (IndexError, FileNotFoundError):
            print(f"  skip  {stem} (transcript not cached)")
            continue
        def covering(ref):
            """Cards showing this verse, including merged ones like 6:1-2."""
            book, _, cv = ref.rpartition(" ")
            ch, v = (int(x) for x in cv.split(":"))
            return [c for c in cues if c.book == book and c.chapter == ch and c.verse
                    and c.verse <= v <= (c.verse_end or c.verse)]
        for fact in facts:
            kind, ref = fact[0], fact[1]
            found = covering(ref)
            live = [c for c in found if c.enabled]
            checks = {
                "shown": lambda: bool(live),
                "absent": lambda: not found,
                "flagged": lambda: any(c.check for c in live),
                "unflagged": lambda: bool(live) and not any(c.check for c in live),
                "off": lambda: bool(found) and not live,
                "short": lambda: bool(live) and all(c.duration <= fact[2] for c in live),
                "short_read": lambda: bool(live) and all(
                    c.duration <= fact[2] for c in live
                    if "quoted again, not named" not in c.notes),
                "narrow": lambda: bool(live) and all(
                    ((c.verse_end or c.verse) - c.verse) <= fact[2] for c in live),
                "readable": lambda: bool(live) and all(c.duration >= 3.0 for c in live),
                "atleast": lambda: len(live) >= fact[2],
                "off_at": lambda: any(abs(c.start / 60 - fact[2]) < 0.4 and not c.enabled
                                      for c in found)
                                  and not any(abs(c.start / 60 - fact[2]) < 0.4 for c in live),
                "absent_at": lambda: not any(abs(c.start / 60 - fact[2]) < 0.4 for c in live),
                "shown_at": lambda: any(abs(c.start / 60 - fact[2]) < 0.4 for c in live),
            }
            ok = checks[kind]()
            checked += 1
            if not ok:
                failed += 1
                got = ", ".join(f"{c.duration:.0f}s{' off' if not c.enabled else ''}"
                                f"{' flagged' if c.check else ''}" for c in found) or "not there"
                print(f"  FAIL  {stem}: {ref} should be {kind} — got {got}")
    print(f"\n  {checked - failed} of {checked} real-sermon facts hold")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
