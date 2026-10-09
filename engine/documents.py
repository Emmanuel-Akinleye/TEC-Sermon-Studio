"""Confessions and catechisms on screen, the way verses are.

The preacher quotes the church's confessions as readily as scripture — "the
canons, under the doctrine of election, that's the first head of doctrine…
particularly in the first article". This finds those references, and the
places he reads an article aloud without naming it, and puts the text up.

A document is stored article by article (or question by question) in the same
local database as the Bible, downloaded once from a public-domain source.

Names take a beating in transcription — "Canons of Dort" comes through as
"canons of God", Heidelberg as "hydrobreak" — so a reference is accepted
either from the name plus a numbered part, or from the wording itself when he
reads it out.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

import bible
import books as B
import quotes as Q
import spoken_numbers as N

# --------------------------------------------------------------------------
# what we know how to fetch
# --------------------------------------------------------------------------
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
            "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    url: str
    # Spoken forms, including the ones transcription mangles it into. Each is
    # matched as whole words; "canons" alone counts when a part follows.
    aliases: tuple[str, ...]
    section_word: str           # "head", "lord's day", … ("" when it has none)
    item_word: str              # "article", "question", …
    sections: dict[int, str]    # number -> how it is written on screen
    parser: str = "dort"        # which reader turns the download into articles


DORT = Document(
    id="dort",
    title="Canons of Dort",
    url="https://www.ccel.org/creeds/canons-of-dort.html",
    aliases=("canons of dort", "canons of dordt", "canons of dordrecht",
             "canon of dort", "canons of god", "canons of doubt", "canons of dot",
             "canons of the dort", "cannons of dort", "synod of dort", "the canons"),
    section_word="head",
    item_word="article",
    sections={1: "First Head", 2: "Second Head", 3: "Third and Fourth Head",
              5: "Fifth Head"},
)

HEIDELBERG = Document(
    id="heidelberg",
    title="Heidelberg Catechism",
    url="https://www.ccel.org/creeds/heidelberg-cat.txt",
    # "Heidelberg" survives transcription badly: these are the forms this
    # preacher's sermons actually came back as.
    aliases=("heidelberg catechism", "heidelburg catechism", "hydrobreak catechism",
             "hydro break catechism", "heavyweight catechism", "highdelberg catechism",
             "heidelberg", "hydrobreak"),
    section_word="lord's day",
    item_word="question",
    sections={},
    parser="heidelberg",
)

WESTMINSTER_SHORTER = Document(
    id="wsc",
    title="Westminster Shorter Catechism",
    url="https://www.ccel.org/creeds/westminster-shorter-cat.txt",
    aliases=("westminster shorter catechism", "shorter catechism", "westminster catechism",
             "westminster"),
    section_word="",
    item_word="question",
    sections={},
    parser="wsc",
)

WESTMINSTER_LARGER = Document(
    id="wlc",
    title="Westminster Larger Catechism",
    url="https://www.ccel.org/creeds/westminster-larger-cat.html",
    aliases=("westminster larger catechism", "larger catechism"),
    section_word="",
    item_word="question",
    sections={},
    parser="wlc",
)

KNOWN = {d.id: d for d in (DORT, HEIDELBERG, WESTMINSTER_SHORTER, WESTMINSTER_LARGER)}
# Names that could be either of a pair ("the Westminster catechism" is the
# shorter one as often as the larger), so a name that settles it wins.
AMBIGUOUS = {"westminster catechism", "westminster", "the canons", "catechism"}
HEAD_NUMBER = {"FIRST": 1, "SECOND": 2, "THIRD AND FOURTH": 3, "FIFTH": 5}


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------
def _table(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            doc     TEXT NOT NULL,
            section INTEGER NOT NULL,
            item    INTEGER NOT NULL,
            label   TEXT NOT NULL,
            text    TEXT NOT NULL,
            PRIMARY KEY (doc, section, item)
        )""")


def have(doc_id: str) -> int:
    con = bible.connect()
    try:
        _table(con)
        return con.execute("SELECT COUNT(*) FROM documents WHERE doc=?", (doc_id,)).fetchone()[0]
    finally:
        con.close()


