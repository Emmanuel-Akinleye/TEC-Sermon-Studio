"""Verse text: one local SQLite cache in front of several translation sources.

Two different needs are served here.

1. Display text for a detected reference. Whatever translation the church
   preaches from, fetched live from that publisher's API the first time and
   cached forever after.
2. A complete local corpus for the quote matcher, which has to compare every
   sentence the pastor speaks against all 31,102 verses. No API can serve
   that, so a public-domain translation is downloaded once and kept offline.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import ssl
import urllib.parse
import time
import urllib.request
from pathlib import Path

import books as B

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "bible.db"
KEYS_PATH = ROOT / "config" / "keys.json"

SCROLLMAPPER = "https://raw.githubusercontent.com/scrollmapper/bible_databases/master/formats/json/{}.json"

# Public-domain / freely redistributable translations available offline.
OFFLINE_SOURCES = {
    "KJV": "KJV", "ASV": "ASV", "BSB": "BSB", "YLT": "YLT",
    "BBE": "BBE", "LEB": "LEB", "MKJV": "MKJV", "DARBY": "Darby",
    "NHEB": "NHEB", "WEB": "NHEB", "AKJV": "AKJV", "WEBSTER": "Webster",
}

# Translations reachable over the network, given a key.
ONLINE_PROVIDERS = {
    "ESV": "esv",
    "NLT": "nlt",
    "NIV": "apibible", "NKJV": "apibible", "NASB": "apibible",
    "CSB": "apibible", "AMP": "apibible", "MSG": "apibible",
}

# Default api.bible ids; override in config/keys.json -> "bible_ids".
APIBIBLE_IDS = {
    "ASV": "06125adad2d5898a-01",
    "KJV": "de4e12af7f28f599-02",
    "WEB": "9879dbb7cfe39e4d-04",
}

USFM = {
    "Genesis": "GEN", "Exodus": "EXO", "Leviticus": "LEV", "Numbers": "NUM",
    "Deuteronomy": "DEU", "Joshua": "JOS", "Judges": "JDG", "Ruth": "RUT",
    "1 Samuel": "1SA", "2 Samuel": "2SA", "1 Kings": "1KI", "2 Kings": "2KI",
    "1 Chronicles": "1CH", "2 Chronicles": "2CH", "Ezra": "EZR",
    "Nehemiah": "NEH", "Esther": "EST", "Job": "JOB", "Psalms": "PSA",
    "Proverbs": "PRO", "Ecclesiastes": "ECC", "Song of Solomon": "SNG",
    "Isaiah": "ISA", "Jeremiah": "JER", "Lamentations": "LAM",
    "Ezekiel": "EZK", "Daniel": "DAN", "Hosea": "HOS", "Joel": "JOL",
    "Amos": "AMO", "Obadiah": "OBA", "Jonah": "JON", "Micah": "MIC",
    "Nahum": "NAM", "Habakkuk": "HAB", "Zephaniah": "ZEP", "Haggai": "HAG",
    "Zechariah": "ZEC", "Malachi": "MAL", "Matthew": "MAT", "Mark": "MRK",
    "Luke": "LUK", "John": "JHN", "Acts": "ACT", "Romans": "ROM",
    "1 Corinthians": "1CO", "2 Corinthians": "2CO", "Galatians": "GAL",
    "Ephesians": "EPH", "Philippians": "PHP", "Colossians": "COL",
    "1 Thessalonians": "1TH", "2 Thessalonians": "2TH", "1 Timothy": "1TI",
    "2 Timothy": "2TI", "Titus": "TIT", "Philemon": "PHM", "Hebrews": "HEB",
    "James": "JAS", "1 Peter": "1PE", "2 Peter": "2PE", "1 John": "1JN",
    "2 John": "2JN", "3 John": "3JN", "Jude": "JUD", "Revelation": "REV",
}

_ROMAN_PREFIX = re.compile(r"^(i{1,3})\s+", re.IGNORECASE)


def ssl_context() -> ssl.SSLContext:
    """Verified TLS even on python.org macOS builds, which ship no CA bundle."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class BibleError(RuntimeError):
    pass


