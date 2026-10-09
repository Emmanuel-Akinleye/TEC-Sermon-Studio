"""Turn mentions and quote hits into cues with in/out times.

The behaviour being imitated is a live operator: the card appears as the
reference is spoken, stays while the passage is being read or discussed, and
leaves once the preacher has clearly moved on. So the out point is evidence
driven — the end of the reading, the next citation, a long pause — with a
hard cap only as a backstop.
"""
from __future__ import annotations

import re

from dataclasses import dataclass, asdict, field

import bible
import books as B
import quotes as Q


@dataclass
class Cue:
    ref: str
    book: str
    chapter: int
    verse: int
    verse_end: int | None
    start: float
    end: float
    text: str = ""
    translation: str = ""
    confidence: float = 0.0
    source: str = "spoken"       # spoken | read | spoken+read
    kind: str = "verse"          # verse | document (a confession) | slide (a deck)
    # A presentation slide: the picture to put on screen, and its place in
    # the deck. Empty for everything else.
    image: str = ""
    slide: int = 0
    said: str = ""
    enabled: bool = True
    # True when the timing comes from aligning the verse text against the
    # speech, rather than from guessing around a spoken reference.
    precise: bool = False
    # Share of the verse's distinctive words actually said while the card is
    # up; None where the transcript has nothing there to check against.
    verified: float | None = None
    # Why a person should look at this card, in plain words. Empty if fine.
    check: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = round(self.duration, 3)
        return d


DEFAULTS = {
    "lead_in": 0.15,          # appear slightly before the book name lands
    "min_hold": 4.5,          # never flash a verse shorter than this
    "max_hold": 15.0,         # backstop when nothing else ends the cue
    "reading_cap": 45.0,      # absolute ceiling while still being read
    "read_tail": 1.6,         # linger after the last quoted word
    "link_window": 30.0,      # how far from a mention a reading may sit
    "regroup_gap": 25.0,      # re-mentions inside this window extend the cue
    "next_cue_gap": 0.4,      # clearance before the following card
    "silence_out": 3.0,       # a pause this long ends the cue early
    "min_confidence": 0.7,
    # Below this, Whisper was unsure of the words the citation was built from.
    "heard_floor": 0.55,    # below this the cue is kept but off by default
    "include_unannounced": True,
    # "Turn to Exodus chapter 32" with no verse and no reading: there is no
    # verse text to show, but the editor still wants to see that it happened.
    # These become reference-only chips, switched off until ticked.
    "include_chapter_only": True,
    "chapter_only_enabled": False,
    "chapter_only_hold": 6.0,
    # A spoken range ("verses one to thirteen") becomes one card per verse,
    # each held while that verse is actually being read.
    "split_ranges": True,
    "range_window": 900.0,
    "min_verse_hold": 3.0,
    "min_range_support": 0.10,
    "range_gap": 0.25,
    # "From verse 8" means start at 8 and keep going. Follow the reading into
    # the verses after a citation for as long as the words keep matching.
    "follow_reading": True,
    "follow_max": 15,
    "follow_min_matched": 2,
    "follow_min_coverage": 0.4,
    "follow_max_pause": 25.0,
    # A reading with no spoken reference has to stand entirely on its own
    # evidence, so it faces a far stricter test than one that merely confirms
    # a citation the preacher already said out loud.
    "unannounced_min_score": 28.0,
    "unannounced_min_matched": 4,
    "unannounced_min_order": 0.8,
    "link_min_order": 0.55,
    "fps": 25.0,
}


def _snap(t: float, fps: float) -> float:
    return round(round(t * fps) / fps, 4)


def _word_gap_after(words: list[dict], t: float) -> float:
    """Length of the first silence starting at or after time t."""
    prev_end = None
    for w in words:
        start = float(w.get("start", 0.0))
        if start >= t:
            if prev_end is not None and start - prev_end > 0:
                return start - prev_end
            return 0.0
        prev_end = float(w.get("end", start))
    return 0.0


def _next_speech_gap(words: list[dict], after: float, min_gap: float) -> float | None:
    """Time at which the first silence longer than min_gap begins."""
    prev_end = None
    for w in words:
        start = float(w.get("start", 0.0))
        if prev_end is not None and start > after and start - prev_end >= min_gap:
            return prev_end
        prev_end = max(prev_end or 0.0, float(w.get("end", start)))
    return None


