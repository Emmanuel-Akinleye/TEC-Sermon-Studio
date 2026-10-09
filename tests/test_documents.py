"""Confessions and catechisms: parsing, hearing a reference, and the cards.

Every phrasing here is one a preacher actually uses, including the ones
transcription mangles ("the canons of God" for the Canons of Dort).
"""
import glob, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
import documents as D  # noqa: E402
import transcribe  # noqa: E402

passed = failed = 0


def ok(name, cond, got=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {name}  {got}")


def speak(text, rate=0.3):
    words, t = [], 0.0
    for w in text.split():
        words.append({"word": w, "start": round(t, 3), "end": round(t + rate - 0.05, 3)})
        t += rate
    return words


def cited(text):
    return [(h.section, h.item) for h in D.citations(speak(text))]


# --- the text itself ------------------------------------------------------
SAMPLE = ("FIRST HEAD OF DOCTRINE. DIVINE ELECTION AND REPROBATION "
          "FIRST HEAD: ARTICLE 1. As all men have sinned in Adam, lie under the curse, and are "
          "deserving of eternal death, God would have done no injustice by leaving them all to perish. "
          "FIRST HEAD: ARTICLE 2. But in this the love of God was manifested, that He sent His Son "
          "into the world, that whoever believes in Him should not perish but have eternal life. "
          "THIRD AND FOURTH HEAD: ARTICLE 1. Man was originally formed after the image of God, and "
          "his understanding was adorned with a true and saving knowledge of his Creator. "
          "REJECTION OF THE ERRORS Paragraph 1. Who teach that something or other.")
rows = D.parse_dort(SAMPLE)
ok("every article is found", len(rows) == 3, len(rows))
ok("the third and fourth heads are one, as the document has them",
   [r[0] for r in rows] == [1, 1, 3], [r[0] for r in rows])
ok("articles are labelled as they are cited", rows[2][2] == "Third and Fourth Head, Article 1", rows[2][2])
ok("the rejections are not swallowed into the last article",
   "Rejection" not in rows[2][3] and "teach that" not in rows[2][3])

# --- hearing a reference --------------------------------------------------
CASES = [
    ("Turn with me to the canons of Dort, first head of doctrine, article 6", [(1, 6)]),
    # what the transcript actually heard him say
    ("when you read the canons of God under the doctrine of election, that's the first head of "
     "doctrine. It tells us, particularly in the first article, how that", [(1, 1)]),
    ("the canons of Dort, head 2, article 3", [(2, 3)]),
    ("the canons of Dordt, the fourth head, article 12", [(3, 12)]),
    ("the canons of Dort teach election", [(None, None)]),
    ("article 5 says we are kept", []),                       # nothing named it
    ("the cannons fired at dawn in article 5", []),
]
for text, want in CASES:
    got = cited(text)
    ok(f"heard: {text[:52]}", got == want, f"-> {got}")

# after the document is named, a later article on its own belongs to it
later = cited("the canons of Dort are clear. " + "and so on " * 20 + " article 9 says")
ok("an article named later still belongs to the document", (None, 9) in later or (1, 9) in later, later)

# --- an article read aloud ------------------------------------------------
if D.have("dort"):
    own = D.own_words(D.article("dort", 1, 5)[1])
    heard = D.readings(speak("so listen to this. " + " ".join(own.split()[:30])), "dort")
    ok("an article read aloud is recognised", any(h.section == 1 and h.item == 5 for h in heard),
       [(h.section, h.item) for h in heard])
    # the verse the article quotes is not the article
    eph = "For by grace you have been saved through faith. And this is not your own doing. It is the gift of God."
    ok("the scripture an article quotes is not mistaken for it", not D.readings(speak(eph), "dort"))

    body = D.article("dort", 1, 1)[1]
    short = D.excerpt(body)
    ok("a passing reference shows a readable piece of the article",
       len(short.split()) <= 60 and (short.endswith(".") or short.endswith("…")), short[-40:])

    # --- on a real sermon --------------------------------------------------
    found = sorted(glob.glob(str(ROOT / "data/transcripts/CQ-ST001*.json")))
    if found:
        words = transcribe.clean_seams(json.loads(Path(found[-1]).read_text())["words"])
        cfg = {**json.loads((ROOT / "config/timing.json").read_text()), "fps": 29.97}
        cards = D.cues(words, cfg)
        ok("the sermon's one reference to the Canons becomes a card", len(cards) == 1, len(cards))
        if cards:
            c = cards[0]
            ok("...the right article", c.ref == "Canons of Dort, First Head, Article 1", c.ref)
            ok("...at the moment he says it", 765 < c.start < 775, round(c.start))
            ok("...long enough to read", c.duration >= 8, round(c.duration, 1))
            ok("...marked as a confession, not a verse", c.kind == "document", c.kind)
else:
    print("  skip  the Canons of Dort are not downloaded on this machine")

# --- the catechisms -------------------------------------------------------
import quotes as Q  # noqa: E402

COUNTS = {"dort": 59, "heidelberg": 129, "wsc": 107, "wlc": 196}
for doc_id, total in COUNTS.items():
    n = D.have(doc_id)
    if not n:
        print(f"  skip  {D.KNOWN[doc_id].title} is not downloaded on this machine")
        continue
    ok(f"{D.KNOWN[doc_id].title}: every part is stored", n == total, f"{n} of {total}")

if D.have("heidelberg"):
    # this edition misprints question 52 as "62"; numbering follows the order
    q52 = D.find("heidelberg", None, 52)
    ok("Heidelberg 52 is the question that belongs there",
       q52 and "come again" in q52[2], q52[2][:60] if q52 else None)
    ok("...and 62 is still its own question",
       D.find("heidelberg", None, 62)[2] != q52[2])

if D.have("wsc") and D.have("wlc"):
    ok("the Shorter and the Larger are told apart by their wording",
       "chief end of man" in D.find("wsc", 0, 1)[2] and "chief and highest" in D.find("wlc", 0, 1)[2])
    heard = speak("I don't know which one starts better between the Heidelberg Catechism and the "
                  "Westminster Catechism, the larger catechism. The question is, what is the chief "
                  "and the highest end of man? And the answer is man's chief and highest end is to "
                  "glorify God and fully to enjoy Him.")
    index = Q.load_index("BSB")
    cards = D.cues(heard, {"fps": 29.97}, index=index)
    ok("quoting the Larger's wording shows the Larger",
       len(cards) == 1 and cards[0].ref.startswith("Westminster Larger"),
       [c.ref for c in cards])

    # reading the Bible is not quoting a confession that retells it
    genesis = speak("And the Lord God commanded the man saying, you may surely eat of every tree of "
                    "the garden, but of the tree of the knowledge of good and evil you shall not eat. "
                    "For in the day that you eat of it you shall surely die.")
    ok("scripture a catechism retells is not mistaken for the catechism",
       not D.cues(genesis, {"fps": 29.97}, index=index), [c.ref for c in D.cues(genesis, {"fps": 29.97}, index=index)])

    named = cited("the Heidelberg Catechism, question 1")
    ok("a catechism question is heard", named == [(None, 1)] or named == [(1, 1)], named)
    ok("a mangled name is still heard",
       cited("the hydrobreak catechism, question 60") in ([(None, 60)], [(16, 60)]),
       cited("the hydrobreak catechism, question 60"))

print(f"\n  {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
