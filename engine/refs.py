"""Find scripture citations in a timestamped transcript.

The unit of work is a *mention*: a span of spoken words that names a passage,
with the timestamps of the first and last word. Three shapes matter:

    full         "Judges chapter sixteen verse twenty"   -> book+ch+vs
    chapter-only "turn with me to Judges sixteen"        -> sets context
    verse-only   "now look at verse twenty one"          -> uses context

The third is why this file holds state. Preachers announce a passage once and
then walk through it by verse number alone, so a detector without carry-over
catches the opening citation and misses the next twenty minutes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

import books as B
import spoken_numbers as N

CHAPTER_WORDS = {"chapter", "chapters", "chp", "ch"}
VERSE_WORDS = {"verse", "verses", "vs", "vss", "v", "verser"}
RANGE_WORDS = {"to", "through", "thru", "till", "until", "dash", "and", "unto"}
# Words that may pad a range: "all the way down to verse 8", "right up to 12".
RANGE_PADDING = {"all", "the", "way", "down", "up", "right", "on", "straight"}
# "the numbers 6 and 7", "my job 2 years ago", "his acts 3 times": these book
# names are everyday words, and after a determiner they are being used as such.
COMMON_WORD_BOOKS = {"numbers", "num", "judges", "judge", "kings", "king", "acts",
                     "act", "job", "mark", "proverbs", "proverb", "lamentation",
                     "lamentations", "revelation", "revelations", "james", "john",
                     "ruth", "daniel", "joel", "amos", "titus", "peter", "timothy",
                     "jude", "philip"}
DETERMINERS = {"the", "my", "his", "her", "your", "our", "their", "these", "those",
               "a", "an", "some", "any", "many", "few", "its", "this", "that",
               "no", "every", "each", "whose"}
# Books a transcript confuses with each other. Only used when the chapter
# spoken is impossible for the book heard ("1 Corinthians chapter 29" can only
# be 1 Chronicles), never to second-guess a chapter that exists.
CONFUSABLE = {
    "1 Corinthians": ["1 Chronicles"], "2 Corinthians": ["2 Chronicles"],
    "1 Chronicles": ["1 Corinthians"], "2 Chronicles": ["2 Corinthians"],
    "Colossians": ["1 Corinthians"], "Philemon": ["Philippians"],
    "Jonah": ["John"], "Joel": ["John"], "Ezra": ["Ezekiel"], "Hosea": ["Joshua"],
    "Proverbs": ["Psalms"],
}
NEXT_WORDS = {"next", "following", "subsequent"}
PREV_WORDS = {"previous", "preceding", "prior"}
RANGE_DASH = re.compile(r"(\d)\s*[-\u2013\u2014]\s*(\d)")
TRAILING_DASH = re.compile(r"\d\s*[-\u2013\u2014]\s*$")
ORDINAL_NUMERAL = re.compile(r"^\d+(st|nd|rd|th)$")
# Tolerated between a book name and its chapter number.
BOOK_FILLER = {"the", "book", "of", "chapter", "chapters", "ch", "chp"}
# Tolerated between a chapter number and its verse number.
VERSE_FILLER = {"verse", "verses", "vs", "v", "and", "from", "colon", "the", "at"}
# Lead-ins that make a bare "verse N" a real citation.
VERSE_LEADIN = {
    "verse", "verses", "look", "see", "read", "says", "said", "in", "at",
    "from", "to", "now", "next", "back", "down", "up", "beginning", "starting",
}

TRANSLATIONS = {
    "esv": "ESV", "english standard": "ESV", "english standard version": "ESV",
    "niv": "NIV", "new international": "NIV", "new international version": "NIV",
    "nlt": "NLT", "new living": "NLT", "new living translation": "NLT",
    "kjv": "KJV", "king james": "KJV", "king james version": "KJV",
    "nkjv": "NKJV", "new king james": "NKJV",
    "nasb": "NASB", "new american standard": "NASB",
    "msg": "MSG", "the message": "MSG",
    "amp": "AMP", "amplified": "AMP", "amplified bible": "AMP",
    "csb": "CSB", "christian standard": "CSB",
    "asv": "ASV", "american standard": "ASV",
    "web": "WEB", "world english": "WEB",
}
_TRANSLATION_PHRASES = sorted(TRANSLATIONS, key=lambda s: -len(s.split()))
MAX_TRANSLATION_WORDS = max(len(s.split()) for s in _TRANSLATION_PHRASES)


@dataclass
class Token:
    text: str
    start: float
    end: float
    word_index: int
    prob: float = 1.0


@dataclass
class Mention:
    book: str
    chapter: int | None
    verse: int | None
    verse_end: int | None
    kind: str                 # full | chapter-only | book-only | verse-only
    start: float
    end: float
    confidence: float
    said: str                 # the raw words that produced this mention
    translation: str | None = None
    token_span: tuple[int, int] = (0, 0)
    context_carried: bool = False
    # How clearly the words behind this citation were heard, per Whisper.
    heard: float = 1.0
    # Said as "the next verse" / "the verse before that": worked out from the
    # last verse named, so it only stands if the verse is then read.
    relative: bool = False
    # A book name that is also an everyday word ("Numbers", "Job", "Acts"),
    # said with no "chapter" or "verse" to make it unmistakably a citation.
    loose: bool = False

    @property
    def ref(self) -> str:
        return B.display(self.book, self.chapter or 1, self.verse, self.verse_end)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["token_span"] = list(self.token_span)
        d["ref"] = self.ref
        return d


def tokenize(words: list[dict]) -> list[Token]:
    """Flatten transcript words into normalized tokens.

    A single word can yield several tokens, which is exactly what we want for
    "16:20" -> ["16", "20"] and "1Cor" -> ["1", "cor"].
    """
    tokens: list[Token] = []
    for wi, w in enumerate(words):
        raw = w.get("word") or w.get("text") or ""
        # "3:5-6" is a range; punctuation-stripping would leave "3 5 6".
        raw = RANGE_DASH.sub(r"\1 to \2", raw)
        if TRAILING_DASH.search(raw):
            raw = raw.rstrip("-\u2013\u2014 ") + " to"
        for piece in B.normalize(raw).split():
            # "1st", "2nd", "3rd" stay whole: they are ordinals ("2nd
            # Corinthians"), and split up they read as "1 st".
            if ORDINAL_NUMERAL.match(piece):
                parts = [piece]
            # Split a glued alphanumeric like "john3" into parts.
            elif piece.isalnum() and not piece.isdigit() and not piece.isalpha():
                buf, mode = "", None
                parts = []
                for ch in piece:
                    m = "d" if ch.isdigit() else "a"
                    if mode and m != mode:
                        parts.append(buf)
                        buf = ""
                    buf, mode = buf + ch, m
                if buf:
                    parts.append(buf)
            else:
                parts = [piece]
            for part in parts:
                tokens.append(Token(
                    part,
                    float(w.get("start", 0.0)),
                    float(w.get("end", 0.0)),
                    wi,
                    float(w.get("prob", w.get("probability", 1.0)) or 1.0),
                ))
    return tokens


def _skip_filler(tokens: list[Token], i: int, allowed: set[str], limit: int) -> int:
    """Advance past up to `limit` filler tokens, returning the new index."""
    skipped = 0
    while i < len(tokens) and skipped < limit and tokens[i].text in allowed:
        i += 1
        skipped += 1
    return i


def _match_translation(tokens: list[Token], i: int) -> tuple[str | None, int]:
    for phrase in _TRANSLATION_PHRASES:
        parts = phrase.split()
        if i + len(parts) <= len(tokens):
            if all(tokens[i + k].text == parts[k] for k in range(len(parts))):
                return TRANSLATIONS[phrase], i + len(parts)
    return None, i


def _match_book(tokens: list[Token], i: int, fuzzy_cutoff: float) -> tuple[str, float, int, bool] | None:
    """Try to read a book name at tokens[i].

    Returns (canonical_book, confidence, next_index, was_ambiguous_stem).
    """
    ordinal = N.parse_ordinal(tokens[i].text)
    start = i + 1 if ordinal is not None else i
    if start >= len(tokens):
        return None

    # A leading digit is only an ordinal if a numbered-book stem follows.
    for span in range(min(B.MAX_STEM_WORDS, len(tokens) - start), 0, -1):
        stem = " ".join(t.text for t in tokens[start:start + span])
        exact = stem in B.STEM_ALIASES
        conf = 1.0
        if not exact:
            if span > 1:
                continue
            guess, ratio = B.fuzzy_stem(stem, fuzzy_cutoff)
            if guess is None:
                continue
            stem, conf = guess, ratio
        if ordinal is not None and stem not in B.NUMBERED_STEMS:
            # "two Samuel" is a book, "two kings were" is not; but an ordinal
            # in front of an unnumbered book means we misread the ordinal.
            if tokens[i].text.isdigit():
                continue
            ordinal = None
        book = B.resolve_stem(stem, ordinal)
        if book is None:
            continue
        if ordinal is not None and stem in B.NUMBERED_STEMS:
            conf = min(1.0, conf + 0.05)
        return book, conf, start + span, stem in B.AMBIGUOUS_STEMS
    return None


def _read_verse_range(tokens: list[Token], i: int) -> tuple[int | None, int | None, int]:
    """Read one verse or a run of them starting at tokens[i].

    '20', '20 to 22', '4-7', '5 and verse 6', '1, 2 and 3', 'from 4 all the
    way down to verse 8'. Every step must follow on within a few seconds, so
    a number said later in an unrelated sentence is never swallowed.
    """
    verse, j = N.read_number(tokens, i)
    if verse is None:
        return None, None, i
    last, k = verse, j
    while k < len(tokens):
        # "1, 2 and 3": the next verse number straight after.
        n, after = N.read_number(tokens, k)
        if n is not None:
            if n == last + 1 and tokens[k].start - tokens[k - 1].end <= 1.2:
                last, k = n, after
                continue
            break
        # "to", "through", "all the way down to", optionally then "verse".
        x, core, first = k, None, k
        while x < len(tokens) and x - first < 5 and (tokens[x].text in RANGE_WORDS
                                                      or tokens[x].text in RANGE_PADDING):
            if tokens[x].text in RANGE_WORDS:
                core = tokens[x].text
            x += 1
        if core is None:
            break
        if x < len(tokens) and tokens[x].text in VERSE_WORDS:
            x += 1
        n, after = N.read_number(tokens, x)
        if (n is None or n <= last or x >= len(tokens)
                or tokens[x].start - tokens[k - 1].end > 3.0):
            break
        # "verses 5 and 20" names two verses; it is not 5 through 20.
        if core == "and" and n - last > 2:
            break
        if n - last > 60:
            break
        last, k = n, after
    return verse, (last if last > verse else None), k


def _book_after_of(tokens: list[Token], i: int, fuzzy_cutoff: float):
    """'of John 3', 'of the book of Romans': a book named after 'of'.

    Returns (book, chapter_or_None, next_index) or None.
    """
    if i >= len(tokens) or tokens[i].text != "of":
        return None
    x = _skip_filler(tokens, i + 1, {"the", "book", "of", "gospel", "letter", "epistle"}, 4)
    if x >= len(tokens) or tokens[x].start - tokens[i].end > 2.0:
        return None
    hit = _match_book(tokens, x, fuzzy_cutoff)
    if hit is None:
        return None
    book, _conf, j, _amb = hit
    k = _skip_filler(tokens, j, {"chapter", "chapters", "ch"}, 1)
    chapter, after = N.read_number(tokens, k)
    if chapter is not None and 1 <= chapter <= B.max_chapter(book):
        return book, chapter, after
    return book, (1 if B.max_chapter(book) == 1 else None), j


class Detector:
    """Stateful scan over the token stream."""

    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.fuzzy_cutoff = cfg.get("fuzzy_cutoff", 0.82)
        self.book_number_gap = cfg.get("book_number_gap", 3)
        # How far past a book name an explicit "chapter" may sit. Preachers
        # say "James tells us in chapter one verse fourteen" constantly.
        self.book_lookahead = cfg.get("book_lookahead", 7)
        self.book_lookahead_seconds = cfg.get("book_lookahead_seconds", 3.5)
        self.verse_number_gap = cfg.get("verse_number_gap", 3)
        # Seconds after which a carried book/chapter context goes stale.
        self.context_ttl = cfg.get("context_ttl", 420.0)
        self.min_confidence = cfg.get("min_confidence", 0.6)
        self.ctx_book: str | None = None
        self.ctx_chapter: int | None = None
        self.ctx_time: float = -1e9
        self.ctx_translation: str | None = None
        # Last chapter used in each book. "Hosea ... Jeremiah ... Hosea, verse
        # 5" means back to the Hosea chapter being read, not a blank.
        self.book_chapter: dict[str, int] = {}
        # The last verse named, for "the next verse" and "the verse before".
        self.last_verse: tuple[str, int, int, bool, float] | None = None

    # -- context -----------------------------------------------------------
    def _context_alive(self, now: float) -> bool:
        return self.ctx_book is not None and (now - self.ctx_time) <= self.context_ttl

    def _set_context(self, book: str | None, chapter: int | None, now: float) -> None:
        if book:
            if book != self.ctx_book:
                self.ctx_chapter = self.book_chapter.get(book)
            self.ctx_book = book
        if chapter:
            self.ctx_chapter = chapter
            if self.ctx_book:
                self.book_chapter[self.ctx_book] = chapter
        self.ctx_time = now

    # -- main loop ---------------------------------------------------------
    def run(self, tokens: list[Token]) -> list[Mention]:
        mentions: list[Mention] = []
        i = 0
        n = len(tokens)
        while i < n:
            tr, tr_next = _match_translation(tokens, i)
            if tr:
                self.ctx_translation = tr
                if mentions and tokens[i].start - mentions[-1].end < 4.0:
                    mentions[-1].translation = tr
                i = tr_next
                continue

            m = self._try_reverse(tokens, i)
            if m is None:
                m = self._try_book_reference(tokens, i)
            if m is None:
                m = self._try_relative(tokens, i)
            if m is None:
                m = self._try_bare_verse(tokens, i)
            if m is not None:
                if m.confidence >= self.min_confidence:
                    mentions.append(m)
                if m.verse and m.chapter:
                    self.last_verse = (m.book, m.chapter, m.verse_end or m.verse,
                                       bool(m.verse_end), m.end)
                i = m.token_span[1]
                continue
            i += 1
        return mentions

    # -- "verse 16 of John 3", "chapter 3 of John, verse 16" ---------------
    def _try_reverse(self, tokens: list[Token], i: int) -> Mention | None:
        tok = tokens[i]
        if tok.text in VERSE_WORDS:
            verse, verse_end, after = _read_verse_range(tokens, i + 1)
            if verse is None:
                return None
            hit = _book_after_of(tokens, after, self.fuzzy_cutoff)
            if hit is None:
                return None
            book, chapter, end = hit
            if chapter is None:
                if self._context_alive(tok.start) and self.ctx_book == book and self.ctx_chapter:
                    chapter = self.ctx_chapter
                else:
                    return None
            self._set_context(book, chapter, tok.start)
            return Mention(
                book=book, chapter=chapter, verse=verse, verse_end=verse_end,
                kind="full", start=tok.start, end=tokens[end - 1].end, confidence=0.86,
                said=self._say(tokens, i, end), translation=self.ctx_translation,
                token_span=(i, end), heard=self._heard(tokens, i, end),
            )

        # "chapter 3 of John", "the third chapter of John"
        if tok.text in CHAPTER_WORDS:
            chapter, after = N.read_number(tokens, i + 1)
            begin = i
        else:
            ordinal = N.parse_ordinal(tok.text) if not tok.text.isdigit() else None
            if ordinal is None or i + 1 >= len(tokens) or tokens[i + 1].text not in CHAPTER_WORDS:
                return None
            chapter, after, begin = ordinal, i + 2, i
        if chapter is None:
            return None
        hit = _book_after_of(tokens, after, self.fuzzy_cutoff)
        if hit is None:
            return None
        book, named_chapter, end = hit
        if chapter < 1 or chapter > B.max_chapter(book):
            return None
        v_start = _skip_filler(tokens, end, VERSE_FILLER | {"s", "gospel"}, self.verse_number_gap)
        had_verse_word = any(tokens[x].text in VERSE_WORDS
                             for x in range(end, min(v_start + 1, len(tokens))))
        verse, verse_end, after_verse = (_read_verse_range(tokens, v_start)
                                         if had_verse_word else (None, None, end))
        self._set_context(book, chapter, tok.start)
        if verse is not None:
            return Mention(
                book=book, chapter=chapter, verse=verse, verse_end=verse_end,
                kind="full", start=tok.start, end=tokens[after_verse - 1].end,
                confidence=0.86, said=self._say(tokens, begin, after_verse),
                translation=self.ctx_translation, token_span=(begin, after_verse),
                heard=self._heard(tokens, begin, after_verse),
            )
        return Mention(
            book=book, chapter=chapter, verse=None, verse_end=None, kind="chapter-only",
            start=tok.start, end=tokens[end - 1].end, confidence=0.8,
            said=self._say(tokens, begin, end), translation=self.ctx_translation,
            token_span=(begin, end), heard=self._heard(tokens, begin, end),
        )

    # -- "the next verse", "the verse before that", "the next chapter" ------
    def _try_relative(self, tokens: list[Token], i: int) -> Mention | None:
        tok = tokens[i]
        nxt = tokens[i + 1].text if i + 1 < len(tokens) else ""
        after2 = tokens[i + 2].text if i + 2 < len(tokens) else ""
        step, span, unit = 0, 0, None
        if tok.text in NEXT_WORDS and (nxt in VERSE_WORDS or nxt in CHAPTER_WORDS):
            step, span, unit = 1, 2, nxt
        elif tok.text in PREV_WORDS and (nxt in VERSE_WORDS or nxt in CHAPTER_WORDS):
            step, span, unit = -1, 2, nxt
        elif tok.text in VERSE_WORDS and nxt in ("after", "before") and after2 in ("that", "this", "it"):
            step, span, unit = (1 if nxt == "after" else -1), 3, tok.text
        if not step or not self._context_alive(tok.start):
            return None

        if unit in CHAPTER_WORDS:
            if not self.ctx_chapter:
                return None
            chapter = self.ctx_chapter + step
            if chapter < 1 or chapter > B.max_chapter(self.ctx_book):
                return None
            self._set_context(self.ctx_book, chapter, tok.start)
            return Mention(
                book=self.ctx_book, chapter=chapter, verse=None, verse_end=None,
                kind="chapter-only", start=tok.start, end=tokens[i + span - 1].end,
                confidence=0.72, said=self._say(tokens, i, i + span),
                translation=self.ctx_translation, token_span=(i, i + span),
                context_carried=True, relative=True,
            )

        # Only from a single verse named recently: after "verses 1 and 2",
        # "next verse" while reading them means verse 2, not 3.
        if not self.last_verse:
            return None
        book, chapter, verse, was_range, when = self.last_verse
        if was_range or tok.start - when > 120 or book != self.ctx_book:
            return None
        target = verse + step
        if target < 1:
            return None
        return Mention(
            book=book, chapter=chapter, verse=target, verse_end=None,
            kind="verse-only", start=tok.start, end=tokens[i + span - 1].end,
            confidence=0.74, said=self._say(tokens, i, i + span),
            translation=self.ctx_translation, token_span=(i, i + span),
            context_carried=True, relative=True,
        )

    # -- patterns ----------------------------------------------------------
    def _try_book_reference(self, tokens: list[Token], i: int) -> Mention | None:
        hit = _match_book(tokens, i, self.fuzzy_cutoff)
        if hit is None:
            return None
        book, conf, j, ambiguous = hit
        book_end_token = j
        # "the numbers 6 and 7", "my job 2 years ago": an everyday word after
        # a determiner is not a book - unless it is "the book of Numbers".
        everyday = j == i + 1 and tokens[i].text in COMMON_WORD_BOOKS
        if everyday and i > 0 and tokens[i - 1].text in DETERMINERS:
            if not (i >= 2 and tokens[i - 1].text == "of" or
                    i >= 2 and tokens[i - 2].text == "book"):
                return None

        # Look for the chapter number, tolerating "chapter"/"the book of".
        k = _skip_filler(tokens, j, BOOK_FILLER, self.book_number_gap)
        had_chapter_word = any(
            tokens[x].text in CHAPTER_WORDS for x in range(j, min(k + 1, len(tokens)))
        )
        chapter, after_chapter = N.read_number(tokens, k)

        if chapter is None:
            # Second chance: the chapter may arrive a few words later, but
            # only when the word "chapter" makes the intent explicit.
            c = self._find_chapter_word(tokens, j)
            if c is not None:
                chapter, after_chapter = self._number_after_stumble(tokens, c + 1)
                if chapter is not None:
                    had_chapter_word = True
                    k = c

        # Obadiah, Philemon, 2-3 John and Jude have one chapter, so whatever
        # number follows is the verse: "Jude verse three" and "Jude three"
        # both mean 1:3. There is no chapter to hear.
        if B.max_chapter(book) == 1:
            v_only = _skip_filler(tokens, j, VERSE_FILLER | CHAPTER_WORDS, self.verse_number_gap)
            only_verse, after_only = N.read_number(tokens, v_only)
            if only_verse == 1:
                # "Jude 1:3", "Obadiah 1 verse 3": the 1 is the chapter.
                v2 = _skip_filler(tokens, after_only, VERSE_FILLER, self.verse_number_gap)
                said_verse = any(tokens[x].text in VERSE_WORDS
                                 for x in range(after_only, min(v2 + 1, len(tokens))))
                maybe, _ = N.read_number(tokens, v2)
                if maybe is not None and v2 < len(tokens) and (
                        said_verse or tokens[v2].start - tokens[after_only - 1].end <= 0.9):
                    v_only = v2
            if only_verse is not None:
                only_verse, verse_end, k2 = _read_verse_range(tokens, v_only)
                self._set_context(book, 1, tokens[i].start)
                return Mention(
                    book=book, chapter=1, verse=only_verse, verse_end=verse_end,
                    kind="full", start=tokens[i].start, end=tokens[k2 - 1].end,
                    confidence=min(1.0, conf), said=self._say(tokens, i, k2),
                    translation=self.ctx_translation, token_span=(i, k2),
                    heard=self._heard(tokens, *(i, k2)),
                )

        if chapter is not None and chapter > B.max_chapter(book):
            # A chapter this book does not have: the book was misheard.
            for alt in CONFUSABLE.get(book, []):
                if chapter <= B.max_chapter(alt):
                    book, conf = alt, conf * 0.85
                    break
            else:
                # Nothing it could be. Do not let a later bare "verse 10"
                # land in whatever chapter of this book came up an hour ago.
                self.ctx_book, self.ctx_chapter, self.ctx_time = book, None, tokens[i].start
                return None

        if chapter is None or chapter < 1 or chapter > B.max_chapter(book):
            # Bare book name. Only useful as context, and only when the name
            # is unmistakable ("Habakkuk" yes, "Mark" no).
            if ambiguous or conf < 0.95:
                return None
            self._set_context(book, None, tokens[i].start)
            return Mention(
                book=book, chapter=None, verse=None, verse_end=None,
                kind="book-only", start=tokens[i].start, end=tokens[book_end_token - 1].end,
                confidence=conf * 0.7, said=self._say(tokens, i, book_end_token),
                translation=self.ctx_translation, token_span=(i, book_end_token),
                heard=self._heard(tokens, *(i, book_end_token)),
            )

        if had_chapter_word:
            conf = min(1.0, conf + 0.05)

        # Now the verse, if one was spoken.
        v_start = _skip_filler(tokens, after_chapter, VERSE_FILLER, self.verse_number_gap)
        had_verse_word = any(
            tokens[x].text in VERSE_WORDS for x in range(after_chapter, min(v_start + 1, len(tokens)))
        )
        verse, verse_end, after_verse = _read_verse_range(tokens, v_start)

        if verse is not None and not had_verse_word:
            # "Judges sixteen twenty" is a citation; "Judges sixteen. Twenty
            # years later..." is not. A pause between the two numbers is the
            # tell, so require them to be spoken close together.
            gap = tokens[v_start].start - tokens[after_chapter - 1].end
            if gap > 0.9:
                verse, verse_end, after_verse = None, None, after_chapter
            else:
                conf = max(0.0, conf - 0.05)

        if verse is not None:
            if had_verse_word:
                conf = min(1.0, conf + 0.05)
            self._set_context(book, chapter, tokens[i].start)
            return Mention(
                book=book, chapter=chapter, verse=verse, verse_end=verse_end,
                kind="full", start=tokens[i].start, end=tokens[after_verse - 1].end,
                confidence=conf, said=self._say(tokens, i, after_verse),
                translation=self.ctx_translation, token_span=(i, after_verse),
                heard=self._heard(tokens, *(i, after_verse)),
                loose=everyday and not had_verse_word and not had_chapter_word,
            )

        self._set_context(book, chapter, tokens[i].start)
        return Mention(
            book=book, chapter=chapter, verse=None, verse_end=None,
            kind="chapter-only", start=tokens[i].start, end=tokens[after_chapter - 1].end,
            confidence=conf * 0.9, said=self._say(tokens, i, after_chapter),
            translation=self.ctx_translation, token_span=(i, after_chapter),
        )

    def _number_after_stumble(self, tokens: list[Token], i: int) -> tuple[int | None, int]:
        """The number after "chapter", allowing a short stumble first.

        Preachers correct themselves mid-citation — "Numbers chapter, I was,
        6" — and a strict parser gives up on the stray words. Up to three
        words may sit between "chapter" and the number, provided it is still
        spoken within a couple of seconds and no other book intervenes.
        """
        number, after = N.read_number(tokens, i)
        if number is not None or i >= len(tokens):
            return number, after
        start = tokens[i - 1].end if i > 0 else tokens[i].start
        for k in range(i + 1, min(len(tokens), i + 4)):
            if tokens[k].start - start > 2.5:
                break
            if _match_book(tokens, k, self.fuzzy_cutoff) is not None:
                break
            number, after = N.read_number(tokens, k)
            if number is not None:
                return number, after
        return None, i

    def _find_chapter_word(self, tokens: list[Token], j: int) -> int | None:
        """Index of an explicit 'chapter' shortly after a book name."""
        limit = min(len(tokens), j + self.book_lookahead)
        for x in range(j, limit):
            if tokens[x].text in CHAPTER_WORDS:
                if tokens[x].start - tokens[j - 1].end > self.book_lookahead_seconds:
                    return None
                return x
            # Another book name in between means this chapter belongs to it.
            if _match_book(tokens, x, self.fuzzy_cutoff) is not None:
                return None
        return None

    def _try_bare_verse(self, tokens: list[Token], i: int) -> Mention | None:
        """'verse twenty one' / 'verses 4 through 7', resolved from context."""
        tok = tokens[i]
        if tok.text not in VERSE_WORDS and tok.text not in CHAPTER_WORDS:
            return None
        if not self._context_alive(tok.start):
            return None

        if tok.text in CHAPTER_WORDS:
            chapter, after = N.read_number(tokens, i + 1)
            if chapter is None or chapter > B.max_chapter(self.ctx_book):
                return None
            book = self.ctx_book
            v_start = _skip_filler(tokens, after, VERSE_FILLER, self.verse_number_gap)
            had_verse_word = any(
                tokens[x].text in VERSE_WORDS for x in range(after, min(v_start + 1, len(tokens)))
            )
            verse, verse_end, after_verse = _read_verse_range(tokens, v_start)
            if verse is None or not had_verse_word:
                self._set_context(book, chapter, tok.start)
                return Mention(
                    book=book, chapter=chapter, verse=None, verse_end=None,
                    kind="chapter-only", start=tok.start, end=tokens[after - 1].end,
                    confidence=0.7, said=self._say(tokens, i, after),
                    translation=self.ctx_translation, token_span=(i, after),
                    heard=self._heard(tokens, *(i, after)),
                    context_carried=True,
                )
            self._set_context(book, chapter, tok.start)
            return Mention(
                book=book, chapter=chapter, verse=verse, verse_end=verse_end,
                kind="full", start=tok.start, end=tokens[after_verse - 1].end,
                confidence=0.78, said=self._say(tokens, i, after_verse),
                translation=self.ctx_translation, token_span=(i, after_verse),
                context_carried=True,
            )

        if self.ctx_chapter is None:
            return None
        verse, verse_end, after = _read_verse_range(tokens, i + 1)
        if verse is None:
            return None
        # Guard against "verse" used loosely; require a plausible lead-in or
        # that the previous token is sentence-like filler.
        prev = tokens[i - 1].text if i > 0 else ""
        conf = 0.82 if (prev in VERSE_LEADIN or i == 0) else 0.74
        self._set_context(self.ctx_book, self.ctx_chapter, tok.start)
        return Mention(
            book=self.ctx_book, chapter=self.ctx_chapter, verse=verse, verse_end=verse_end,
            kind="verse-only", start=tok.start, end=tokens[after - 1].end,
            confidence=conf, said=self._say(tokens, i, after),
            translation=self.ctx_translation, token_span=(i, after),
            context_carried=True,
        )

    @staticmethod
    def _say(tokens: list[Token], a: int, b: int) -> str:
        return " ".join(t.text for t in tokens[a:b])

    @staticmethod
    def _heard(tokens: list[Token], a: int, b: int) -> float:
        span = tokens[a:b]
        return round(sum(t.prob for t in span) / len(span), 3) if span else 1.0


def detect(words: list[dict], cfg: dict | None = None) -> list[Mention]:
    return Detector(cfg).run(tokenize(words))
