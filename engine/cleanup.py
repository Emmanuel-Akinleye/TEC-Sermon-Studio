"""Clean up the mic recording, on this machine, with nothing uploaded.

Adobe Podcast works because it does two things: it listens to the recording
before deciding what to do, and it finishes with a broadcast level. A fixed
"denoise preset" does neither, which is why generic chains either leave the
hiss alone or chew the voice up.

So this measures the recording first - noise floor, mains hum and its
harmonics, sibilance, how live the room is, whether it was clipped - and
builds the filter chain from what it found. A hum notch is only cut at a
frequency actually humming; the de-esser is only as hard as the sibilance
needs; the level at the end is set from a real loudness measurement of the
processed sound, not guessed.

What this cannot do is invent what the mic never captured. Podcast runs a
generative model that resynthesises the voice; the honest local equivalents
are an RNNoise model for `arnndn` or DeepFilterNet. Drop a `.rnnn` model in
data/models and the chain picks it up automatically (see `model_path`).

    clean = cleanup.run(mic, out, strength="medium")
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "data" / "models"

#: Loudness the finished file is set to - what YouTube and the podcast
#: platforms expect, so nothing is turned down on upload.
TARGET_LUFS = -16.0
TARGET_PEAK_DB = -1.5

#: Where the voice is put before the gate and the compressor see it.
#:
#: These filters work to absolute thresholds, and our recordings arrive with
#: the voice near -43 dBFS - far below a compressor threshold of -18 dBFS, so
#: it never engaged at all. The whole -28 dB of make-up then landed on the
#: limiter at the end, which pulled the peaks back down and left the file at
#: -20.3 LUFS instead of -16. Setting the working level first lets the
#: dynamics do their job and leaves only a small trim at the end.
WORK_SPEECH_DB = -20.0

PROBE_RATE = 16000          # envelope scan: speech detail needs no more
HUM_WINDOWS = 3             # quiet stretches sampled for hum
HUM_SECONDS = 2.0

#: How hard each setting pushes. Everything else is measured.
STRENGTHS = {
    "light":  {"nr": 8.0,  "gate": 0.00, "deess": 0.6, "presence": 1.5, "air": 0.0, "level": False},
    "medium": {"nr": 14.0, "gate": 0.35, "deess": 1.0, "presence": 2.5, "air": 1.0, "level": False},
    "strong": {"nr": 22.0, "gate": 0.70, "deess": 1.2, "presence": 3.0, "air": 1.6, "level": True},
    "voice":  {"nr": 30.0, "gate": 1.00, "deess": 1.4, "presence": 3.5, "air": 2.4, "level": True},
}


class CleanupError(RuntimeError):
    pass


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise CleanupError("ffmpeg is not installed, so audio cleanup cannot run")
    return exe


def _ffprobe() -> str:
    return shutil.which("ffprobe") or _ffmpeg().replace("ffmpeg", "ffprobe")


def _db(x: float) -> float:
    return 20.0 * math.log10(max(float(x), 1e-12))


def _lin(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def _progress(fn):
    """Accept both `say(message)` and `say(message, pct=…)` callbacks, so a
    plain `print` works as well as the panel's reporter."""
    def say(message, pct=None):
        if fn is None:
            return
        try:
            fn(message, pct=pct)
        except TypeError:
            fn(message)
    return say


