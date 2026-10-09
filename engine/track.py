"""Keep the pastor centred in a vertical crop of a wide shot.

A few frames a second are pulled from the camera file and Apple's Vision
framework finds faces and upper bodies in them (engine/track.swift, compiled
once into data/bin/vc-track). From his position the crop is steered the way a
camera operator would:

* it holds still while he sways, turns his head or gestures;
* it pans, smoothly, when he actually moves;
* it drifts back to centre if he settles a little to one side.

The result is a handful of keyframes, easy to adjust by hand afterwards.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

import transcribe as tx

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "engine" / "track.swift"
TOOL = ROOT / "data" / "bin" / "vc-track"
FPS = 6.0


class TrackError(RuntimeError):
    pass


def ensure_tool() -> Path:
    """The detector, compiled from its Swift source the first time it is needed."""
    if TOOL.exists() and TOOL.stat().st_mtime >= SOURCE.stat().st_mtime:
        return TOOL
    swiftc = shutil.which("swiftc") or "/usr/bin/swiftc"
    TOOL.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([swiftc, "-O", str(SOURCE), "-o", str(TOOL)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not TOOL.exists():
        raise TrackError("Could not build the pastor tracker (needs Apple's command line "
                         "tools: run  xcode-select --install ). " + r.stderr.strip()[-300:])
    return TOOL


def detect(video: Path, start: float, end: float, fps: float = FPS,
           progress=None) -> list[tuple[float, float | None]]:
    """Where the pastor is, as a 0-1 position across the frame, at `fps`
    samples a second between `start` and `end` of the camera file."""
    tool = ensure_tool()
    ffmpeg = tx.find_tool("ffmpeg") or "ffmpeg"
    with tempfile.TemporaryDirectory(prefix="vc-track-") as tmp:
        if progress:
            progress("Reading the video to follow the pastor")
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-hwaccel", "videotoolbox",
               "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.1, end - start):.3f}",
               "-i", str(video), "-vf", f"fps={fps},scale=960:-2", "-q:v", "4",
               str(Path(tmp) / "%06d.jpg")]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise TrackError("Could not read the video: " + r.stderr.strip()[-300:])
        if progress:
            progress("Finding the pastor in each frame")
        out = subprocess.run([str(tool), tmp], capture_output=True, text=True)
        if out.returncode != 0:
            raise TrackError("The tracker failed: " + out.stderr.strip()[-300:])
    frames = [json.loads(line) for line in out.stdout.splitlines() if line.startswith("{")]
    frames.sort(key=lambda f: f["file"])
    samples, prev = [], None
    for i, f in enumerate(frames):
        x = choose(f, prev)
        samples.append((start + i / fps, x))
        if x is not None:
            prev = x
    return samples


def choose(frame: dict, prev: float | None) -> float | None:
    """The pastor in one frame: the biggest face (he is nearest the camera),
    preferring whoever is where he was a moment ago; his body steadies the
    estimate, since a turned head moves the face box but not the man."""
    def near(b):
        cx = b["x"] + b["w"] / 2
        size = b["w"] * b["h"]
        return size * (1.0 if prev is None else max(0.2, 1 - abs(cx - prev) * 3))

    face = max(frame.get("faces") or [], key=near, default=None)
    body = max(frame.get("bodies") or [], key=near, default=None)
    fx = face["x"] + face["w"] / 2 if face else None
    bx = body["x"] + body["w"] / 2 if body else None
    if fx is not None and bx is not None and abs(fx - bx) < max(body["w"], 0.05):
        return 0.35 * fx + 0.65 * bx
    return fx if fx is not None else bx


# --------------------------------------------------------------------------
# steering
# --------------------------------------------------------------------------
def _fill(values: list[float | None]) -> np.ndarray:
    known = [v for v in values if v is not None]
    if not known:
        return np.full(len(values), 0.5)
    out, last = [], known[0]
    for v in values:
        last = v if v is not None else last
        out.append(last)
    return np.array(out, dtype=float)


def _median(x: np.ndarray, k: int) -> np.ndarray:
    k = max(1, k | 1)
    pad = np.pad(x, k // 2, mode="edge")
    return np.array([np.median(pad[i:i + k]) for i in range(len(x))])


def _gauss(x: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0 or len(x) < 3:
        return x.copy()
    r = int(3 * sigma) + 1
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    return np.convolve(np.pad(x, r, mode="edge"), k, mode="valid")


def _simplify(t: np.ndarray, x: np.ndarray, eps: float) -> list[int]:
    """Douglas-Peucker: the fewest keyframes that stay within eps of the path."""
    keep = {0, len(t) - 1}
    stack = [(0, len(t) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = x[a] + (x[b] - x[a]) * (t[a + 1:b] - t[a]) / (t[b] - t[a])
        err = np.abs(x[a + 1:b] - seg)
        i = int(np.argmax(err))
        if err[i] > eps:
            m = a + 1 + i
            keep.add(m)
            stack += [(a, m), (m, b)]
    return sorted(keep)


def steer(samples: list[tuple[float, float | None]], crop_width: float,
          fps: float = FPS) -> list[tuple[float, float]]:
    """Crop centre over time (0-1 across the source), as keyframes.

    `crop_width` is how much of the source's width the crop shows, which is
    what "a small movement" is measured against."""
    if not samples:
        return []
    t = np.array([s[0] for s in samples])
    x = _gauss(_median(_fill([s[1] for s in samples]), int(fps)), 0.4 * fps)

    hold = 0.12 * crop_width        # sways and gestures inside this are ignored
    settle = 0.03 * crop_width
    drift = 0.09 * crop_width       # settled this far off centre: ease back
    dt = 1.0 / fps
    alpha = 1 - np.exp(-dt / 0.35)  # how briskly a pan catches up
    c = np.empty_like(x)
    cur = float(np.median(x[: max(1, int(1.5 * fps))]))
    moving = False
    for i, v in enumerate(x):
        recent = x[max(0, i - int(3 * fps)): i + 1]
        if not moving and (abs(v - cur) > hold or
                           (len(recent) >= 3 * fps and abs(recent.mean() - cur) > drift)):
            moving = True
        if moving:
            cur += (v - cur) * alpha
            j = max(0, i - int(0.5 * fps))
            if abs(v - cur) < settle and abs(x[i] - x[j]) < settle:
                moving = False
        c[i] = cur
    c = _gauss(c, 0.3 * fps)
    half = crop_width / 2
    c = np.clip(c, half, 1 - half)
    idx = _simplify(t, c, 0.0025)
    return [(round(float(t[i]), 3), round(float(c[i]), 5)) for i in idx]


# --------------------------------------------------------------------------
# following him through a whole sermon's panel sections
# --------------------------------------------------------------------------
CACHE = ROOT / "data" / "cache"


def keyframe_times(video: Path, start: float, end: float) -> list[float]:
    """When the video's own keyframes fall in this stretch.

    Sampling those instead of a fixed rate means the decoder never has to
    unpack the frames in between: a two-minute stretch is read in seconds
    rather than a minute."""
    probe = tx.find_tool("ffprobe") or "ffprobe"
    r = subprocess.run([probe, "-v", "error", "-skip_frame", "nokey", "-select_streams", "v:0",
                        "-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0",
                        "-read_intervals", f"{max(0.0, start):.3f}%+{max(0.1, end - start):.3f}",
                        str(video)], capture_output=True, text=True)
    times = []
    for line in r.stdout.splitlines():
        value = line.strip().strip(",")
        try:
            t = float(value)
        except ValueError:
            continue
        if start - 0.5 <= t <= end + 0.5:
            times.append(t)
    return times


def sample(video: Path, start: float, end: float, progress=None) -> list[tuple[float, float | None]]:
    """Where he is, at every keyframe between `start` and `end`."""
    tool = ensure_tool()
    ffmpeg = tx.find_tool("ffmpeg") or "ffmpeg"
    times = keyframe_times(video, start, end)
    with tempfile.TemporaryDirectory(prefix="ss-follow-") as tmp:
        r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-skip_frame", "nokey",
                            "-hwaccel", "videotoolbox", "-ss", f"{max(0.0, start):.3f}",
                            "-t", f"{max(0.1, end - start):.3f}", "-i", str(video),
                            "-vsync", "0", "-vf", "scale=960:-2", "-q:v", "4",
                            str(Path(tmp) / "%06d.jpg")], capture_output=True, text=True)
        if r.returncode != 0:
            raise TrackError("Could not read the video: " + r.stderr.strip()[-200:])
        out = subprocess.run([str(tool), tmp], capture_output=True, text=True)
    frames = [json.loads(line) for line in out.stdout.splitlines() if line.startswith("{")]
    frames.sort(key=lambda f: f["file"])
    if len(times) != len(frames):
        # Fall back to spreading them evenly, which is close enough at this
        # sampling rate and only happens when the two passes disagree.
        n = max(1, len(frames))
        times = [start + (end - start) * (i + 0.5) / n for i in range(n)]
    samples, prev = [], None
    for t, f in zip(times, frames):
        x = choose(f, prev)
        samples.append((t, x))
        if x is not None:
            prev = x
    return samples


def _resample(samples, hz: float = 2.0) -> tuple[list[float], list[float | None]]:
    """Put uneven samples on an even grid, which is what the steering wants."""
    if not samples:
        return [], []
    start, end = samples[0][0], samples[-1][0]
    n = max(2, int((end - start) * hz) + 1)
    grid = [start + i / hz for i in range(n)]
    out, j = [], 0
    for t in grid:
        while j + 1 < len(samples) and samples[j + 1][0] <= t:
            j += 1
        out.append(samples[j][1])
    return grid, out


def follow(video: Path, ranges: list[tuple[float, float]], crop_width: float,
           identity: str = "", progress=None) -> list[dict]:
    """Framing keyframes for each stretch where the panel is up.

    Cached against the recording and the stretches asked for, so rebuilding
    the same sermon does not read the video again."""
    key = hashlib.sha1(json.dumps([identity or str(video), ranges, round(crop_width, 4)],
                                  sort_keys=True).encode()).hexdigest()[:16]
    cached = CACHE / f"follow-{key}.json"
    if cached.exists():
        try:
            return json.loads(cached.read_text())
        except Exception:
            pass
    out = []
    for n, (a, b) in enumerate(ranges):
        if progress:
            progress(f"Following the preacher, section {n + 1} of {len(ranges)}")
        samples = sample(Path(video), a, b, progress)
        grid, values = _resample(samples)
        keys = steer(list(zip(grid, values)), crop_width, fps=2.0) if grid else []
        out.append({"start": a, "end": b, "keys": keys})
    CACHE.mkdir(parents=True, exist_ok=True)
    try:
        cached.write_text(json.dumps(out))
    except OSError:
        pass
    return out
