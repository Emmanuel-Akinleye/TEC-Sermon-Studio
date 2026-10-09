"""Canonical Bible book table plus the aliases a speech transcript actually produces.

Whisper writes what it hears, so "2 Corinthians" arrives as any of
"2 corinthians", "second corinthians", "two corinthians", "ii corinthians",
and occasionally misspelled. Matching is therefore alias-first, fuzzy-second.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# name, abbreviation used on screen, chapter count, ordinal prefix (or None)
BOOKS: list[tuple[str, str, int, int | None]] = [
    ("Genesis", "Gen", 50, None), ("Exodus", "Ex", 40, None),
    ("Leviticus", "Lev", 27, None), ("Numbers", "Num", 36, None),
    ("Deuteronomy", "Deut", 34, None), ("Joshua", "Josh", 24, None),
    ("Judges", "Judg", 21, None), ("Ruth", "Ruth", 4, None),
    ("1 Samuel", "1 Sam", 31, 1), ("2 Samuel", "2 Sam", 24, 2),
    ("1 Kings", "1 Kgs", 22, 1), ("2 Kings", "2 Kgs", 25, 2),
    ("1 Chronicles", "1 Chr", 29, 1), ("2 Chronicles", "2 Chr", 36, 2),
    ("Ezra", "Ezra", 10, None), ("Nehemiah", "Neh", 13, None),
    ("Esther", "Esth", 10, None), ("Job", "Job", 42, None),
    ("Psalms", "Ps", 150, None), ("Proverbs", "Prov", 31, None),
    ("Ecclesiastes", "Eccl", 12, None), ("Song of Solomon", "Song", 8, None),
    ("Isaiah", "Isa", 66, None), ("Jeremiah", "Jer", 52, None),
    ("Lamentations", "Lam", 5, None), ("Ezekiel", "Ezek", 48, None),
    ("Daniel", "Dan", 12, None), ("Hosea", "Hos", 14, None),
    ("Joel", "Joel", 3, None), ("Amos", "Amos", 9, None),
    ("Obadiah", "Obad", 1, None), ("Jonah", "Jonah", 4, None),
    ("Micah", "Mic", 7, None), ("Nahum", "Nah", 3, None),
    ("Habakkuk", "Hab", 3, None), ("Zephaniah", "Zeph", 3, None),
    ("Haggai", "Hag", 2, None), ("Zechariah", "Zech", 14, None),
    ("Malachi", "Mal", 4, None), ("Matthew", "Matt", 28, None),
    ("Mark", "Mark", 16, None), ("Luke", "Luke", 24, None),
    ("John", "John", 21, None), ("Acts", "Acts", 28, None),
    ("Romans", "Rom", 16, None), ("1 Corinthians", "1 Cor", 16, 1),
    ("2 Corinthians", "2 Cor", 13, 2), ("Galatians", "Gal", 6, None),
    ("Ephesians", "Eph", 6, None), ("Philippians", "Phil", 4, None),
    ("Colossians", "Col", 4, None), ("1 Thessalonians", "1 Thess", 5, 1),
    ("2 Thessalonians", "2 Thess", 3, 2), ("1 Timothy", "1 Tim", 6, 1),
    ("2 Timothy", "2 Tim", 4, 2), ("Titus", "Titus", 3, None),
    ("Philemon", "Phlm", 1, None), ("Hebrews", "Heb", 13, None),
    ("James", "Jas", 5, None), ("1 Peter", "1 Pet", 5, 1),
    ("2 Peter", "2 Pet", 3, 2), ("1 John", "1 Jn", 5, 1),
    ("2 John", "2 Jn", 1, 2), ("3 John", "3 Jn", 1, 3),
    ("Jude", "Jude", 1, None), ("Revelation", "Rev", 22, None),
]

CANONICAL = [b[0] for b in BOOKS]
ABBREV = {b[0]: b[1] for b in BOOKS}
CHAPTER_COUNT = {b[0]: b[2] for b in BOOKS}
ORDINAL = {b[0]: b[3] for b in BOOKS}

# Extra spellings keyed to the canonical name. The stem (the part after any
# ordinal) is what we actually match; ordinals are stripped before lookup.
STEM_ALIASES: dict[str, str] = {
    "genesis": "Genesis", "gen": "Genesis",
    "exodus": "Exodus", "exo": "Exodus", "ex": "Exodus",
    "leviticus": "Leviticus", "lev": "Leviticus",
    "numbers": "Numbers", "num": "Numbers",
    "deuteronomy": "Deuteronomy", "deut": "Deuteronomy", "deuter": "Deuteronomy",
    "joshua": "Joshua", "josh": "Joshua",
    "judges": "Judges", "judge": "Judges", "judg": "Judges",
    "ruth": "Ruth",
    "samuel": "1 Samuel", "sam": "1 Samuel", "samuels": "1 Samuel",
    "kings": "1 Kings", "king": "1 Kings", "kgs": "1 Kings",
    "chronicles": "1 Chronicles", "chronicle": "1 Chronicles", "chron": "1 Chronicles",
    "ezra": "Ezra",
    "nehemiah": "Nehemiah", "neh": "Nehemiah",
    "esther": "Esther", "esth": "Esther",
    "job": "Job",
    "psalms": "Psalms", "psalm": "Psalms", "psalmes": "Psalms", "ps": "Psalms",
    "proverbs": "Proverbs", "proverb": "Proverbs", "prov": "Proverbs",
    "ecclesiastes": "Ecclesiastes", "eccl": "Ecclesiastes", "ecclesiastic": "Ecclesiastes",
    "song of solomon": "Song of Solomon", "song of songs": "Song of Solomon",
    "songs of solomon": "Song of Solomon", "canticles": "Song of Solomon",
    "isaiah": "Isaiah", "isiah": "Isaiah", "esaias": "Isaiah", "isa": "Isaiah",
    "jeremiah": "Jeremiah", "jer": "Jeremiah",
    "lamentations": "Lamentations", "lamentation": "Lamentations", "lam": "Lamentations",
    "ezekiel": "Ezekiel", "ezek": "Ezekiel", "ezekial": "Ezekiel",
    "daniel": "Daniel", "dan": "Daniel",
    "hosea": "Hosea", "hoshea": "Hosea",
    "joel": "Joel", "amos": "Amos",
    "obadiah": "Obadiah", "obadias": "Obadiah",
    "jonah": "Jonah", "jonas": "Jonah",
    "micah": "Micah", "mic": "Micah",
    "nahum": "Nahum", "naham": "Nahum",
    "habakkuk": "Habakkuk", "habbakuk": "Habakkuk", "habakuk": "Habakkuk",
    "zephaniah": "Zephaniah", "zeph": "Zephaniah",
    "haggai": "Haggai", "hag": "Haggai",
    "zechariah": "Zechariah", "zachariah": "Zechariah", "zech": "Zechariah",
    "malachi": "Malachi", "malachy": "Malachi", "mal": "Malachi",
    "matthew": "Matthew", "mathew": "Matthew", "matt": "Matthew", "mat": "Matthew",
    "mark": "Mark", "marks": "Mark",
    "luke": "Luke", "lk": "Luke",
    "john": "John", "jn": "John",
    "acts": "Acts", "act": "Acts", "acts of the apostles": "Acts",
    "romans": "Romans", "roman": "Romans", "rom": "Romans",
    "corinthians": "1 Corinthians", "corinthian": "1 Corinthians", "cor": "1 Corinthians",
    "galatians": "Galatians", "galatian": "Galatians", "gal": "Galatians",
    "ephesians": "Ephesians", "ephesian": "Ephesians", "eph": "Ephesians",
    "philippians": "Philippians", "phillipians": "Philippians", "philippian": "Philippians",
    "colossians": "Colossians", "colossian": "Colossians", "col": "Colossians",
    "thessalonians": "1 Thessalonians", "thessalonian": "1 Thessalonians",
    "thess": "1 Thessalonians", "thessalonia": "1 Thessalonians",
    "timothy": "1 Timothy", "timothys": "1 Timothy", "tim": "1 Timothy",
    "titus": "Titus",
    "philemon": "Philemon", "philemons": "Philemon",
    "hebrews": "Hebrews", "hebrew": "Hebrews", "heb": "Hebrews",
    "james": "James", "jas": "James",
    "peter": "1 Peter", "peters": "1 Peter", "pet": "1 Peter",
    "jude": "Jude",
    "revelation": "Revelation", "revelations": "Revelation", "rev": "Revelation",
    "apocalypse": "Revelation",
}

# Books whose stem changes meaning with an ordinal ("Peter" alone is a person).
NUMBERED_STEMS = {
    "samuel": ("1 Samuel", "2 Samuel"),
    "sam": ("1 Samuel", "2 Samuel"),
    "kings": ("1 Kings", "2 Kings"),
    "king": ("1 Kings", "2 Kings"),
    "kgs": ("1 Kings", "2 Kings"),
    "chronicles": ("1 Chronicles", "2 Chronicles"),
    "chronicle": ("1 Chronicles", "2 Chronicles"),
    "chron": ("1 Chronicles", "2 Chronicles"),
    "corinthians": ("1 Corinthians", "2 Corinthians"),
    "corinthian": ("1 Corinthians", "2 Corinthians"),
    "cor": ("1 Corinthians", "2 Corinthians"),
    "thessalonians": ("1 Thessalonians", "2 Thessalonians"),
    "thessalonian": ("1 Thessalonians", "2 Thessalonians"),
    "thess": ("1 Thessalonians", "2 Thessalonians"),
    "timothy": ("1 Timothy", "2 Timothy"),
    "tim": ("1 Timothy", "2 Timothy"),
    "peter": ("1 Peter", "2 Peter"),
    "pet": ("1 Peter", "2 Peter"),
    "john": ("1 John", "2 John", "3 John"),
    "jn": ("1 John", "2 John", "3 John"),
}

# Stems that are ordinary English words or common personal names. A bare
# mention of these is not a citation unless a number follows closely.
AMBIGUOUS_STEMS = {
    "john", "mark", "james", "acts", "act", "judge", "judges", "numbers",
    "num", "job", "kings", "king", "ruth", "jude", "song of solomon",
    "peter", "timothy", "titus", "philip", "daniel", "joel", "amos",
    "revelation", "revelations", "proverb", "proverbs", "lamentation",
    "ex", "mat", "mal", "dan", "gal", "col", "rev", "sam", "jas", "ps",
}

# Longest alias first so "song of solomon" wins over "song".
_ALIAS_BY_LENGTH = sorted(STEM_ALIASES, key=lambda s: -len(s.split()))
MAX_STEM_WORDS = max(len(s.split()) for s in STEM_ALIASES)


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def resolve_stem(stem: str, ordinal: int | None) -> str | None:
    """Map a normalized stem plus an optional ordinal to a canonical book."""
    base = STEM_ALIASES.get(stem)
    if base is None:
        return None
    if stem in NUMBERED_STEMS:
        options = NUMBERED_STEMS[stem]
        if ordinal is None:
            # An unnumbered "Corinthians" is ambiguous; default to the first
            # book, which is by far the more preached of the pair.
            return options[0] if stem not in ("john", "jn") else "John"
        idx = ordinal - 1
        return options[idx] if 0 <= idx < len(options) else options[0]
    return base


# Everyday words that sit a letter or two from a book abbreviation. "from"
# is one edit from "rom" (Romans) and turns up in every sermon; matching it
# once is enough to move the whole sermon's context into Romans.
NOT_BOOKS = {
    "from", "form", "roman", "romance", "room", "roam", "genre", "gene",
    "general", "gentle", "jobs", "joke", "mark", "marks", "market", "acts",
    "action", "judge", "numbers", "number", "kings", "king", "lamb", "hose",
    "hoses", "hosed", "amos", "jude", "ruth", "james", "john", "peter",
    "timothy", "titus", "daniel", "joel", "luke", "look", "looks", "matter",
    "mattered", "psalm", "palm", "palms", "exodus", "exit", "list",
    "lists", "levity", "deuce", "revel", "revelry", "revival", "hebrew",
    "hebrews", "colossal", "column", "galaxy", "gallant", "galley",
}


def fuzzy_stem(stem: str, cutoff: float = 0.84) -> tuple[str | None, float]:
    """Nearest book name for a word Whisper or Parakeet spelled wrong.

    Only a rescue for mangled *full* book names ("habbakuk", "ephesian").
    Abbreviations are never fuzzy targets and everyday words are never
    candidates, because a false match here silently relabels every
    verse-only citation that follows.
    """
    if len(stem) < 5 or stem in NOT_BOOKS or " " in stem:
        return None, 0.0
    best, score = None, 0.0
    for alias in _ALIAS_BY_LENGTH:
        if len(alias) < 5 or " " in alias:
            continue
        if abs(len(alias) - len(stem)) > 3:
            continue
        ratio = SequenceMatcher(None, stem, alias).ratio()
        if ratio > score:
            best, score = alias, ratio
    return (best, score) if score >= cutoff else (None, score)


def max_chapter(book: str) -> int:
    return CHAPTER_COUNT.get(book, 150)


def display(book: str, chapter: int, verse: int | None, verse_end: int | None = None) -> str:
    """'Judges 16:20' / 'Judges 16:20-22' / 'Psalms 23'."""
    if verse is None:
        return f"{book} {chapter}"
    if verse_end and verse_end != verse:
        return f"{book} {chapter}:{verse}-{verse_end}"
    return f"{book} {chapter}:{verse}"