# ------------------------------------------------------------------ probing
def probe(path: str | Path) -> dict:
    """Length, rate and channel count of the recording."""
    out = subprocess.run(
        [_ffprobe(), "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=sample_rate,channels,codec_name:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True)
    try:
        data = json.loads(out.stdout)
        stream = (data.get("streams") or [{}])[0]
    except (json.JSONDecodeError, IndexError):
        raise CleanupError(f"Could not read any audio from {Path(path).name}")
    if not stream:
        raise CleanupError(f"{Path(path).name} has no audio track")
    return {
        "rate": int(stream.get("sample_rate") or 48000),
        "channels": int(stream.get("channels") or 1),
        "codec": stream.get("codec_name") or "",
        "duration": float((data.get("format") or {}).get("duration") or 0.0),
    }


def _decode(path: str | Path, rate: int, start: float | None = None,
            seconds: float | None = None, chunk: int = 1 << 20, af: str = ""):
    """Mono float32 samples, straight out of ffmpeg."""
    cmd = [_ffmpeg(), "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if seconds is not None:
        cmd += ["-t", f"{seconds:.3f}"]
    cmd += ["-map", "0:a:0"]
    if af:
        cmd += ["-af", af]
    cmd += ["-ac", "1", "-ar", str(rate), "-f", "f32le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    tail = b""
    try:
        while True:
            block = proc.stdout.read(chunk)
            if not block:
                break
            block = tail + block
            usable = len(block) - (len(block) % 4)
            tail = block[usable:]
            if usable:
                yield np.frombuffer(block[:usable], dtype="<f4")
    finally:
        proc.stdout.close()
        proc.wait()


def _envelope(path: str | Path, rate: int = PROBE_RATE, hop: float = 0.02):
    """Per-frame level in dB - once for the recording as it stands, and once
    with everything below 200 Hz taken out.

    The second one matters: hum and rumble sit under the voice and are removed
    by the first filters in the chain, so a noise floor measured with them
    still in it would tell the denoiser the hiss is far louder than it is, and
    it would cut into the voice. The high-passed floor is the hiss alone.
    """
    from scipy import signal

    step = max(1, int(rate * hop))
    sos = signal.butter(2, 200.0, btype="highpass", fs=rate, output="sos")
    zi = signal.sosfilt_zi(sos) * 0.0
    frames, hp_frames, clipped, total = [], [], 0, 0
    carry = np.zeros(0, dtype="<f4")
    for block in _decode(path, rate):
        hp, zi = signal.sosfilt(sos, block.astype(np.float64), zi=zi)
        carry = np.concatenate([carry, block])
        _stash_hp(hp_frames, hp, step)
        n = (len(carry) // step) * step
        if n:
            rows = carry[:n].reshape(-1, step)
            frames.append(np.sqrt((rows.astype(np.float64) ** 2).mean(axis=1)))
            clipped += int((np.abs(rows) >= 0.995).sum())
            total += int(rows.size)
            carry = carry[n:]
    if not frames:
        raise CleanupError("That file decoded to no audio at all")
    with np.errstate(divide="ignore"):
        levels = 20.0 * np.log10(np.maximum(np.concatenate(frames), 1e-12))
        hp_levels = 20.0 * np.log10(np.maximum(np.concatenate(hp_frames), 1e-12)) \
            if hp_frames else levels
    return levels, hp_levels, (clipped / total if total else 0.0)


def _stash_hp(out: list, hp: np.ndarray, step: int) -> None:
    """Frame levels of the high-passed stream, in step with the main one."""
    n = (len(hp) // step) * step
    if n:
        out.append(np.sqrt((hp[:n].reshape(-1, step) ** 2).mean(axis=1)))


def _hum(path: str | Path, rate: int, quiet_at: list[float]) -> list[dict]:
    """Mains hum, found by looking for a narrow peak standing above its
    neighbours at 50 or 60 Hz - and only reporting the harmonics that are
    really there."""
    spectra = []
    for at in quiet_at[:HUM_WINDOWS]:
        samples = np.concatenate(list(_decode(path, rate, at, HUM_SECONDS)) or [np.zeros(1, "<f4")])
        if len(samples) < rate // 2:
            continue
        n = 1 << int(math.ceil(math.log2(len(samples))))
        window = np.hanning(len(samples))
        spectra.append(np.abs(np.fft.rfft(samples * window, n)) / len(samples))
    if not spectra:
        return []
    size = min(len(s) for s in spectra)
    mag = np.median(np.array([s[:size] for s in spectra]), axis=0)
    freqs = np.fft.rfftfreq((size - 1) * 2, 1.0 / rate)

    found = []
    for base in (50.0, 60.0):
        hits = []
        for k in range(1, 7):
            f = base * k
            if f >= rate / 2 - 20:
                break
            near = (freqs > f - 2.5) & (freqs < f + 2.5)
            around = ((freqs > f - 40) & (freqs < f - 6)) | ((freqs > f + 6) & (freqs < f + 40))
            if not near.any() or not around.any():
                continue
            peak, floor = float(mag[near].max()), float(np.median(mag[around]))
            over = _db(peak) - _db(floor)
            # Above 200 Hz a notch starts to sit in the voice, so ask for
            # clearer evidence before cutting one there.
            if over >= (8.0 if f <= 200.0 else 16.0):
                hits.append({"freq": round(f, 1), "over": round(over, 1)})
        if hits and hits[0]["freq"] == base:
            found.append({"base": base, "lines": hits})
    if not found:
        return []
    best = max(found, key=lambda h: sum(x["over"] for x in h["lines"]))
    return best["lines"]


def _reverb(levels: np.ndarray, floor_db: float, speech_db: float, hop: float = 0.02) -> float:
    """Roughly how long the room rings, in seconds.

    Measured as a decay slope rather than a fixed 20 dB fall: a sermon
    recording often has only 15 dB of quiet underneath the voice, and
    demanding a full 20 dB throws away every usable phrase ending. The slope
    is fitted over the tail and then quoted as the RT20 it implies.
    """
    ref_floor = max(float(floor_db), -90.0)        # digital silence is not a floor
    loud = levels > (speech_db - 9.0)
    look = max(1, int(0.06 / hop))
    span = max(4, int(0.8 / hop))
    rates = []
    for i in range(look, len(levels) - 1):
        if not (loud[i - 1] and not loud[i]):
            continue
        start = float(levels[i - look:i].max())
        tail = levels[i:i + span]
        settled = tail <= ref_floor + 3.0
        if not settled.any():                      # never settled: speech carried on
            continue
        n = max(4, int(np.argmax(settled)))
        piece = np.concatenate([[start], tail[:n]])
        if len(piece) < 4:
            continue
        slope = float(np.polyfit(np.arange(len(piece)) * hop, piece, 1)[0])
        if slope < -3.0:
            rates.append(20.0 / -slope)
    if len(rates) < 4:
        return 0.0
    return float(np.median(rates))


def _gap_mask(levels: np.ndarray, speech_db: float, hop: float = 0.02,
              reach: float = 1.5) -> np.ndarray:
    """Frames that are quiet but sit right beside speech - the gaps between
    phrases, which is where the noise you can actually hear under the voice
    shows itself."""
    loud = levels > speech_db - 12.0
    span = max(1, int(reach / hop))
    near = np.convolve(loud.astype(float), np.ones(2 * span + 1), mode="same") > 0
    return near & ~loud


def _floor_of(pool: np.ndarray, speech_db: float,
              min_share: float = 0.05) -> tuple[float, float, bool]:
    """The noise floor in those gaps, how steady it is, and whether it is real.

    Two things were learned here from real recordings. A low percentile of the
    whole file measures the dead air before and after the service, not the
    room - on a 75-minute sermon that read -90 dB and called a noisy recording
    quiet. And taking the quietest populated level anywhere in the file is no
    better: this church's mixer gates the mic, so the quietest populated level
    during the preaching is digital silence while the noise that matters sits
    under the voice. Looking only at the gaps between phrases measures the
    right thing.

    The steadiness that comes back is how much of those gaps sit at that
    level. Room tone is continuous and nearly all of them will; a gated
    channel gives a thin band, and leaning hard on the denoiser there buys
    nothing and risks chewing the voice.
    """
    # Gaps that are digital silence hold no noise to measure, so the level is
    # read off the ones that are not - but steadiness is judged against all of
    # them, because a channel that is silent between half its phrases has
    # noise under the voice only half the time.
    cut = max(-85.0, speech_db - 50.0)
    live = pool[pool > cut]
    if pool.size < 50 or live.size < 50:
        return speech_db - 45.0, 0.0, False
    bins = np.arange(np.floor(live.min()), np.ceil(live.max()) + 1.0, 1.0)
    hist, edges = np.histogram(live, bins=bins)
    share = hist / max(1, hist.sum())
    hit = np.nonzero(share >= min_share)[0]
    floor = float(edges[hit[0]] + 0.5) if hit.size else float(np.percentile(live, 10))
    steady = float(((pool > floor - 3.0) & (pool < floor + 3.0)).mean())
    known = (speech_db - floor) >= 10.0 and floor > -85.0
    if not known:
        floor = speech_db - 45.0            # nothing steady there to subtract
        steady = 0.0
    return floor, steady, known


def _tilt(path: str | Path, rate: int, speech_at: list[float]) -> float:
    """How bright the mic is: 6-10 kHz against 1-4 kHz, in dB, over frames
    where someone is actually speaking.

    This is deliberately not a harshness detector. Real speech has consonant
    transients 15-20 dB above the voice body, and without a reference corpus
    there is no honest absolute line between "crisp" and "harsh" - the first
    attempt here rated a real sermon worse than audio with harshness put in on
    purpose. What this does measure reliably is whether the microphone rolls
    off at the top, which is what decides whether adding air would help.
    """
    from scipy import signal

    vals = []
    for at in speech_at[:8]:
        samples = np.concatenate(list(_decode(path, rate, at, 3.0)) or [np.zeros(1, "<f4")])
        if len(samples) < rate:
            continue
        freqs, _, spec = signal.stft(samples, fs=rate, nperseg=1024, noverlap=512)
        power = np.abs(spec) ** 2
        hi = power[(freqs >= 6000) & (freqs <= 10000)].sum(axis=0)
        mid = power[(freqs >= 1000) & (freqs <= 4000)].sum(axis=0)
        speaking = mid > np.percentile(mid, 60)
        if speaking.sum() < 10:
            continue
        vals.append(float(np.median(10.0 * np.log10(
            np.maximum(hi[speaking], 1e-20) / np.maximum(mid[speaking], 1e-20)))))
    return float(np.median(vals)) if vals else -11.0


def _quiet_windows(levels: np.ndarray, speech_db: float, duration: float,
                   count: int, seconds: float = HUM_SECONDS,
                   hop: float = 0.02) -> list[float]:
    """Stretches that are quiet for their whole length.

    Picking the single quietest frame is not enough: a two-second window
    starting at a gap between phrases runs straight into the next sentence,
    and the hum test would then measure the voice.
    """
    width = max(1, int(seconds / hop))
    if len(levels) <= width:
        return []
    power = np.power(10.0, levels / 10.0)
    box = np.convolve(power, np.ones(width) / width, mode="valid")
    with np.errstate(divide="ignore"):
        mean_db = 10.0 * np.log10(np.maximum(box, 1e-24))
    limit = speech_db - 15.0                       # must be well under the voice
    picked: list[float] = []
    for i in np.argsort(mean_db):
        if mean_db[i] > limit:
            break
        at = float(i) * hop
        if at + seconds > max(0.0, duration - 0.05):
            continue
        if all(abs(at - p) > 20.0 for p in picked):
            picked.append(round(at, 2))
        if len(picked) >= count:
            break
    return picked


def _speech_windows(levels: np.ndarray, speech_db: float, duration: float,
                    count: int, hop: float = 0.02) -> list[float]:
    """Moments of ordinary speech, spread right across the service.

    Two things here were learned from a real recording. Not the loudest
    moments: those turn out to be applause or the band, which measure nothing
    useful about the preaching voice. And not simply the first few that
    qualify: that put every window inside the opening three minutes, so the
    loudness of a 75-minute sermon was being set from the announcements. The
    file is divided into equal sections instead and one ordinary speaking
    moment is taken from each.
    """
    target = float(np.percentile(levels, 90))
    out: list[float] = []
    n = len(levels)
    sections = max(1, count)
    for k in range(sections):
        lo, hi = int(n * k / sections), int(n * (k + 1) / sections)
        if hi <= lo:
            continue
        seg = levels[lo:hi]
        idx = lo + int(np.argmin(np.abs(seg - target)))
        if abs(float(levels[idx]) - target) > 6.0:
            continue                       # nothing like speech in this stretch
        at = min(float(idx) * hop, max(0.0, duration - 3.05))
        if all(abs(at - p) > 5.0 for p in out):
            out.append(round(at, 2))
    _ = speech_db
    return out


def profile(path: str | Path, progress=None) -> dict:
    """Listen to the whole recording and report what needs fixing."""
    say = _progress(progress)
    info = probe(path)
    say("Listening to the recording")
    levels, hp_levels, clipping = _envelope(path)
    hop = 0.02

    speech_db = float(np.percentile(levels, 95))
    peak_db = float(levels.max())
    gaps = _gap_mask(levels, speech_db, hop)
    floor_db, steady, known = _floor_of(levels[gaps], speech_db)
    size = min(len(levels), len(hp_levels))
    hiss_db, _steady, hiss_known = _floor_of(hp_levels[:size][gaps[:size]], speech_db)
    if not hiss_known:
        hiss_db = min(hiss_db, floor_db)
    # Dead air: the recorder running with nothing on the mic, which is not
    # noise and must not be mistaken for the room.
    dead = float((levels <= max(-85.0, speech_db - 50.0)).mean())

    quiet_at = _quiet_windows(levels, speech_db, info["duration"], HUM_WINDOWS, hop=hop)
    speech_at = _speech_windows(levels, speech_db, info["duration"], 8, hop=hop)

    say("Checking for hum")
    hum = _hum(path, info["rate"], quiet_at)
    say("Measuring the room")
    reverb = _reverb(levels, floor_db, speech_db, hop)
    tilt = _tilt(path, info["rate"], speech_at)

    return {
        **info,
        "floor_db": round(floor_db, 1),
        "hiss_db": round(hiss_db, 1),
        "floor_known": bool(known),
        "steady": round(steady, 3),
        "dead_air": round(dead, 4),
        "speech_db": round(speech_db, 1),
        "peak_db": round(peak_db, 1),
        "snr_db": round(speech_db - floor_db, 1),
        "clipping": round(clipping, 6),
        "hum": hum,
        "reverb": round(reverb, 3),
        "tilt_db": round(tilt, 1),
        "speech_at": speech_at,
        "quiet_at": quiet_at,
        "model": model_path(),
    }


def _spread(times: list[float], count: int, duration: float) -> list[float]:
    """Pick moments that are not all in the same breath, and that leave room
    for the window that will be read from them."""
    picked: list[float] = []
    for t in times:
        t = max(0.0, min(t, max(0.0, duration - HUM_SECONDS - 0.05)))
        if all(abs(t - p) > 20.0 for p in picked):
            picked.append(t)
        if len(picked) >= count:
            break
    if not picked and duration > 0:
        picked = [max(0.0, min(duration / 3.0, duration - HUM_SECONDS - 0.05))]
    return [round(t, 2) for t in picked]


def describe(prof: dict) -> list[str]:
    """The profile, in words the panel can show."""
    out = []
    snr = prof["snr_db"]
    if not prof.get("floor_known", True):
        out.append("No steady background noise to measure — denoise kept gentle")
    elif prof.get("steady", 1.0) < 0.5:
        out.append(f"Background noise comes and goes — {snr:.0f} dB under the voice where "
                   "it is there, so denoise is kept gentle")
    else:
        out.append(("Very noisy" if snr < 20 else "Some background noise" if snr < 35
                    else "Quiet recording")
                   + f" — voice sits {snr:.0f} dB above the room")
    if prof["hum"]:
        lines = prof["hum"]
        out.append(f"Mains hum at {lines[0]['freq']:.0f} Hz"
                   + (f" and {len(lines) - 1} harmonic(s)" if len(lines) > 1 else ""))
    rev = prof["reverb"]
    if rev >= 0.55:
        out.append(f"Live room — about {rev:.1f}s of ring-off")
    elif rev >= 0.3:
        out.append(f"A little room on the voice ({rev:.1f}s)")
    tilt = prof.get("tilt_db", -11.0)
    if tilt < -14.0:
        out.append("Dull microphone — air added back")
    elif tilt > -9.0:
        out.append("Bright microphone — no air added")
    if prof.get("dead_air", 0) > 0.1:
        out.append(f"{prof['dead_air'] * 100:.0f}% of the file is dead air "
                   "(before and after the service)")
    if prof["clipping"] > 0.0005:
        out.append(f"Clipped in places ({prof['clipping'] * 100:.2f}% of samples)")
    quiet = prof.get("speech_db", 0) < -35.0
    if quiet:
        out.append("Recorded quietly — the level is brought up to broadcast loudness")
    if prof["model"]:
        out.append("Neural denoise model installed")
    return out


# ------------------------------------------------------------------- chain
def model_path() -> str:
    """An RNNoise model, if one has been put in data/models."""
    if MODELS.is_dir():
        for p in sorted(MODELS.glob("*.rnnn")):
            return str(p)
    return ""


def chain(prof: dict, strength: str = "medium", dereverb: bool | None = None) -> list[str]:
    """The filters to apply, in order, for this recording at this strength."""
    if strength not in STRENGTHS:
        raise CleanupError(f"Unknown strength {strength!r}; "
                           f"choose from {', '.join(STRENGTHS)}")
    s = STRENGTHS[strength]
    parts: list[str] = []

    if prof.get("clipping", 0.0) > 0.0005:
        # Rebuild the flattened peaks before anything else reads them as level.
        parts.append("adeclip=window=55:overlap=75:arorder=8:threshold=10:hsize=1000:method=add")

    # Rumble: lectern knocks, air handling, footsteps on a hollow stage.
    parts.append("highpass=f=80:p=2")

    for line in prof.get("hum", []):
        depth = min(30.0, max(10.0, line["over"] + 6.0))
        parts.append(f"equalizer=f={line['freq']:g}:t=q:w=28:g=-{depth:.0f}")

    # Broadband noise. A neural model does this far better than spectral
    # subtraction, so it is used instead when one is available. This stays
    # ahead of the working gain below, because the floor it is given is an
    # absolute level measured on the recording as it arrived.
    nr = s["nr"]
    if not prof.get("floor_known", True):
        nr = min(nr, 6.0)                 # no measured room tone: do not guess
    elif prof.get("snr_db", 99) < 18:
        nr = min(40.0, nr + 6.0)          # noisier than usual: lean on it harder
    elif prof.get("snr_db", 0) > 45:
        nr = max(4.0, nr - 4.0)           # already clean: stay out of the way
    # A floor that only shows up in a fraction of the gaps is a gated channel,
    # not room tone, and there is little there to subtract.
    steady = prof.get("steady", 1.0)
    if prof.get("floor_known", True) and steady < 0.5:
        nr = max(4.0, nr * max(0.4, steady / 0.5))
    model = prof.get("model") or ""
    if model:
        mix = min(1.0, 0.55 + nr / 60.0)
        parts.append(f"arnndn=m={_escape(model)}:mix={mix:.2f}")
    else:
        # The hiss floor, not the overall floor: see `_envelope`.
        floor = min(-20.0, max(-80.0, prof.get("hiss_db", prof.get("floor_db", -50.0)) + 3.0))
        parts.append(f"afftdn=nr={nr:.0f}:nf={floor:.0f}:tn=1")

    # Bring the voice up to a working level before anything that reacts to
    # level sees it: see WORK_SPEECH_DB.
    work = max(0.0, min(40.0, WORK_SPEECH_DB - prof.get("speech_db", WORK_SPEECH_DB)))
    if work > 0.1:
        parts.append(f"volume={work:.2f}dB")

    # The room. There is no true dereverb here: this pulls down the tails
    # between words, which is what makes a live hall sound closer.
    want = prof.get("reverb", 0.0) >= 0.3 if dereverb is None else dereverb
    if want and s["gate"] > 0 and prof.get("floor_known", True):
        lean = s["gate"] * min(1.0, prof.get("reverb", 0.0) / 0.8)
        floor_db = prof.get("floor_db", -50.0)
        spread = max(6.0, prof.get("speech_db", -20.0) - floor_db)
        # Sit the threshold between the room and the voice, never near it:
        # a gate set at speaking level chews the ends off quiet sentences.
        # Shifted by the working gain, since that is what the gate now hears.
        threshold = _lin(floor_db + min(12.0, max(4.0, spread * 0.35)) + work)
        release = int(max(120, min(400, prof.get("reverb", 0.4) * 450)))
        parts.append(f"agate=threshold={max(0.0005, min(0.2, threshold)):.4f}"
                     f":range={max(0.05, 1.0 - 0.75 * lean):.2f}"
                     f":ratio=2:attack=12:release={release}:knee=4")

    # De-essing is set by the chosen strength, nudged by how bright the mic
    # is - not by a harshness detector, for the reason given in `_tilt`.
    tilt = prof.get("tilt_db", -11.0)
    if s["deess"] > 0:
        amount = 0.18 * s["deess"] * (1.3 if tilt > -9.0 else 0.75 if tilt < -14.0 else 1.0)
        parts.append(f"deesser=i={min(0.5, amount):.2f}:m=0.5:f=0.5:s=o")

    # Tone: take the boxiness out, bring the consonants forward.
    parts.append("equalizer=f=300:t=q:w=1.2:g=-2")
    parts.append(f"equalizer=f=2800:t=q:w=1.0:g={s['presence']:.1f}")
    # Harmonics above the voice, which is how a dull mic gets its sparkle
    # back - the nearest honest thing to Podcast's "restoration". A mic that
    # is already bright does not need it and only sounds fizzy with it.
    air = s["air"] * (0.0 if tilt > -9.0 else 0.5 if tilt > -14.0 else 1.0)
    if air > 0 and prof.get("rate", 48000) >= 32000:
        parts.append(f"aexciter=level_in=1:level_out=1:amount={air:.1f}"
                     ":drive=4:blend=0:freq=7500:ceil=15000")

    # Even out the delivery: quiet asides up, shouted lines down.
    parts.append("acompressor=level_in=1:threshold=0.126:ratio=3:attack=10"
                 ":release=180:makeup=1:knee=3:detection=rms")
    if s["level"]:
        parts.append("speechnorm=p=0.92:e=4:r=0.0003")
    return parts


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _graph(parts: list[str]) -> str:
    return ",".join(parts)


# ------------------------------------------------------------------ running
def _measure(path: str | Path, parts: list[str], progress=None,
             duration: float = 0.0, start: float | None = None,
             seconds: float | None = None) -> dict:
    """What the processed recording measures, by EBU R128.

    Measured over the whole thing, in a pass of its own, because that is what
    the target is defined over: sampling speech-heavy spots instead read a
    75-minute sermon 4.7 LU too loud, and the finished file sat at -20.7 LUFS.
    """
    say = _progress(progress)
    cmd = [_ffmpeg(), "-v", "info", "-hide_banner", "-nostats"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if seconds is not None:
        cmd += ["-t", f"{seconds:.3f}"]
    cmd += ["-map", "0:a:0",
            "-af", _graph(parts + [f"loudnorm=I={TARGET_LUFS}:TP={TARGET_PEAK_DB}"
                                   ":LRA=11:print_format=json"]),
            "-progress", "pipe:1", "-f", "null", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    last = -1.0
    for line in proc.stdout:
        if not line.startswith(("out_time_us", "out_time_ms")):
            continue
        try:
            done = int(line.split("=", 1)[1]) / (1e6 if "us" in line else 1e3)
        except ValueError:
            continue
        if duration > 0 and done - last >= 10.0:
            last = done
            say(f"Measuring the level — {done / 60:.0f} of {duration / 60:.0f} min",
                pct=min(0.49, 0.5 * done / duration))
    err = proc.stderr.read()
    proc.wait()
    found = {}
    for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        hits = re.findall(rf'"{key}"\s*:\s*"(-?[\d.]+|-?inf)"', err)
        if hits and hits[-1] not in ("-inf", "inf"):
            found[key] = float(hits[-1])
    if "input_i" not in found:
        raise CleanupError("Could not measure the loudness of the processed audio")
    return found


def _level_stage(measured: dict | None) -> list[str]:
    """Set the finished loudness: a measured gain, then a limiter.

    loudnorm can do this itself and lands closer to the target, but how much
    it delays the audio depends on whether it normalises linearly or falls
    back to its dynamic mode - one strength came out 19 ms late and another
    exactly on time. A gain and a limiter delay by a fixed amount that can be
    measured once and taken off, which matters more here than the last half a
    LU: the cleaned file has to sit against the camera frame-accurately.

    The limiter is set a dB below the true-peak target because it works on
    sample peaks, and reconstructed true peaks run a little above them. The
    result lands around -16.5 LUFS and -1.1 dBTP.
    """
    if not measured:
        return [f"loudnorm=I={TARGET_LUFS}:TP={TARGET_PEAK_DB}:LRA=11"]
    gain = max(-30.0, min(30.0, TARGET_LUFS - measured["input_i"]))
    return [f"volume={gain:.2f}dB",
            f"alimiter=level_in=1:level_out=1:limit={_lin(TARGET_PEAK_DB - 1.0):.4f}"
            ":attack=5:release=50:level=disabled"]


def _latency(path: str | Path, parts: list[str], prof: dict,
             window: float = 12.0, limit_ms: float = 150.0) -> int:
    """How many samples late the filtered sound comes out.

    Filters with an FFT inside them - `afftdn` here, and any neural model
    added later - delay what passes through, and ffmpeg does not report it.
    Measured on this chain it came to 1439 samples, almost exactly 30 ms:
    about a frame at 30 fps. Left uncorrected that pushes the cleaned audio
    out of sync with the camera and shifts every cue timing with it, so the
    delay is measured on a few seconds of the recording itself and taken back
    off the result.

    The recording is used rather than a synthetic click because the gate and
    the compressor react to level, and a test tone would not tell the truth
    about how they behave on speech.
    """
    rate = int(prof.get("rate") or 48000)
    at = (prof.get("speech_at") or [0.0])[0]
    dry = np.concatenate(list(_decode(path, rate, at, window)) or [np.zeros(1, "<f4")])
    wet = np.concatenate(list(_decode(path, rate, at, window, af=_graph(parts)))
                         or [np.zeros(1, "<f4")])
    n = min(len(dry), len(wet))
    edge = int(rate * limit_ms / 1000.0)
    if n < edge * 4:
        return 0
    dry = dry[:n].astype(np.float64)
    wet = wet[:n].astype(np.float64)
    dry -= dry.mean()
    wet -= wet.mean()
    if not np.any(dry) or not np.any(wet):
        return 0
    corr = np.correlate(wet, dry[edge:-edge], mode="valid")
    lag = int(np.argmax(np.abs(corr))) - edge
    return lag if 0 < lag <= edge else 0


def plan(path: str | Path, strength: str = "medium", dereverb: bool | None = None,
         progress=None, exact: bool = True) -> dict:
    """Everything the run needs: the profile, the filters and the final gain.

    `exact` measures the loudness over the whole recording, which is what the
    file that gets written needs; a preview passes False and takes the sampled
    estimate instead, since its two halves only have to match each other.
    """
    say = _progress(progress)
    prof = profile(path, progress=say)
    parts = chain(prof, strength, dereverb)
    measured = None
    if exact:
        say("Setting the level")
        measured = _measure(path, parts, progress=say, duration=prof.get("duration", 0.0))
    gain = round(TARGET_LUFS - measured["input_i"], 2) if measured else 0.0
    full = parts + _level_stage(measured)
    lag = 0
    if exact:
        say("Checking the result still lines up")
        lag = _latency(path, full, prof)
        if lag:
            # Take the filters' own delay back off, and put the same number of
            # samples back at the end so the file is exactly as long as it was.
            # No `asetpts` here: renumbering the timestamps defeats ffmpeg's
            # own output duration limit, which a preview depends on.
            full = full + [f"atrim=start_sample={lag}", f"apad=pad_len={lag}"]
    return {"profile": prof, "filters": parts, "gain_db": gain,
            "measured_lufs": round(measured["input_i"], 2) if measured else None,
            "graph": _graph(full), "latency_samples": lag,
            "strength": strength, "notes": describe(prof)}


def run(path: str | Path, out: str | Path, strength: str = "medium",
        dereverb: bool | None = None, progress=None, ready: dict | None = None) -> dict:
    """Clean the whole recording and write a WAV that After Effects can use."""
    say = _progress(progress)
    source, out = Path(path), Path(out)
    if not source.exists():
        raise CleanupError(f"No such file: {source}")
    spec = ready or plan(source, strength, dereverb, progress=say)
    out.parent.mkdir(parents=True, exist_ok=True)

    rate = max(44100, min(48000, spec["profile"]["rate"]))
    duration = spec["profile"]["duration"]
    tmp = out.with_suffix(".part.wav")
    # The channel count is left as it came in. Writing mono from a stereo
    # recording cost 3 LU against the target: R128 counts both channels of a
    # dual-mono pair, so the measurement pass read 3 dB louder than the mono
    # file it was setting the level for.
    cmd = [_ffmpeg(), "-v", "error", "-nostats", "-y", "-i", str(source),
           "-map", "0:a:0", "-af", spec["graph"],
           "-ar", str(rate), "-c:a", "pcm_s24le",
           "-progress", "pipe:1", str(tmp)]
    say("Cleaning the audio")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    last = -1.0
    for line in proc.stdout:
        if not line.startswith("out_time_us") and not line.startswith("out_time_ms"):
            continue
        try:
            done = int(line.split("=", 1)[1]) / (1e6 if "us" in line else 1e3)
        except ValueError:
            continue
        if duration > 0 and done - last >= 5.0:
            last = done
            say(f"Cleaning the audio — {done / 60:.0f} of {duration / 60:.0f} min",
                pct=min(0.99, 0.5 + 0.5 * done / duration))
    err = proc.stderr.read()
    proc.wait()
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise CleanupError(err.strip().split("\n")[-1] if err.strip()
                           else "ffmpeg could not clean that file")
    tmp.replace(out)
    return {"path": str(out), "strength": strength, "gain_db": spec["gain_db"],
            "channels": spec["profile"].get("channels", 1),
            "latency_samples": spec.get("latency_samples", 0),
            "measured_lufs": spec["measured_lufs"], "target_lufs": TARGET_LUFS,
            "notes": spec["notes"], "profile": spec["profile"],
            "filters": spec["filters"], "duration": duration, "rate": rate}


def preview(path: str | Path, out_dir: str | Path, at: float | None = None,
            seconds: float = 20.0, strength: str = "medium",
            dereverb: bool | None = None, progress=None) -> dict:
    """The same stretch before and after, so the result can be judged on a
    few seconds instead of an hour."""
    say = _progress(progress)
    source, out_dir = Path(path), Path(out_dir)
    spec = plan(source, strength, dereverb, progress=say, exact=False)
    duration = spec["profile"]["duration"]
    if at is None:
        at = (spec["profile"]["speech_at"] or [max(0.0, duration / 3.0)])[0]
    at = max(0.0, min(float(at), max(0.0, duration - 1.0)))
    seconds = max(2.0, min(float(seconds), max(2.0, duration - at)))
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = source.stem
    before, after = out_dir / f"{stem} before.wav", out_dir / f"{stem} after.wav"
    say("Rendering the before and after")
    # Both halves are levelled, each from its own measurement of this window,
    # and nothing else is done to the before one. Against a raw original the
    # cleaned version would simply sound louder - this recording needed +28 dB
    # - which tells you nothing about the cleaning. Matched, the only
    # difference left to hear is the cleanup itself.
    halves = (
        (before, _level_stage(_measure(source, [], start=at, seconds=seconds))),
        (after, spec["filters"] + _level_stage(
            _measure(source, spec["filters"], start=at, seconds=seconds))),
    )
    for target, graph in halves:
        cmd = [_ffmpeg(), "-v", "error", "-nostats", "-y", "-ss", f"{at:.3f}",
               "-i", str(source), "-t", f"{seconds:.3f}", "-map", "0:a:0",
               "-af", _graph(graph), "-ar", "48000", "-c:a", "pcm_s16le", str(target)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise CleanupError(res.stderr.strip().split("\n")[-1] or "preview failed")
    return {"before": str(before), "after": str(after), "at": round(at, 2),
            "seconds": round(seconds, 2), "strength": strength,
            "notes": spec["notes"], "gain_db": spec["gain_db"],
            "profile": spec["profile"]}
