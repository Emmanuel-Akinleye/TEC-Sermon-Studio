"""Match spoken words against the text of every verse.

Two jobs:

* Catch passages that get read aloud without being announced.
* Tell `timing` how long the pastor actually stays in a verse, which is what
  decides when the graphic leaves the screen. A card that disappears on a
  fixed timer looks robotic; one that holds while he is still reading the
  verse and leaves when he moves on looks like a human operator ran it.

Matching is deliberately translation-tolerant. The corpus is one local
translation but the pastor may be reading another, so scoring rewards rare
shared words ("Philistines", "awoke", "Nazirite") and ignores the connective
tissue that differs between translations.
"""
from __future__ import annotations

import math
import pickle
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import bible
import books as B

ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = ROOT / "data"

STOPWORDS = {
    "a", "about", "after", "again", "against", "all", "also", "am", "an",
    "and", "any", "are", "as", "at", "be", "because", "been", "before",
    "being", "but", "by", "came", "can", "come", "could", "did", "do",
    "does", "down", "each", "even", "for", "from", "get", "give", "go",
    "had", "has", "have", "he", "her", "here", "him", "his", "how", "i",
    "if", "in", "into", "is", "it", "its", "just", "let", "like", "made",
    "make", "man", "many", "may", "me", "men", "more", "most", "my", "no",
    "not", "now", "of", "on", "one", "only", "or", "other", "our", "out",
    "over", "own", "said", "say", "says", "see", "shall", "she", "should",
    "so", "some", "such", "than", "that", "the", "their", "them", "then",
    "there", "these", "they", "thing", "things", "this", "those", "thou",
    "thee", "thy", "through", "to", "unto", "up", "upon", "us", "was",
    "we", "were", "what", "when", "where", "which", "while", "who", "whom",
    "will", "with", "would", "ye", "yet", "you", "your", "god", "lord",
    "jesus", "christ", "people", "know", "knew", "went", "came", "let",
}

WORD_RE = re.compile(r"[a-z]+")


def stem(word: str) -> str:
    """Crude suffix stripping so 'awoke'/'awake' and 'shaking'/'shake' meet."""
    for suffix in ("ings", "ing", "edly", "ly", "ed", "es", "s"):
        # "-ly" is safe to strip from shorter words ("dimly" -> "dim");
        # the others are not ("used" must not become "us").
        floor = 2 if suffix == "ly" else 3
        if len(word) > len(suffix) + floor and word.endswith(suffix):
            base = word[: -len(suffix)]
            if base.endswith(base[-1] * 2) and len(base) > 4:
                base = base[:-1]
            return base
    return word


def content_words(text: str) -> list[str]:
    out = []
    for w in WORD_RE.findall(B.normalize(text)):
        if w in STOPWORDS or len(w) < 3:
            continue
        out.append(stem(w))
    return out


@dataclass
class VerseSpan:
    """One verse of a range, and the stretch of speech that reads it."""
    verse: int
    verse_end: int          # equal to `verse` unless short verses were merged
    start: float
    end: float
    matched: int
    score: float
    total: int = 0          # distinctive words the verse has in all

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def coverage(self) -> float:
        return self.matched / self.total if self.total else 0.0


@dataclass
class QuoteHit:
    book: str
    chapter: int
    verse: int
    start: float
    end: float
    score: float
    matched: int
    total: int
    order: float = 1.0      # how closely the spoken order follows the verse
    density: float = 1.0    # matched words per content word of speech

    @property
    def ref(self) -> str:
        return f"{self.book} {self.chapter}:{self.verse}"