def build_cues(mentions, quote_hits, words, translation="BSB",
               corpus="BSB", cfg=None, fetch_text=True, index=None) -> list[Cue]:
    cfg = {**DEFAULTS, **(cfg or {})}
    fps = float(cfg["fps"])

    anchors = [m for m in mentions if m.kind in ("full", "verse-only") and m.verse]

    # Fetch the passages he cited in the translation he is reading, before the
    # readings are matched: a verse is recognised by the words he actually
    # says, which are not always the corpus's words.
    if fetch_text and translation.upper() != corpus.upper():
        wanted = []
        for m in anchors:
            last = (m.verse_end or m.verse) + cfg.get("follow_max", 15)
            for v in range(m.verse, min(last, m.verse + 40) + 1):
                wanted.append((m.book, m.chapter, v))
        try:
            bible.prefetch(translation, wanted)
        except Exception:
            pass    # the corpus still works; cards will say if text is missing

    quotes_by_ref: dict[tuple[str, int, int], list] = {}
    for h in quote_hits:
        quotes_by_ref.setdefault((h.book, h.chapter, h.verse), []).append(h)

    cues: list[Cue] = []
    used_quotes: set[int] = set()

    covered: list[tuple[str, int, int, int, float]] = []

    if index is not None and cfg.get("correct_books", True):
        seen: list[tuple[str, int]] = []
        for mention in mentions:
            if mention in anchors:
                mention.earlier_passages = tuple(seen)
                _correct_book(mention, index, words, cfg, quote_hits)
            if mention.chapter and (mention.book, mention.chapter) not in seen:
                seen.append((mention.book, mention.chapter))

    # Only now: a range that cannot fit the book was usually the wrong book
    # ("verse 19 to 22" of what he called Romans 12, which has 21 verses),
    # and correcting it first saves the whole passage. What is still
    # impossible is trimmed to the chapter, or dropped.
    kept_anchors = []
    for m in anchors:
        total = bible.verse_count(corpus, m.book, m.chapter)
        if total and m.verse_end and m.verse <= total < m.verse_end:
            m.verse_end = total
        if _plausible(m, corpus):
            kept_anchors.append(m)
    anchors = kept_anchors

    # Citations first; then any run of readings from one chapter that no
    # citation accounted for is treated as a passage in its own right.
    queue, qi, implied_done = list(anchors), 0, False
    while True:
        if qi >= len(queue):
            if implied_done or index is None:
                break
            implied_done = True
            queue.extend(_implied_passages(quote_hits, used_quotes, cues, mentions, cfg))
            continue
        m = queue[qi]
        qi += 1
        v_end = m.verse_end or m.verse

        # The same passage cited twice in a row ("chapter 13 from verse 8 …
        # chapter 13 verse 8 says") is already on screen from the first time.
        # Skip only if this citation falls while that verse is already on
        # screen; "verse 20 again" half a minute later is a fresh reading.
        if any(c.precise and c.book == m.book and c.chapter == m.chapter
               and c.verse <= m.verse <= (c.verse_end or c.verse)
               and c.start - 1 <= m.start <= c.end + 1 for c in cues):
            continue

        followed = []
        if cfg["follow_reading"] and index is not None:
            v_end, followed = _follow_reading(m, v_end, index, words, mentions, cfg)

        # A range gets split into one card per verse, timed by aligning the
        # verses against what was actually said.
        if cfg["split_ranges"] and index is not None and (v_end > m.verse or followed):
            spans = (Q._merge_short(followed, cfg["min_verse_hold"]) if followed
                     else _split_range(m, v_end, index, words, mentions, cfg))
            if spans:
                # Wherever the reading stops, the card should stop with it.
                last = spans[-1]
                horizon = min(_reading_limit(m, mentions, words, cfg),
                              last.start + cfg["reading_cap"])
                last.end = Q.reading_end(index, words, m.book, m.chapter,
                                         last.verse_end, last.start, horizon,
                                         cfg.get("final_hit_gap", 3.5))
            if spans:
                covered.append((m.book, m.chapter, m.verse, v_end, spans[-1].end))
                for i, span in enumerate(spans):
                    end = min(span.end + cfg["read_tail"],
                              span.start + cfg["reading_cap"])
                    if i + 1 < len(spans):
                        # Normally hold until the next verse begins, so one
                        # fades into the next with no blank panel. But if he
                        # stopped to preach before reading on, end with the
                        # reading; the next card comes when he picks it up.
                        nxt = spans[i + 1].start
                        stopped = Q.reading_end(index, words, m.book, m.chapter,
                                                span.verse_end, span.start, nxt,
                                                cfg.get("final_hit_gap", 3.5))
                        if nxt - stopped > cfg.get("handover_gap", 10.0):
                            end = stopped + cfg["read_tail"]
                        else:
                            end = nxt - cfg["range_gap"]
                            end = max(end, span.end + 0.2)
                    split = Cue(
                        ref=B.display(m.book, m.chapter, span.verse,
                                      span.verse_end if span.verse_end != span.verse else None),
                        book=m.book, chapter=m.chapter,
                        verse=span.verse,
                        verse_end=span.verse_end if span.verse_end != span.verse else None,
                        start=max(0.0, (m.start - cfg["lead_in"]) if i == 0
                                  else span.start - cfg["lead_in"]),
                        end=end,
                        translation=translation,
                        confidence=round(min(1.0, m.confidence), 3),
                        source="spoken+read",
                        precise=True,
                        said=m.said if i == 0 else f"read verse {span.verse}",
                        notes=(["from the range " + m.ref] if i else ["range split by reading"])
                              + ([f"corrected from {m.book_corrected_from} by the reading"]
                                 if getattr(m, "book_corrected_from", None) and not i else []),
                    )
                    _apply_heard(split, m, cfg)
                    cues.append(split)
                _merge_brief(cues, m, cfg)
                for h in quote_hits:
                    if (h.book == m.book and h.chapter == m.chapter
                            and m.verse <= h.verse <= v_end
                            and spans[0].start - 5 <= h.start <= spans[-1].end + 5):
                        used_quotes.add(id(h))
                continue
        # A reading of any verse in the cited range, near the mention, both
        # confirms the detection and tells us when the passage is done.
        reading_end = None
        reading_seen = False
        for v in range(m.verse, v_end + 1):
            for idx, h in enumerate(quotes_by_ref.get((m.book, m.chapter, v), [])):
                if (-cfg["link_window"] <= h.start - m.end <= cfg["link_window"]
                        and h.order >= cfg["link_min_order"]):
                    reading_seen = True
                    reading_end = max(reading_end or 0.0, h.end)
                    used_quotes.add(id(h))

        start = max(0.0, m.start - cfg["lead_in"])
        end = m.end + cfg["min_hold"]
        notes: list[str] = []

        if reading_seen and reading_end:
            # Stop where the reading stopped, not where the verse's words last
            # came up while he preached on it.
            stopped = Q.reading_end(index, words, m.book, m.chapter, v_end, m.start,
                                    min(reading_end, m.start + cfg["reading_cap"]),
                                    cfg.get("final_hit_gap", 3.5)) if index is not None \
                else reading_end
            end = max(end, stopped + cfg["read_tail"])
            end = min(end, m.start + cfg["reading_cap"])
            notes.append("held through reading")
        else:
            end = min(end, m.start + cfg["max_hold"])

        conf = m.confidence
        if reading_seen:
            conf = min(1.0, conf + 0.12)
            source = "spoken+read"
        else:
            source = "spoken"

        # A range announced but not yet read through ("I'll read from verse 19
        # to 22", then half a minute of preamble) shows the verse he is about
        # to read, not the whole passage as a wall of text. The verse-by-verse
        # cards take over as soon as he reads.
        show_to = m.verse_end
        if show_to and show_to > m.verse:
            show_to = None
            notes.append(f"announced as {m.ref}; showing the first verse until he reads on")

        cue = Cue(
            ref=B.display(m.book, m.chapter, m.verse, show_to),
            book=m.book, chapter=m.chapter, verse=m.verse,
            verse_end=show_to, start=start, end=end,
            translation=translation, confidence=round(conf, 3),
            source=source, said=m.said, notes=notes,
        )
        if m.kind == "verse-only":
            cue.notes.append(CARRIED_NOTE)
        if getattr(m, "relative", False):
            cue.notes.append(RELATIVE_NOTE)
        if getattr(m, "loose", False):
            cue.notes.append(LOOSE_NOTE)
        _apply_heard(cue, m, cfg)
        cues.append(cue)

    if cfg["include_chapter_only"]:
        for m in mentions:
            if m.kind != "chapter-only" or not m.chapter:
                continue
            if any(c.book == m.book and c.chapter == m.chapter
                   and not (m.end < c.start - 2.0 or m.start > c.end + 2.0)
                   for c in cues):
                continue  # the verse itself is already on screen here
            chapter_cue = Cue(
                ref=f"{m.book} {m.chapter}", book=m.book, chapter=m.chapter,
                verse=0, verse_end=None,
                start=max(0.0, m.start - cfg["lead_in"]),
                end=m.end + cfg["chapter_only_hold"],
                translation=translation, confidence=round(m.confidence, 3),
                source="chapter", said=m.said,
                enabled=bool(cfg["chapter_only_enabled"]),
                notes=["chapter only - no verse spoken"],
            )
            _apply_heard(chapter_cue, m, cfg)
            cues.append(chapter_cue)

    if cfg["include_unannounced"]:
        for h in quote_hits:
            if id(h) in used_quotes:
                continue
            # While a passage is confirmed to be being read, a loose match to
            # some other verse is almost always shared wording (Judges 13:5
            # and 13:7 say nearly the same thing), not a second reading.
            if any(c.precise and not (h.end < c.start or h.start > c.end) for c in cues):
                continue
            if (h.score < cfg["unannounced_min_score"]
                    or h.matched < cfg["unannounced_min_matched"]
                    or h.order < cfg["unannounced_min_order"]):
                continue
            if any(c.book == h.book and c.chapter == h.chapter
                   and c.verse <= h.verse <= (c.verse_end or c.verse)
                   and not (h.end < c.start or h.start > c.end)
                   for c in cues):
                continue
            cues.append(Cue(
                ref=h.ref, book=h.book, chapter=h.chapter, verse=h.verse,
                verse_end=None,
                start=max(0.0, h.start - cfg["lead_in"]),
                # Same ceiling the announced readings get: a card should not
                # sit there for minutes because a word recurred late.
                end=min(h.end + cfg["read_tail"], h.start + cfg["reading_cap"]),
                translation=translation,
                confidence=round(min(0.95, 0.55 + h.score / 120.0), 3),
                source="read",
                said=f"read aloud ({h.matched}/{h.total} words)",
                notes=["not announced aloud"],
            ))

    cues.sort(key=lambda c: c.start)
    cues = _merge_repeats(cues, cfg)
    cues = _apply_boundaries(cues, words, cfg)
    if words and cfg.get("snap_to_wording", True):
        _snap_to_wording(cues, words, cfg)
    cues = [c for c in cues if not c.enabled or c.duration >= 1.2]

    for c in cues:
        c.start = _snap(c.start, fps)
        c.end = _snap(max(c.end, c.start + 1.0 / fps), fps)
        if c.verse and c.confidence < cfg["min_confidence"]:
            c.enabled = False
            c.notes.append("low confidence - review")

    if fetch_text:
        _attach_text(cues, translation, corpus)
    if index is not None and words:
        cues = verify(cues, words, index, cfg, mentions)
        _resolve_unread(cues, words, cfg, translation, corpus, fetch_text)
    if words and cfg.get("echoes", True):
        cues = _echoes(cues, words, translation, corpus, cfg, fetch_text, index)
    return cues


