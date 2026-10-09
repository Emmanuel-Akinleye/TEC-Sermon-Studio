"""Draw the slide layout's still parts once, as a single picture.

Everything around the camera — the backdrop, the panel, the frame and the
logo — never moves. Left as separate layers, After Effects rebuilds them on
every frame: scaling a 6688x3764 backdrop and a 3000x3000 logo down to size,
then drawing two shape layers over the top, 30 times a second for as long as
the panel is up. That is why panel stretches render slower than full frame.

Drawn here instead, at exactly the comp's size, with a hole where the camera
goes, it becomes one layer that composites pixel for pixel with no scaling.
The look is identical; the work per frame is a fraction.

The file is cached under data/cache and only redrawn when the design or the
comp size changes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"


def _cover(image: Image.Image, width: int, height: int) -> Image.Image:
    """Fill the frame, cropping the overflow - as `fitToComp` does in AE."""
    scale = max(width / image.width, height / image.height)
    size = (max(width, int(image.width * scale + 0.5)), max(height, int(image.height * scale + 0.5)))
    image = image.resize(size, Image.LANCZOS)
    left, top = (image.width - width) // 2, (image.height - height) // 2
    return image.crop((left, top, left + width, top + height))


def geometry(style: dict, deck: bool = False) -> dict:
    """The camera and panel boxes for this kind of session.

    A deck gets its own arrangement: the slides are the point, so the panel
    takes the frame's full height and the camera a narrower column. Sermons
    keep the layout they have always had - a bigger panel for a verse was
    never asked for.
    """
    slide = style["slide"]
    if deck and slide.get("deck"):
        return {**slide, **slide["deck"]}
    return slide


def plan(style: dict, width: int, height: int, deck: bool = False) -> dict:
    """The boxes, in comp pixels, that the build and this drawing must agree on."""
    slide = geometry(style, deck)
    ref = style["reference"]
    scale = min(width / ref[0], height / ref[1])
    ox = (width - ref[0] * scale) / 2
    oy = (height - ref[1] * scale) / 2
    cam, panel = slide["camera"], slide["panel"]
    return {
        "scale": scale,
        "camera": (ox + cam["x"] * scale, oy + cam["y"] * scale,
                   cam["width"] * scale, cam["height"] * scale),
        "panel": (ox + panel["x"] * scale, oy + panel["y"] * scale,
                  panel["width"] * scale, panel["height"] * scale),
        "border": cam["border"]["width"] * scale,
        "stroke": panel.get("strokeWidth", 0) * scale,
    }


def signature(style: dict, width: int, height: int, deck: bool = False) -> str:
    slide = geometry(style, deck)
    bits = {
        "size": [width, height], "reference": style["reference"],
        "camera": slide["camera"], "panel": slide["panel"],
        "background": slide["background"], "logo": slide["logo"],
        "version": 2, "deck": bool(deck),
    }
    for key in ("background", "logo"):
        path = Path(slide[key].get("image") or "")
        if path.exists():
            st = path.stat()
            bits[key + "_file"] = [path.name, st.st_size, int(st.st_mtime)]
    return hashlib.sha1(json.dumps(bits, sort_keys=True).encode()).hexdigest()[:16]


def make(style: dict, width: int, height: int, force: bool = False,
         deck: bool = False) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"slide-furniture-{signature(style, width, height, deck)}.png"
    if out.exists() and not force:
        return str(out)

    slide = geometry(style, deck)
    geo = plan(style, width, height, deck)
    scale = geo["scale"]
    img = Image.new("RGBA", (width, height), tuple(slide["background"]["fill"]) + (255,))

    backdrop = Path(slide["background"].get("image") or "")
    if backdrop.exists():
        img.paste(_cover(Image.open(backdrop).convert("RGB"), width, height), (0, 0))

    draw = ImageDraw.Draw(img)
    px, py, pw, ph = geo["panel"]
    draw.rectangle([px, py, px + pw, py + ph], fill=tuple(slide["panel"]["fill"]) + (255,))
    if geo["stroke"] > 0:
        # After Effects centres a stroke on the edge; Pillow draws it inward,
        # so the rectangle is grown by half a stroke to land in the same place.
        half = geo["stroke"] / 2
        draw.rectangle([px - half, py - half, px + pw + half, py + ph + half],
                       outline=tuple(slide["panel"]["strokeColor"]) + (255,),
                       width=max(1, int(round(geo["stroke"]))))

    logo_path = Path(slide["logo"].get("image") or "")
    if logo_path.exists():
        logo = Image.open(logo_path).convert("RGBA")
        target = slide["logo"]["width"] * scale
        factor = min(target / logo.width, (width * 0.25) / logo.width)
        logo = logo.resize((max(1, int(logo.width * factor)), max(1, int(logo.height * factor))),
                           Image.LANCZOS)
        img.alpha_composite(logo, (int(width - slide["logo"]["marginRight"] * scale - logo.width),
                                   int(height - slide["logo"]["marginBottom"] * scale - logo.height)))

    # The camera shows through here, with its frame drawn around the opening.
    cx, cy, cw, ch = geo["camera"]
    hole = Image.new("RGBA", (int(round(cw)), int(round(ch))), (0, 0, 0, 0))
    img.paste(hole, (int(round(cx)), int(round(cy))))
    if geo["border"] > 0:
        line = max(1, int(round(geo["border"])))
        ImageDraw.Draw(img).rectangle([cx - line / 2, cy - line / 2,
                                       cx + cw + line / 2, cy + ch + line / 2],
                                      outline=tuple(slide["camera"]["border"]["color"]) + (255,),
                                      width=line)
    img.save(out, "PNG")
    return str(out)