class Index:
    """Inverted index over rare verse words."""

    def __init__(self, translation: str):
        self.translation = translation.upper()
        self.verses: list[tuple[str, int, int]] = []
        self.verse_words: list[frozenset[str]] = []
        self.verse_seq: list[tuple[str, ...]] = []
        self.postings: dict[str, list[int]] = {}
        self.idf: dict[str, float] = {}
        self._by_ref: dict[tuple[str, int, int], int] | None = None

    def vid_of(self, book: str, chapter: int, verse: int) -> int | None:
        if self._by_ref is None:
            self._by_ref = {ref: i for i, ref in enumerate(self.verses)}
        return self._by_ref.get((book, chapter, verse))

    # -- build / cache -----------------------------------------------------
    @property
    def cache_path(self) -> Path:
        return INDEX_DIR / f"quoteindex_v4_{self.translation}.pkl"

    def build(self, max_df_ratio: float = 0.03) -> "Index":
        df: dict[str, int] = defaultdict(int)
        rows = list(bible.iter_corpus(self.translation))
        if not rows:
            raise bible.BibleError(f"No local corpus for {self.translation}")

        word_sets, sequences = [], []
        for book, chapter, verse, text in rows:
            ordered, seen = [], set()
            for w in content_words(text):
                if w not in seen:
                    seen.add(w)
                    ordered.append(w)
            words = frozenset(ordered)
            self.verses.append((book, chapter, verse))
            word_sets.append(words)
            sequences.append(tuple(ordered))
            for w in words:
                df[w] += 1

        total = len(rows)
        max_df = max(40, int(total * max_df_ratio))
        postings: dict[str, list[int]] = defaultdict(list)
        for vid, words in enumerate(word_sets):
            kept = frozenset(w for w in words if df[w] <= max_df)
            self.verse_words.append(kept)
            self.verse_seq.append(tuple(w for w in sequences[vid] if w in kept))
            for w in kept:
                postings[w].append(vid)

        self.postings = dict(postings)
        self.idf = {w: math.log(total / df[w]) for w in self.postings}
        return self

    def save(self) -> None:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("wb") as fh:
            pickle.dump(
                {"verses": self.verses, "verse_words": self.verse_words,
                 "verse_seq": self.verse_seq, "postings": self.postings,
                 "idf": self.idf}, fh, protocol=5)

    @classmethod
    def load(cls, translation: str) -> "Index":
        idx = cls(translation)
        if idx.cache_path.exists():
            try:
                with idx.cache_path.open("rb") as fh:
                    blob = pickle.load(fh)
                idx.verses = blob["verses"]
                idx.verse_words = blob["verse_words"]
                idx.verse_seq = blob["verse_seq"]
                idx.postings = blob["postings"]
                idx.idf = blob["idf"]
                return idx
            except Exception:
                pass
        idx.build()
        idx.save()
        return idx

    # -- search ------------------------------------------------------------
    def find(self, words: list[dict], cfg: dict | None = None) -> list[QuoteHit]:
        """Locate verses being read aloud in a timestamped word list."""
        cfg = cfg or {}
        min_words = cfg.get("min_matched_words", 3)
        min_coverage = cfg.get("min_coverage", 0.42)
        cluster_gap = cfg.get("cluster_gap_words", 22)
        max_postings = cfg.get("max_postings", 600)
        min_score = cfg.get("min_score", 12.0)

        # Flatten to content words, remembering the source word index.
        seq: list[tuple[str, int]] = []
        for wi, w in enumerate(words):
            raw = w.get("word") or w.get("text") or ""
            for token in WORD_RE.findall(B.normalize(raw)):
                if token in STOPWORDS or len(token) < 3:
                    continue
                seq.append((stem(token), wi))

        hits: dict[int, list[tuple[int, str]]] = defaultdict(list)
        for pos, (token, _wi) in enumerate(seq):
            posting = self.postings.get(token)
            if not posting or len(posting) > max_postings:
                continue
            for vid in posting:
                hits[vid].append((pos, token))

        results: list[QuoteHit] = []
        for vid, entries in hits.items():
            if len(entries) < min_words:
                continue
            verse_vocab = self.verse_words[vid]
            if not verse_vocab:
                continue
            entries.sort()
            cluster: list[tuple[int, str]] = []
            for entry in entries + [(10**9, "")]:
                if cluster and entry[0] - cluster[-1][0] > cluster_gap:
                    hit = self._score(vid, cluster, seq, words, verse_vocab,
                                      min_words, min_coverage, min_score)
                    if hit:
                        results.append(hit)
                    cluster = []
                if entry[1]:
                    cluster.append(entry)
            if cluster:
                hit = self._score(vid, cluster, seq, words, verse_vocab,
                                  min_words, min_coverage, min_score)
                if hit:
                    results.append(hit)

        results.sort(key=lambda h: (h.start, -h.score))
        return self._dedupe(results)

    def _score(self, vid, cluster, seq, words, verse_vocab,
               min_words, min_coverage, min_score) -> QuoteHit | None:
        matched = {tok for _, tok in cluster}
        if len(matched) < min_words:
            return None
        coverage = len(matched) / len(verse_vocab)
        if coverage < min_coverage:
            return None
        score = sum(self.idf.get(tok, 0.0) for tok in matched)
        if score < min_score:
            return None

        # Someone reading a verse says its words in the verse's own order and
        # close together. Someone who merely happens to reuse a few of them
        # does neither, which is what separates a reading from a coincidence.
        order = self._order_ratio(vid, cluster)
        span = cluster[-1][0] - cluster[0][0] + 1
        density = len(matched) / max(1, span)

        first_wi = seq[cluster[0][0]][1]
        last_wi = seq[cluster[-1][0]][1]
        book, chapter, verse = self.verses[vid]
        return QuoteHit(
            book=book, chapter=chapter, verse=verse,
            start=float(words[first_wi].get("start", 0.0)),
            end=float(words[last_wi].get("end", 0.0)),
            score=score, matched=len(matched), total=len(verse_vocab),
            order=round(order, 3), density=round(density, 3),
        )

    def _order_ratio(self, vid: int, cluster) -> float:
        """Longest run of matched words that keeps the verse's own order."""
        positions = {w: i for i, w in enumerate(self.verse_seq[vid])}
        seen, spoken = set(), []
        for _, tok in cluster:
            if tok in positions and tok not in seen:
                seen.add(tok)
                spoken.append(positions[tok])
        if len(spoken) < 2:
            return 1.0
        best = [1] * len(spoken)
        for i in range(1, len(spoken)):
            for j in range(i):
                if spoken[j] < spoken[i] and best[j] + 1 > best[i]:
                    best[i] = best[j] + 1
        return max(best) / len(spoken)

    @staticmethod
    def _dedupe(results: list[QuoteHit]) -> list[QuoteHit]:
        """Adjacent verses of one passage overlap heavily; keep the strongest
        reading per time window, but allow genuinely sequential verses."""
        kept: list[QuoteHit] = []
        for hit in results:
            clash = None
            for other in kept:
                overlap = min(hit.end, other.end) - max(hit.start, other.start)
                span = max(0.1, min(hit.end - hit.start, other.end - other.start))
                if overlap > 0.75 * span and (hit.book, hit.chapter) != (other.book, other.chapter):
                    clash = other
                    break
            if clash is None:
                kept.append(hit)
            elif hit.score > clash.score:
                kept[kept.index(clash)] = hit
        return kept


