"""Short vertical clips for social media, cut from the sermon.

Two jobs:

* `suggest` reads the sermon for moments that stand on their own as a clip:
  a complete thought, 25-60 seconds, that starts cleanly and ends on a full
  stop, preferably where he is louder and more emphatic than usual, repeats
  himself for effect, and speaks straight to the listener. Housekeeping and
  long stretches of reading are marked down. These are suggestions to pick
  from, not decisions.
* `prepare` gets one clip ready for After Effects: its captions, a few words
  at a time, and the path that keeps the pastor centred in the vertical frame.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np

import align
import captions as C
import track
import transcribe as tx

BACK_REFERENCE = {"and", "but", "or", "because", "which", "that", "this", "it", "he",
                  "she", "they", "him", "them", "these", "those", "then", "also"}
HOUSEKEEPING = ("have your seat", "your seats", "good evening", "good morning", "welcome",
                "announcement", "offering", "microphone", "stop looking at the screen",
                "open your bibles", "turn with me", "i forgot to", "let us pray")
PROMPTS = ("do you understand", "say amen", "are we together", "do you see what i")
KEY_WORDS = {"god", "jesus", "christ", "love", "grace", "hope", "faith", "cross", "gospel",
             "sin", "heart", "joy", "peace", "comfort", "suffering", "pray", "spirit", "lord",
             "salvation", "mercy", "forgive", "victory", "overcome", "unity", "blood", "life"}


def sentences(words: list[dict]) -> list[dict]:
    out, cur = [], []
    for w in words:
        if cur and w["cut"]:
            out.append(cur)
            cur = []
        cur.append(w)
        if C._ends_sentence(w["text"]):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    res = []
    for i, s in enumerate(out):
        res.append({
            "words": s, "start": s[0]["start"], "end": s[-1]["end"],
            "text": " ".join(w["text"] for w in s),
            "before": s[0]["start"] - out[i - 1][-1]["end"] if i else 9.9,
            "after": out[i + 1][0]["start"] - s[-1]["end"] if i + 1 < len(out) else 9.9,
            "cut_before": s[0]["cut"],
        })
    return res


def _loudness(audio: Path, sents: list[dict]) -> np.ndarray:
    """How loud he is in each sentence, compared with the rest of the sermon."""
    hop = 10
    try:
        env = align.energy_envelope(audio, 8000, hop)
    except Exception:
        return np.zeros(len(sents))
    db = 20 * np.log10(env + 1e-5)
    vals = []
    for s in sents:
        a = int(s["words"][0]["mic"] * hop)
        b = max(a + 1, int(s["words"][-1]["mic"] * hop) + 1)
        seg = db[a:b]
        # speech only: the loud half of the stretch, so pauses do not count
        vals.append(float(np.percentile(seg, 75)) if seg.size else float("nan"))
    v = np.array(vals)
    ok = ~np.isnan(v)
    if ok.sum() < 3:
        return np.zeros(len(sents))
    z = (v - np.nanmean(v)) / (np.nanstd(v) + 1e-6)
    return np.nan_to_num(z)


def suggest(payload: dict, edit_map=None, count: int = 5,
            shortest: float = 25.0, longest: float = 60.0) -> list[dict]:
    words = C.tidy(C.edit_words(payload, edit_map))
    sents = sentences(words)
    if not sents:
        return []
    loud = _loudness(Path(payload.get("audio") or payload["media"]), sents)
    read = [(float(c["start"]), float(c["end"])) for c in payload.get("cues", [])
            if c.get("enabled") is not False and c.get("verse") and "read" in c.get("source", "")]

    def to_edit(t):
        if not edit_map:
            return t
        for m in edit_map:
            x = m["offset"] + t * m["scale"]
            if m["tMin"] <= x <= m["tMax"]:
                return x
        return None
    read = [(to_edit(a), to_edit(b)) for a, b in read]
    read = [(a, b) for a, b in read if a is not None and b is not None]

    def good_start(s):
        first = C._bare(s["words"][0]["text"])
        return (s["before"] >= 0.35 or s["cut_before"]) and first not in BACK_REFERENCE \
            and not first.isdigit() and len(s["words"]) >= 3

    def score(i, j):
        span = sents[i:j + 1]
        text = " ".join(s["text"] for s in span).lower()
        toks = re.findall(r"[a-z']+", text)
        n = max(1, len(toks))
        dur = span[-1]["end"] - span[0]["start"]
        weights = np.array([s["end"] - s["start"] for s in span])
        z = loud[i:j + 1]
        energy = float((z * weights).sum() / max(weights.sum(), 1e-6))
        swing = float(z.std()) if len(z) > 1 else 0.0
        punch = sum(1 for s in span if len(s["words"]) <= 9) / len(span)
        grams = [" ".join(toks[k:k + 3]) for k in range(len(toks) - 2)]
        repeats = len(grams) - len(set(grams))
        address = sum(1 for t in toks if t in ("you", "your", "we", "our", "us")) / n * 10
        key = sum(1 for t in toks if t.rstrip("s") in KEY_WORDS) / n * 10
        chores = sum(text.count(h) for h in HOUSEKEEPING)
        prompts = sum(text.count(h) for h in PROMPTS)
        verses = len(re.findall(r"\bverse \d+", text))
        reading = sum(max(0.0, min(b, span[-1]["end"]) - max(a, span[0]["start"])) for a, b in read) / dur
        total = (1.0 * energy + 0.5 * swing + 1.4 * punch + 0.6 * min(1.0, repeats / 3)
                 + 0.35 * min(3.0, address) + 0.35 * min(3.0, key)
                 - 1.5 * chores - 0.4 * prompts - 0.35 * verses - 1.2 * reading)
        why = []
        if energy > 0.4:
            why.append("he is at his most emphatic")
        if repeats >= 3:
            why.append("repeats for effect")
        if punch > 0.45:
            why.append("short, quotable lines")
        if address > 1.2:
            why.append("speaks straight to the listener")
        if reading > 0.3:
            why.append("includes scripture")
        return total, why

    candidates = []
    for i, s in enumerate(sents):
        if not good_start(s):
            continue
        best = None
        for j in range(i, len(sents)):
            if j > i and sents[j]["cut_before"]:
                break
            dur = sents[j]["end"] - s["start"]
            if dur > longest:
                break
            if dur < shortest or sents[j]["after"] < 0.3:
                continue
            sc, why = score(i, j)
            if best is None or sc > best[0]:
                best = (sc, j, why)
        if best:
            candidates.append((best[0], i, best[1], best[2]))

    candidates.sort(key=lambda c: -c[0])
    picked = []
    for sc, i, j, why in candidates:
        a, b = sents[i]["start"], sents[j]["end"]
        if any(not (b + 20 < p["start"] or a > p["end"] + 20) for p in picked):
            continue
        # A breath either side, but never the first word of the next sentence.
        begin = max(0.0, a - 0.4, sents[i - 1]["end"] + 0.05 if i else 0.0)
        until = min(b + 0.8, sents[j + 1]["start"] - 0.05 if j + 1 < len(sents) else b + 0.8)
        picked.append({
            "start": round(begin, 3), "end": round(until, 3),
            "duration": round(until - begin, 1), "score": round(sc, 2),
            "opening": " ".join(w["text"] for w in sents[i]["words"][:16]),
            "why": why,
        })
        if len(picked) >= count:
            break
    return sorted(picked, key=lambda p: p["start"])


def video_size(video: Path) -> tuple[int, int]:
    probe = tx.find_tool("ffprobe") or "ffprobe"
    try:
        r = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "json", str(video)],
                           capture_output=True, text=True, timeout=30)
        st = json.loads(r.stdout)["streams"][0]
        return int(st["width"]), int(st["height"])
    except Exception:
        return 3840, 2160


def prepare(payload: dict, edit_map, t0: float, t1: float, style: dict,
            progress=None, follow: bool = True) -> dict:
    """Everything After Effects needs to build one clip, in clip time (0 at t0)."""
    words = C.tidy(C.edit_words(payload, edit_map))
    # Words that start inside the clip; one begun just before the end is cut
    # off in the sound anyway, so it is not captioned either.
    inside = [w for w in words if t0 - 0.05 <= w["start"] < t1 - 0.15]
    caps = [(round(a - t0, 3), round(min(b, t1) - t0, 3), text)
            for a, b, text in C.subtitles(inside, C.SOCIAL) if a < t1]

    sn = style["snippet"]
    video = Path(payload["media"])
    src_w, src_h = video_size(video)
    frame = sn["frame"]
    zoom = float(sn.get("camera", {}).get("zoom", 1.0))
    # How much of the source's width the portrait frame shows.
    crop_width = min(1.0, (frame["width"] / frame["height"]) * (src_h / src_w) / zoom)

    keys, cuts = [], []
    if follow and sn.get("camera", {}).get("follow", True):
        pieces = []
        for m in (edit_map or [{"offset": 0.0, "scale": 1.0, "tMin": t0, "tMax": t1}]):
            a, b = max(t0, m["tMin"]), min(t1, m["tMax"])
            if b - a > 0.2:
                pieces.append((a, b, m))
        for n, (a, b, m) in enumerate(pieces):
            cam_a, cam_b = (a - m["offset"]) / m["scale"], (b - m["offset"]) / m["scale"]
            samples = track.detect(video, cam_a, cam_b, progress=progress)
            path = track.steer(samples, crop_width)
            clip = [(round(m["offset"] + t * m["scale"] - t0, 3), x) for t, x in path]
            if n and clip:
                cuts.append(clip[0][0])
            keys += clip
    return {"from": t0, "to": t1, "captions": [list(c) for c in caps], "follow": keys,
            "cuts": cuts, "cropWidth": crop_width, "sourceSize": [src_w, src_h],
            "media": payload["media"]}