RELATIVE_NOTE = "said as 'the next verse' or similar"
CARRIED_NOTE = "verse number only - book and chapter carried over"
LOOSE_NOTE = "book name that is also an everyday word"


def _vocab(cue: Cue, index) -> set[str]:
    """The distinctive words of the verse, in both the matching translation
    and the one on screen, so a preacher reading the ESV is not marked down
    for not using the BSB's wording."""
    vocab: set[str] = set()
    for v in range(cue.verse, (cue.verse_end or cue.verse) + 1):
        vid = index.vid_of(cue.book, cue.chapter, v)
        if vid is not None:
            vocab |= index.verse_words[vid]
    for token in Q.WORD_RE.findall(B.normalize(cue.text or "")):
        if token in Q.STOPWORDS or len(token) < 3:
            continue
        stem = Q.stem(token)
        if stem in index.idf:
            vocab.add(stem)
    return vocab


# "Let's go back to Judges" returns to a passage; only "no, let's…" retracts.
CORRECTIONS = ("no let's", "no let us", "sorry", "i mean", "not verse",
               "rather verse", "correction", "no no")


def _corrects_himself(words: list[dict], at: float, cfg: dict) -> bool:
    """Whether a correction is spoken shortly after a citation."""
    window = cfg.get("verify_correction_window", 12.0)
    text = " " + " ".join(B.normalize(w.get("word") or "") for w in words
                          if at <= float(w.get("start", 0.0)) <= at + window) + " "
    text = " ".join(text.split())
    # Normalise the phrases exactly as the transcript is ("let's" -> "let s").
    return any(" " + B.normalize(p) + " " in " " + text + " " for p in CORRECTIONS)


def _named_another(c: Cue, mentions, cfg: dict) -> bool:
    """Whether a different verse was cited within seconds of this one."""
    window = cfg.get("verify_next_citation", 10.0)
    for m in mentions or []:
        if not m.verse or m.book != c.book:
            continue
        if c.start + 0.5 < m.start <= c.start + window and m.verse != c.verse:
            return True
    return False


def verify(cues: list[Cue], words: list[dict], index, cfg: dict,
           mentions=None) -> list[Cue]:
    """Check every card against what was actually said while it is on screen.

    Three outcomes matter, and they are told apart by whether anyone was
    speaking at all:

    * nothing transcribed there        -> cannot check, leave it alone
    * speech, but none of the verse    -> the verse was not read
    * speech containing the verse      -> confirmed

    What happens to an unread verse depends on how it got there. A verse the
    preacher named but paraphrased is a real citation and stays. A verse the
    tool assumed he read next, or guessed from a loose match, goes.
    """
    pad = cfg.get("verify_pad", 2.0)
    kept: list[Cue] = []
    for c in cues:
        if not c.verse:
            kept.append(c)
            continue
        vocab = _vocab(c, index)
        said, speech = set(), 0
        # A card of two seconds holds too few words to judge; look at no less
        # than a few seconds of speech whatever its length.
        until = max(c.end, c.start + cfg.get("verify_min_window", 6.0)) + pad
        for w in words:
            t = float(w.get("start", 0.0))
            if t < c.start - pad:
                continue
            if t > until:
                break
            for token in Q.WORD_RE.findall(B.normalize(w.get("word") or "")):
                if token in Q.STOPWORDS or len(token) < 3:
                    continue
                speech += 1
                stem = Q.stem(token)
                if stem in vocab:
                    said.add(stem)

        if speech < cfg.get("verify_min_speech", 4) or len(vocab) < 2:
            c.verified = None
            if c.duration > 4:
                c.notes.append("no transcript here to check against")
            kept.append(c)
            continue

        share = len(said) / len(vocab)
        matched = len(said)
        c.verified = round(share, 2)
        followed = any(n.startswith("from the range") for n in c.notes)

        # A preacher who names a verse and immediately takes it back
        # ("verse 28? ... no, let's go back") has not cited it.
        if matched <= 1 and (_corrects_himself(words, c.start, cfg)
                             or _named_another(c, mentions, cfg)):
            c.check = "He named this verse, then moved straight to another without reading it."
            c.enabled = False
            kept.append(c)
            continue

        if matched == 0:
            if followed:
                c.notes.append("dropped: none of this verse was said - skipped")
                continue
            if RELATIVE_NOTE in c.notes:
                # "The next verse" worked out wrong, or never read: nothing to show.
                continue
            if LOOSE_NOTE in c.notes:
                c.enabled = False
                c.check = (f"Heard \u201c{c.said}\u201d - that may be ordinary speech, "
                           "not a Bible reference. Nothing from the verse was read.")
                kept.append(c)
                continue
            if c.source == "read":
                c.enabled = False
                c.check = "Guessed from a loose match, and none of it was read."
            else:
                c.notes.append("cited, not read aloud")
            kept.append(c)
            continue

        # Counting words, not percentages: a short card or a partly read
        # verse catches only a slice of the verse, and is still right.
        if c.source == "read" and matched < cfg.get("verify_read_min", 3) \
                and share < cfg.get("verify_read_floor", 0.25):
            c.enabled = False
            c.check = "Only a word or two of this matched - probably a loose match."
        elif followed and matched == 1:
            # Followed on from the verse before, with one word to show for it:
            # he moved on to talking about the last verse, not reading this one.
            c.notes.append("dropped: only one word of this verse was said")
            continue
        elif c.duration > 15 and share < cfg.get("verify_thin", 0.12):
            c.check = f"Very little of this verse was said in {c.duration:.0f} seconds."
        kept.append(c)

    # No hole in a reading. The next verse of the same passage takes over
    # straight from the one before — however it was cited ("…verse 29 says"
    # after reading 27-28 counts as carrying on) — unless he preached long
    # enough in between for the panel to dissolve out anyway.
    on = [c for c in kept if c.enabled]
    for i in range(len(on) - 1):
        a, b = on[i], on[i + 1]
        if not (a.precise and b.precise and a.book == b.book and a.chapter == b.chapter):
            continue
        gap = b.start - a.end
        next_verse = b.verse == (a.verse_end or a.verse) + 1
        limit = cfg.get("handover_gap", 40.0) if next_verse else cfg.get("verify_bridge", 12.0)
        if 0 < gap < limit:
            a.end = b.start
    return kept