# A word that only one translation uses ("grumbling" where another says
# "complaining") carries no weight from the index it is missing from, but it
# is still the word he said. This is what it counts for.
OTHER_TRANSLATION_IDF = 2.5

_VOCAB: dict[tuple, frozenset[str]] = {}


def vocab_for(index: "Index", book: str, chapter: int, verse: int) -> frozenset[str]:
    """The distinctive words of a verse in every translation on this machine.

    The corpus is one translation; the preacher reads another. Philippians
    2:14 is "Do all things without grumbling or disputing" in the ESV and "Do
    everything without complaining or arguing" in the BSB - not one word in
    common - so a verse matched against the corpus alone can vanish out of the
    middle of a reading."""
    key = (index.translation, book, chapter, verse)
    if key in _VOCAB:
        return _VOCAB[key]
    vid = index.vid_of(book, chapter, verse)
    words = set(index.verse_words[vid]) if vid is not None else set()
    for text in bible.local_texts(book, chapter, verse):
        for word in content_words(text):
            # Keep what the corpus itself considers distinctive, and words it
            # does not have at all; skip the ones it dropped for being common.
            if word in index.postings or (word not in index.idf and len(word) >= 5):
                words.add(word)
    out = frozenset(words)
    _VOCAB[key] = out
    return out