def load_keys() -> dict:
    if KEYS_PATH.exists():
        try:
            return json.loads(KEYS_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


# The shipped keys.example.json is copied into place on install, so an
# untouched placeholder must read as "no key" rather than being sent to the
# publisher and coming back as a 401 mid-analysis.
_PLACEHOLDER = ("paste ", "your-", "api.bible id", "here")


def _key(name: str, *env: str) -> str | None:
    """A key from the environment or config/keys.json. Several environment
    names are accepted so keys set before the rename still work."""
    value = next((os.environ[e] for e in env if os.environ.get(e)), None) \
        or load_keys().get(name) or None
    if not value:
        return None
    value = str(value).strip()
    # Publishers show the key as a ready-made header ("Authorization: Token
    # abc123"). Paste either the whole line or just the token; both work.
    value = re.sub(r"^\s*authorization\s*:\s*", "", value, flags=re.I)
    value = re.sub(r"^\s*(token|bearer)\s+", "", value, flags=re.I)
    value = value.strip().strip("\"'")
    low = value.lower()
    if any(mark in low for mark in _PLACEHOLDER) or len(low) < 8:
        return None
    return value


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS verses (
            translation TEXT NOT NULL,
            book        TEXT NOT NULL,
            chapter     INTEGER NOT NULL,
            verse       INTEGER NOT NULL,
            text        TEXT NOT NULL,
            PRIMARY KEY (translation, book, chapter, verse)
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS corpora (
            translation TEXT PRIMARY KEY,
            complete    INTEGER NOT NULL DEFAULT 0,
            verse_count INTEGER NOT NULL DEFAULT 0
        )""")
    return con


def canonical_book(raw: str) -> str | None:
    """Map a source-file book name ('II Kings', 'Revelation of John')."""
    name = B.normalize(raw)
    ordinal = None
    m = _ROMAN_PREFIX.match(name)
    if m:
        ordinal = len(m.group(1))
        name = name[m.end():]
    elif name[:2] in ("1 ", "2 ", "3 "):
        ordinal, name = int(name[0]), name[2:]

    if name in B.STEM_ALIASES:
        return B.resolve_stem(name, ordinal)
    first = name.split()[0] if name.split() else ""
    if first in B.STEM_ALIASES:
        return B.resolve_stem(first, ordinal)
    guess, _ = B.fuzzy_stem(name, 0.86)
    return B.resolve_stem(guess, ordinal) if guess else None


# --------------------------------------------------------------------------
# offline corpora
# --------------------------------------------------------------------------
def have_corpus(translation: str, con: sqlite3.Connection | None = None) -> bool:
    own = con is None
    con = con or connect()
    try:
        row = con.execute(
            "SELECT complete FROM corpora WHERE translation = ?", (translation,)
        ).fetchone()
        return bool(row and row[0])
    finally:
        if own:
            con.close()


def download_corpus(translation: str, progress=None) -> int:
    """Fetch a whole public-domain translation into the local database."""
    code = translation.upper()
    if code not in OFFLINE_SOURCES:
        raise BibleError(
            f"{code} is not available offline. Offline options: "
            + ", ".join(sorted(OFFLINE_SOURCES))
        )
    url = SCROLLMAPPER.format(OFFLINE_SOURCES[code])
    if progress:
        progress(f"Downloading {code} corpus")
    with urllib.request.urlopen(url, timeout=180, context=ssl_context()) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    rows = []
    for book in payload.get("books", []):
        canon = canonical_book(book.get("name", ""))
        if canon is None:
            continue
        for chapter in book.get("chapters", []):
            cnum = int(chapter.get("chapter", 0))
            for verse in chapter.get("verses", []):
                text = re.sub(r"\s+", " ", str(verse.get("text", ""))).strip()
                if text:
                    rows.append((code, canon, cnum, int(verse["verse"]), text))

    if len(rows) < 20000:
        raise BibleError(f"{code} download looks incomplete ({len(rows)} verses)")

    con = connect()
    try:
        con.executemany(
            "INSERT OR REPLACE INTO verses VALUES (?,?,?,?,?)", rows
        )
        con.execute(
            "INSERT OR REPLACE INTO corpora VALUES (?,1,?)", (code, len(rows))
        )
        con.commit()
    finally:
        con.close()
    if progress:
        progress(f"{code}: {len(rows):,} verses stored")
    return len(rows)


def ensure_corpus(translation: str, progress=None) -> str:
    """Guarantee a complete local translation, returning the code in use."""
    code = translation.upper()
    if have_corpus(code):
        return code
    if code in OFFLINE_SOURCES:
        download_corpus(code, progress)
        return code
    raise BibleError(f"No offline corpus for {code}")


# --------------------------------------------------------------------------
# online providers
# --------------------------------------------------------------------------
def _http(url: str, headers: dict | None = None) -> bytes:
    """Any network or auth failure becomes a BibleError so callers can fall
    back to the offline translation instead of losing the whole analysis."""
    # A busy service (HTTP 429, or a 5xx) or a dropped connection is usually
    # gone within seconds; giving up at once would put the wrong translation
    # on screen for the whole sermon.
    waits = [2.0, 6.0, 15.0]
    for attempt in range(len(waits) + 1):
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < len(waits):
                try:
                    wait = float(exc.headers.get("Retry-After") or waits[attempt])
                except (TypeError, ValueError):
                    wait = waits[attempt]
                time.sleep(min(wait, 60.0))
                continue
            hint = {401: "key rejected", 403: "key not authorised",
                    404: "passage not found", 429: "too many requests - try again later"}.get(
                        exc.code, f"HTTP {exc.code}")
            raise BibleError(f"{hint} ({url.split('/')[2]})") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < len(waits):
                time.sleep(waits[attempt])
                continue
            reason = getattr(exc, "reason", exc)
            raise BibleError(f"cannot reach {url.split('/')[2]}: {reason}") from exc
    raise BibleError(f"cannot reach {url.split('/')[2]}")


def _strip_html(raw: str) -> str:
    raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    raw = re.sub(r"<span class=\"vn\">\d+</span>", " ", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = (raw.replace("&nbsp;", " ").replace("&mdash;", "—")
              .replace("&rsquo;", "’").replace("&lsquo;", "‘")
              .replace("&ldquo;", "“").replace("&rdquo;", "”")
              .replace("&amp;", "&").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", raw).strip()


def _fetch_esv(book: str, chapter: int, v1: int, v2: int) -> str:
    key = _key("esv", "SERMONSTUDIO_ESV_KEY", "VERSECASTER_ESV_KEY")
    if not key:
        raise BibleError(
            "ESV needs a free key from api.esv.org — put it in config/keys.json "
            'as {"esv": "..."} or set SERMONSTUDIO_ESV_KEY.'
        )
    ref = f"{book} {chapter}:{v1}" + (f"-{v2}" if v2 and v2 != v1 else "")
    url = "https://api.esv.org/v3/passage/text/?" + urllib.parse.urlencode({
        "q": ref, "include-headings": "false", "include-footnotes": "false",
        "include-verse-numbers": "false", "include-short-copyright": "false",
        "include-passage-references": "false", "include-first-verse-numbers": "false",
    })
    data = json.loads(_http(url, {"Authorization": f"Token {key}"}))
    passages = data.get("passages") or []
    if not passages:
        raise BibleError(f"ESV returned nothing for {ref}")
    return re.sub(r"\s+", " ", passages[0]).strip()


def _fetch_nlt(book: str, chapter: int, v1: int, v2: int) -> str:
    key = _key("nlt", "SERMONSTUDIO_NLT_KEY", "VERSECASTER_NLT_KEY")
    if not key:
        raise BibleError(
            "NLT needs a free key from api.nlt.to — put it in config/keys.json "
            'as {"nlt": "..."}.'
        )
    ref = f"{book.replace(' ', '')}.{chapter}.{v1}"
    if v2 and v2 != v1:
        ref += f"-{v2}"
    url = "https://api.nlt.to/api/passages?" + urllib.parse.urlencode(
        {"ref": ref, "version": "NLT", "key": key}
    )
    return _strip_html(_http(url).decode("utf-8", "replace"))


def _fetch_apibible(translation: str, book: str, chapter: int, v1: int, v2: int) -> str:
    key = _key("apibible", "SERMONSTUDIO_APIBIBLE_KEY", "VERSECASTER_APIBIBLE_KEY")
    if not key:
        raise BibleError(
            f"{translation} is served through api.scripture.api.bible — add a key "
            'to config/keys.json as {"apibible": "..."}.'
        )
    ids = {**APIBIBLE_IDS, **load_keys().get("bible_ids", {})}
    bible_id = ids.get(translation.upper())
    if not bible_id:
        raise BibleError(
            f"No api.bible id configured for {translation}. Add it to "
            'config/keys.json under "bible_ids".'
        )
    usfm = USFM[book]
    passage = f"{usfm}.{chapter}.{v1}" + (f"-{usfm}.{chapter}.{v2}" if v2 and v2 != v1 else "")
    url = (f"https://api.scripture.api.bible/v1/bibles/{bible_id}/passages/{passage}?"
           + urllib.parse.urlencode({
               "content-type": "text", "include-notes": "false",
               "include-titles": "false", "include-verse-numbers": "false",
               "include-chapter-numbers": "false",
           }))
    data = json.loads(_http(url, {"api-key": key}))
    return _strip_html(data.get("data", {}).get("content", ""))


def _fetch_esv_verses(book: str, chapter: int, v1: int, v2: int) -> dict[int, str]:
    """A run of ESV verses in one request, split verse by verse."""
    key = _key("esv", "SERMONSTUDIO_ESV_KEY", "VERSECASTER_ESV_KEY")
    if not key:
        raise BibleError("ESV needs a free key from api.esv.org")
    ref = f"{book} {chapter}:{v1}-{v2}"
    url = "https://api.esv.org/v3/passage/text/?" + urllib.parse.urlencode({
        "q": ref, "include-headings": "false", "include-footnotes": "false",
        "include-verse-numbers": "true", "include-short-copyright": "false",
        "include-passage-references": "false", "include-first-verse-numbers": "true",
        "include-selahs": "true", "indent-poetry": "false",
    })
    data = json.loads(_http(url, {"Authorization": f"Token {key}"}))
    text = " ".join(data.get("passages") or [])
    out: dict[int, str] = {}
    parts = re.split(r"\[(\d+)\]", text)
    for i in range(1, len(parts) - 1, 2):
        v = int(parts[i])
        body = re.sub(r"\s+", " ", parts[i + 1]).strip()
        if v1 <= v <= v2 and body:
            out[v] = body
    return out


def prefetch(translation: str, verses) -> None:
    """Fetch every verse a sermon needs up front, a passage per request.

    One request per verse runs into the ESV's 60-a-minute limit on a sermon
    that reads through a long passage, and a refused request would otherwise
    put another translation on screen. Only the ESV is batched; the rest are
    fetched as they are asked for.
    """
    code = translation.upper()
    if ONLINE_PROVIDERS.get(code) != "esv":
        return
    con = connect()
    try:
        have = {(b, c, v) for b, c, v in con.execute(
            "SELECT book, chapter, verse FROM verses WHERE translation=?", (code,))}
        missing: dict[tuple[str, int], set[int]] = {}
        for book, chapter, verse in verses:
            if (book, chapter, verse) not in have:
                missing.setdefault((book, chapter), set()).add(verse)
        for (book, chapter), wanted in missing.items():
            ordered = sorted(wanted)
            # Runs close together share a request; far-apart verses do not
            # drag a whole chapter along with them.
            runs, run = [], [ordered[0]]
            for v in ordered[1:]:
                if v - run[-1] <= 6:
                    run.append(v)
                else:
                    runs.append(run)
                    run = [v]
            runs.append(run)
            for run in runs:
                try:
                    got = _fetch_esv_verses(book, chapter, run[0], run[-1])
                except BibleError:
                    continue      # the per-verse lookup will try again, and report
                for v, body in got.items():
                    con.execute("INSERT OR REPLACE INTO verses VALUES (?,?,?,?,?)",
                                (code, book, chapter, v, body))
                    _TEXTS.pop((book, chapter, v), None)
                con.commit()
    finally:
        con.close()


def fetch_online(translation: str, book: str, chapter: int, v1: int, v2: int) -> str:
    provider = ONLINE_PROVIDERS.get(translation.upper())
    if provider == "esv":
        return _fetch_esv(book, chapter, v1, v2)
    if provider == "nlt":
        return _fetch_nlt(book, chapter, v1, v2)
    if provider == "apibible" or translation.upper() in APIBIBLE_IDS:
        return _fetch_apibible(translation, book, chapter, v1, v2)
    raise BibleError(f"No source configured for {translation}")


# --------------------------------------------------------------------------
# public lookup
# --------------------------------------------------------------------------
def verse_count(translation: str, book: str, chapter: int) -> int:
    con = connect()
    try:
        row = con.execute(
            "SELECT COUNT(*) FROM verses WHERE translation=? AND book=? AND chapter=?",
            (translation.upper(), book, chapter),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        con.close()


def passage(translation: str, book: str, chapter: int,
            verse: int, verse_end: int | None = None,
            fallback: str | None = None) -> tuple[str, str]:
    """Return (text, translation_actually_used)."""
    code = translation.upper()
    v2 = verse_end or verse
    con = connect()
    try:
        rows = con.execute(
            "SELECT verse, text FROM verses WHERE translation=? AND book=? "
            "AND chapter=? AND verse BETWEEN ? AND ? ORDER BY verse",
            (code, book, chapter, verse, v2),
        ).fetchall()
        expected = v2 - verse + 1
        if len(rows) == expected:
            return " ".join(r[1] for r in rows), code

        try:
            text = fetch_online(code, book, chapter, verse, v2)
        except Exception:
            if fallback and fallback.upper() != code:
                return passage(fallback, book, chapter, verse, verse_end)
            raise
        if not text:
            raise BibleError(f"Empty passage for {book} {chapter}:{verse}")
        # Cache as a single row keyed to the first verse of the range when the
        # publisher only gives us the range as one blob.
        if verse == v2:
            con.execute("INSERT OR REPLACE INTO verses VALUES (?,?,?,?,?)",
                        (code, book, chapter, verse, text))
            con.commit()
        return text, code
    finally:
        con.close()


_TEXTS: dict[tuple[str, int, int], list[str]] = {}
_READER: sqlite3.Connection | None = None


def local_texts(book: str, chapter: int, verse: int) -> list[str]:
    """Every wording of one verse this machine has, in any translation.

    Used to recognise a verse being read whichever Bible the preacher holds.
    Cached, because the checks ask about the same verses over and over."""
    global _READER
    key = (book, chapter, verse)
    if key not in _TEXTS:
        if _READER is None:
            _READER = connect()
        _TEXTS[key] = [r[0] for r in _READER.execute(
            "SELECT text FROM verses WHERE book=? AND chapter=? AND verse=?", key)]
    return _TEXTS[key]


def local_verse(translation: str, book: str, chapter: int, verse: int) -> str | None:
    """One verse's text if it is already on this machine; never goes online."""
    con = connect()
    try:
        row = con.execute(
            "SELECT text FROM verses WHERE translation=? AND book=? AND chapter=? AND verse=?",
            (translation.upper(), book, chapter, verse),
        ).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def iter_corpus(translation: str):
    """Yield (book, chapter, verse, text) for the whole local translation."""
    con = connect()
    try:
        yield from con.execute(
            "SELECT book, chapter, verse, text FROM verses WHERE translation=? "
            "ORDER BY book, chapter, verse", (translation.upper(),)
        )
    finally:
        con.close()
