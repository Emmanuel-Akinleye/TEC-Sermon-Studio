"""Rules the cue timing must hold to, whatever the sermon."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import json          # noqa: E402
import quotes        # noqa: E402
import refs          # noqa: E402
import timing        # noqa: E402

def _latest_per_recording():
    """One transcript per recording: the newest, which is the current model's."""
    newest = {}
    for f in (ROOT / "data" / "transcripts").glob("*.json"):
        stem = f.name.split(".")[0]
        if stem not in newest or f.stat().st_mtime > newest[stem].stat().st_mtime:
            newest[stem] = f
    return sorted(newest.values())


TRANSCRIPTS = _latest_per_recording()


def analyse(words):
    detect_cfg = json.loads((ROOT / "config" / "detect.json").read_text())
    timing_cfg = json.loads((ROOT / "config" / "timing.json").read_text())
    timing_cfg["fps"] = 29.97
    timing_cfg.update(detect_cfg.get("quotes", {}))
    index = quotes.load_index("BSB")
    mentions = refs.detect(words, detect_cfg)
    hits = index.find(words, detect_cfg.get("quotes", {}))
    return timing.build_cues(mentions, hits, words, translation="BSB",
                             corpus="BSB", cfg=timing_cfg, index=index,
                             fetch_text=False)


CHECKS = []


def check(name):
    def wrap(fn):
        CHECKS.append((name, fn))
        return fn
    return wrap


@check("cards never overlap")
def _(cues):
    on = [c for c in cues if c.enabled]
    return [f"{on[i].ref} runs into {on[i+1].ref}"
            for i in range(len(on) - 1) if on[i].end > on[i + 1].start + 0.001]


@check("cards are long enough to read")
def _(cues):
    return [f"{c.ref} only {c.duration:.1f}s"
            for c in cues if c.enabled and c.duration < 2.0]


@check("no card outstays its welcome")
def _(cues):
    return [f"{c.ref} runs {c.duration:.0f}s"
            for c in cues if c.enabled and c.duration > 70]


HANDOVER = json.loads((ROOT / "config" / "timing.json").read_text()).get("handover_gap", 40.0)


@check("verses of one passage hand straight over")
def _(cues):
    """Neighbouring verses meet, unless he preached for a long while between
    them — then a gap is right and the panel dissolves out."""
    on = [c for c in cues if c.enabled]
    bad = []
    for i in range(len(on) - 1):
        a, b = on[i], on[i + 1]
        # Only neighbouring verses: "17" then "28" has preaching between.
        if (a.precise and b.precise and a.book == b.book
                and a.chapter == b.chapter
                and b.verse == (a.verse_end or a.verse) + 1
                and 0.05 < b.start - a.end < HANDOVER):
            bad.append(f"{a.ref} -> {b.ref} leaves {b.start - a.end:.2f}s blank")
    return bad


@check("no card is cut mid-reading by a pause")
def _(cues):
    return [f"{c.ref}" for c in cues
            if c.enabled and "cut at pause" in c.notes and "read" in c.source]


@check("cues are in order")
def _(cues):
    on = [c for c in cues if c.enabled]
    return [f"{on[i].ref} starts before {on[i-1].ref}"
            for i in range(1, len(on)) if on[i].start < on[i - 1].start]


@check("every enabled cue has text")
def _(cues):
    return [c.ref for c in cues
            if c.enabled and c.verse and not c.ref]


def main():
    if not TRANSCRIPTS:
        print("  no transcripts cached — run an analysis first")
        return 0

    failures = 0
    for path in TRANSCRIPTS:
        data = json.loads(path.read_text())
        words = data.get("words") or []
        if len(words) < 50:
            continue
        cues = analyse(words)
        on = sum(1 for c in cues if c.enabled)
        print(f"\n  {path.name[:34]:<34} {on} cues")
        for name, fn in CHECKS:
            problems = fn(cues)
            if problems:
                failures += 1
                print(f"     FAIL {name}")
                for p in problems[:4]:
                    print(f"          {p}")
            else:
                print(f"     ok   {name}")

    print(f"\n  {failures} failing checks")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