def align_range(index: "Index", words: list[dict], book: str, chapter: int,
                first: int, last: int, t_from: float, t_to: float,
                cfg: dict | None = None) -> list[VerseSpan]:
    """Work out when each verse of a passage is read.

    A preacher who announces "Hosea chapter two, verses one to thirteen" then
    reads straight through. One card holding all thirteen verses is unreadable;
    what is wanted is thirteen cards, each on screen while that verse is being
    read.

    Knowing the verses *and their order* makes this a monotonic alignment
    rather than a search: every word of the reading belongs to exactly one
    verse, and the verses arrive in sequence. A dynamic program finds the
    boundaries that best explain the transcript, which tolerates the preacher
    breaking off to comment and does not need every verse to match strongly.
    """
    cfg = cfg or {}
    min_hold = cfg.get("min_verse_hold", 3.0)
    min_support = cfg.get("min_range_support", 0.10)

    verse_ids = []
    for v in range(first, last + 1):
        vid = index.vid_of(book, chapter, v)
        if vid is None:
            return []
        verse_ids.append((v, vid))
    if not verse_ids:
        return []

    # Transcript content words inside the window, remembering their source word.
    seq: list[tuple[str, int]] = []
    for wi, w in enumerate(words):
        start = float(w.get("start", 0.0))
        if start < t_from:
            continue
        if start > t_to:
            break
        for token in WORD_RE.findall(B.normalize(w.get("word") or w.get("text") or "")):
            if token in STOPWORDS or len(token) < 3:
                continue
            seq.append((stem(token), wi))

    n_pos, n_verses = len(seq), len(verse_ids)
    if n_pos < n_verses * 2:
        return []

    vocabs = [vocab_for(index, book, chapter, v) for v, _ in verse_ids]
    mismatch = cfg.get("align_mismatch", 0.35)

    # States: 0 = before the reading, 1..n = the verses in order, n+1 = after
    # the reading. Every spoken word belongs to exactly one state, and the
    # states only move forward.
    #
    # Words inside a verse that are not that verse's cost a little; words
    # before or after the reading cost nothing. That single asymmetry is what
    # lets the reading end: without an "after" state the last verse — and
    # through it every verse — is stretched across the preaching that
    # follows, wherever its words happen to recur.
    n_states = n_verses + 2
    post = n_verses + 1

    # A reading says each word of a verse once; preaching about it keeps
    # returning to the same few ("food and coverings… if you have food…
    # coverings covers…"). So a word heard again shortly after counts for
    # progressively less, which stops a verse being stretched over the
    # sermon that discusses it.
    lookback = cfg.get("align_repeat_window", 80)
    decay = cfg.get("align_repeat_decay", 0.5)
    weight = [1.0] * n_pos
    recent: dict[str, list[int]] = {}
    for p_, (tok, _) in enumerate(seq):
        seen = [q for q in recent.get(tok, []) if p_ - q <= lookback]
        weight[p_] = decay ** len(seen)
        seen.append(p_)
        recent[tok] = seen

    def emit(p: int, k: int) -> float:
        if k == 0 or k == post:
            return 0.0
        token = seq[p][0]
        if token in vocabs[k - 1]:
            return index.idf.get(token, OTHER_TRANSLATION_IDF) * weight[p]
        return -mismatch

    # Preachers skip verses ("…root of all kinds of evil" straight after
    # verse 8) and stop before the end of what they announced. Both are
    # allowed at a price: skipping a verse costs about one distinctive word,
    # so a verse is only placed where its own words actually are.
    skip = cfg.get("align_skip_cost", 4.0)
    NEG = float("-inf")
    from array import array

    dp = [NEG] * n_states
    dp[0] = 0.0
    preds: list[array] = []
    for p in range(n_pos):
        new = [NEG] * n_states
        pred = array("H", [65535]) * n_states
        # Best way into "after the reading" from any verse, paying for the
        # verses left unread.
        best_exit, best_from = NEG, 65535
        for j in range(1, n_verses + 1):
            if dp[j] == NEG:
                continue
            v = dp[j] - skip * (n_verses - j)
            if v > best_exit:
                best_exit, best_from = v, j
        for k in range(n_states):
            cands = [(dp[k], k)]
            if 1 <= k <= n_verses:
                cands.append((dp[k - 1], k - 1))
                if k >= 2:
                    cands.append((dp[k - 2] - skip, k - 2))
                    cands.append((dp[0] - skip * (k - 1), 0))
            elif k == post:
                cands.append((best_exit, best_from))
            value, origin = max(cands)
            if value == NEG:
                continue
            new[k] = value + emit(p, k)
            pred[k] = origin
        dp = new
        preds.append(pred)

    finals = [(dp[post], post)] + [(dp[j] - skip * (n_verses - j), j)
                                    for j in range(1, n_verses + 1)]
    value, k = max(finals)
    if value == NEG:
        return []

    state_at = [0] * n_pos
    for p in range(n_pos - 1, -1, -1):
        state_at[p] = k
        k = preds[p][k]

    placed = {}
    for p, st in enumerate(state_at):
        if 1 <= st <= n_verses:
            placed.setdefault(st - 1, [p, p])[1] = p
    if not placed:
        return []

    spans: list[VerseSpan] = []
    for i, (verse, vid) in enumerate(verse_ids):
        if i not in placed:
            continue            # skipped, or never reached
        lo, hi = placed[i]

        # Anchor to words that actually belong to this verse. The boundary
        # itself often lands on a filler word the preacher used while turning
        # the page, and starting there puts the card up before he reads it.
        hits = [p for p in range(lo, hi + 1) if seq[p][0] in vocabs[i]]
        if not hits:
            continue            # squeezed in to keep the order; never read

        # One of the verse's words turning up again minutes later would
        # otherwise stretch the card to cover the gap, so drop hits that sit
        # far away in time from the body of the reading.
        if len(hits) > 1:
            def at(p):
                return float(words[seq[p][1]].get("start", 0.0))
            max_gap = cfg.get("max_hit_gap", 8.0)
            # The last verse of a passage has nothing after it to hold it in
            # place, so its words recurring in the sermon that follows would
            # keep it on screen. End it at the first real break instead.
            is_last = i == max(placed)
            tail_gap = cfg.get("final_hit_gap", 3.5) if is_last else max_gap
            for k in range(1, len(hits)):
                if at(hits[k]) - at(hits[k - 1]) > tail_gap and is_last:
                    hits = hits[:k]
                    break
            while len(hits) > 1 and at(hits[-1]) - at(hits[-2]) > max_gap:
                hits.pop()
            while len(hits) > 1 and at(hits[1]) - at(hits[0]) > max_gap:
                hits.pop(0)

        first = hits[0] if hits else lo
        last = hits[-1] if hits else hi

        matched = {seq[p][0] for p in hits}
        spans.append(VerseSpan(
            verse=verse, verse_end=verse,
            start=float(words[seq[first][1]].get("start", 0.0)),
            end=float(words[seq[last][1]].get("end", 0.0)),
            matched=len(matched),
            score=sum(index.idf.get(t, OTHER_TRANSLATION_IDF) for t in matched),
            total=len(vocabs[i]),
        ))

    # If almost nothing matched, this was not a reading at all.
    # Nothing placed at all (every verse skipped) means nothing was read.
    if not spans:
        return []
    supported = sum(1 for s in spans if s.matched >= 2)
    if supported / len(spans) < min_support:
        return []

    return _merge_short(spans, min_hold)


