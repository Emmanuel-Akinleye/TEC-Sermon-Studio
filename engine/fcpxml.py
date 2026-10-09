"""Write a Final Cut Pro timeline of verse cards.

The analysis engine is editor-agnostic: transcription, reference detection,
mic sync and cue timing all produce a plain list of (reference, in, out).
Only the last step knows what an editor is. This is that step for Final Cut,
the same way `cep/jsx/SermonStudio.jsx` is that step for After Effects.

FCPXML counts time in exact rational seconds tied to the frame rate, so every
time written here is a whole number of frames over the rate's denominator.
Anything else and Final Cut either refuses the file or silently rounds it.
"""
from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

FCPXML_VERSION = "1.11"


@dataclass
class Rate:
    """A frame rate as FCPXML wants it: a rational tick."""
    num: int
    den: int
    fps: float

    @classmethod
    def of(cls, fps: float) -> "Rate":
        nearest = int(round(fps))
        if abs(fps - nearest) < 0.001:          # 24, 25, 30, 50, 60
            return cls(100, nearest * 100, float(nearest))
        return cls(1001, nearest * 1000, nearest * 1000 / 1001.0)  # 23.976, 29.97, 59.94

    @property
    def frame_duration(self) -> str:
        return f"{self.num}/{self.den}s"

    def frames(self, seconds: float) -> int:
        return int(round(seconds * self.fps))

    def time(self, seconds: float) -> str:
        return self.tick(self.frames(seconds))

    def tick(self, frames: int) -> str:
        if frames == 0:
            return "0s"
        return f"{frames * self.num}/{self.den}s"


def file_url(path: Path) -> str:
    return "file://" + urllib.parse.quote(str(Path(path).resolve()))


def _keyframes(parent: ET.Element, name: str, points: list[tuple[str, str]],
               interp: str = "easeInOut") -> None:
    """A parameter animated over time, e.g. opacity or position."""
    param = ET.SubElement(parent, "param", {"name": name})
    animation = ET.SubElement(param, "keyframeAnimation")
    for when, value in points:
        # 'easeInOut' is not a legal value; FCP uses ease for both directions.
        ET.SubElement(animation, "keyframe",
                      {"time": when, "value": value, "interp": "ease", "curve": "smooth"})


def build(payload: dict, card_paths: list[Path], out_path: Path,
          offset: float = 0.0, project_name: str | None = None,
          include_video: bool = False, version: str = FCPXML_VERSION) -> Path:
    """Write an .fcpxml placing one card per enabled cue."""
    cues = [c for c in payload["cues"] if c.get("enabled", True)]
    if len(card_paths) != len(cues):
        raise ValueError(f"{len(card_paths)} cards for {len(cues)} cues")

    rate = Rate.of(float(payload.get("fps", 25.0)))
    width, height = 1920, 1080
    style_ref = payload.get("style", {}).get("reference")
    if style_ref:
        width, height = int(style_ref[0]), int(style_ref[1])

    media = Path(payload["media"])
    duration = float(payload.get("duration") or 0.0)
    if not duration and cues:
        duration = max(c["end"] for c in cues) + 10.0
    total_frames = max(1, rate.frames(duration + offset))

    root = ET.Element("fcpxml", {"version": version})
    resources = ET.SubElement(root, "resources")

    ET.SubElement(resources, "format", {
        "id": "r1",
        "frameDuration": rate.frame_duration,
        "width": str(width),
        "height": str(height),
        "colorSpace": "1-1-1 (Rec. 709)",
    })
    # Stills carry no rate of their own.
    ET.SubElement(resources, "format", {
        "id": "r2",
        "width": str(width),
        "height": str(height),
        "colorSpace": "1-1-1 (Rec. 709)",
    })

    next_id = 3
    video_asset_id = None
    if include_video and media.exists():
        video_asset_id = f"r{next_id}"
        next_id += 1
        asset = ET.SubElement(resources, "asset", {
            "id": video_asset_id, "name": media.stem, "start": "0s",
            "duration": rate.tick(rate.frames(duration)),
            "hasVideo": "1", "hasAudio": "1", "format": "r1",
            "videoSources": "1", "audioSources": "1", "audioChannels": "2",
        })
        ET.SubElement(asset, "media-rep",
                      {"kind": "original-media", "src": file_url(media)})

    card_ids = []
    for cue, card in zip(cues, card_paths):
        asset_id = f"r{next_id}"
        next_id += 1
        card_ids.append(asset_id)
        asset = ET.SubElement(resources, "asset", {
            "id": asset_id, "name": cue["ref"], "start": "0s", "duration": "0s",
            "hasVideo": "1", "format": "r2", "videoSources": "1",
        })
        ET.SubElement(asset, "media-rep",
                      {"kind": "original-media", "src": file_url(card)})

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", {"name": "TEC Sermon Studio"})
    project = ET.SubElement(event, "project", {
        "name": project_name or f"{media.stem} — Verse Captions"
    })
    sequence = ET.SubElement(project, "sequence", {
        "format": "r1", "duration": rate.tick(total_frames),
        "tcStart": "0s", "tcFormat": "NDF",
    })
    spine = ET.SubElement(sequence, "spine")

    # The cards hang off a single base clip. With no video that is a gap, which
    # is what you want when the plan is to copy the cards onto a real edit.
    if video_asset_id:
        base = ET.SubElement(spine, "asset-clip", {
            "ref": video_asset_id, "name": media.stem, "offset": "0s",
            "start": "0s", "duration": rate.tick(total_frames), "format": "r1",
        })
    else:
        base = ET.SubElement(spine, "gap", {
            "name": "Gap", "offset": "0s", "start": "0s",
            "duration": rate.tick(total_frames),
        })

    anim = payload.get("style", {}).get("animation", {})
    fade_in = float(anim.get("inDuration", 0.5))
    fade_out = float(anim.get("outDuration", 0.35))
    slide_y = float(anim.get("slideY", 46))

    for cue, asset_id in zip(cues, card_ids):
        start = max(0.0, float(cue["start"]) + offset)
        end = max(start + 1.0 / rate.fps, float(cue["end"]) + offset)
        in_frames = rate.frames(start)
        length = max(1, rate.frames(end) - in_frames)

        clip = ET.SubElement(base, "video", {
            "ref": asset_id, "lane": "1", "offset": rate.tick(in_frames),
            "name": cue["ref"], "start": "0s", "duration": rate.tick(length),
        })

        span = length / rate.fps
        rise = min(fade_in, span * 0.4)
        fall = min(fade_out, span * 0.4)

        # Order matters: the DTD fixes the sequence of intrinsic adjustments.
        if slide_y:
            transform = ET.SubElement(clip, "adjust-transform")
            _keyframes(transform, "position", [
                ("0s", f"0 {-slide_y}"),
                (rate.time(rise), "0 0"),
                (rate.time(max(0.0, span - fall)), "0 0"),
                (rate.tick(length), f"0 {-slide_y}"),
            ])

        blend = ET.SubElement(clip, "adjust-blend")
        _keyframes(blend, "amount", [
            ("0s", "0"),
            (rate.time(rise), "1"),
            (rate.time(max(0.0, span - fall)), "1"),
            (rate.tick(length), "0"),
        ])

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="  ")
    xml = ET.tostring(root, encoding="unicode")
    out_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE fcpxml>\n' + xml + "\n",
        encoding="utf-8",
    )
    return out_path
