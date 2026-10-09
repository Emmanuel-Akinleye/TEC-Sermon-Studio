"""Line up a separately-recorded mic track with the camera.

The camera records its own scratch audio and the lapel/desk mic records the
same room, so both files contain the same acoustic events at the same real
times — just starting at different moments, because two people hit record.

Comparing the two energy envelopes by cross-correlation recovers that
difference to about a millisecond, which is far tighter than eyeballing a
waveform and far tighter than matching transcripts (a word boundary is only
good to a couple of hundred milliseconds).

It also measures drift. Two recorders running off different clocks diverge
slowly; at 20 parts per million that is a second and a half over an 85 minute
sermon, which starts as perfect sync and ends visibly out.

Convention used throughout: `offset` is what you add to a time in the audio
file to get the same moment in the video file.

    video_time = audio_time + offset

In After Effects that is exactly the audio layer's start time, relative to the
video layer's.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

from transcribe import find_tool


CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "alignments"


class AlignError(RuntimeError):
    pass


def _identity(path: Path) -> str:
    """Size and mtime identify a media file without reading 90 GB of it."""
    st = Path(path).stat()
    return f"{Path(path).name}|{st.st_size}|{int(st.st_mtime)}"


@dataclass
class Anchor:
    at: float           # time in the video file
    offset: float       # measured offset there
    confidence: float


@dataclass
class Alignment:
    offset: float           # seconds to add to audio time
    confidence: float       # correlation peak over background, >8 is solid
    drift_ppm: float        # clock divergence, parts per million
    drift_seconds: float    # total divergence across the overlap
    stretch_percent: float  # what to set the audio layer's stretch to
    overlap: float          # seconds of material compared
    anchors: list[Anchor]
    # offset as a function of audio time: offset = a0 + a1 * audio_time
    a0: float = 0.0
    a1: float = 0.0

    def video_time(self, audio_time: float) -> float:
        """Convert a moment in the mic recording to the camera's timeline."""
        return audio_time + self.a0 + self.a1 * audio_time

    @property
    def solid(self) -> bool:
        return self.confidence >= 8.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["solid"] = self.solid
        return d

    def describe(self) -> str:
        # A negative offset means a moment sits later in the mic file than in
        # the video, which happens when the mic was rolling first.
        started = "before" if self.offset < 0 else "after"
        lines = [
            f"offset {self.offset:+.3f}s "
            f"(the mic started {abs(self.offset):.3f}s {started} the camera)",
            f"confidence {self.confidence:.1f} "
            f"({'solid' if self.solid else 'weak - check by ear'})",
        ]
        if abs(self.drift_seconds) >= 0.08:
            lines.append(
                f"drift {self.drift_seconds:+.2f}s across {self.overlap/60:.0f} min "
                f"({self.drift_ppm:+.0f} ppm) - set the audio layer stretch to "
                f"{self.stretch_percent:.4f}%"
            )
        else:
            lines.append("no meaningful drift")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# decoding