def reading_end(index: "Index", words: list[dict], book: str, chapter: int,
                verse: int, start: float, end: float, gap: float,
                resume: float = 12.0) -> float:
    """When the reading of one verse actually stopped.

    A pause in the verse's words ends the reading — unless the preacher then
    picks the verse back up. Resuming brings words of the verse not yet said
    ("…bear a son. Listen to what the angel said. He said, no razor…");
    preaching about it only repeats words already heard. So after a pause,
    carry on only if at least two new words of the verse follow soon.
    """
    if index.vid_of(book, chapter, verse) is None:
        return end
    vocab = vocab_for(index, book, chapter, verse)

    hits: list[tuple[float, str]] = []
    for w in words:
        t = float(w.get("start", 0.0))
        if t < start:
            continue
        if t > end:
            break
        for token in WORD_RE.findall(B.normalize(w.get("word") or "")):
            if token in STOPWORDS or len(token) < 3:
                continue
            s_ = stem(token)
            if s_ in vocab:
                hits.append((t, s_))
    if not hits:
        return end

    heard = {hits[0][1]}
    last = hits[0][0]
    i = 1
    while i < len(hits):
        t, word = hits[i]
        if t - last > gap:
            ahead = {w for tt, w in hits[i:] if tt - t <= resume and w not in heard}
            if len(ahead) < 2:
                break
        heard.add(word)
        last = t
        i += 1
    return min(end, last + 0.6)


def _merge_short(spans: list[VerseSpan], min_hold: float) -> list[VerseSpan]:
    """Verses read in a rush share a card rather than flashing individually.

    Measured by how long each card would actually be on screen — from its
    first word to the next verse's first word — since a short verse read
    at a steady pace is on screen far longer than its own words take.
    """
    def shown(i, group):
        nxt = spans[i + 1].start if i + 1 < len(spans) else group.end
        return nxt - group.start

    merged: list[VerseSpan] = []
    last_index: list[int] = []
    for i, span in enumerate(spans):
        if merged and shown(last_index[-1], merged[-1]) < min_hold:
            prev = merged[-1]
            prev.verse_end = span.verse_end
            prev.end = span.end
            prev.matched += span.matched
            prev.score += span.score
            last_index[-1] = i
        else:
            merged.append(VerseSpan(span.verse, span.verse_end, span.start, span.end,
                                    span.matched, span.score, span.total))
            last_index.append(i)
    return merged


def load_index(translation: str) -> Index:
    return Index.load(translation)
