"""Presentation slides: pictures to put on screen, and the words on them.

A session on church history is not carried by Bible verses. The speaker works
through a deck, and what belongs beside him is the slide he is on. So a deck
is imported once, converted to one picture per slide, and the words on each
slide are read out of it - the text layer where there is a sound one, Apple's
Vision where there is not. Those words are what lets the slide be matched to
the moment he starts talking about it.

The conversion is done by a small Swift tool (engine/deck.swift) using
PDFKit, because this machine has no pdftoppm, no poppler, no LibreOffice and
no PyObjC, and needing any of them installed to put a slide on screen would be
a poor trade.

    deck = deck.load("Reformation.pdf")
    deck["slides"][0]["image"], deck["slides"][0]["text"]

What to export from your own software: a PDF, or a folder of PNGs. Both come
out of Canva, Keynote, PowerPoint and Google Slides in one step, and both
keep the design exactly as it was made.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "engine" / "deck.swift"
TOOL = ROOT / "data" / "bin" / "vc-deck"
DECKS = ROOT / "data" / "decks"

#: Pictures After Effects imports without being asked twice. Anything else a
#: phone or a design tool produces is converted on the way in.
NATIVE = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
IMAGES = NATIVE | {".heic", ".heif", ".webp", ".bmp", ".gif", ".psd"}
#: Rendered for a 4K comp, where the panel alone is about 2400px across. A
#: slide whose artwork is a small part of the page is trimmed down to that
#: part, so the page itself has to be rendered well beyond the panel's size
#: for what is left to stay sharp.
MAX_WIDTH = 3840

PPTX_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


class DeckError(RuntimeError):
    pass


def ensure_tool() -> Path:
    """The converter, compiled from its Swift source the first time."""
    if TOOL.exists() and TOOL.stat().st_mtime >= SOURCE.stat().st_mtime:
        return TOOL
    swiftc = shutil.which("swiftc") or "/usr/bin/swiftc"
    TOOL.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([swiftc, "-O", str(SOURCE), "-o", str(TOOL)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not TOOL.exists():
        raise DeckError("Could not build the slide converter (needs Apple's command line "
                        "tools: run  xcode-select --install ). " + r.stderr.strip()[-300:])
    return TOOL


def natural_key(name: str) -> list:
    """Sort "slide2" before "slide10", the way a person would."""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", str(name))]


def _pages(folder: Path) -> list[Path]:
    return sorted((p for p in folder.iterdir()
                   if p.suffix.lower() in IMAGES and not p.name.startswith(".")),
                  key=lambda p: natural_key(p.name))


def signature(source: Path) -> str:
    """Identifies this deck as it is now, so a changed deck is converted again
    and an unchanged one is not."""
    bits: list = [source.name]
    if source.is_dir():
        for page in _pages(source):
            st = page.stat()
            bits.append([page.name, st.st_size, int(st.st_mtime)])
    else:
        st = source.stat()
        bits.append([st.st_size, int(st.st_mtime)])
    bits.append(4)                      # bumped when the conversion changes
    return hashlib.sha1(json.dumps(bits).encode()).hexdigest()[:16]


def _title_of(text: str) -> str:
    """The first line with something on it - what the slide is called."""
    for line in (text or "").splitlines():
        line = line.strip()
        if len(line) >= 2:
            return line[:120]
    return ""


# --------------------------------------------------------------- conversion
def _run_tool(args: list[str]) -> list[dict]:
    out = subprocess.run([str(ensure_tool())] + args, capture_output=True, text=True)
    if out.returncode != 0:
        raise DeckError(out.stderr.strip().split("\n")[-1] or "the slide converter failed")
    found = []
    for line in out.stdout.splitlines():
        try:
            found.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return found


def _from_pdf(source: Path, folder: Path, progress) -> list[dict]:
    progress(f"Rendering {source.name}")
    rows = _run_tool(["pdf", str(source), str(folder), str(MAX_WIDTH)])
    if not rows:
        raise DeckError(f"No pages came out of {source.name}")
    return [{"index": r["index"], "image": str(folder / r["file"]),
             "text": r.get("text", ""), "read": r.get("source", "")} for r in rows]


def _copy_image(page: Path, target: Path) -> Path:
    """Into the deck folder, in a format After Effects will import."""
    if page.suffix.lower() in NATIVE:
        shutil.copy2(page, target.with_suffix(page.suffix.lower()))
        return target.with_suffix(page.suffix.lower())
    out = target.with_suffix(".png")
    r = subprocess.run(["sips", "-s", "format", "png", str(page), "--out", str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        raise DeckError(f"Could not convert {page.name} to a picture "
                        "After Effects can import")
    return out


def _from_images(pages: list[Path], folder: Path, progress) -> list[dict]:
    if not pages:
        raise DeckError("That folder has no pictures in it")
    progress(f"Copying {len(pages)} slides")
    slides = []
    for i, page in enumerate(pages, start=1):
        slides.append({"index": i,
                       "image": str(_copy_image(page, folder / f"slide-{i:03d}")),
                       "text": "", "read": ""})
    progress("Reading the slides")
    for row in _run_tool(["ocr"] + [s["image"] for s in slides]):
        at = int(row.get("index", 0)) - 1
        if 0 <= at < len(slides):
            slides[at]["text"] = row.get("text", "")
            slides[at]["read"] = "ocr"
    return slides


def _pptx_text(book: zipfile.ZipFile, name: str) -> str:
    """Every run of text on one slide, in the order it is laid down."""
    try:
        tree = ElementTree.fromstring(book.read(name))
    except (KeyError, ElementTree.ParseError):
        return ""
    lines = []
    for para in tree.iter(f"{{{PPTX_NS['a']}}}p"):
        words = "".join(node.text or "" for node in para.iter(f"{{{PPTX_NS['a']}}}t"))
        if words.strip():
            lines.append(words.strip())
    return "\n".join(lines)


def _from_pptx(source: Path, folder: Path, progress) -> list[dict]:
    """A .pptx whose slides are each one full-bleed picture - which is what
    Canva and the other design tools export - can be unpacked directly.

    A deck that is really text and shapes cannot be: drawing it needs
    PowerPoint, Keynote or LibreOffice, none of which are here. That asks for
    a PDF instead, which is one step in the same software.
    """
    progress(f"Opening {source.name}")
    with zipfile.ZipFile(source) as book:
        names = sorted((n for n in book.namelist()
                        if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                       key=natural_key)
        if not names:
            raise DeckError(f"{source.name} has no slides in it")
        slides, missing = [], 0
        for i, name in enumerate(names, start=1):
            text = _pptx_text(book, name)
            picture = _pptx_full_bleed(book, name)
            if not picture:
                missing += 1
                continue
            data = book.read(picture)
            out = (folder / f"slide-{i:03d}").with_suffix(Path(picture).suffix.lower())
            out.write_bytes(data)
            if out.suffix.lower() not in NATIVE:
                out = _copy_image(out, folder / f"slide-{i:03d}")
            slides.append({"index": i, "image": str(out), "text": text, "read": "pptx"})
        if missing:
            raise DeckError(
                f"{missing} of {len(names)} slides in {source.name} are built from text and "
                "shapes rather than pictures, and drawing those needs PowerPoint, Keynote "
                "or LibreOffice, which are not installed. Export the deck as a PDF "
                "(File › Export, or Download › PDF) and import that instead — "
                "it keeps the design exactly.")
    progress("Reading the slides")
    for row in _run_tool(["ocr"] + [s["image"] for s in slides]):
        at = int(row.get("index", 0)) - 1
        if 0 <= at < len(slides) and len(slides[at]["text"].strip()) < 8:
            slides[at]["text"] = row.get("text", "")
            slides[at]["read"] = "ocr"
    return slides


def _pptx_full_bleed(book: zipfile.ZipFile, slide: str) -> str:
    """The one picture that covers this slide, if that is what it is."""
    rels = f"ppt/slides/_rels/{Path(slide).name}.rels"
    try:
        tree = ElementTree.fromstring(book.read(slide))
        links = ElementTree.fromstring(book.read(rels))
    except (KeyError, ElementTree.ParseError):
        return ""
    targets = {}
    for rel in links:
        if "image" in rel.get("Type", ""):
            targets[rel.get("Id")] = "ppt/" + rel.get("Target", "").replace("../", "")
    if len(targets) != 1:
        return ""                       # no picture, or more than one
    picture = next(iter(targets.values()))
    # Only when it is the only thing on the slide: a photo beside a bullet
    # list would otherwise be mistaken for the slide itself.
    shapes = sum(1 for _ in tree.iter(f"{{{PPTX_NS['p']}}}sp"))
    if shapes > 0:
        return ""
    return picture if picture in book.namelist() else ""


#: How much of a slide may be blank border before it is worth trimming, and
#: how much breathing space to leave when it is.
TRIM_TOLERANCE = 16          # how far from the background colour counts as ink
TRIM_MARGIN = 0.03           # kept around the content, as a share of the page
TRIM_FLOOR = 0.80            # never crop away more than this much of the page


def trim(path: str | Path) -> tuple[int, int]:
    """Crop the dead border off a slide, in place.

    The deck this was built for puts a heading and a couple of pictures in the
    middle of a 16:9 page and leaves the rest white - on one slide the content
    was 15% of the page. Fitted whole into the panel, that content ends up
    about a sixth of the frame wide and cannot be read. Trimming the border
    off first lets the part that matters fill the panel instead.

    The background is taken from the corners, so a dark deck trims as happily
    as a white one, and a slide whose artwork runs to the edges is left alone.
    """
    from PIL import Image, ImageChops

    image = Image.open(path).convert("RGB")
    width, height = image.size
    corners = [image.getpixel(at) for at in
               ((1, 1), (width - 2, 1), (1, height - 2), (width - 2, height - 2))]
    background = max(set(corners), key=corners.count)
    mask = ImageChops.difference(image, Image.new("RGB", image.size, background))
    box = mask.convert("L").point(lambda v: 255 if v > TRIM_TOLERANCE else 0).getbbox()
    if not box:
        return image.size                      # nothing on it at all
    pad = int(round(min(width, height) * TRIM_MARGIN))
    left, top = max(0, box[0] - pad), max(0, box[1] - pad)
    right, bottom = min(width, box[2] + pad), min(height, box[3] + pad)
    kept = (right - left) * (bottom - top)
    if kept >= width * height * TRIM_FLOOR:
        return image.size                      # already fills the page
    image.crop((left, top, right, bottom)).save(path)
    return right - left, bottom - top


# --------------------------------------------------------------- the deck
def load(source: str | Path, progress=None, force: bool = False) -> dict:
    """Convert a deck, or return the one already converted from it."""
    say = progress or (lambda m: None)
    source = Path(source).expanduser()
    if not source.exists():
        raise DeckError(f"No such file: {source}")
    kind = "images" if source.is_dir() else source.suffix.lower()
    if kind == ".key":
        raise DeckError(
            "Keynote files can only be opened by Keynote, which is not installed here. "
            "In Keynote: File › Export To › PDF, then import that — it keeps "
            "the design exactly.")

    deck_id = signature(source)
    folder = DECKS / deck_id
    stored = folder / "deck.json"
    if stored.exists() and not force:
        try:
            return json.loads(stored.read_text())
        except json.JSONDecodeError:
            pass
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)

    if kind == ".pdf":
        slides = _from_pdf(source, folder, say)
    elif kind == ".pptx":
        slides = _from_pptx(source, folder, say)
    elif kind == "images":
        slides = _from_images(_pages(source), folder, say)
    elif kind in IMAGES:
        slides = _from_images([source], folder, say)
    else:
        raise DeckError(
            f"Don't know how to read {source.name}. Import a PDF, a folder of pictures, "
            "or a .pptx whose slides are pictures.")

    say("Trimming the slides")
    for slide in slides:
        slide["title"] = _title_of(slide["text"])
        try:
            slide["width"], slide["height"] = trim(slide["image"])
        except Exception:
            # A slide that cannot be trimmed is still perfectly usable.
            pass
    deck = {"id": deck_id, "source": str(source), "kind": kind,
            "count": len(slides), "slides": slides}
    stored.write_text(json.dumps(deck, indent=1))
    say(f"{len(slides)} slides ready")
    return deck


def read(deck_id: str) -> dict:
    """A deck that has already been converted."""
    stored = DECKS / deck_id / "deck.json"
    if not stored.exists():
        raise DeckError("That deck has not been imported yet")
    return json.loads(stored.read_text())


def forget(deck_id: str) -> None:
    folder = DECKS / deck_id
    if folder.exists():
        shutil.rmtree(folder)


# ------------------------------------------------- putting them on screen
#: A lecture is not a sermon: one slide can be talked over for minutes, and
#: the deck only ever moves forwards.
TIMING = {
    "window": 75.0,        # how long a slide's own material tends to run
    "bucket": 2.0,         # how finely the search looks for the change
    "min_dwell": 6.0,      # no slide flashes by faster than this
    "max_hold": 300.0,     # backstop for the last slide of the deck
    "lead_in": 0.4,        # on screen just before the first word of it
    "tail": 2.0,           # linger after the last word that belongs to it
    "gap": 0.4,            # clearance before the next slide
    "min_coverage": 0.22,  # below this, the match is not worth trusting
    "skip_penalty": 0.08,  # what it costs to leave a slide unplaced
}


def _stream(words: list[dict]) -> list[tuple[str, float, float]]:
    """The transcript as content words with their times."""
    import quotes as Q
    out = []
    for word in words:
        for token in Q.content_words(str(word.get("word", ""))):
            out.append((token, float(word.get("start", 0.0)), float(word.get("end", 0.0))))
    return out


def _weights(stream: list[tuple[str, float, float]]) -> dict[str, float]:
    """How much each word is worth as evidence.

    A word the speaker uses all session says nothing about which slide he is
    on; "Wittenberg" or "Tyndale", said once, says everything. This is the
    plain inverse-frequency weighting the verse matcher already uses, taken
    from this talk rather than from a corpus - the words that matter in a
    session on the Reformation are not the words that matter in one on Samson.
    """
    import math
    from collections import Counter
    counts = Counter(token for token, _s, _e in stream)
    total = max(1, len(stream))
    return {token: math.log(1.0 + total / (1.0 + n)) for token, n in counts.items()}


def _slide_terms(slides: list[dict], weights: dict[str, float]) -> list[dict]:
    import quotes as Q
    out = []
    for slide in slides:
        terms = {}
        for token in Q.content_words(slide.get("text", "")):
            if token in weights:
                terms[token] = weights[token]
        out.append(terms)
    return out


def _coverage(terms: dict[str, float], seen: dict[str, int]) -> float:
    """Share of a slide's evidence that turned up in a stretch of speech."""
    total = sum(terms.values())
    if total <= 0:
        return 0.0
    return sum(w for token, w in terms.items() if seen.get(token)) / total