def _verse_words(index, book: str, chapter: int, first: int, last: int) -> set[str] | None:
    vocab: set[str] = set()
    for v in range(first, last + 1):
        if index.vid_of(book, chapter, v) is None:
            return None
        vocab |= Q.vocab_for(index, book, chapter, v)
    return vocab


def _heard_in(words, start: float, end: float) -> set[str]:
    heard = set()
    for w in words:
        t = float(w.get("start", 0.0))
        if t < start:
            continue
        if t > end:
            break
        for token in Q.WORD_RE.findall(B.normalize(w.get("word") or "")):
            if token not in Q.STOPWORDS and len(token) >= 3:
                heard.add(Q.stem(token))
    return heard


def _stream(words: list[dict], a: float, b: float) -> list[tuple[str, float, float]]:
    out = []
    for w in words:
        t = float(w.get("start", 0.0))
        if t < a:
            continue
        if t > b:
            break
        for tok in B.normalize(w.get("word") or "").split():
            out.append((Q.stem(tok), t, float(w.get("end", t))))
    return out


def _phrase_score(stream, book: str, chapter: int, first: int, last: int, cfg: dict) -> int:
    """Most words of these verses heard in order, in any local translation.

    Rare-word counting misses a verse read from a different Bible ("Have this
    mind among yourselves" shares almost no rare words with "Let this mind be
    in you"); runs of the actual wording do not."""
    best = 0
    for v in range(first, last + 1):
        for text in bible.local_texts(book, chapter, v):
            for seg in _find_quotes(stream, _tokens(text), cfg):
                if seg["longest"] >= 3 and len(seg["content"]) >= 2:
                    best = max(best, seg["matched"])
    return best


def _correct_book(m, index, words, cfg, quote_hits=()) -> None:
    """Fix a citation whose book or chapter was misheard or carried over wrongly.

    "1 Timothy 6" can come through as "in 1966", after which the chapter and
    verses are right but the book is whatever was carried over from earlier;
    a bare "verse 5" can land in the chapter named last when he has gone back
    to his main text. The reading that follows settles it: find the passage
    whose wording was actually read, and switch only when that match is
    strong and the named one's is not.
    """
    first, last = m.verse, m.verse_end or m.verse
    window = cfg.get("book_check_window", 75.0)
    heard = _heard_in(words, m.start, m.end + window)
    if not heard:
        return
    stream = _stream(words, m.start, m.end + window)

    def score_at(book, chapter):
        vocab = _verse_words(index, book, chapter, first, last)
        if not vocab:
            return 0, 0.0
        hit = len(vocab & heard)
        return hit, hit / len(vocab)

    have, have_share = score_at(m.book, m.chapter)
    have_phrase = _phrase_score(stream, m.book, m.chapter, first, last, cfg)
    if (have >= 3 and have_share >= 0.25) or have_phrase >= 6:
        return

    # Every way a citation goes wrong, as a place the verse could really be:
    #   the same chapter and verse in every book      — a misheard book name
    #   the same verse in every earlier passage        — a bare "verse 24"
    #                                                    pointing back
    #   the same verse in every chapter of that book   — a misheard chapter
    #   whatever he starts reading straight after      — any of the above
    # The words that follow decide between them.
    candidates = {(book, m.chapter) for book in B.CANONICAL}
    candidates |= set(getattr(m, "earlier_passages", ()))
    candidates |= {(m.book, ch) for ch in range(1, B.max_chapter(m.book) + 1)}
    candidates |= {(h.book, h.chapter) for h in quote_hits
                   if m.start - 5 <= h.start <= m.end + 40}
    candidates.discard((m.book, m.chapter))

    best, best_hit, best_share = None, 0, 0.0
    phrase_best, phrase_at = 0, None
    for book, chapter in candidates:
        hit, share = score_at(book, chapter)
        if hit > best_hit or (hit == best_hit and share > best_share):
            best, best_hit, best_share = (book, chapter), hit, share
        if hit or (book, chapter) in getattr(m, "earlier_passages", ()):
            p = _phrase_score(stream, book, chapter, first, last, cfg)
            if p > phrase_best:
                phrase_best, phrase_at = p, (book, chapter)

    if phrase_at and phrase_best >= 6 and phrase_best >= have_phrase + 4:
        best = phrase_at
    elif not (best and best_hit >= max(4, have + 3) and best_share >= 0.3):
        return
    old = m.ref
    m.book, m.chapter = best
    m.confidence = min(m.confidence, 0.9)
    m.book_corrected_from = old


def _implied_passages(quote_hits, used_quotes, cues, mentions, cfg) -> list:
    """Passages he read without announcing them.

    After reading Hosea 2 he went back through verses 5 to 8, stopping to
    explain each. Taken one at a time those look like stray quotations and
    each must prove itself alone; taken together they are plainly a
    reading, and verse 7 in the middle belongs with them. So readings from
    the same chapter close together in time become one passage, placed
    verse by verse like any cited one. A chapter already cited in the sermon
    needs less proof: he is returning to it.
    """
    import refs as R

    cited = {(m.book, m.chapter) for m in mentions if m.chapter}
    floor = cfg.get("implied_min_score", 18.0)
    free = sorted((h for h in quote_hits
                   if id(h) not in used_quotes and h.score >= floor and h.matched >= 3
                   and not any(c.precise and not (h.end < c.start or h.start > c.end)
                               for c in cues)),
                  key=lambda h: h.start)

    # Hits become one passage only if they carry on from each other: the same
    # chapter, close in time, and close in the book. Without that a stray
    # match on verse 2 joins a reading of 19 to 23 and the card becomes the
    # whole chapter.
    span = cfg.get("implied_max_span", 8)
    groups: list[list] = []
    for h in free:
        g = groups[-1] if groups else None
        if (g and (g[-1].book, g[-1].chapter) == (h.book, h.chapter)
                and h.start - max(x.end for x in g) <= cfg.get("implied_gap", 90.0)
                and max(x.verse for x in g) - span < h.verse < min(x.verse for x in g) + span):
            g.append(h)
        else:
            groups.append([h])

    implied = []
    for g in groups:
        known = (g[0].book, g[0].chapter) in cited
        strong = [h for h in g if h.score >= cfg["unannounced_min_score"]]
        if not (known and len(g) >= 2) and not (len(g) >= 2 and len(strong) >= 2):
            continue
        first = min(h.verse for h in g)
        last = max(h.verse for h in g)
        start = min(h.start for h in g)
        for h in g:
            used_quotes.add(id(h))
        implied.append(R.Mention(
            book=g[0].book, chapter=g[0].chapter, verse=first,
            verse_end=last if last > first else None, kind="implied",
            start=max(0.0, start - 0.5), end=start, confidence=0.85,
            said="read without being announced",
        ))
    return implied


