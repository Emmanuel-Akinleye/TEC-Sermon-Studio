"""Subtitles and transcript: punctuation repairs, and YouTube-ready captions
for every analysed sermon (line length, overlaps, reading time, edit cuts)."""
import json, re, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
import captions  # noqa: E402

passed = failed = 0


def ok(name, cond, got=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {name}  {got}")


def said(text, gaps=None):
    """Words with timings; gaps = {index: seconds of pause before that word}."""
    out, t = [], 0.0
    for i, w in enumerate(text.split()):
        t += (gaps or {}).get(i, 0.05)
        out.append({"text": w, "start": t, "end": t + 0.25, "cut": False, "piece_end": None})
        t += 0.25
    return out


def tidied(text, gaps=None):
    return " ".join(w["text"] for w in captions.tidy(said(text, gaps)))


CASES = [
    ("There is consolation in Christ,, if there", "There is consolation in Christ, if there"),
    ("his suffering.. was speaking", "His suffering. Was speaking"),
    ("good evening., brothers", "Good evening. Brothers"),
    ("them to have something And then he gives", "Them to have something. And then he gives"),
    ("what He has done for us", "What He has done for us"),          # capital He is God
    ("we thank You Lord for", "We thank You Lord for"),
    ("filled with the holy spirit today", "Filled with the Holy Spirit today"),
    ("the fruit of the spirit is love", "The fruit of the Spirit is love"),
    ("any participation in the spirit, any", "Any participation in the Spirit, any"),
    ("the spirit of god is here", "The Spirit of God is here"),
    ("they bowed to a god of wood", "They bowed to a god of wood"),
    ("and i said amen.", "And I said Amen."),
    ("praise the lord jesus", "Praise the Lord Jesus"),
    ("read philippians 2 verse 1", "Read Philippians 2 verse 1"),
    ("the end. so we go", "The end. So we go"),
]
for raw, want in CASES:
    got = tidied(raw)
    ok(f"tidy: {raw!r}", got == want, f"-> {got!r}")

# Standards for YouTube, on every sermon analysed on this machine, with the
# recording placed in an edit that cuts 5 minutes out of the middle.
SRT_TIME = re.compile(r"^\d\d:\d\d:\d\d,\d\d\d --> \d\d:\d\d:\d\d,\d\d\d$")


def secs(t):
    h, m, s = t.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


for path in sorted((ROOT / "out").glob("*:*:*.cues.json")):
    if path.name.endswith(".selected.json"):
        continue
    payload = json.loads(path.read_text())
    stem = path.name.split(".")[0]
    edit = [{"offset": 5.8, "scale": 1, "tMin": 5.8, "tMax": 605.8},
            {"offset": -294.2, "scale": 1, "tMin": 605.8, "tMax": 1e6}]   # 300s cut after 10 min
    out = Path(tempfile.mkdtemp())
    try:
        res = captions.make(payload, out, stem, edit)
    except FileNotFoundError:
        print(f"  skip  {stem} (no transcript on this machine)")
        continue
    blocks = Path(res["srt"]).read_text(encoding="utf-8").strip().split("\n\n")
    caps = []
    for n, b in enumerate(blocks, 1):
        lines = b.split("\n")
        ok(f"{stem}: caption {n} numbered", lines[0] == str(n), lines[0])
        ok(f"{stem}: caption {n} timecode", bool(SRT_TIME.match(lines[1])), lines[1])
        a, z = (secs(x) for x in lines[1].split(" --> "))
        caps.append((a, z, lines[2:]))
    long = [l for c in caps for l in c[2] if len(l) > captions.MAX_LINE]
    ok(f"{stem}: no line over {captions.MAX_LINE} characters", not long, long[:2])
    ok(f"{stem}: never more than two lines", all(len(c[2]) <= 2 for c in caps))
    ok(f"{stem}: captions never overlap", all(x[1] <= y[0] for x, y in zip(caps, caps[1:])))
    # Up long enough to see, unless he said it faster than that and the next
    # caption had to follow straight on.
    short = [(round(c[0], 1), c[2]) for c, nxt in zip(caps, caps[1:] + [None])
             if c[1] - c[0] < 0.8 and not (nxt and nxt[0] - c[1] < 0.1)]
    ok(f"{stem}: every caption up long enough to see", not short, short[:2])
    straddle = [c for c in caps if c[0] < 605.8 - 0.01 and c[1] > 605.8 + 0.01]
    ok(f"{stem}: no caption runs across the cut in the edit", not straddle, straddle[:1])
    # Word-for-word captions can only go as slowly as he speaks. What can be
    # done is to leave every fast one up until the next one takes over.
    fast = [(round(c[0], 1), c[2]) for c, nxt in zip(caps, caps[1:] + [None])
            if len(" ".join(c[2])) / (c[1] - c[0]) > 20 and nxt and nxt[0] - c[1] > 0.1
            and c[1] - c[0] < captions.MAX_DURATION]
    ok(f"{stem}: every fast caption stays up until the next", not fast, fast[:2])
    txt = Path(res["txt"]).read_text(encoding="utf-8")
    paras = re.findall(r"^\[\d+:\d\d(?::\d\d)?\]  (.+)$", txt, flags=re.M)
    ok(f"{stem}: transcript is in timestamped paragraphs", len(paras) > 10, len(paras))
    ok(f"{stem}: no wall-of-text paragraph", max(len(p.split()) for p in paras) <= 220,
       max(len(p.split()) for p in paras))
    ok(f"{stem}: scriptures listed with times", "\nScriptures\n" in txt and re.search(r"^\d+:\d\d  \S", txt, re.M))
    print(f"  {stem}: {len(caps)} captions, {len(paras)} paragraphs")

print(f"\n  {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