def _scores(terms: list[dict], stream, buckets: int, cfg: dict) -> list[list[float]]:
    """For every slide, how well each starting point explains it.

    Not plain coverage over a window. Coverage is flat across every window
    that happens to contain the slide's words, so a whole window's worth of
    starting points all scored a perfect 1.00 and each slide was placed up to
    75 seconds early - which then cut the slide before it short, because it
    ends where the next one begins.

    What marks a slide going up is its words starting *now*, so each word
    counts for less the further into the window it is first heard. The peak
    then falls on the moment he turns to it.
    """
    size = cfg["bucket"]
    reach = max(1, int(round(cfg["window"] / size)))
    wanted = set()
    for slide in terms:
        wanted.update(slide)
    if not wanted:
        return [[0.0] * (buckets + 1) for _ in terms]

    # For each word that matters, the first bucket at or after b holding it.
    seen = {token: [None] * (buckets + 2) for token in wanted}
    here: dict[str, list[int]] = {token: [] for token in wanted}
    for token, start, _end in stream:
        if token in wanted:
            here[token].append(min(buckets, max(0, int(start // size))))
    for token, slots in here.items():
        nxt = seen[token]
        at = None
        mark = set(slots)
        for b in range(buckets, -1, -1):
            if b in mark:
                at = b
            nxt[b] = at

    scores = []
    for slide in terms:
        total = sum(slide.values())
        row = [0.0] * (buckets + 1)
        if total > 0:
            for b in range(buckets + 1):
                got = 0.0
                for token, weight in slide.items():
                    at = seen[token][b]
                    if at is None:
                        continue
                    away = at - b
                    if away < reach:
                        got += weight * (1.0 - away / reach)
                row[b] = got / total
        scores.append(row)
    return scores


def _place(scores: list[list[float]], cfg: dict) -> list[int | None]:
    """Choose a starting bucket for each slide, in order.

    The deck only goes forwards and a slide cannot be replaced the instant it
    appears, so this is the same monotonic alignment the verse matcher does
    over a passage. A slide may also go unplaced - he may never discuss it, or
    it may carry no words to recognise - and those are filled in afterwards
    from where their neighbours landed.

    The state carried through is the last slide actually placed, which is what
    the next one has to come after; skipping a slide leaves that unchanged.
    """
    slides = len(scores)
    if not slides:
        return []
    buckets = len(scores[0]) - 1
    step = max(1, int(round(cfg["min_dwell"] / cfg["bucket"])))
    floor, penalty = cfg["min_coverage"], cfg["skip_penalty"]
    NONE = -1                                  # "nothing placed yet"
    FLOOR = float("-inf")

    prev = [FLOOR] * (buckets + 1)             # best score, last placed at b
    prev_none = 0.0                            # best score, nothing placed
    trail: list[dict] = []

    for i in range(slides):
        # Running best of "last placed at or before b", for the gap rule.
        run, arg = FLOOR, None
        pref, pref_at = [FLOOR] * (buckets + 1), [None] * (buckets + 1)
        for b in range(buckets + 1):
            if prev[b] > run:
                run, arg = prev[b], b
            pref[b], pref_at[b] = run, arg

        cur = [FLOOR] * (buckets + 1)
        choice: dict = {}
        for b in range(buckets + 1):
            if scores[i][b] < floor:
                continue                       # not worth putting it here
            from_at, value = NONE, prev_none
            back = b - step
            if back >= 0 and pref[back] > value:
                from_at, value = pref_at[back], pref[back]
            if value == FLOOR:
                continue
            cur[b] = value + scores[i][b]
            choice[b] = ("place", from_at)
        # Leaving this slide out keeps whatever was placed last.
        for b in range(buckets + 1):
            if prev[b] == FLOOR:
                continue
            passed = prev[b] - penalty
            if passed > cur[b]:
                cur[b] = passed
                choice[b] = ("skip", b)
        trail.append(choice)
        prev, prev_none = cur, prev_none - penalty

    # The best way to have finished, then walk it back.
    at: int | None = None
    best = prev_none
    for b in range(buckets + 1):
        if prev[b] > best:
            best, at = prev[b], b
    out: list[int | None] = [None] * slides
    for i in range(slides - 1, -1, -1):
        if at is None:
            continue                           # every remaining slide skipped
        made = trail[i].get(at)
        if not made:
            at = None
            continue
        how, from_at = made
        if how == "place":
            out[i] = at
            at = None if from_at == NONE else from_at
    return out


def _fill_gaps(found: list[int | None], slides: int, cfg: dict,
               buckets: int) -> list[tuple[int | None, bool]]:
    """Put the unplaced slides between the ones around them.

    A deck is shown in order and nearly every slide gets shown, so a slide
    the words could not place is almost certainly up between its neighbours -
    it may be a portrait, a map or a quotation with nothing distinctive said
    about it. Spreading them evenly puts the whole deck on screen in the right
    order, and each one is flagged for a look rather than passed off as
    matched.
    """
    out: list[tuple[int | None, bool]] = [(b, False) for b in found]
    step = max(1, int(round(cfg["min_dwell"] / cfg["bucket"])))
    i = 0
    while i < slides:
        if found[i] is not None:
            i += 1
            continue
        run_end = i
        while run_end < slides and found[run_end] is None:
            run_end += 1
        before = next((found[j] for j in range(i - 1, -1, -1) if found[j] is not None), None)
        after = found[run_end] if run_end < slides else None
        count = run_end - i
        if before is None and after is None:
            # Nothing in the whole deck could be matched - a deck of
            # photographs or maps with nothing distinctive said about any of
            # them. Spread it evenly over the talk so the slides are all there
            # in order to adjust, rather than handing back nothing at all.
            spacing = max(step, buckets // (slides + 1))
            for k in range(slides):
                out[k] = (min(buckets, (k + 1) * spacing), True)
            return out
        if before is None:
            # Leading slides: back from the first one that was placed.
            start = max(0, after - count * step)
            spots = [start + k * step for k in range(count)]
        elif after is None:
            spots = [before + (k + 1) * step for k in range(count)]
        else:
            room = after - before
            spacing = max(step, room // (count + 1))
            spots = [before + (k + 1) * spacing for k in range(count)]
        for k, j in enumerate(range(i, run_end)):
            out[j] = (max(0, min(buckets, spots[k])), True)
        i = run_end
    return out


def cues(info: dict, words: list[dict], cfg: dict | None = None) -> list:
    """Cards for a deck: each slide, over the stretch it is being talked about."""
    import timing as T
    settings = {**TIMING, **(cfg or {})}
    slides = info.get("slides") or []
    if not slides or not words:
        return []

    stream = _stream(words)
    if not stream:
        return []
    weights = _weights(stream)
    terms = _slide_terms(slides, weights)
    span = max(end for _t, _s, end in stream)
    buckets = max(1, int(span // settings["bucket"]) + 1)
    scores = _scores(terms, stream, buckets, settings)
    found = _place(scores, settings)
    placed = _fill_gaps(found, len(slides), settings, buckets)

    # Where each slide starts: its own words, as actually said, rather than a
    # bucket boundary.
    span_end = max(end for _t, _s, end in stream)
    starts: list[float | None] = [None] * len(slides)
    heard: list[list[tuple[float, float]]] = [[] for _ in slides]
    for i, (bucket, guessed) in enumerate(placed):
        if bucket is None:
            continue
        window = (bucket * settings["bucket"],
                  bucket * settings["bucket"] + settings["window"])
        said = [(s, e) for token, s, e in stream
                if token in terms[i] and window[0] <= s <= window[1]]
        heard[i] = said
        at = (min(s for s, _e in said) if said and not guessed
              else bucket * settings["bucket"])
        starts[i] = max(0.0, at - settings["lead_in"])
    # Keep them in order, and far enough apart, after that nudge.
    #
    # Snapping a slide to the first of its own words can move it forward by up
    # to a window, which on a real talk pushed one slide past the start of the
    # next and left it on screen for six seconds. The spacing the placement
    # worked to has to hold afterwards as well.
    seen_at = None
    for i, at in enumerate(starts):
        if at is None:
            continue
        if seen_at is not None and at < seen_at + settings["min_dwell"]:
            starts[i] = seen_at + settings["min_dwell"]
        seen_at = starts[i]

    made = []
    for i, (bucket, guessed) in enumerate(placed):
        if bucket is None or starts[i] is None:
            continue
        slide = slides[i]
        start = starts[i]
        # It stays up until the next slide takes over - measured from where
        # that slide really starts, not from its bucket, or the panel would
        # go briefly empty between the two.
        following = next((starts[j] for j in range(i + 1, len(starts))
                          if starts[j] is not None), None)
        if following is not None:
            end = max(start + settings["min_dwell"], following - settings["gap"])
        else:
            # The last slide of the deck stays up to the end of the talk.
            # No backstop here: there is no slide behind it to take over, so
            # cutting it short would leave the panel empty for the rest of the
            # session. Trim it in the panel if the talk runs on past the deck.
            end = max(span_end, start + settings["min_dwell"])

        coverage = scores[i][bucket] if bucket < len(scores[i]) else 0.0
        notes, check = [], ""
        if guessed:
            notes.append("placed between the slides around it")
            check = "nothing on this slide was said aloud \u2014 check where it sits"
        elif coverage < settings["min_coverage"] * 1.6:
            check = "only a little of this slide was mentioned \u2014 worth a look"
        made.append(T.Cue(
            ref=f"Slide {slide['index']}"
                + (f" \u2014 {slide['title']}" if slide.get("title") else ""),
            book="Slide", chapter=0, verse=slide["index"], verse_end=None,
            start=start, end=end, text=slide.get("text", ""), translation="",
            confidence=round(min(1.0, coverage * 2.0), 3),
            source="read" if not guessed else "spoken",
            kind="slide", precise=not guessed, image=slide.get("image", ""),
            slide=slide["index"], check=check, notes=notes, enabled=True))
    return made


def merge(existing: list, slide_cues: list) -> list:
    """Slides alongside whatever verses were found, in time order."""
    return sorted(list(existing) + list(slide_cues), key=lambda c: (c.start, c.end))


def words_for(payload: dict) -> list[dict]:
    """The sermon's words on the camera's clock.

    The same transcript the verses were found in, shifted by the measured
    sync the way `reanalyze` does it - slides have to land on the camera
    timeline, not the mic's.
    """
    import captions
    import transcribe as tx
    words = tx.clean_seams(captions._transcript_for(payload)["words"])
    shift = payload.get("alignment")
    if shift:
        words = [{**w,
                  "start": w["start"] + shift["a0"] + shift["a1"] * w["start"],
                  "end": w["end"] + shift["a0"] + shift["a1"] * w["end"]}
                 for w in words]
    return words


def place(cues_path: str | Path, source: str | Path, cfg: dict | None = None,
          progress=None) -> dict:
    """Work out when each slide was being talked about, and add them to an
    analysed sermon's cues.

    The verses that were found stay exactly as they are, including whatever
    was ticked or unticked in the panel. Running this again replaces the
    slides rather than stacking a second copy of the deck on top, and carries
    the review choices across by slide number.
    """
    import timing as T
    say = progress or (lambda m: None)
    path = Path(cues_path)
    payload = json.loads(path.read_text())

    say("Reading the saved transcript")
    words = words_for(payload)
    info = read(str(source)) if (DECKS / str(source) / "deck.json").exists() \
        else load(source, progress=say)

    say(f"Placing {info['count']} slides")
    fresh = cues(info, words, cfg)

    # What was ticked or unticked for this deck last time.
    chosen = {c.get("slide"): c for c in payload.get("cues", [])
              if c.get("kind") == "slide"}
    for cue in fresh:
        was = chosen.get(cue.slide)
        if was and was.get("userSet"):
            cue.enabled = bool(was.get("enabled", True))
            cue.start = float(was.get("start", cue.start))
            cue.end = float(was.get("end", cue.end))
            cue.notes = list(was.get("notes") or [])
            cue.check = ""

    kept = [c for c in payload.get("cues", []) if c.get("kind") != "slide"]
    payload["cues"] = sorted(kept + [c.to_dict() for c in fresh],
                             key=lambda c: (c["start"], c["end"]))
    payload["deck"] = {"id": info["id"], "source": info["source"],
                       "count": info["count"]}
    # Stamped so an open panel notices. The panel holds the cues in memory and
    # writes them back when you press Build, and it only reloads when this
    # changes - without it, placing a deck under an open panel was quietly
    # overwritten by the copy it already had, and the old slides were built.
    payload["generated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(json.dumps(payload, indent=2))
    _ = T
    return {"cues_path": str(path), "deck": info["id"], "slides": len(fresh),
            "placed": sum(1 for c in fresh if c.precise),
            "guessed": sum(1 for c in fresh if not c.precise),
            "verses": len(kept)}