def _merge_brief(cues: list[Cue], m, cfg: dict) -> None:
    """A card on screen too briefly to read joins the one after it.

    Verses read back to back ("…free in regard to righteousness. But what
    fruit were you getting…") can leave one card with a second or two.
    """
    floor = cfg.get("min_verse_hold", 2.5)
    group = [c for c in cues if c.precise and c.book == m.book and c.chapter == m.chapter
             and c.start >= m.start - 1]
    i = 0
    while i < len(group) - 1:
        a, b = group[i], group[i + 1]
        if a.duration < floor and b.verse == (a.verse_end or a.verse) + 1:
            b.start, b.verse = a.start, a.verse
            b.ref = B.display(b.book, b.chapter, b.verse, b.verse_end or b.verse)
            b.notes = a.notes[:1] + b.notes[1:]
            cues.remove(a)
            group.pop(i)
            continue
        i += 1


def _reading_limit(m, mentions, words, cfg) -> float:
    """Where the reading of a passage must have finished: the next citation
    of a different passage, or the end of the window."""
    limit = m.end + cfg["range_window"]
    first, last = m.verse or 0, m.verse_end or m.verse or 0
    for other in mentions:
        if other.start <= m.end or not other.verse:
            continue
        # Naming any other verse — even in the same chapter ("…verse 21.
        # Let's read from verse 20") — ends what he was reading. Repeating
        # the verse already cited does not.
        same_passage = (other.book, other.chapter) == (m.book, m.chapter)
        repeat_now = other.start - m.end <= cfg.get("repeat_within", 10.0)
        if not same_passage or not (first <= other.verse <= last) or not repeat_now:
            limit = min(limit, other.start)
            break
    if words:
        limit = min(limit, float(words[-1].get("end", limit)))
    return limit


def _follow_reading(m, v_end: int, index, words, mentions, cfg):
    """How far past the cited verses the preacher actually read.

    The passage is aligned well past the citation in one pass. Aligning only
    up to the verse being tested does not work: the final verse of any
    alignment soaks up everything said afterwards, so a verse that was read
    looks unread until the verses after it are in place to hold it there.

    Then walk forward: a verse counts as read when enough of its distinctive
    words turn up and it follows closely on the verse before. Two misses in a
    row ends the reading. Returns (last verse read, spans up to it) — spans are
    empty when the reading went no further than cited.
    """
    top = bible_verse_count(index, m.book, m.chapter)
    ceiling = min(top, v_end + cfg["follow_max"]) if top else v_end
    if ceiling <= v_end:
        return v_end, []

    probe = {**cfg, "min_verse_hold": 0.0, "min_range_support": 0.0}
    spans = Q.align_range(index, words, m.book, m.chapter, m.verse, ceiling,
                          t_from=m.start, t_to=_reading_limit(m, mentions, words, cfg),
                          cfg=probe)
    if not spans:
        return v_end, []

    # Following on only makes sense once the cited verse has been read.
    # "Read from verse 8" followed by five minutes on Genesis is not a
    # reading of Romans 1, whatever stray words turn up later.
    if not any(sp.verse <= v_end <= sp.verse_end or m.verse <= sp.verse <= v_end
               for sp in spans):
        return v_end, []

    best, misses = v_end, 0
    for i, span in enumerate(spans):
        if span.verse <= v_end or i == 0:
            continue
        prev = spans[i - 1]
        # Where the previous verse's reading really stopped — not where its
        # words last happened to recur while the preacher explained it.
        prev_end = Q.reading_end(index, words, m.book, m.chapter, prev.verse_end,
                                 prev.start, span.start, cfg.get("final_hit_gap", 3.5))
        # A preacher stops to explain a word and then reads on. How long a
        # break to allow depends on how sure we are he is reading the next
        # verse: most of its words heard earns a long aside, a couple of
        # words earns only a short one.
        strong = span.matched >= 4 and span.coverage >= 0.5
        allowed = cfg.get("follow_max_pause_strong", 60.0) if strong else cfg["follow_max_pause"]
        read = (span.matched >= cfg["follow_min_matched"]
                and (span.coverage >= cfg["follow_min_coverage"] or span.matched >= 3)
                and span.start - prev_end <= allowed)
        if read:
            best, misses = span.verse, 0
        else:
            misses += 1
            if misses >= 2:
                break
    # Even when the reading goes no further than cited, the alignment knows
    # exactly when the cited verse itself was read — better evidence than a
    # loose match — so hand it back whenever that verse was really heard.
    cited = [sp for sp in spans if sp.verse <= v_end]
    if best == v_end and not any(sp.matched >= cfg["follow_min_matched"] for sp in cited):
        return v_end, []
    # A reading minutes after the citation is a later, separate quotation,
    # not this one — it gets its own card from its own evidence.
    if spans and spans[0].start - m.end > cfg.get("reading_start_within", 40.0):
        return v_end, []
    return best, [s for s in spans if s.verse <= best]


def bible_verse_count(index, book: str, chapter: int) -> int:
    n = 0
    while index.vid_of(book, chapter, n + 1) is not None:
        n += 1
    return n


def _split_range(m, v_end: int, index, words, mentions, cfg):
    """Align the verses of a spoken range against the reading that follows."""
    limit = _reading_limit(m, mentions, words, cfg)

    spans = Q.align_range(index, words, m.book, m.chapter, m.verse, v_end,
                          t_from=m.start, t_to=limit, cfg=cfg)
    spans = _fill_skipped(spans, m, v_end, index, words, limit, cfg)
    if spans and spans[0].start - m.end > cfg.get("reading_start_within", 40.0):
        return []
    return spans


