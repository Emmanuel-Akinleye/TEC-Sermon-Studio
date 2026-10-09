"""Rebuild the cues for a sermon already analysed, without re-transcribing.

    python3 tests/replay.py out/23:08:2026.cues.json [Book] [chapter]
"""
import glob, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
import quotes, refs, timing, transcribe  # noqa: E402


def words_for(payload):
    source = Path(payload.get("audio") or payload["media"])
    # The newest transcript: the one the current model produced.
    found = sorted(glob.glob(str(ROOT / "data" / "transcripts" / (source.stem + ".*.json"))),
                   key=lambda f: Path(f).stat().st_mtime)
    words = transcribe.clean_seams(json.loads(Path(found[-1]).read_text())["words"])
    a = payload.get("alignment")
    if a:
        words = [{**w, "start": w["start"] + a["a0"] + a["a1"] * w["start"],
                  "end": w["end"] + a["a0"] + a["a1"] * w["end"]} for w in words]
    return words


def rebuild(payload):
    words = words_for(payload)
    dcfg = json.loads((ROOT / "config/detect.json").read_text())
    tcfg = {**json.loads((ROOT / "config/timing.json").read_text()), "fps": payload.get("fps", 29.97)}
    tcfg.update(dcfg.get("quotes", {}))
    index = quotes.load_index(payload.get("corpus", "BSB"))
    mentions = refs.detect(words, dcfg)
    hits = index.find(words, dcfg.get("quotes", {}))
    # Same translation and text as a real build, so the checker sees what
    # the preacher actually read from.
    return timing.build_cues(mentions, hits, words,
                             translation=payload.get("translation", "BSB"),
                             corpus=payload.get("corpus", "BSB"),
                             cfg=tcfg, index=index, fetch_text=True)


if __name__ == "__main__":
    payload = json.loads(Path(sys.argv[1]).read_text())
    book = sys.argv[2] if len(sys.argv) > 2 else None
    chapter = int(sys.argv[3]) if len(sys.argv) > 3 else None
    for c in rebuild(payload):
        if book and c.book != book: continue
        if chapter and c.chapter != chapter: continue
        print(f"{'on ' if c.enabled else 'off'} {c.start:8.1f}-{c.end:8.1f} {c.duration:5.1f}s  "
              f"{c.ref:<24} {c.source:<12} {'; '.join(c.notes)}")
