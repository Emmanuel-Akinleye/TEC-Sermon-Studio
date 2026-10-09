"""Subtitles for YouTube, and a transcript a person can read.

Both come from the mic transcript the analysis already made, placed on the
edit's own timeline (the comp you render), so they line up with the video you
upload rather than with the raw camera file:

* `<name> - subtitles.srt`   captions of at most two lines of 42 characters,
  broken at sentences and clauses, on screen long enough to read, never
  overlapping, and split wherever the edit cuts.
* `<name> - transcript.txt`  the sermon as paragraphs with timestamps, and
  the scriptures in it listed at the top with the moment each comes up.

Parakeet punctuates well but not perfectly. Doubled marks ("Christ,,"),
lowercase sentence starts, a full stop lost where he paused before a new
sentence, and "holy spirit" in lowercase are all put right here. The words
themselves are left exactly as heard.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import books as B
import transcribe as tx

from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    max_line: int = 42          # characters per line
    max_lines: int = 2
    max_duration: float = 6.5   # seconds one caption may stay up
    min_duration: float = 1.0
    pause_break: float = 0.9    # a pause this long ends a caption
    reading_cps: float = 17.0   # characters per second a viewer reads comfortably
    sentence_min: int = 32      # end a caption with its sentence once it has this much
    hang: float = 0.35          # caption stays this long after the last word
    gap: float = 0.04           # always a frame between captions

    @property
    def max_chars(self) -> int:
        return self.max_line * self.max_lines


YOUTUBE = Limits()
# Social clips: a few words at a time, big, changing with the speech.
SOCIAL = Limits(max_line=20, max_lines=2, max_duration=3.2, min_duration=0.7,
                pause_break=0.6, sentence_min=1, hang=0.25)

MAX_LINE = YOUTUBE.max_line
MAX_DURATION = YOUTUBE.max_duration

# A capitalised word after a long, unpunctuated pause starts a new sentence
# only if it is a word that normally does; "…love Paul" is not.
STARTERS = {
    "a", "all", "amen", "an", "and", "are", "as", "because", "but", "can", "do",
    "does", "even", "for", "he", "her", "here", "his", "how", "i", "if", "in",
    "is", "it", "let", "let's", "look", "my", "no", "now", "okay", "our", "please",
    "say", "she", "so", "some", "that", "that's", "the", "their", "then", "there",
    "these", "they", "this", "those", "we", "what", "when", "where", "which", "who",
    "why", "yes", "you", "your", "church", "brothers", "friends", "listen",
}
# Capitalised mid-sentence, these are almost always a new sentence that lost its full stop.
SENTENCE_OPENERS = {"and", "but", "so", "because", "there", "our", "we", "this", "then",
                    "now", "if", "when", "that's", "it", "she", "they"}
# Capitalised for God ("what He has done", "we thank You"), so a capital
# says nothing about a new sentence.
DEITY_PRONOUNS = {"i", "he", "him", "his", "himself", "you", "your", "yours", "yourself", "us"}
ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "etc.", "e.g.", "i.e.", "rev.", "no."}
# A line or caption should not end on one of these: it belongs with what follows.
CLINGY = {
    "a", "an", "the", "of", "to", "in", "on", "at", "for", "with", "and", "but", "or",
    "nor", "so", "my", "your", "his", "her", "our", "their", "its", "this", "that",
    "these", "those", "is", "are", "was", "were", "be", "by", "from", "as", "if",
    "than", "into", "unto", "upon", "not", "no", "very", "will", "would", "shall",
    "should", "can", "could", "may", "might", "must", "i", "we", "you", "he", "she",
    "they", "it", "who", "which", "whom", "whose", "where", "when", "has", "have",
    "had", "do", "does", "did", "let", "let's", "verse", "chapter",
}
SPIRIT_BEFORE = {"fruit", "gifts", "gift", "power", "fellowship", "participation",
                 "manifestation", "sword", "unity", "work", "leading", "voice", "filled",
                 "led", "walk", "baptized", "born", "anointed", "empowered"}
SMALL_GODS = {"a", "another", "other", "false", "foreign", "strange", "any", "every", "their"}


# --------------------------------------------------------------------------
# words on the edit's timeline
# --------------------------------------------------------------------------
def _transcript_for(payload: dict) -> dict:
    source = Path(payload.get("audio") or payload["media"])
    cached = tx.CACHE_DIR / f"{source.stem}.{tx._fingerprint(source, 'parakeet')}.json" \
        if source.exists() else None
    if cached and cached.exists():
        return json.loads(cached.read_text())
    # The file may have moved since; the newest Parakeet transcript of it will do.
    found = sorted(tx.CACHE_DIR.glob(f"{source.stem}.*.json"), key=lambda p: p.stat().st_mtime)
    for path in reversed(found):
        data = json.loads(path.read_text())
        if str(data.get("model", "")).startswith("parakeet"):
            return data
    raise FileNotFoundError("No transcript for this sermon yet - Analyze it first.")


def edit_words(payload: dict, edit_map: list[dict] | None = None) -> list[dict]:
    """Every word, timed on the edit. Words from parts cut out of the edit are
    dropped, and a word straight after a cut is marked so no caption spans it."""
    words = tx.clean_seams(_transcript_for(payload)["words"])
    a = payload.get("alignment")
    out, last_piece = [], None
    for w in words:
        s, e = float(w["start"]), float(w["end"])
        if a:
            s, e = s + a["a0"] + a["a1"] * s, e + a["a0"] + a["a1"] * e
        piece, piece_end = None, None
        if edit_map:
            for k, m in enumerate(edit_map):
                t = m["offset"] + s * m["scale"]
                if m["tMin"] - 0.0005 <= t <= m["tMax"] + 0.0005:
                    piece, piece_end = k, m["tMax"]
                    s, e = t, min(m["tMax"], m["offset"] + e * m["scale"])
                    break
            if piece is None:
                last_piece = None
                continue
        elif s < 0:
            continue
        text = str(w.get("word") or "").strip()
        if not text:
            continue
        cut = bool(out) and piece != last_piece
        out.append({"text": text, "start": s, "end": max(e, s + 0.05), "cut": cut,
                    "piece_end": piece_end, "mic": float(w["start"])})
        last_piece = piece
    return out


# --------------------------------------------------------------------------
# tidying
# --------------------------------------------------------------------------
def _bare(text: str) -> str:
    return re.sub(r"[^\w']", "", text.lower())


def _ends_sentence(text: str) -> bool:
    return bool(re.search(r"[.?!][\"'”’)]*$", text)) and text.lower() not in ABBREVIATIONS


def _cap(text: str) -> str:
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1:]
    return text


def tidy(words: list[dict]) -> list[dict]:
    out: list[dict] = []
    for w in words:
        text = w["text"]
        # Punctuation that came through as a word of its own joins the last.
        if not re.search(r"[A-Za-z0-9]", text):
            if out:
                out[-1]["text"] += text
            continue
        out.append(dict(w, text=text))

    for w in out:
        # "Christ,," "suffering.." "evening.,": keep the strongest mark.
        m = re.search(r"([.?!,;:]{2,})$", w["text"])
        if m and not w["text"].endswith("..."):
            run = m.group(1)
            mark = "?" if "?" in run else "!" if "!" in run else "." if "." in run else run[0]
            w["text"] = w["text"][: m.start()] + mark
        if re.fullmatch(r"i('\w+)?[,.?!;:]?", w["text"]):
            w["text"] = "I" + w["text"][1:]

    n = len(out)
    for i, w in enumerate(out):
        low = _bare(w["text"])
        prev = _bare(out[i - 1]["text"]) if i else ""
        prev2 = _bare(out[i - 2]["text"]) if i > 1 else ""
        nxt = _bare(out[i + 1]["text"]) if i + 1 < n else ""
        cap = False
        if low in ("jesus", "christ", "bible", "amen", "hallelujah", "christ's", "jesus'"):
            cap = True
        elif low == "god" and prev not in SMALL_GODS:
            cap = True
        elif low == "lord" and (prev in ("the", "o", "our", "my") or nxt in ("jesus", "god")):
            cap = True
        elif low in ("spirit", "ghost"):
            cap = prev == "holy" or (prev == "the" and prev2 in ("of", "in", "by", "with") and i > 2
                                     and _bare(out[i - 3]["text"]) in SPIRIT_BEFORE) \
                or (prev == "the" and nxt == "of" and i + 2 < n and
                    _bare(out[i + 2]["text"]) in ("god", "the", "christ", "truth"))
        elif low == "holy" and nxt in ("spirit", "ghost"):
            cap = True
        elif low in B.STEM_ALIASES and len(low) > 3 and re.match(r"\d", out[i + 1]["text"] if i + 1 < n else ""):
            cap = low not in B.NOT_BOOKS or low in ("john", "james", "mark", "luke", "peter", "jude",
                                                    "ruth", "daniel", "joel", "amos", "titus",
                                                    "timothy", "acts", "numbers", "judges", "kings")
        if cap:
            w["text"] = _cap(w["text"])

    for i in range(n - 1):
        a, b = out[i], out[i + 1]
        nb = _bare(b["text"])
        # A full stop lost before a new sentence: Parakeet capitalised the
        # next word as a sentence start ("…something And then he…") but
        # dropped the mark. Also where he paused long and began again.
        if (not re.search(r"[.?!,;:]$", a["text"]) and _bare(a["text"]) not in CLINGY
                and b["text"][:1].isupper() and nb in STARTERS and nb not in DEITY_PRONOUNS
                and (b["start"] - a["end"] > 1.2 or nb in SENTENCE_OPENERS)):
            a["text"] += "."


    # Sentences start with a capital.
    start = True
    for w in out:
        if start:
            w["text"] = _cap(w["text"])
        start = _ends_sentence(w["text"])
    return out


# --------------------------------------------------------------------------
# subtitles
# --------------------------------------------------------------------------
def _text(ws: list[dict]) -> str:
    return " ".join(w["text"] for w in ws)


def _break_quality(ws: list[dict], k: int) -> float:
    """How good a place it is to break after ws[k-1] (higher is better)."""
    left = ws[k - 1]["text"]
    score = 0.0
    if _ends_sentence(left):
        score += 10
    elif re.search(r"[;:]$", left):
        score += 7
    elif re.search(r"[,—-]$", left):
        score += 5
    if _bare(left) in CLINGY and not re.search(r"[.?!,;:]$", left):
        score -= 6
    if (k < len(ws) and left[:1].isupper() and ws[k]["text"][:1].isupper()
            and not re.search(r"[.?!,;:]$", left)):
        score -= 5          # "Lord / Jesus Christ", "Hebrews / chapter"
    if k < len(ws):
        pause = ws[k]["start"] - ws[k - 1]["end"]
        score += min(4.0, pause * 6)
    return score


def _line_split(ws: list[dict], lim: Limits = YOUTUBE) -> tuple[int | None, float]:
    """Where to break a caption into two lines, and how good that break is."""
    best, best_score = None, None
    for k in range(1, len(ws)):
        top, bottom = _text(ws[:k]), _text(ws[k:])
        if len(top) > lim.max_line or len(bottom) > lim.max_line:
            continue
        score = _break_quality(ws, k) * 1.5 - abs(len(top) - len(bottom)) / 4.0
        if k == 1 or k == len(ws) - 1:
            score -= 8          # a word left on a line of its own reads as an orphan
        if len(bottom) >= len(top):
            score += 1          # a bottom-heavy pair reads better
        if best_score is None or score > best_score:
            best, best_score = k, score
    return best, (best_score if best_score is not None else -99.0)


def _two_lines(ws: list[dict], lim: Limits = YOUTUBE) -> str:
    text = _text(ws)
    if len(text) <= lim.max_line:
        return text
    best, _ = _line_split(ws, lim)
    if best is None:            # an overlong word: split as evenly as we can
        best = min(range(1, len(ws)), key=lambda k: abs(len(_text(ws[:k])) - len(_text(ws[k:]))))
    return _text(ws[:best]) + "\n" + _text(ws[best:])


def _fits(ws: list[dict], lim: Limits = YOUTUBE) -> bool:
    if len(_text(ws)) > lim.max_chars or ws[-1]["end"] - ws[0]["start"] > lim.max_duration:
        return False
    if len(_text(ws)) <= lim.max_line:
        return True
    k, score = _line_split(ws, lim)
    # Two lines only if they break somewhere sensible ("the / good of" is not);
    # otherwise the caption is split in two instead.
    return k is not None and (score > -8 or len(ws) <= 3)


def caption_groups(words: list[dict], lim: Limits = YOUTUBE) -> list[list[dict]]:
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur and (w["cut"] or w["start"] - cur[-1]["end"] > lim.pause_break):
            groups.append(cur)
            cur = []
        cur.append(w)
        if not _fits(cur, lim):
            # Too much for one caption: break at the best place before this word.
            options = range(max(1, len(cur) // 3), len(cur))
            k = max(options, key=lambda k: (_break_quality(cur, k), k)) if len(cur) > 1 else 1
            while k > 1 and not _fits(cur[:k], lim):
                k -= 1
            groups.append(cur[:k])
            cur = cur[k:]
            if _ends_sentence(w["text"]) and len(_text(cur)) >= lim.sentence_min:
                groups.append(cur)   # what is left still ends with the sentence
                cur = []
        elif _ends_sentence(w["text"]) and len(_text(cur)) >= lim.sentence_min:
            groups.append(cur)       # end the caption with the sentence
            cur = []
    if cur:
        groups.append(cur)
    groups = [g for g in groups if g]
    return _absorb_fragments(groups, lim)


def _absorb_fragments(groups: list[list[dict]], lim: Limits = YOUTUBE) -> list[list[dict]]:
    """A caption of a word or two, or one with no time to be read before the
    next, joins its neighbour when they fit together - unless it was said on
    its own ("Amen.", "Glory.")."""
    changed = True
    while changed:
        changed = False
        for i, g in enumerate(groups):
            prev = groups[i - 1] if i else None
            # "There is comfort … from love.": the end of a sentence after a
            # pause for effect belongs with its beginning.
            finishes = (prev is not None and not g[0]["cut"]
                        and not _ends_sentence(prev[-1]["text"])
                        and g[0]["start"] - prev[-1]["end"] <= 2.0)
            said_alone = (len(g) <= 2 and _ends_sentence(g[-1]["text"]) and not finishes
                          and (i == 0 or g[0]["start"] - groups[i - 1][-1]["end"] > 0.5))
            nxt = groups[i + 1] if i + 1 < len(groups) else None
            slot = (nxt[0]["start"] if nxt else g[-1]["end"] + 1) - g[0]["start"]
            small = len(g) <= 2 or slot < 0.8
            if not small or said_alone:
                continue
            options = [i - 1] if finishes else []
            if nxt and not nxt[0]["cut"] and nxt[0]["start"] - g[-1]["end"] <= lim.pause_break \
                    and not _ends_sentence(g[-1]["text"]):
                options.append(i + 1)
            if i and not g[0]["cut"] and g[0]["start"] - groups[i - 1][-1]["end"] <= lim.pause_break \
                    and i - 1 not in options:
                options.append(i - 1)
            if nxt and not nxt[0]["cut"] and i + 1 not in options:
                options.append(i + 1)
            # Never join across a full stop: two sentences are two captions.
            options = [j for j in options
                       if not (j < i and _ends_sentence(groups[j][-1]["text"]))
                       and not (j > i and _ends_sentence(g[-1]["text"]))]
            for j in options:
                merged = groups[j] + g if j < i else g + groups[j]
                if _fits(merged, lim):
                    lo = min(i, j)
                    groups[lo:lo + 2] = [merged]
                    changed = True
                    break
            if changed:
                break
    return groups


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def subtitles(words: list[dict], lim: Limits = YOUTUBE) -> list[tuple[float, float, str]]:
    groups = caption_groups(words, lim)
    caps = []
    for i, g in enumerate(groups):
        start, end = g[0]["start"], g[-1]["end"] + lim.hang
        nxt = groups[i + 1][0]["start"] if i + 1 < len(groups) else None
        text = _two_lines(g, lim)
        chars = len(text.replace("\n", " "))
        # Long enough to read, and to see at all, without running into the next.
        want = max(end, start + lim.min_duration, start + chars / lim.reading_cps)
        end = min(want, start + max(lim.max_duration, g[-1]["end"] - start + lim.hang))
        if nxt is not None:
            if nxt - end < 0.6:          # nearly touching: close the gap, no flicker
                end = nxt - lim.gap
            end = min(end, nxt - lim.gap)
        if nxt is None or nxt - lim.gap > start + 0.3:
            end = max(end, start + 0.3)
        # Never carry words over a cut in the edit; a word the cut clips to
        # a blink is left out rather than flashed.
        cut_at = g[-1].get("piece_end")
        if cut_at is not None and cut_at < end:
            end = cut_at
            if end - start < 0.4:
                continue
        caps.append((start, end, text))
    return caps


def write_srt(caps, path: Path) -> None:
    lines = []
    for n, (a, b, text) in enumerate(caps, 1):
        lines += [str(n), f"{_srt_time(a)} --> {_srt_time(b)}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


# --------------------------------------------------------------------------
# readable transcript
# --------------------------------------------------------------------------
def _clock(t: float) -> str:
    t = int(max(0, t))
    h, rest = divmod(t, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def scripture_list(payload: dict, edit_map: list[dict] | None) -> list[tuple[float, str]]:
    """The verses on screen, joined into passages: 'Philippians 2:1-11' once,
    not eleven lines."""
    def to_edit(t):
        if not edit_map:
            return t
        for m in edit_map:
            x = m["offset"] + t * m["scale"]
            if m["tMin"] - 0.0005 <= x <= m["tMax"] + 0.0005:
                return x
        return None

    items = []
    for c in payload.get("cues", []):
        if c.get("enabled") is False or not c.get("verse"):
            continue
        at = to_edit(float(c["start"]))
        if at is None:
            continue
        until = at + float(c["end"]) - float(c["start"])
        items.append([at, until, c["book"], c["chapter"], c["verse"], c.get("verse_end") or c["verse"]])
    items.sort()
    joined = []
    for it in items:
        if joined:
            p = joined[-1]
            if (p[2], p[3]) == (it[2], it[3]) and p[4] <= it[4] <= p[5] + 1 and it[0] - p[1] < 90:
                p[5] = max(p[5], it[5])
                p[1] = max(p[1], it[1])
                continue
        joined.append(list(it))
    return [(j[0], B.display(j[2], j[3], j[4], j[5] if j[5] != j[4] else None)) for j in joined]


def paragraphs(words: list[dict], breaks_at: list[float]) -> list[tuple[float, str]]:
    """Paragraphs at natural pauses between sentences, and where a scripture
    comes up, so a reading stands apart from the preaching around it."""
    paras, cur = [], []
    marks = sorted(breaks_at)
    mi = 0
    for i, w in enumerate(words):
        while mi < len(marks) and marks[mi] < w["start"] - 0.5:
            mi += 1
        prev_end = cur and _ends_sentence(cur[-1]["text"])
        new = False
        if cur and w["cut"]:
            new = True
        elif cur and prev_end:
            pause = w["start"] - cur[-1]["end"]
            count = len(cur)
            at_mark = mi < len(marks) and abs(marks[mi] - w["start"]) < 3.0
            new = ((pause >= 1.5 and count >= 40) or count >= 150
                   or (at_mark and count >= 20))
        if new:
            paras.append((cur[0]["start"], _text(cur)))
            cur = []
        cur.append(w)
    if cur:
        paras.append((cur[0]["start"], _text(cur)))
    return paras


def write_transcript(payload, words, edit_map, path: Path, title: str) -> None:
    scriptures = scripture_list(payload, edit_map)
    paras = paragraphs(words, [t for t, _ in scriptures])
    out = [title, "Transcript made by TEC Sermon Studio from the mic recording. "
                  "Times match the edited video.", ""]
    if scriptures:
        out.append("Scriptures")
        out += [f"{_clock(t)}  {ref}" for t, ref in scriptures]
        out.append("")
    out.append("Transcript")
    out.append("")
    for t, text in paras:
        out.append(f"[{_clock(t)}]  {text}")
        out.append("")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def make(payload: dict, out_dir: Path, name: str, edit_map: list[dict] | None = None) -> dict:
    words = tidy(edit_words(payload, edit_map))
    if not words:
        raise RuntimeError("No words of the sermon fall inside this edit.")
    out_dir.mkdir(parents=True, exist_ok=True)
    srt = out_dir / f"{name} - subtitles.srt"
    txt = out_dir / f"{name} - transcript.txt"
    caps = subtitles(words)
    write_srt(caps, srt)
    write_transcript(payload, words, edit_map, txt, name)
    return {"ok": True, "srt": str(srt), "txt": str(txt), "captions": len(caps),
            "words": len(words)}