def _fill_skipped(spans, m, v_end: int, index, words, limit: float, cfg):
    """Put back a verse of the announced range that the alignment stepped over.

    Verses carrying only a word or two of their own ("And God spoke all these
    words, saying") are cheaper for the alignment to skip than to place, and
    the reading then appears to start at the verse after - far enough from the
    citation to be thrown out altogether. Their wording is still there to be
    found, so it is looked for between the verses that were placed."""
    if not spans:
        return spans
    placed = {s.verse for s in spans}
    missing = [v for v in range(m.verse, v_end + 1) if v not in placed]
    if not missing:
        return spans
    out = list(spans)
    for v in missing:
        before = [s for s in out if s.verse < v]
        after = [s for s in out if s.verse > v]
        lo = max(m.end, before[-1].end if before else m.end)
        hi = min(after[0].start if after else limit, limit)
        if hi - lo < 0.8:
            continue
        stream = _stream(words, lo - 0.5, hi + 0.5)
        best = None
        for text in bible.local_texts(m.book, m.chapter, v):
            for seg in _find_quotes(stream, _tokens(text), {**cfg, "echo_min_run": 3}):
                # Its opening words, said in order, with something of its own
                # in them: enough to say he read it, not merely echoed a word.
                if seg["j"] > 1 or seg["matched"] < 4 or len(seg["content"]) < 2:
                    continue
                start = stream[seg["i"]][1]
                if best is None or start < best[0]:
                    best = (start, stream[seg["i_end"] - 1][2], seg["matched"])
        if not best:
            continue
        out.append(Q.VerseSpan(verse=v, verse_end=v, start=best[0], end=best[1],
                               matched=best[2], score=float(best[2]), total=best[2]))
        out.sort(key=lambda s: s.verse)
    out.sort(key=lambda s: (s.start, s.verse))
    return out


def _apply_heard(cue: Cue, mention, cfg: dict) -> None:
    """Temper a cue by how clearly its words were actually heard."""
    heard = getattr(mention, "heard", 1.0)
    if heard >= 0.85:
        return
    # Scale from 1.0 at 0.85 down to 0.6 at the floor.
    span = max(0.01, 0.85 - cfg["heard_floor"])
    factor = 0.6 + 0.4 * max(0.0, (heard - cfg["heard_floor"])) / span
    cue.confidence = round(cue.confidence * min(1.0, factor), 3)
    cue.notes.append(f"unclear audio ({heard:.2f})")


def _plausible(m, corpus: str) -> bool:
    """Drop citations whose verse number cannot exist."""
    total = bible.verse_count(corpus, m.book, m.chapter)
    if total == 0:
        return True  # no corpus knowledge; let it through
    top = m.verse_end or m.verse
    return 1 <= m.verse <= total and top <= total


def _merge_repeats(cues: list[Cue], cfg: dict) -> list[Cue]:
    """One continuous card when the same verse is cited again right away."""
    merged: list[Cue] = []
    for c in cues:
        # Only the card immediately before: "21, then 20, then 21 again" is
        # three cards, not one long 21 with 20 buried inside it.
        last = merged[-1] if merged else None
        prev = last if (last and last.book == c.book and last.chapter == c.chapter
                        and last.verse == c.verse and last.verse_end == c.verse_end) else None
        if prev and c.start - prev.end <= cfg["regroup_gap"]:
            prev.end = max(prev.end, c.end)
            prev.confidence = round(min(1.0, max(prev.confidence, c.confidence) + 0.05), 3)
            if "repeated mention" not in prev.notes:
                prev.notes.append("repeated mention")
            if prev.source != c.source:
                prev.source = "spoken+read"
            continue
        merged.append(c)
    return merged


def _apply_boundaries(cues: list[Cue], words: list[dict], cfg: dict) -> list[Cue]:
    """Cut a cue at the next citation or at a long silence."""
    out: list[Cue] = []
    for i, c in enumerate(cues):
        if i + 1 < len(cues):
            # Verses of the same passage hand straight over; the fades meet
            # and no blank panel appears. Unrelated cues keep their clearance.
            nxt = cues[i + 1]
            same_passage = (c.precise and nxt.precise
                            and c.book == nxt.book and c.chapter == nxt.chapter)
            limit = nxt.start - (0.0 if same_passage else cfg["next_cue_gap"])
            if limit < c.end:
                c.end = limit
                if not same_passage:
                    c.notes.append("cut by next verse")

        # A pause only ends a card when nothing else tells us when the
        # passage finished. Where the reading itself was matched, a preacher
        # pausing mid-verse is just a preacher pausing mid-verse.
        if words and not c.precise and "read" not in c.source:
            pause = _next_speech_gap(words, c.start + 1.0, cfg["silence_out"])
            if pause is not None and c.start + cfg["min_hold"] < pause < c.end:
                c.end = pause + 0.5
                c.notes.append("cut at pause")

        if c.end - c.start < 1.2:
            continue  # squeezed out entirely by a neighbouring cue
        out.append(c)
    return out


def _attach_text(cues: list[Cue], translation: str, corpus: str) -> None:
    cache: dict[tuple, tuple[str, str]] = {}
    try:
        bible.prefetch(translation, [(c.book, c.chapter, v) for c in cues if c.verse
                                     for v in range(c.verse, (c.verse_end or c.verse) + 1)])
    except Exception:
        pass    # each verse is still looked up below, and failures reported
    for c in cues:
        if not c.verse:
            continue  # reference-only chip: nothing to look up
        key = (c.book, c.chapter, c.verse, c.verse_end)
        if key not in cache:
            try:
                cache[key] = bible.passage(
                    translation, c.book, c.chapter, c.verse, c.verse_end,
                    fallback=corpus,
                )
            except Exception as exc:  # keep the cue, flag the lookup
                cache[key] = ("", "")
                c.notes.append(f"text lookup failed: {exc}")
        text, used = cache[key]
        c.text = text
        if used and used.upper() != translation.upper():
            # Never let a substitution pass unremarked: the translation is
            # printed on screen, so the wrong one is a visible mistake.
            c.notes.append(f"{translation.upper()} unavailable - showing {used}")
            c.check = (f"The {translation.upper()} text could not be fetched, so this card "
                       f"shows the {used}. Check the internet connection and Analyze again.")
        if used:
            c.translation = used


# --------------------------------------------------------------------------
# Verses quoted again
# --------------------------------------------------------------------------
# Preachers come back to a verse without naming it: "He desired to see them
# having the same love, being in full accord and of one mind" a minute after
# reading Philippians 2:2. Nothing cites it and the quote finder files it
# under the first reading, so without this the panel would sit there empty
# (or the verse would be missing) while he quotes it word for word.
#
# Only verses already on screen somewhere in the sermon are looked for, and
# only runs of their actual wording count, so ordinary preaching that shares
# a word or two with a verse never turns into a card.

def _tokens(text: str) -> list[str]:
    return [Q.stem(t) for t in B.normalize(text).split()]


def _verse_texts(c_book, c_chapter, v, translation, corpus) -> list[list[str]]:
    seqs = []
    for tr in dict.fromkeys([translation.upper(), corpus.upper()]):
        try:
            text = bible.local_verse(tr, c_book, c_chapter, v)
        except Exception:
            text = None
        if text:
            seqs.append(_tokens(text))
    return seqs


