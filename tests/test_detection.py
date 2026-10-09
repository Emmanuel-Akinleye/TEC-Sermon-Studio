"""How preachers actually cite scripture.

Every case here is a phrasing that turns up in real preaching. The point is
not that the current code passes them — it is that a change which breaks one
gets caught before it reaches a sermon.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

import refs  # noqa: E402

RATE = 0.30


def speak(script, rate=RATE):
    """Turn sentences into word timings. A tuple adds a pause before it."""
    words, t = [], 0.0
    for item in script:
        sentence, pause = item if isinstance(item, tuple) else (item, 0.35)
        t += pause
        for w in sentence.split():
            words.append({"word": w, "start": round(t, 3),
                          "end": round(t + rate - 0.05, 3), "prob": 0.9})
            t += rate
    return words


def refs_in(script, cfg=None):
    found = refs.detect(speak(script), cfg or {})
    return [m.ref for m in found if m.kind in ("full", "verse-only")]


CASES = [
    # --- the plain forms -------------------------------------------------
    (["John three sixteen"], ["John 3:16"]),
    (["John chapter three verse sixteen"], ["John 3:16"]),
    (["Turn to John 3:16"], ["John 3:16"]),
    (["Romans eight twenty eight"], ["Romans 8:28"]),
    (["Psalm twenty three verse one"], ["Psalms 23:1"]),
    (["Psalm one hundred and nineteen verse one hundred and five"],
     ["Psalms 119:105"]),

    # --- numbered books --------------------------------------------------
    (["First Corinthians thirteen four"], ["1 Corinthians 13:4"]),
    (["Second Timothy three sixteen"], ["2 Timothy 3:16"]),
    (["Third John verse four"], ["3 John 1:4"]),
    (["1 Peter five seven"], ["1 Peter 5:7"]),
    (["Two Samuel chapter twelve verse seven"], ["2 Samuel 12:7"]),

    # --- ranges ----------------------------------------------------------
    (["Ephesians six verses ten through eighteen"], ["Ephesians 6:10-18"]),
    (["First Corinthians thirteen four to seven"], ["1 Corinthians 13:4-7"]),

    # --- context carry-over ----------------------------------------------
    (["Turn with me to Judges chapter sixteen", "Now look at verse twenty"],
     ["Judges 16:20"]),
    (["Judges chapter sixteen", "verse twenty", "verse twenty one", "verse twenty two"],
     ["Judges 16:20", "Judges 16:21", "Judges 16:22"]),
    (["James tells us in chapter one verse fourteen"], ["James 1:14"]),

    # --- things that are NOT citations -----------------------------------
    (["Mark said to me last Sunday that he was tired"], []),
    (["My brother John came to church"], []),
    (["There were about five hundred people"], []),
    (["Judges sixteen.", ("Twenty years later the nation forgot", 1.6)],
     []),
    (["We sang hymn number four hundred"], []),
    (["In the year two thousand and twenty six"], []),

    # --- impossible verses should be dropped downstream, not detected wrong
    (["Genesis one verse one"], ["Genesis 1:1"]),
    (["Revelation twenty two seventeen"], ["Revelation 22:17"]),

    # --- awkward but real -------------------------------------------------
    (["Go with me to the book of Habakkuk", "Chapter two verse four"],
     ["Habakkuk 2:4"]),
    (["Hebrews chapter eleven and verse one"], ["Hebrews 11:1"]),
    (["Look at Galatians five twenty two and twenty three"],
     ["Galatians 5:22-23"]),
    (["Colossians three verse two says"], ["Colossians 3:2"]),
    (["Isaiah fifty three five"], ["Isaiah 53:5"]),
    (["Song of Solomon chapter two verse four"], ["Song of Solomon 2:4"]),
    (["Acts chapter two verse thirty eight"], ["Acts 2:38"]),
    (["Exodus 32 verse 5 and verse 6"], ["Exodus 32:5-6"]),
    # "from" is one letter from "rom" (Romans) and must never count as a book.
    (["as we read from chapter 1 that God made them", "verse 24 says"], []),
    (["Habbakuk chapter two verse four"], ["Habakkuk 2:4"]),
    # Naming other books in passing must not lose the chapter being read.
    (["Hosea chapter 2 verse 1", "like Hosea and Jeremiah said", "Hosea says in verse 5 to verse 8"],
     ["Hosea 2:1", "Hosea 2:5-8"]),
    (["Numbers chapter, I was 6. Verse 1."], ["Numbers 6:1"]),
    (["Jude three"], ["Jude 1:3"]),
    (["Third John verse four"], ["3 John 1:4"]),

    # --- phrasings added in the 21/09 audit ---------------------------------
    # Ranges written with a dash, ordinals written as "2nd", "all the way to",
    # lists of verses, "verse 16 of John 3", "the next verse", and everyday
    # words that are also book names.
    (["Romans 8:28 says"], ["Romans 8:28"]),
    (["In Romans 8, from verse 28 to 30"], ["Romans 8:28-30"]),
    (["Proverbs 3:5-6"], ["Proverbs 3:5-6"]),
    (["John 3:16-18"], ["John 3:16-18"]),
    (["Romans 8", "verses 28 through to 30"], ["Romans 8:28-30"]),
    (["1 Corinthians 13 from verse 4 all the way to verse 8"], ["1 Corinthians 13:4-8"]),
    (["Romans 8", "verse 28 down to verse 30"], ["Romans 8:28-30"]),
    (["Romans 12", "verses 1, 2 and 3"], ["Romans 12:1-3"]),
    (["1st John 1:9"], ["1 John 1:9"]),
    (["2nd Corinthians 5:17"], ["2 Corinthians 5:17"]),
    (["verse 16 of John 3"], ["John 3:16"]),
    (["in chapter 3 of John verse 16"], ["John 3:16"]),
    (["Revelations 3:20"], ["Revelation 3:20"]),
    (["Song of Songs 2:4"], ["Song of Solomon 2:4"]),
    (["John's gospel chapter 3 verse 16"], ["John 3:16"]),
    (["Phillipians 4:13"], ["Philippians 4:13"]),
    (["Ecclesiastics 3:1"], ["Ecclesiastes 3:1"]),
    (["Zachariah 4:6"], ["Zechariah 4:6"]),
    (["First Thessalonian 5:17"], ["1 Thessalonians 5:17"]),
    (["Deuteronomy 6, 4"], ["Deuteronomy 6:4"]),
    (["Romans 8 verse 28", "the next verse says"], ["Romans 8:28", "Romans 8:29"]),
    (["Romans 8 verse 28", "and the following verse"], ["Romans 8:28", "Romans 8:29"]),
    (["Romans 8 verse 28", "the verse before that says"], ["Romans 8:28", "Romans 8:27"]),
    (["Romans 8 verse 28", "let's go to the next chapter", "verse 1"], ["Romans 8:28", "Romans 9:1"]),
    (["Matthew chapter 6 from verse 25"], ["Matthew 6:25"]),
    (["Isaiah 40, 31"], ["Isaiah 40:31"]),
    (["Psalm 119 verses 9 to 11"], ["Psalms 119:9-11"]),
    (["Philippians chapter 4 verse 6 and 7"], ["Philippians 4:6-7"]),
    (["James 1 verse 2 to verse 4"], ["James 1:2-4"]),
    (["I lost my job 2 years ago"], []),
    (["the numbers 6 and 7 are small"], []),
    (["he acts 3 times a day"], []),
    (["Mark 16 verse 15"], ["Mark 16:15"]),
    (["Ruth 1 16"], ["Ruth 1:16"]),
    (["Genesis 50 20"], ["Genesis 50:20"]),
    (["Ephesians 2:8 and 9"], ["Ephesians 2:8-9"]),
    (["Hebrews 11 verse 6"], ["Hebrews 11:6"]),
    (["Second Chronicles 7 14"], ["2 Chronicles 7:14"]),
    (["Lamentations 3 22 and 23"], ["Lamentations 3:22-23"]),
    (["Micah 6 8"], ["Micah 6:8"]),
    (["Joshua 1 9"], ["Joshua 1:9"]),
    (["Philemon verse 6"], ["Philemon 1:6"]),
    (["Obadiah 1 3"], ["Obadiah 1:3"]),
    (["Jude verse 24 and 25"], ["Jude 1:24-25"]),
    (["John chapter 15, verse 5."], ["John 15:5"]),
    (["John 15. 5."], ["John 15:5"]),
    (["verse 5", "John 15"], []),
    (["Matthew 28, verses 19 and 20"], ["Matthew 28:19-20"]),
    (["Romans chapter 10, verses 9 to 10"], ["Romans 10:9-10"]),
    (["2 Timothy 3, 16 to 17"], ["2 Timothy 3:16-17"]),
]


def main():
    passed = failed = 0
    failures = []
    for script, expected in CASES:
        got = refs_in(script)
        label = " / ".join(s if isinstance(s, str) else s[0] for s in script)
        if got == expected:
            passed += 1
        else:
            failed += 1
            failures.append((label, expected, got))

    for label, expected, got in failures:
        print(f"  FAIL  {label[:64]}")
        print(f"        want {expected}")
        print(f"        got  {got}")

    print(f"\n  {passed} passed, {failed} failed, {len(CASES)} total")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
