"""Read real font names out of the font files installed on this Mac.

After Effects identifies a font by its PostScript name, and that name lives
inside the file's `name` table. It frequently differs from the filename —
"Tomato Grotesk Regular.otf" may be "TomatoGrotesk-Regular" — so guessing from
the filename produces a name After Effects cannot find, and the build quietly
falls back to whatever default is last in the list.

Parsing the table is a few dozen lines and removes the guesswork entirely.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, asdict
from pathlib import Path

FONT_DIRS = [
    Path.home() / "Library/Fonts",
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
]

NAME_POSTSCRIPT = 6
NAME_FAMILY = 1
NAME_SUBFAMILY = 2
NAME_TYPO_FAMILY = 16
NAME_TYPO_SUBFAMILY = 17


@dataclass
class Font:
    ps: str
    family: str
    style: str
    file: str


def _decode(raw: bytes, platform_id: int) -> str:
    try:
        # Platform 0 is Unicode and platform 3 is Windows; both store UTF-16BE.
        # Only the Macintosh platform uses a single-byte encoding.
        if platform_id in (0, 3):
            return raw.decode("utf-16-be", "ignore")
        if platform_id == 1:
            return raw.decode("mac-roman", "ignore")
        return raw.decode("utf-8", "ignore")
    except (UnicodeDecodeError, LookupError):
        return ""


def _read_name_table(data: bytes, offset: int) -> dict[int, str]:
    """Pull the interesting name records out of one sfnt font."""
    try:
        num_tables = struct.unpack_from(">H", data, offset + 4)[0]
    except struct.error:
        return {}

    name_offset = None
    for i in range(num_tables):
        rec = offset + 12 + i * 16
        try:
            tag, _checksum, tbl_offset, _length = struct.unpack_from(">4sIII", data, rec)
        except struct.error:
            return {}
        if tag == b"name":
            name_offset = tbl_offset
            break
    if name_offset is None:
        return {}

    try:
        _fmt, count, strings = struct.unpack_from(">HHH", data, name_offset)
    except struct.error:
        return {}

    found: dict[int, str] = {}
    ranks: dict[int, int] = {}
    for i in range(count):
        rec = name_offset + 6 + i * 12
        try:
            platform, _enc, _lang, name_id, length, str_off = struct.unpack_from(
                ">HHHHHH", data, rec)
        except struct.error:
            break
        if name_id not in (NAME_POSTSCRIPT, NAME_FAMILY, NAME_SUBFAMILY,
                           NAME_TYPO_FAMILY, NAME_TYPO_SUBFAMILY):
            continue
        start = name_offset + strings + str_off
        value = _decode(data[start:start + length], platform).strip()
        if not value:
            continue
        # A font carries its names in every language it was localised for.
        # Without this, Arial's weights come back as "Gras" and "Normal".
        english = (platform == 3 and _lang == 0x0409) or (platform == 1 and _lang == 0)
        rank = (2 if english else 0) + (1 if platform == 3 else 0)
        if name_id not in found or rank > ranks.get(name_id, -1):
            found[name_id] = value
            ranks[name_id] = rank
    return found


def read_file(path: Path) -> list[Font]:
    """Every font in a file — collections (.ttc) hold several."""
    try:
        data = path.read_bytes()
    except OSError:
        return []
    if len(data) < 12:
        return []

    offsets = [0]
    if data[:4] == b"ttcf":
        try:
            count = struct.unpack_from(">I", data, 8)[0]
            offsets = list(struct.unpack_from(f">{count}I", data, 12))
        except struct.error:
            return []

    fonts = []
    for offset in offsets:
        names = _read_name_table(data, offset)
        ps = names.get(NAME_POSTSCRIPT)
        if not ps:
            continue
        family = names.get(NAME_TYPO_FAMILY) or names.get(NAME_FAMILY) or ps
        style = names.get(NAME_TYPO_SUBFAMILY) or names.get(NAME_SUBFAMILY) or "Regular"
        # Apple hides its UI fonts behind a leading dot; they are not for use.
        if ps.startswith(".") or family.startswith("."):
            continue
        fonts.append(Font(ps=ps, family=family, style=style, file=str(path)))
    return fonts


def scan(dirs: list[Path] | None = None) -> list[Font]:
    found: dict[str, Font] = {}
    for folder in (dirs or FONT_DIRS):
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            continue
        for path in entries:
            if path.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                continue
            for font in read_file(path):
                found.setdefault(font.ps, font)
    return list(found.values())


def families(dirs: list[Path] | None = None) -> list[dict]:
    """Grouped for a picker: {family, styles: {style: postScriptName}}."""
    grouped: dict[str, dict[str, str]] = {}
    for font in scan(dirs):
        grouped.setdefault(font.family, {})[font.style] = font.ps
    return [{"family": name, "styles": styles}
            for name, styles in sorted(grouped.items(), key=lambda kv: kv[0].lower())]


def as_json(dirs: list[Path] | None = None) -> str:
    return json.dumps(families(dirs))


if __name__ == "__main__":
    print(as_json())
