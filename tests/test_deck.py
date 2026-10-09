"""Importing a presentation: the pictures, the words on them, and the order.

The conversion is done by Apple's own frameworks through engine/deck.swift,
so these tests run it for real - a deck that converts to blank pictures, or
reads a slide's words as debris, would pass a mocked test and be useless for
putting slides on screen at the right moment.

Both decks a design tool actually produces are built here: a PDF whose pages
are pictures (Canva, Keynote, Google Slides) and a folder of PNGs. So is a
PDF with a broken font encoding, because that is what made the text layer
untrustworthy in the first place.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
import deck as D  # noqa: E402

passed = failed = 0


def ok(name, cond, got=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {name}  {got}")


SLIDES = [("The Protestant Reformation", "Martin Luther 1517"),
          ("The Ninety-Five Theses", "Wittenberg 31 October 1517"),
          ("The Diet of Worms", "Here I stand")]


def slide_images(folder: Path, names=None, count=3, ext=".png") -> list[Path]:
    """Slides as a design tool would export them."""
    from PIL import Image, ImageDraw, ImageFont
    try:
        big = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 96)
        small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 56)
    except OSError:                                   # pragma: no cover
        big = small = ImageFont.load_default()
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    for i in range(count):
        title, sub = SLIDES[i % len(SLIDES)]
        image = Image.new("RGB", (1920, 1080), (250, 248, 244))
        draw = ImageDraw.Draw(image)
        draw.text((140, 420), title, fill=(20, 20, 20), font=big)
        draw.text((140, 580), sub, fill=(90, 90, 90), font=small)
        name = names[i] if names else f"slide{i + 1}{ext}"
        path = folder / name
        image.save(path)
        made.append(path)
    return made


def image_pdf(path: Path, count=3) -> Path:
    """A PDF whose pages are pictures - no text layer at all."""
    from PIL import Image
    with tempfile.TemporaryDirectory() as tmp:
        pages = [Image.open(p).convert("RGB") for p in slide_images(Path(tmp), count=count)]
        pages[0].save(path, save_all=True, append_images=pages[1:])
    return path


def broken_text_pdf(path: Path) -> Path:
    """A PDF with a text layer that cannot be read: a base-14 font with no
    encoding, which is what turned "31 October 1517" into "3 Octobe 5"."""
    parts = [(1, "<< /Type /Catalog /Pages 2 0 R >>")]
    page_ids = [3 + i * 2 for i in range(len(SLIDES))]
    font_id = 3 + len(SLIDES) * 2
    parts.append((2, "<< /Type /Pages /Kids ["
                  + " ".join(f"{p} 0 R" for p in page_ids)
                  + f"] /Count {len(SLIDES)} >>"))
    for (title, sub), pid in zip(SLIDES, page_ids):
        body = (f"BT /F1 48 Tf 72 600 Td ({title}) Tj ET "
                f"BT /F1 24 Tf 72 540 Td ({sub}) Tj ET")
        parts.append((pid, f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 720 540] "
                           f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                           f"/Contents {pid + 1} 0 R >>"))
        parts.append((pid + 1, f"<< /Length {len(body)} >>\nstream\n{body}\nendstream"))
    parts.append((font_id, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))

    buf, offsets = "%PDF-1.4\n", {}
    for oid, content in sorted(parts):
        offsets[oid] = len(buf)
        buf += f"{oid} 0 obj\n{content}\nendobj\n"
    start, top = len(buf), max(offsets) + 1
    buf += f"xref\n0 {top}\n0000000000 65535 f \n"
    for oid in range(1, top):
        buf += "%010d 00000 n \n" % offsets.get(oid, 0)
    buf += f"trailer\n<< /Size {top} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n"
    path.write_text(buf)
    return path


A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def pptx(path: Path, pictures: list[Path] | None) -> Path:
    """A .pptx: slides that are one full-bleed picture, or slides of text."""
    count = len(pictures) if pictures else 2
    with zipfile.ZipFile(path, "w") as book:
        types = ['<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                 'package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/>'
                 '<Default Extension="png" ContentType="image/png"/>'
                 '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
                 'relationships+xml"/>']
        for i in range(1, count + 1):
            types.append(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType='
                         '"application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>')
        book.writestr("[Content_Types].xml", "".join(types) + "</Types>")
        book.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://'
                      'schemas.openxmlformats.org/package/2006/relationships"><Relationship '
                      f'Id="rId1" Type="{R}/officeDocument" Target="ppt/presentation.xml"/>'
                      '</Relationships>')
        book.writestr("ppt/presentation.xml", f'<?xml version="1.0"?><p:presentation xmlns:p="{P}"'
                      f' xmlns:a="{A}"><p:sldSz cx="12192000" cy="6858000"/></p:presentation>')
        for i in range(1, count + 1):
            if pictures:
                body = (f'<?xml version="1.0"?><p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}">'
                        '<p:cSld><p:spTree><p:pic><p:nvPicPr><p:cNvPr id="2" name="Picture"/>'
                        '<p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rId2"/>'
                        '</p:blipFill><p:spPr><a:xfrm><a:off x="0" y="0"/>'
                        '<a:ext cx="12192000" cy="6858000"/></a:xfrm></p:spPr></p:pic>'
                        '</p:spTree></p:cSld></p:sld>')
            else:
                title, sub = SLIDES[(i - 1) % len(SLIDES)]
                body = (f'<?xml version="1.0"?><p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}">'
                        '<p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/>'
                        '<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/>'
                        f'<a:p><a:r><a:t>{title}</a:t></a:r></a:p>'
                        f'<a:p><a:r><a:t>{sub}</a:t></a:r></a:p></p:txBody></p:sp>'
                        '</p:spTree></p:cSld></p:sld>')
            book.writestr(f"ppt/slides/slide{i}.xml", body)
            rels = ['<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                    'package/2006/relationships">']
            if pictures:
                rels.append(f'<Relationship Id="rId2" Type="{R}/image" '
                            f'Target="../media/image{i}.png"/>')
            book.writestr(f"ppt/slides/_rels/slide{i}.xml.rels", "".join(rels) + "</Relationships>")
        for i, picture in enumerate(pictures or [], start=1):
            book.write(picture, f"ppt/media/image{i}.png")
    return path


# --- the small parts, which need nothing installed -----------------------
ok("slide 2 sorts before slide 10",
   D.natural_key("slide2.png") < D.natural_key("slide10.png"))
ok("slide 9 sorts before slide 10",
   D.natural_key("slide9.png") < D.natural_key("slide10.png"))
ok("case does not change the order",
   D.natural_key("Slide2.PNG") < D.natural_key("slide10.png"))
ok("the title is the first line with something on it",
   D._title_of("\n  \nThe Diet of Worms\nHere I stand") == "The Diet of Worms")
ok("a slide with no words has no title", D._title_of("\n \n") == "")
ok("a very long heading is cut short", len(D._title_of("x" * 400)) == 120)


def one_deck(fn):
    """Run a check with its own deck folder, and clear it up afterwards."""
    def wrapper(*args, **kw):
        made = []
        try:
            return fn(made, *args, **kw)
        finally:
            for deck_id in made:
                D.forget(deck_id)
    return wrapper


if not shutil.which("swiftc") and not Path("/usr/bin/swiftc").exists():
    print("  (swiftc unavailable - the conversion tests were skipped)")
else:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        made_ids = []

        # --- a PDF of pictures, which is what every design tool exports ---
        pdf = image_pdf(tmp / "deck.pdf")
        info = D.load(pdf)
        made_ids.append(info["id"])
        ok("every page becomes a slide", info["count"] == 3, info["count"])
        ok("the slides are numbered in order",
           [s["index"] for s in info["slides"]] == [1, 2, 3])
        images = [Path(s["image"]) for s in info["slides"]]
        ok("a picture is written for each one", all(p.exists() for p in images))
        ok("the pictures are not blank",
           all(p.stat().st_size > 5000 for p in images), [p.stat().st_size for p in images])
        from PIL import Image
        first = Image.open(images[0])
        ok("rendered at the width asked for", first.size[0] == D.MAX_WIDTH, first.size)
        ok("and keeps the slide's shape",
           abs(first.size[0] / first.size[1] - 16 / 9) < 0.02, first.size)
        ok("the words on a picture-only slide are read anyway",
           "Protestant Reformation" in info["slides"][0]["text"], info["slides"][0]["text"])
        ok("...by recognising them", info["slides"][0]["read"] == "ocr",
           info["slides"][0]["read"])
        ok("each slide is titled by its heading",
           info["slides"][2]["title"] == "The Diet of Worms", info["slides"][2]["title"])

        # --- the same deck again is not converted twice -------------------
        stored = D.DECKS / info["id"] / "deck.json"
        was = stored.stat().st_mtime_ns
        again = D.load(pdf)
        ok("importing the same deck again reuses it",
           again["id"] == info["id"] and stored.stat().st_mtime_ns == was)
        forced = D.load(pdf, force=True)
        ok("...unless it is asked to do it again",
           forced["id"] == info["id"] and stored.stat().st_mtime_ns != was)
        ok("a converted deck can be read back", D.read(info["id"])["count"] == 3)

        # --- a text layer that cannot be read -----------------------------
        broken = D.load(broken_text_pdf(tmp / "broken.pdf"))
        made_ids.append(broken["id"])
        ok("a deck with a broken font encoding still reads correctly",
           all("Reformation" in broken["slides"][0]["text"] for _ in [0])
           and "October" in broken["slides"][1]["text"],
           [s["text"] for s in broken["slides"]])
        ok("...because the debris is not trusted",
           all(s["read"] == "ocr" for s in broken["slides"]),
           [s["read"] for s in broken["slides"]])

        # --- a sound text layer is preferred ------------------------------
        if Path("/usr/sbin/cupsfilter").exists():
            plain = tmp / "plain.txt"
            plain.write_text("The Protestant Reformation\nMartin Luther 1517\n")
            good = tmp / "good.pdf"
            with open(good, "wb") as out:
                subprocess.run(["/usr/sbin/cupsfilter", "-m", "application/pdf", str(plain)],
                               stdout=out, stderr=subprocess.DEVNULL)
            if good.exists() and good.stat().st_size > 1000:
                sound = D.load(good)
                made_ids.append(sound["id"])
                ok("a sound text layer is used as it is",
                   sound["slides"][0]["read"] == "pdf", sound["slides"][0]["read"])
                ok("...and reads correctly",
                   "Protestant Reformation" in sound["slides"][0]["text"],
                   sound["slides"][0]["text"])

        # --- a folder of exported pictures --------------------------------
        folder = tmp / "export"
        slide_images(folder, names=["slide1.png", "slide2.png", "slide10.png"], count=3)
        shot = D.load(folder)
        made_ids.append(shot["id"])
        ok("a folder of pictures becomes a deck", shot["count"] == 3, shot["count"])
        ok("slide10 is last, not second",
           shot["slides"][2]["title"] == "The Diet of Worms",
           [s["title"] for s in shot["slides"]])
        ok("the pictures are copied in, so moving the originals is safe",
           all(str(D.DECKS / shot["id"]) in s["image"] for s in shot["slides"]))
        ok("their words are read", "Ninety" in shot["slides"][1]["text"],
           shot["slides"][1]["text"])

        # A format After Effects will not import is converted on the way in.
        odd = tmp / "odd"
        slide_images(odd, names=["a.bmp"], count=1, ext=".bmp")
        converted = D.load(odd)
        made_ids.append(converted["id"])
        ok("a picture After Effects cannot import is converted",
           Path(converted["slides"][0]["image"]).suffix == ".png",
           converted["slides"][0]["image"])

        # --- a deck that changed is converted again -----------------------
        moved = tmp / "export2"
        shutil.copytree(folder, moved)
        slide_images(moved, names=["slide11.png"], count=1)
        changed = D.load(moved)
        made_ids.append(changed["id"])
        ok("adding a slide makes it a different deck", changed["id"] != shot["id"])
        ok("...with the new slide in it", changed["count"] == 4, changed["count"])

        # --- .pptx --------------------------------------------------------
        pics = slide_images(tmp / "pics", count=3)
        canva = D.load(pptx(tmp / "canva.pptx", pics))
        made_ids.append(canva["id"])
        ok("a pptx of full-bleed pictures is unpacked", canva["count"] == 3, canva["count"])
        ok("...with its pictures kept",
           all(Path(s["image"]).exists() for s in canva["slides"]))
        ok("...and its words read", "Reformation" in canva["slides"][0]["text"],
           canva["slides"][0]["text"])

        try:
            D.load(pptx(tmp / "text.pptx", None))
            ok("a pptx of text and shapes says what to do instead", False)
        except D.DeckError as exc:
            ok("a pptx of text and shapes says what to do instead",
               "PDF" in str(exc) and "2 of 2" in str(exc), str(exc))

        # --- things that cannot be imported -------------------------------
        for name, expect in (("deck.key", "Keynote"), ("deck.odp", "Don't know how")):
            target = tmp / name
            target.write_text("x")
            try:
                D.load(target)
                ok(f"{name} is refused with advice", False)
            except D.DeckError as exc:
                ok(f"{name} is refused with advice", expect in str(exc), str(exc))
        try:
            D.load(tmp / "nothing-here.pdf")
            ok("a missing deck is reported", False)
        except D.DeckError as exc:
            ok("a missing deck is reported", "No such file" in str(exc), str(exc))

        empty = tmp / "empty"
        empty.mkdir()
        try:
            D.load(empty)
            ok("an empty folder is reported", False)
        except D.DeckError as exc:
            ok("an empty folder is reported", "no pictures" in str(exc), str(exc))

        for deck_id in made_ids:
            D.forget(deck_id)
        ok("a deck can be forgotten", not (D.DECKS / made_ids[0]).exists())



# --- putting the slides where he talks about them -------------------------
TALK = [
    (0, "good evening and welcome to our session on church history tonight"),
    (30, "the reformers spoke of scripture alone sola scriptura the bible as the "
         "final authority above popes and councils"),
    (120, "then they said faith alone sola fide justified by faith and not by our own working"),
    (210, "and grace alone sola gratia salvation is the gift of grace never earned"),
    (300, "christ alone solus christus one mediator and no other priest required"),
    (390, "rome answered at the council of trent in fifteen forty five the counter "
          "reformation had begun"),
    (480, "william tyndale translated the bible into english and paid with his life"),
    (570, "are there any questions before we close let us pray"),
]
DECK_TEXT = ["Sola Scriptura", "Sola Fide", "Sola Gratia", "Solus Christus",
             "The Council of Trent", "Translating the Bible", "Questions"]
TRUTH = [30, 120, 210, 300, 390, 480, 570]


def spoken(script=None):
    words = []
    for start, line in (script or TALK):
        at = start
        for word in line.split():
            words.append({"word": word, "start": round(at, 2), "end": round(at + 0.33, 2)})
            at += 0.38
    return words


def fake_deck(texts):
    return {"id": "test", "source": "test", "kind": ".pdf", "count": len(texts),
            "slides": [{"index": i + 1, "image": f"/tmp/slide-{i + 1}.png", "text": t,
                        "title": D._title_of(t), "read": "ocr"}
                       for i, t in enumerate(texts)]}


made = D.cues(fake_deck(DECK_TEXT), spoken())
ok("every slide is placed", len(made) == len(DECK_TEXT), len(made))
ok("they are in the order of the deck",
   [c.slide for c in made] == list(range(1, len(DECK_TEXT) + 1)), [c.slide for c in made])
ok("they stay in time order",
   all(made[i].start < made[i + 1].start for i in range(len(made) - 1)))
worst = max(abs(c.start - want) for c, want in zip(made, TRUTH))
ok("each lands where he turns to it", worst <= 5.0, f"worst {worst:.1f}s")

# The failure this scoring exists to prevent: a slide placed a whole window
# early, because every window containing its words scored the same.
ok("no slide is placed a window early",
   all(c.start > want - 10.0 for c, want in zip(made, TRUTH)),
   [(c.slide, round(c.start - w, 1)) for c, w in zip(made, TRUTH)])

gaps = [(made[i].end, made[i + 1].start) for i in range(len(made) - 1)
        if made[i + 1].start - made[i].end > 0.5]
ok("the panel is never left empty between slides", not gaps, gaps)
ok("none of them overlap",
   all(made[i].end <= made[i + 1].start + 0.001 for i in range(len(made) - 1)))
ok("none of them flash by",
   min(c.end - c.start for c in made) >= D.TIMING["min_dwell"] - 0.01,
   min(round(c.end - c.start, 1) for c in made))
spoke_until = max(w["end"] for w in spoken())
ok("the last slide stays up to the end of the talk",
   made[-1].end >= spoke_until - 1.0, (made[-1].end, spoke_until))
ok("every slide here was matched, not guessed", all(c.precise for c in made),
   [c.slide for c in made if not c.precise])
ok("each cue carries its picture and its number",
   all(c.image and c.slide and c.kind == "slide" for c in made))
ok("the reference names the slide",
   made[4].ref == "Slide 5 \u2014 The Council of Trent", made[4].ref)

# A slide he never mentions still goes on screen, between its neighbours.
with_extra = DECK_TEXT[:4] + ["Johann Tetzel and the Indulgence Trade"] + DECK_TEXT[4:]
spread = D.cues(fake_deck(with_extra), spoken())
odd = [c for c in spread if c.slide == 5]
ok("a slide nothing was said about is still placed", len(odd) == 1, len(odd))
if odd:
    ok("...between the slides either side of it",
       spread[3].start < odd[0].start < spread[5].start,
       (spread[3].start, odd[0].start, spread[5].start))
    ok("...and is flagged to be looked at", bool(odd[0].check) and not odd[0].precise,
       (odd[0].check, odd[0].precise))
ok("the slides around it are unaffected",
   abs(spread[0].start - made[0].start) < 0.01)

# A deck of pictures with no words at all: spread out, every one flagged.
wordless = D.cues(fake_deck(["", "", ""]), spoken())
ok("a deck with no words on it is spread across the talk",
   len(wordless) == 3 and all(not c.precise for c in wordless), len(wordless))
ok("...in order", all(wordless[i].start < wordless[i + 1].start for i in range(2)))

ok("no transcript means no slides", D.cues(fake_deck(DECK_TEXT), []) == [])
ok("no slides means no cues", D.cues(fake_deck([]), spoken()) == [])


class FakeCue:
    def __init__(self, start):
        self.start, self.end = start, start + 1


ordered = D.merge([FakeCue(100), FakeCue(10)], [FakeCue(50)])
ok("slides are merged into the verses in time order",
   [c.start for c in ordered] == [10, 50, 100], [c.start for c in ordered])

print(f"\n  {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