def store(doc_id: str, rows: list[tuple[int, int, str, str]]) -> int:
    con = bible.connect()
    try:
        _table(con)
        # A fresh download replaces the document; otherwise a correction (a
        # question that moved) would leave the old copy behind.
        con.execute("DELETE FROM documents WHERE doc=?", (doc_id,))
        con.executemany("INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?)",
                        [(doc_id, s, i, label, text) for s, i, label, text in rows])
        con.commit()
        return len(rows)
    finally:
        con.close()


def article(doc_id: str, section: int, item: int) -> tuple[str, str] | None:
    """(label, text) for one article, or None."""
    con = bible.connect()
    try:
        _table(con)
        row = con.execute("SELECT label, text FROM documents WHERE doc=? AND section=? AND item=?",
                          (doc_id, section, item)).fetchone()
        return (row[0], row[1]) if row else None
    finally:
        con.close()


def find(doc_id: str, section: int | None, item: int) -> tuple[int, str, str] | None:
    """(section, label, text) for a cited part.

    A catechism is cited by question number alone - nobody says which Lord's
    Day it falls in - so the part is optional."""
    con = bible.connect()
    try:
        _table(con)
        if section is not None:
            row = con.execute("SELECT section, label, text FROM documents "
                              "WHERE doc=? AND section=? AND item=?",
                              (doc_id, section, item)).fetchone()
            if row:
                return tuple(row)
        row = con.execute("SELECT section, label, text FROM documents WHERE doc=? AND item=? "
                          "ORDER BY section LIMIT 1", (doc_id, item)).fetchone()
        return tuple(row) if row else None
    finally:
        con.close()


