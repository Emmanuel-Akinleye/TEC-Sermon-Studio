"""Render verse cards as transparent PNGs.

After Effects builds its cards out of live layers, but Final Cut has no
equivalent thing to build — its titles come from Motion templates, which are
not something you can generate reliably. Drawing the card to a PNG with an
alpha channel sidesteps that entirely: it drops onto an FCP timeline as an
ordinary connected clip, and it looks exactly like the AE version because both
read the same `config/style.json`.

The trade is that the text is baked. For an auto-generated verse card that is
usually fine — if a verse is wrong you untick it and rebuild.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import fonts as font_index

FONT_DIRS = [
    Path.home() / "Library/Fonts",
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
]
LAST_RESORT = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]


class CardError(RuntimeError):
    pass


_BY_PS: dict[str, str] | None = None


def find_font_file(candidates: list[str]) -> str | None:
    """Turn a list of PostScript names into a font file on this machine."""
    global _BY_PS
    # The authoritative answer: each file's own PostScript name, so the card
    # renders in the same face After Effects would use.
    if _BY_PS is None:
        try:
            _BY_PS = {f.ps: f.file for f in font_index.scan()}
        except Exception:
            _BY_PS = {}
    for name in candidates:
        if name in _BY_PS:
            return _BY_PS[name]

    for name in candidates:
        for folder in FONT_DIRS:
            for ext in (".ttf", ".otf", ".ttc"):
                path = folder / f"{name}{ext}"
                if path.exists():
                    return str(path)
        # "Montserrat-SemiBold" may be filed as "Montserrat SemiBold.ttf".
        spaced = name.replace("-", " ")
        for folder in FONT_DIRS:
            for ext in (".ttf", ".otf"):
                path = folder / f"{spaced}{ext}"
                if path.exists():
                    return str(path)
    for path in LAST_RESORT:
        if Path(path).exists():
            return path
    return None


def load_font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    path = find_font_file(candidates)
    if path is None:
        raise CardError(f"no usable font found for {candidates}")
    try:
        return ImageFont.truetype(path, size)
    except OSError as exc:
        raise CardError(f"could not load {path}: {exc}") from exc


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
          width: float) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _fit(draw, text, candidates, size, leading_ratio, width, max_height):
    """Shrink until the wrapped block fits, mirroring the AE builder."""
    for _ in range(40):
        font = load_font(candidates, max(14, int(size)))
        lines = _wrap(draw, text, font, width)
        leading = size * leading_ratio
        height = leading * len(lines)
        if height <= max_height or size <= 16:
            return font, lines, leading
        size = max(16, size * 0.94)
    return font, lines, leading


def render(cue: dict, style: dict, size: tuple[int, int] = (1920, 1080)) -> Image.Image:
    """One verse card, transparent everywhere except the plate."""
    width, height = size
    scale = width / style["reference"][0]
    bar, chip = style["bar"], style["chip"]

    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    has_body = bool(cue.get("text"))
    bar_width = width if bar["fullWidth"] else bar["width"] * scale
    bar_left = 0 if bar["fullWidth"] else bar["paddingX"] * scale
    inner = bar_width - bar["paddingX"] * 2 * scale
    pad_top = bar["paddingTop"] * scale
    pad_bottom = bar["paddingBottom"] * scale

    body_font = body_lines = leading = None
    if has_body:
        body_font, body_lines, leading = _fit(
            draw, cue["text"], style["fonts"]["verse"],
            bar["fontSize"] * scale, bar["leading"] / bar["fontSize"],
            inner, bar["maxHeight"] * scale - pad_top - pad_bottom,
        )
        bar_height = max(bar["minHeight"] * scale, leading * len(body_lines) + pad_top + pad_bottom)
    else:
        bar_height = 0

    bar_top = height - bar["bottomOffset"] * scale - bar_height

    ref_font = load_font(style["fonts"]["reference"], int(chip["fontSize"] * scale))
    ref_text = cue["ref"]
    ref_w = draw.textlength(ref_text, font=ref_font)
    ref_h = chip["fontSize"] * scale

    trans_font = trans_text = None
    trans_w = 0.0
    if chip.get("showTranslation") and cue.get("translation") and has_body:
        trans_font = load_font(style["fonts"]["translation"],
                               int(chip["translationFontSize"] * scale))
        trans_text = f"({cue['translation']})"
        trans_w = draw.textlength(trans_text, font=trans_font)

    gap = chip["paddingX"] * 0.4 * scale if trans_text else 0
    chip_h = ref_h + chip["paddingY"] * 2 * scale
    chip_w = ref_w + trans_w + gap + chip["paddingX"] * 2 * scale
    chip_left = chip["offsetX"] * scale
    chip_top = (bar_top - chip_h - chip["gapBelow"] * scale) if has_body \
        else height - bar["bottomOffset"] * scale - chip_h - pad_bottom

    if has_body:
        draw.rectangle([bar_left, bar_top, bar_left + bar_width, bar_top + bar_height],
                       fill=tuple(bar["fill"]) + (255,))

    radius = chip["cornerRadius"] * scale
    box = [chip_left, chip_top, chip_left + chip_w, chip_top + chip_h]
    if radius >= 1:
        draw.rounded_rectangle(box, radius=radius, fill=tuple(chip["fill"]) + (255,))
    else:
        draw.rectangle(box, fill=tuple(chip["fill"]) + (255,))

    draw.text((chip_left + chip["paddingX"] * scale, chip_top + chip["paddingY"] * scale),
              ref_text, font=ref_font, fill=tuple(chip["textColor"]) + (255,))
    if trans_text:
        baseline_drop = ref_h - chip["translationFontSize"] * scale
        draw.text((chip_left + chip["paddingX"] * scale + ref_w + gap,
                   chip_top + chip["paddingY"] * scale + baseline_drop),
                  trans_text, font=trans_font, fill=tuple(chip["translationColor"]) + (255,))

    if has_body:
        y = bar_top + pad_top
        for line in body_lines:
            draw.text((bar_left + bar["paddingX"] * scale, y), line,
                      font=body_font, fill=tuple(bar["textColor"]) + (255,))
            y += leading

    return image


def render_all(cues: list[dict], style: dict, out_dir: Path,
               size: tuple[int, int] = (1920, 1080), progress=None) -> list[Path]:
    """One PNG per cue, named so they are readable in the FCP browser."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, cue in enumerate(cues, 1):
        safe = cue["ref"].replace(":", "-").replace(" ", "_")
        path = out_dir / f"VC_{i:02d}_{safe}.png"
        render(cue, style, size).save(path, "PNG")
        paths.append(path)
        if progress:
            progress(f"Rendered {i}/{len(cues)}", pct=i / len(cues))
    return paths