# --------------------------------------------------------------------------
def _pcm_command(path: Path, sr: int, start: float | None, duration: float | None) -> list[str]:
    ffmpeg = find_tool("ffmpeg")
    if ffmpeg is None:
        raise AlignError("ffmpeg not found (brew install ffmpeg)")
    cmd = [ffmpeg, "-nostdin", "-v", "error"]
    if start is not None and start > 0:
        cmd += ["-ss", f"{start:.4f}"]
    cmd += ["-i", str(path)]
    if duration is not None:
        cmd += ["-t", f"{duration:.4f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    return cmd


def energy_envelope(path: Path, sr: int = 8000, hop_hz: int = 100,
                    start: float | None = None, duration: float | None = None,
                    progress=None) -> np.ndarray:
    """Short-time loudness, one value every 1/hop_hz seconds.

    Streamed so an hour-long file never lands in memory all at once.
    """
    hop = max(1, sr // hop_hz)
    proc = subprocess.Popen(_pcm_command(Path(path), sr, start, duration),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frames: list[np.ndarray] = []
    leftover = np.empty(0, dtype=np.float32)
    read_bytes = 2 * hop * 4096
    try:
        while True:
            raw = proc.stdout.read(read_bytes)
            if not raw:
                break
            block = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if leftover.size:
                block = np.concatenate([leftover, block])
            usable = (block.size // hop) * hop
            leftover = block[usable:].copy()
            if usable:
                frames.append(
                    np.sqrt((block[:usable].reshape(-1, hop) ** 2).mean(axis=1))
                )
            if progress:
                progress()
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        proc.stderr.close()
        proc.wait()
    if proc.returncode not in (0, None) and not frames:
        raise AlignError(f"could not read audio from {Path(path).name}: {err.strip()[:200]}")
    if not frames:
        raise AlignError(f"no audio found in {Path(path).name}")
    return np.concatenate(frames)


def feature(env: np.ndarray, hop_hz: int) -> np.ndarray:
    """Make two different microphones comparable.

    Log compression removes the level difference, subtracting a running mean
    removes slow gain changes and room tone, and normalising makes the
    correlation peak mean the same thing for any pair of files.
    """
    x = np.log1p(env * 50.0)
    window = max(3, int(hop_hz * 1.5))
    kernel = np.ones(window, dtype=np.float32) / window
    baseline = np.convolve(x, kernel, mode="same")
    x = x - baseline
    std = float(x.std())
    return (x / std).astype(np.float32) if std > 1e-6 else x.astype(np.float32)


# --------------------------------------------------------------------------
# correlation
# --------------------------------------------------------------------------
def _xcorr(a: np.ndarray, b: np.ndarray) -> tuple[int, float]:
    """Lag in frames such that a[i] lines up with b[i - lag], plus confidence."""
    n = 1 << (len(a) + len(b) - 1).bit_length()
    spectrum = np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n))
    cc = np.fft.irfft(spectrum, n)
    cc = np.concatenate([cc[-(len(b) - 1):], cc[:len(a)]]) if len(b) > 1 else cc[:len(a)]

    idx = int(np.argmax(cc))
    peak = float(cc[idx])
    # Background level, ignoring the peak's own neighbourhood.
    guard = max(1, len(cc) // 200)
    masked = np.delete(cc, slice(max(0, idx - guard), idx + guard))
    noise = float(np.std(masked)) if masked.size else 1.0
    confidence = peak / noise if noise > 1e-9 else 0.0
    return idx - (len(b) - 1), confidence


def _parabolic(cc_left: float, cc_mid: float, cc_right: float) -> float:
    """Sub-frame peak position from three samples around the maximum."""
    denom = cc_left - 2 * cc_mid + cc_right
    if abs(denom) < 1e-12:
        return 0.0
    return float(np.clip(0.5 * (cc_left - cc_right) / denom, -0.5, 0.5))


def _window_offset(video: Path, audio: Path, at: float, span: float,
                   guess: float, search: float,
                   sr: int = 16000, hop_hz: int = 1000) -> tuple[float, float] | None:
    """Refine the offset using a short window, at millisecond resolution."""
    audio_start = at - guess - search
    if audio_start < 0:
        span += audio_start
        at -= audio_start
        audio_start = 0.0
    if span < 5.0:
        return None

    v = feature(energy_envelope(video, sr, hop_hz, start=at, duration=span), hop_hz)
    a = feature(energy_envelope(audio, sr, hop_hz, start=audio_start,
                                duration=span + 2 * search), hop_hz)
    if v.size < hop_hz or a.size < hop_hz:
        return None

    lag, confidence = _xcorr(a, v)
    # a's window began at audio_start, v's at `at`.
    offset = at - (audio_start + lag / hop_hz)
    return offset, confidence


def measure_cached(video: str | Path, audio: str | Path, progress=None,
                   **kwargs) -> Alignment:
    """measure(), but only once per pair of files.

    Building the envelopes means decoding both recordings end to end, which is
    minutes of work on a 90 GB camera file. The answer cannot change unless one
    of the files does, so it is kept.
    """
    video, audio = Path(video), Path(audio)
    key = hashlib.sha1(f"{_identity(video)}::{_identity(audio)}".encode()).hexdigest()[:16]
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        try:
            blob = json.loads(path.read_text())
            blob["anchors"] = [Anchor(**a) for a in blob.get("anchors", [])]
            blob.pop("solid", None)
            if progress:
                progress("Using measured sync")
            return Alignment(**blob)
        except Exception:
            pass

    result = measure(video, audio, progress=progress, **kwargs)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.to_dict()))
    except OSError:
        pass
    return result


def measure(video: str | Path, audio: str | Path, anchors: int = 5,
            window: float = 90.0, progress=None) -> Alignment:
    """Find the offset, and the drift, between a camera file and a mic file."""
    video, audio = Path(video), Path(audio)
    for p in (video, audio):
        if not p.exists():
            raise AlignError(f"file not found: {p}")

    hop_hz = 100
    if progress:
        progress("Reading camera audio")
    v_env = feature(energy_envelope(video, 8000, hop_hz), hop_hz)
    if progress:
        progress("Reading mic recording")
    a_env = feature(energy_envelope(audio, 8000, hop_hz), hop_hz)

    if progress:
        progress("Aligning")
    lag, coarse_conf = _xcorr(v_env, a_env)
    coarse = lag / hop_hz

    v_len, a_len = len(v_env) / hop_hz, len(a_env) / hop_hz
    overlap_start = max(0.0, coarse)
    overlap_end = min(v_len, a_len + coarse)
    overlap = overlap_end - overlap_start
    if overlap < 20:
        raise AlignError(
            "these two files barely overlap - is the mic recording from the "
            "same service?"
        )

    # Refine at several points so drift shows up as a slope, not as noise.
    picked: list[Anchor] = []
    count = max(1, min(anchors, int(overlap // (window * 1.2)) or 1))
    for i in range(count):
        centre = overlap_start + overlap * (i + 0.5) / count
        at = max(overlap_start, centre - window / 2)
        span = min(window, overlap_end - at)
        if span < 10:
            continue
        if progress:
            progress(f"Checking sync at {at/60:.0f} min")
        found = _window_offset(video, audio, at, span, coarse, search=2.0)
        if found and found[1] >= 4.0:
            picked.append(Anchor(round(at, 2), round(found[0], 4), round(found[1], 2)))

    if not picked:
        return Alignment(
            offset=round(coarse, 3), confidence=round(coarse_conf, 2),
            drift_ppm=0.0, drift_seconds=0.0, stretch_percent=100.0,
            overlap=round(overlap, 1), anchors=[],
            a0=float(coarse), a1=0.0,
        )

    times = np.array([p.at for p in picked])
    offsets = np.array([p.offset for p in picked])
    audio_times = times - offsets
    if len(picked) >= 3:
        slope, intercept = np.polyfit(times, offsets, 1)
        a1, a0 = np.polyfit(audio_times, offsets, 1)
    else:
        slope, intercept = 0.0, float(np.median(offsets))
        a1, a0 = 0.0, float(np.median(offsets))

    # Offset at the head of the overlap is what the layer start time wants.
    offset = float(intercept + slope * overlap_start)
    drift_seconds = float(slope * overlap)
    confidence = float(np.median([p.confidence for p in picked]))

    return Alignment(
        offset=round(offset, 4),
        confidence=round(confidence, 2),
        drift_ppm=round(slope * 1e6, 1),
        drift_seconds=round(drift_seconds, 3),
        # Slowing the audio layer down by the drift keeps it locked all the way.
        stretch_percent=round(100.0 * (1.0 + slope), 5),
        overlap=round(overlap, 1),
        anchors=picked,
        a0=float(a0), a1=float(a1),
    )