def all_articles(doc_id: str) -> list[tuple[int, int, str, str]]:
    con = bible.connect()
    try:
        _table(con)
        return list(con.execute(
            "SELECT section, item, label, text FROM documents WHERE doc=? ORDER BY section, item",
            (doc_id,)))
    finally:
        con.close()


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------
def _plain(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    for a, b in (("&nbsp;", " "), ("&#8217;", "’"), ("&#8216;", "‘"),
                 ("&#8220;", "“"), ("&#8221;", "”"), ("&amp;", "&"),
                 ("&quot;", '"'), ("&mdash;", "—"), ("&#8212;", "—")):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def parse_dort(text: str) -> list[tuple[int, int, str, str]]:
    """Split the Canons into their articles: five heads, the third and fourth
    together as the document itself has them."""
    marks = list(re.finditer(
        r"(FIRST|SECOND|THIRD AND FOURTH|FIFTH) HEADS? ?: ?ARTICLE (\d+)\.?", text))
    rows = []
    for n, m in enumerate(marks):
        head = HEAD_NUMBER[m.group(1)]
        number = int(m.group(2))
        end = marks[n + 1].start() if n + 1 < len(marks) else len(text)
        body = text[m.end():end].strip()
        # Anything after the last article belongs to the rejections, not to it.
        body = re.split(r"REJECTION OF (?:THE )?ERRORS|CONCLUSION", body)[0].strip()
        body = re.sub(r"\s+", " ", body)
        if len(body) < 40:
            continue
        label = f"{DORT.sections[head]}, Article {number}"
        rows.append((head, number, label, body))
    return rows


def _qa(question: str, answer: str) -> str:
    """A catechism is a question and its answer, and reads as both."""
    rule = re.compile(r"[-_=]{4,}")     # the rules a plain-text edition draws
    question = re.sub(r"\s+", " ", rule.sub(" ", question)).strip().rstrip(".")
    answer = re.sub(r"\s+", " ", rule.sub(" ", answer)).strip()
    return f"Q. {question}\nA. {answer}"


def parse_heidelberg(raw: str) -> list[tuple[int, int, str, str]]:
    """The Heidelberg, question by question, keeping the Lord's Day it belongs
    to and dropping the lettered scripture proofs."""
    rows, day = [], 0
    # The question may run on from the marker line, and the printed numbers
    # are not always right (this edition prints question 52 as "Q. 62"), so
    # they are numbered in the order they appear.
    marks = list(re.finditer(r"(?m)^[ \t]*(?:Question|Q\.?)[ \t]*(\d+)\.[ \t]*(.*)$", raw))
    for n, m in enumerate(marks):
        number = n + 1
        end = marks[n + 1].start() if n + 1 < len(marks) else len(raw)
        block = (m.group(2) + "\n" if m.group(2).strip() else "") + raw[m.end():end]
        before = raw[marks[n - 1].end() if n else 0:m.start()]
        found = re.findall(r"(\d+)\.\s*Lord's Day", before)
        if found:
            day = int(found[-1])
        lines, part = [[], []], 0
        for line in block.splitlines():
            line = line.strip()
            if re.match(r"^(Answer|A)\.$", line):
                part = 1
                continue
            if not line or re.match(r"^\([a-z]\)", line) or re.search(r"Lord's Day", line):
                continue
            lines[part].append(re.sub(r"\([a-z]\)", "", line))
        question, answer = " ".join(lines[0]), " ".join(lines[1])
        if len(question) < 8 or len(answer) < 8:
            continue
        rows.append((day, number, f"Question {number}", _qa(question, answer)))
    return rows


def parse_westminster(text: str, larger: bool) -> list[tuple[int, int, str, str]]:
    # The Shorter is printed with either "Q1:" or "Q20." from one question to
    # the next, and the same for its answers.
    pattern = (r"Question (\d+)[:.]\s*(.*?)\s*Answer[:.]\s*(.*?)(?=Question \d+[:.]|$)" if larger
               else r"Q(\d+)[:.]\s*(.*?)\s*A\1[:.]\s*(.*?)(?=Q\d+[:.]|$)")
    rows = []
    for m in re.finditer(pattern, text, flags=re.S):
        number, question, answer = int(m.group(1)), m.group(2), m.group(3)
        if len(question) < 8 or len(answer) < 8:
            continue
        rows.append((0, number, f"Question {number}", _qa(question, answer)))
    return rows


PARSERS = {
    "dort": lambda raw: parse_dort(_plain(raw)),
    "heidelberg": parse_heidelberg,
    "wsc": lambda raw: parse_westminster(_plain(raw), larger=False),
    "wlc": lambda raw: parse_westminster(_plain(raw), larger=True),
}


def download(doc_id: str, progress=None) -> int:
    doc = KNOWN[doc_id]
    if progress:
        progress(f"Downloading the {doc.title}")
    raw = bible._http(doc.url).decode("utf-8", "replace")
    rows = PARSERS[doc.parser](raw)
    if len(rows) < 20:
        raise RuntimeError(f"Could not read the {doc.title} from {doc.url}")
    return store(doc_id, rows)


# --------------------------------------------------------------------------
# hearing a reference
# --------------------------------------------------------------------------
@dataclass
class Hit:
    doc: str
    section: int | None
    item: int | None
    start: float
    end: float
    said: str
    confidence: float
    source: str                 # "spoken" | "read"
    score: float = 0.0          # how strong this reading is, for choosing between documents


def _stream(words: list[dict]) -> list[tuple[str, float, float, int]]:
    out = []
    for i, w in enumerate(words):
        for token in B.normalize(w.get("word") or "").split():
            out.append((token, float(w.get("start", 0.0)), float(w.get("end", 0.0)), i))
    return out


def _number_at(stream, i: int) -> tuple[int | None, int]:
    """A number spoken as a word, an ordinal or a numeral, at this position."""
    if i >= len(stream):
        return None, i
    word = stream[i][0]
    if word in ORDINALS:
        return ORDINALS[word], i + 1
    value, after = N.read_number([t[0] for t in stream], i)
    return value, after


def citations(words: list[dict], docs=KNOWN, ttl: float = 420.0) -> list[Hit]:
    """Spoken references: the document's name, and the head/article numbers
    that follow it - including the numbers said later, on their own."""
    stream = _stream(words)
    text = [t[0] for t in stream]
    hits: list[Hit] = []
    context: dict[str, float] = {}     # doc id -> when it was last named
    section: dict[str, int] = {}
    for doc in docs.values():
        i = 0
        while i < len(stream):
            for alias in doc.aliases:
                parts = alias.split()
                if text[i:i + len(parts)] != parts:
                    continue
                j = i + len(parts)
                context[doc.id] = stream[i][1]
                head, item, said_end = section.get(doc.id), None, j
                # "…, first head of doctrine, article 1" - the parts may
                # follow immediately or a sentence later.
                head2, item2, said_end = _parts_after(stream, j, doc, limit=60)
                if head2:
                    head = head2
                    section[doc.id] = head2
                if item2:
                    item = item2
                hits.append(Hit(doc.id, head, item, stream[i][1], stream[said_end - 1][2],
                                " ".join(text[i:said_end]), 0.8 if item else 0.6, "spoken",
                                score=2.0 * len(parts) - (3.0 if alias in AMBIGUOUS else 0.0)))
                i = said_end
                break
            else:
                # A head or article on its own, while the document is in mind.
                if doc.id in context and stream[i][1] - context[doc.id] <= ttl:
                    head2, item2, end = _parts_after(stream, i, doc, limit=12, need=True)
                    if head2 or item2:
                        if head2:
                            section[doc.id] = head2
                        if item2:
                            context[doc.id] = stream[i][1]
                            hits.append(Hit(doc.id, head2 or section.get(doc.id), item2,
                                            stream[i][1], stream[end - 1][2],
                                            " ".join(text[i:end]), 0.72, "spoken", score=2.0))
                        i = end
                        continue
                i += 1
    hits.sort(key=lambda h: h.start)
    return hits


def _parts_after(stream, i: int, doc: Document, limit: int, need: bool = False):
    """Read "…first head of doctrine … article 1" starting at i."""
    text = [t[0] for t in stream]
    head = item = None
    j, seen = i, 0
    while j < len(text) and seen < limit:
        word = text[j]
        if doc.section_word and word in (doc.section_word, doc.section_word + "s"):
            value, after = _number_at(stream, j + 1)
            if value is None:
                value, before = _number_at(stream, max(0, j - 1))
                after = j + 1
                if value is not None and before <= j:
                    head = 3 if value == 4 else value
                    j = after
                    seen += 1
                    continue
            if value is not None:
                head = 3 if value == 4 else value      # heads three and four are one
                j = after
                seen += 1
                continue
        if word in (doc.item_word, doc.item_word + "s"):
            value, after = _number_at(stream, j + 1)
            if value is None:
                value, _ = _number_at(stream, max(0, j - 1))
                after = j + 1
            if value is not None:
                item = value
                j = after
                seen += 1
                if head is not None:
                    break
                continue
        if need and seen == 0 and j > i:
            break
        j += 1
        seen += 1
    return head, item, max(j, i + 1)


def own_words(text: str) -> str:
    """The article's own prose, without the scripture it quotes.

    Articles quote the Bible at length; a preacher quoting the same verse is
    quoting the Bible, not the confession, and must not be mistaken for it."""
    text = re.sub(r"[\"\u201c][^\"\u201d]{10,}?[\"\u201d]", " ", text)
    text = re.sub(r"\([^)]*\)", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_scripture(words: list[dict], a: float, b: float, index, cfg: dict) -> bool:
    """Whether what was said here is the Bible itself.

    Confessions retell scripture in their own words, so a preacher reading
    Genesis can look like a catechism answer about creation. If the words are
    a verse, they are a verse."""
    if index is None:
        return False
    span = [w for w in words if a - 0.5 <= float(w.get("start", 0.0)) <= b + 0.5]
    if len(span) < 6:
        return False
    for hit in index.find(span, cfg.get("quotes", {})):
        if hit.matched >= 4 and hit.order >= 0.6:
            return True
    return False


def readings(words: list[dict], doc_id: str, cfg: dict | None = None, index=None) -> list[Hit]:
    """Where an article is read aloud, named or not, found by its wording."""
    cfg = {**(cfg or {}), "echo_min_run": 3}
    stream = [(Q.stem(t), a, b, k) for t, a, b, k in _stream(words)]
    import timing as T                      # the shared phrase matcher
    found: list[Hit] = []
    for section, item, _label, text in all_articles(doc_id):
        seq = T._tokens(own_words(text))
        for seg in T._find_quotes([(s, a, b) for s, a, b, _ in stream], seq, cfg):
            content = seg["content"]
            if seg["matched"] < 8 or seg["longest"] < 5 or len(content) < 3:
                continue
            a, b = stream[seg["i"]][1], stream[seg["i_end"] - 1][2]
            if _is_scripture(words, a, b, index, cfg):
                continue
            found.append(Hit(doc_id, section, item, a, b,
                             f"read aloud ({seg['matched']} words)", 0.9, "read",
                             score=float(seg["matched"])))
    # Where matches overlap, the strongest one is the reading.
    found.sort(key=lambda h: (-h.score, h.start))
    kept: list[Hit] = []
    for h in found:
        if any(not (h.end < k.start or h.start > k.end) for k in kept):
            continue
        kept.append(h)
    kept.sort(key=lambda h: h.start)
    return kept


# --------------------------------------------------------------------------
# cards
# --------------------------------------------------------------------------
def excerpt(text: str, max_words: int = 55) -> str:
    """As much of an article as can be read in passing, ending on a sentence."""
    words = text.split()
    if len(words) <= max_words:
        return text
    cut = " ".join(words[:max_words])
    stop = cut.rfind(". ")
    if stop > len(cut) * 0.4:
        return cut[:stop + 1]
    stop = cut.rfind("; ")
    if stop > len(cut) * 0.5:
        return cut[:stop] + "\u2026"
    return cut.rstrip(",;:\u2014- ") + "\u2026"


def cues(words: list[dict], cfg: dict | None = None, doc_ids=None, index=None) -> list:
    """Cards for the confessions: named references, and articles read aloud."""
    import timing as T
    cfg = {**T.DEFAULTS, **(cfg or {})}
    made = []
    for doc_id in (doc_ids or list(KNOWN)):
        if not have(doc_id):
            continue
        doc = KNOWN[doc_id]
        read = readings(words, doc_id, cfg, index)
        for hit in citations(words, {doc_id: doc}) + read:
            if not hit.item:
                continue
            found = find(doc_id, hit.section, hit.item)
            if not found:
                continue
            hit.section, label, text = found
            # A reading of the same article around the same moment says how
            # long it is on screen; a passing reference gets the usual hold.
            spoken = hit.source == "spoken"
            # The strongest reading of this same part nearby: the wording is
            # what settles which document it is, and how long it stays up.
            near = [r for r in read if r.item == hit.item
                    and -cfg["link_window"] <= r.start - hit.end <= cfg["link_window"]]
            along = hit if not spoken else (max(near, key=lambda r: r.score) if near else None)
            start = max(0.0, hit.start - cfg["lead_in"])
            if spoken and along:
                end = min(along.end + cfg["read_tail"], start + cfg["reading_cap"])
            elif spoken:
                end = hit.end + cfg["min_hold"]
            else:
                end = hit.end + cfg["read_tail"]
            body = text if (along or not spoken) else excerpt(text)
            # Ranked on the wording, not on how the name was said.
            score = along.score if along else hit.score
            made.append(T.Cue(
                ref=f"{doc.title}, {label}", book=doc.title, chapter=hit.section,
                verse=hit.item, verse_end=None, start=start, end=end, text=body,
                translation="", confidence=hit.confidence,
                source="spoken+read" if (spoken and along) else ("spoken" if spoken else "read"),
                kind="document", precise=bool(along) or not spoken, said=hit.said,
                notes=["from the " + doc.title, f"match {score:.0f}"],
            ))
    # One card per moment. Two documents can say nearly the same thing — the
    # Westminster catechisms share their first question almost word for word —
    # so the one whose wording he actually used wins.
    def strength(cue):
        note = [n for n in cue.notes if n.startswith("match ")]
        return float(note[0].split()[1]) if note else 0.0

    made.sort(key=lambda c: (-strength(c), -c.duration, c.start))
    kept: list = []
    for c in made:
        if any(not (c.end < k.start - 1 or c.start > k.end + 1) for k in kept):
            continue
        kept.append(c)
    for c in kept:
        c.notes = [n for n in c.notes if not n.startswith("match ")]
    kept.sort(key=lambda c: c.start)
    return kept


def merge(verse_cues: list, doc_cues: list, gap: float = 0.4) -> list:
    """Confession cards alongside the verse cards, never on top of one."""
    out = list(verse_cues)
    for c in doc_cues:
        clash = [v for v in out if v.enabled and not (c.end < v.start or c.start > v.end)]
        if any(v.precise for v in clash):
            continue            # he is reading scripture here; that wins
        for v in clash:
            if v.start < c.start:
                v.end = min(v.end, c.start - gap)
            else:
                c.end = min(c.end, v.start - gap)
        if c.duration >= 1.5:
            out.append(c)
    out.sort(key=lambda c: c.start)
    return [c for c in out if c.duration >= 1.2 or not c.enabled]
