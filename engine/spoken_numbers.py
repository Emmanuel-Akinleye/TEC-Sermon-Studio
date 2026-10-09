"""Spoken number handling.

Whisper is inconsistent: "chapter sixteen verse twenty" and "chapter 16
verse 20" both occur, sometimes in the same sentence. Everything downstream
wants integers, so this module turns a run of number-ish tokens into one.
"""
from __future__ import annotations

UNITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
SCALES = {"hundred": 100, "thousand": 1000}

ORDINALS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "sixth": 6, "6th": 6,
    "seventh": 7, "7th": 7, "eighth": 8, "8th": 8, "ninth": 9, "9th": 9,
    "tenth": 10, "10th": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13,
    "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17,
    "eighteenth": 18, "nineteenth": 19, "twentieth": 20, "thirtieth": 30,
    "fortieth": 40, "fiftieth": 50,
}

# Roman numerals show up in "II Corinthians" and as OCR-ish Whisper output.
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5}

NUMBER_WORDS = set(UNITS) | set(TENS) | set(SCALES)

# Words that may sit inside a spoken number without ending it.
GLUE = {"and", "a"}


def text_of(token) -> str:
    """Accept either a plain string or any token object exposing `.text`.

    Callers in this project pass timestamped Token objects; tests pass strings.
    """
    return token if isinstance(token, str) else token.text


def is_number_token(token) -> bool:
    token = text_of(token)
    return token.isdigit() or token in NUMBER_WORDS


def parse_int(token) -> int | None:
    token = text_of(token)
    if token.isdigit():
        return int(token)
    if token in UNITS:
        return UNITS[token]
    if token in TENS:
        return TENS[token]
    return None


def read_number(tokens: list, i: int, limit: int = 4) -> tuple[int | None, int]:
    """Read one number starting at tokens[i].

    Returns (value, next_index). Handles "sixteen", "16", "twenty two",
    "twenty-two", "one hundred and nineteen". Stops as soon as the run stops
    looking like a single number so that "sixteen twenty" (= 16:20) does not
    collapse into 1620.
    """
    n = len(tokens)
    if i >= n or not is_number_token(tokens[i]):
        return None, i

    # A bare multi-digit numeral is already complete.
    first = text_of(tokens[i])
    if first.isdigit():
        return int(first), i + 1

    total = 0      # completed hundreds/thousands groups
    current = 0    # group being built
    consumed = 0
    j = i
    saw_value = False

    while j < n and consumed < limit:
        tok = text_of(tokens[j])
        if tok in GLUE:
            # Only glue if a number follows, else the number has ended.
            if j + 1 < n and is_number_token(tokens[j + 1]) and saw_value:
                j += 1
                continue
            break
        if tok in SCALES:
            scale = SCALES[tok]
            if scale == 100:
                current = (current or 1) * 100
            else:
                total += (current or 1) * scale
                current = 0
            saw_value = True
            j += 1
            consumed += 1
            continue
        if tok in TENS:
            # "twenty" then "two" -> 22, but stop before a second tens word.
            if current % 100 != 0 and current != 0:
                break
            current += TENS[tok]
            saw_value = True
            j += 1
            consumed += 1
            continue
        if tok in UNITS:
            unit = UNITS[tok]
            # A unit after a completed hundreds value extends it
            # ("one hundred and nineteen" -> 119, i.e. Psalm 119).
            if current and current % 100 == 0 and unit < 100:
                current += unit
                saw_value = True
                j += 1
                consumed += 1
                break
            # A unit after a completed tens value extends it (twenty|two).
            if current and current % 10 == 0 and current % 100 != 0 and unit < 10:
                current += unit
                saw_value = True
                j += 1
                consumed += 1
                # 22 is finished; anything after starts a new number.
                break
            if current == 0:
                current = unit
                saw_value = True
                j += 1
                consumed += 1
                # "sixteen" is finished unless a scale word follows.
                if j < n and text_of(tokens[j]) in SCALES:
                    continue
                break
            break
        if tok.isdigit():
            break
        break

    if not saw_value:
        return None, i
    return total + current, j


def parse_ordinal(token) -> int | None:
    """'first' / '1st' / '2' / 'ii' -> int, for book prefixes."""
    token = text_of(token)
    if token in ORDINALS:
        return ORDINALS[token]
    if token in ROMAN:
        return ROMAN[token]
    if token.isdigit() and 1 <= int(token) <= 3:
        return int(token)
    if token in UNITS and 1 <= UNITS[token] <= 3:
        return UNITS[token]
    return None