def _find_quotes(stream, verse_seq, cfg):
    """Runs of the verse's wording in the transcript, joined where they sit
    close together in the same order. Returns (first, last, matched, content)
    as indices into `stream`."""
    min_run = cfg.get("echo_min_run", 3)
    runs = []
    n, m = len(stream), len(verse_seq)
    at: dict[str, list[int]] = {}
    for j, tok in enumerate(verse_seq):
        at.setdefault(tok, []).append(j)
    i = 0
    seen = set()
    for i in range(n):
        for j in at.get(stream[i][0], ()):
            if (i, j) in seen:
                continue
            k = 0
            while i + k < n and j + k < m and stream[i + k][0] == verse_seq[j + k]:
                seen.add((i + k, j + k))
                k += 1
            if k >= min_run:
                runs.append((i, j, k))
    runs.sort()
    segments = []
    for i, j, k in runs:
        if segments:
            s = segments[-1]
            if (0 <= i - s["i_end"] <= cfg.get("echo_join_words", 5)
                    and j >= s["j_end"] - 1 and j - s["j_end"] <= cfg.get("echo_join_verse", 8)):
                s["i_end"] = max(s["i_end"], i + k)
                s["j_end"] = max(s["j_end"], j + k)
                s["matched"] += k
                s["longest"] = max(s["longest"], k)
                s["content"] |= {stream[x][0] for x in range(i, i + k)
                                 if stream[x][0] not in Q.STOPWORDS and len(stream[x][0]) >= 3}
                continue
        segments.append({"i": i, "i_end": i + k, "j": j, "j_end": j + k, "matched": k, "longest": k,
                         "content": {stream[x][0] for x in range(i, i + k)
                                     if stream[x][0] not in Q.STOPWORDS and len(stream[x][0]) >= 3}})
    return segments


def _common_wording(content: set[str], index, cfg: dict) -> bool:
    """Whether words like these turn up together all over the Bible ("seated
    at the right hand of", "the Lord your God"): sharing them says nothing
    about which verse he means."""
    if index is None:
        return False
    rare = [s for s in content if s in index.postings]
    if not rare:
        return True
    vids = set(index.postings[rare[0]])
    for s in rare[1:]:
        vids &= set(index.postings[s])
    return len(vids) > cfg.get("echo_max_verses", 3)


def _echoes(cues: list[Cue], words: list[dict], translation: str, corpus: str,
            cfg: dict, fetch_text: bool, index=None) -> list[Cue]:
    shown = [c for c in cues if c.enabled and c.verse]
    verses = []
    for c in shown:
        for v in range(c.verse, (c.verse_end or c.verse) + 1):
            if (c.book, c.chapter, v) not in verses:
                verses.append((c.book, c.chapter, v))
    if not verses:
        return cues

    stream = []
    for w in words:
        for t in B.normalize(w.get("word") or "").split():
            stream.append((Q.stem(t), float(w.get("start", 0.0)), float(w.get("end", w.get("start", 0.0)))))

    found = []
    for book, chapter, v in verses:
        for seq in _verse_texts(book, chapter, v, translation, corpus):
            for s in _find_quotes(stream, seq, cfg):
                if (s["matched"] < cfg.get("echo_min_matched", 6)
                        or s["longest"] < cfg.get("echo_min_longest", 4)
                        or len(s["content"]) < cfg.get("echo_min_content", 3)
                        or _common_wording(s["content"], index, cfg)):
                    continue
                found.append((stream[s["i"]][1], stream[s["i_end"] - 1][2],
                              s["matched"], book, chapter, v))
    # Where two verses share the wording, the longer match wins.
    found.sort(key=lambda f: (-f[2], f[0]))
    taken: list[tuple[float, float]] = []
    added: list[Cue] = []
    for start, end, matched, book, chapter, v in found:
        if any(not (end < a - 0.5 or start > b + 0.5) for a, b in taken):
            continue
        taken.append((start, end))
        # Already on screen then (the reading itself, or a card for it).
        if any(c.enabled and not (end < c.start - 1.0 or start > c.end + 1.0)
               and (c.book, c.chapter) == (book, chapter)
               and c.verse <= v <= (c.verse_end or c.verse) for c in cues):
            continue
        # Never cut into a verse being read; a card that is only guessing at
        # its end (a verse named but not read) gives way to the quote.
        clash = [c for c in cues if c.enabled and not (end < c.start or start > c.end)]
        if any(c.precise or "read" in c.source or start < c.start + 2.0 for c in clash):
            continue
        for c in clash:
            c.end = start - cfg.get("next_cue_gap", 0.4)
            c.notes.append("gave way to a verse quoted again")
        added.append(Cue(
            ref=B.display(book, chapter, v, None), book=book, chapter=chapter,
            verse=v, verse_end=None,
            start=_snap(max(0.0, start - cfg["lead_in"]), float(cfg["fps"])),
            end=_snap(max(end + cfg["read_tail"], start + cfg["min_hold"]), float(cfg["fps"])),
            translation=translation, confidence=0.9, source="read",
            said=f"quoted again ({matched} words of it)",
            notes=["quoted again, not named"], verified=1.0,
        ))
    if not added:
        return cues
    if fetch_text:
        _attach_text(added, translation, corpus)
    out = sorted(cues + added, key=lambda c: c.start)
    # A quote running straight on from the same verse's card is that card
    # carrying on. One after a stretch of preaching is its own card: whether
    # the panel holds the verse in between is the layout's call, not ours.
    merged: list[Cue] = []
    for c in out:
        prev = next((x for x in reversed(merged) if x.enabled), None) if c.enabled else None
        if (prev and (prev.book, prev.chapter, prev.verse, prev.verse_end)
                == (c.book, c.chapter, c.verse, c.verse_end)
                and c.start - prev.end <= cfg.get("echo_join_gap", 3.0)
                and "quoted again, not named" in prev.notes + c.notes):
            prev.end = max(prev.end, c.end)
            if "quoted again" not in prev.notes:
                prev.notes.append("quoted again")
            continue
        merged.append(c)
    out = merged
    # Nothing overlaps what comes after it.
    on = [c for c in out if c.enabled]
    for a, b in zip(on, on[1:]):
        if a.end > b.start:
            a.end = max(a.start + 1.0, b.start - cfg.get("next_cue_gap", 0.4))
    return out


# --------------------------------------------------------------------------
# A bare verse number that nothing confirms
# --------------------------------------------------------------------------
BACKREF = re.compile(r"\b(that|which|we|i|you|you ve|we ve|i ve)\b( \w+){0,2} "
                     r"(read|just read|have read|read earlier|read before|looked at)\b")


def _resolve_unread(cues: list[Cue], words: list[dict], cfg: dict,
                    translation: str, corpus: str, fetch_text: bool) -> None:
    """Decide where a bare "verse 2" really points when nothing of it is read.

    The chapter named last is only a guess. Two better ones:

    * "that verse 2 that I read" points back to a verse 2 he did read;
    * a chapter that was named in passing and never read is a poor home for
      it when the sermon's own passage has a verse 2 - he has gone back to
      his text. That one is flagged for a look.
    """
    read = [c for c in cues if c.enabled and c.verse and "read" in c.source]
    weight: dict[tuple[str, int], float] = {}
    for c in read:
        weight[(c.book, c.chapter)] = weight.get((c.book, c.chapter), 0.0) + c.duration
    counts: dict[tuple[str, int], int] = {}
    for c in read:
        counts[(c.book, c.chapter)] = counts.get((c.book, c.chapter), 0) + 1
    main = max((k for k in weight if counts[k] >= 3), key=lambda k: weight[k], default=None)

    changed = []
    for c in cues:
        if (not c.enabled or not c.verse or CARRIED_NOTE not in c.notes
                or "cited, not read aloud" not in c.notes):
            continue
        after = " ".join(B.normalize(w.get("word") or "") for w in words
                         if c.start <= float(w.get("start", 0.0)) <= c.start + 6.0)
        target, why, flag = None, "", False
        if BACKREF.search(after):
            earlier = [r for r in read if r.start < c.start and r.chapter != c.chapter
                       and r.verse <= c.verse <= (r.verse_end or r.verse)
                       and (r.book == c.book or (r.book, r.chapter) == main)]
            if earlier:
                target = (earlier[-1].book, earlier[-1].chapter)
                why = "the verse he read earlier"
        if target is None and main and main != (c.book, c.chapter) \
                and (c.book, c.chapter) not in weight \
                and c.verse <= bible_verse_count_local(main[0], main[1]):
            target, flag = main, True
            why = "the passage being preached"
        if target is None:
            continue
        old = c.ref
        c.book, c.chapter = target
        c.ref = B.display(c.book, c.chapter, c.verse, c.verse_end)
        c.notes.append(f"shown as {c.ref}, {why} (heard as {old})")
        if flag:
            c.check = (f"He said \u201c{c.said}\u201d. The last chapter named was {old.rsplit(':', 1)[0]}, "
                       f"but nothing from it was read, so this shows {c.ref} from the passage "
                       "being preached. Check it.")
        changed.append(c)
    if changed and fetch_text:
        _attach_text(changed, translation, corpus)


def bible_verse_count_local(book: str, chapter: int) -> int:
    try:
        return bible.verse_count("BSB", book, chapter) or 200
    except Exception:
        return 200


# --------------------------------------------------------------------------
# Card edges on the words themselves
# --------------------------------------------------------------------------
def _snap_to_wording(cues: list[Cue], words: list[dict], cfg: dict) -> None:
    """Put each card's edges where the verse's own wording starts and stops.

    The alignment places verses by their distinctive words, which is robust
    but coarse at the edges: a verse that opens with ordinary words ("But
    what fruit were you getting…") is placed a few seconds late, and one that
    ends in them ("…that the LORD had left him") is cut off before its last
    words. Runs of the actual text, in any translation on this machine, pin
    both edges to the word.
    """
    lead, tail = cfg["lead_in"], cfg["read_tail"]
    gap = cfg.get("next_cue_gap", 0.4)
    for i, c in enumerate(cues):
        if not c.enabled or not c.verse or not (c.precise or "read" in c.source):
            continue
        prev = cues[i - 1] if i else None
        nxt = cues[i + 1] if i + 1 < len(cues) else None
        # A verse that follows another in the same reading may have begun long
        # before the alignment put it: he reads its opening words, preaches on
        # them, then reads the verse out in full. Look back over the card
        # before it, so the switch lands on the first time he says it.
        carried = bool(prev and prev.enabled and prev.book == c.book
                       and prev.chapter == c.chapter and prev.precise
                       and c.verse == (prev.verse_end or prev.verse) + 1
                       and abs(prev.end - c.start) < 1.0)
        back = (c.start - prev.start - 1.5) if carried else 8.0
        stream = _stream(words, c.start - max(8.0, back), c.end + 15.0)
        if not stream:
            continue
        first_v, last_v = c.verse, c.verse_end or c.verse
        segs = []
        for v in range(first_v, last_v + 1):
            for text in bible.local_texts(c.book, c.chapter, v):
                seq = _tokens(text)
                for seg in _find_quotes(stream, seq, {**cfg, "echo_min_run": 3}):
                    if seg["matched"] >= 3:
                        segs.append((stream[seg["i"]][1], stream[seg["i_end"] - 1][2],
                                     v, seg["j"], seg["j_end"], seg["matched"]))
        if not segs:
            continue
        segs.sort()

        # Start: only for a card that begins where a verse begins inside a
        # reading. A card that opens on his announcing it keeps that moment.
        announced = c.said and not c.said.startswith("read verse") and "range split" in " ".join(c.notes)
        if not announced and "quoted again, not named" not in c.notes:
            # The run nearest the verse's first word, and the longest of
            # those: "…of his separation" closing verse 7 is not verse 8's
            # "All the days of his separation".
            earliest = c.start - (max(6.0, back) if carried else 6.0)
            opening = [sg for sg in segs if sg[2] == first_v and sg[3] <= 3
                       and (sg[5] >= 4 or sg[3] == 0)
                       and earliest <= sg[0] <= c.start + 6.0]
            if carried:
                # Within the reading, the first solid run of this verse's
                # opening words is where he moved on to it, however much he
                # goes over them afterwards.
                solid = [sg for sg in opening if sg[3] == 0 and sg[5] >= 4
                         and sg[0] > prev.start + 1.5]
                opening = [min(solid, key=lambda sg: sg[0])] if solid else opening
            if opening:
                opening.sort(key=lambda sg: (sg[3], -sg[5], sg[0]))
                # Never so late that the card has no time left to be read.
                t = min(opening[0][0] - lead, c.end - cfg.get("min_verse_hold", 2.5))
                low = (prev.start + 1.0) if prev else 0.0
                if abs(t - c.start) > 0.4 and t > low:
                    moved = c.start
                    c.start = max(low, t)
                    # The card before hands over at the same moment.
                    if prev and abs(prev.end - moved) < 0.6:
                        prev.end = c.start
                    elif prev and prev.end > c.start:
                        prev.end = max(prev.start + 1.0, c.start - gap)

        # End: the last run of wording that carries on from the reading. A
        # short pause is part of reading; a longer one (he stops to comment)
        # still is, if he then picks the verse up where he left off rather
        # than going back over words already read.
        end_word, reached = None, (0, 0)
        for a, b, v, j, j_end, _n in segs:
            if a < c.start - 1.0:
                continue
            since = a - (end_word if end_word is not None else c.start)
            onward = (v, j) >= (reached[0], reached[1] - 2) and (v, j_end) > reached
            if since > 4.0 and not (since <= 15.0 and onward):
                if since > 15.0:
                    break
                continue
            end_word = b if end_word is None else max(end_word, b)
            reached = max(reached, (v, j_end))
        if end_word is None:
            continue
        limit = (nxt.start - (0.0 if (nxt.precise and nxt.book == c.book
                                      and nxt.chapter == c.chapter) else gap)) if nxt else end_word + tail
        want = min(end_word + tail, limit)
        if want > c.end:
            c.end = want
            c.notes.append("end set by the last words read")
