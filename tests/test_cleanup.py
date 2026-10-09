"""Audio cleanup: the measurements, the chain it builds, and the sound it makes.

The second half of this file is the part that matters. It generates a
recording with known faults put in on purpose - mains hum at 50 Hz and its
harmonics, hiss, rumble, a quiet room tone, dead air at both ends - runs the
real ffmpeg chain over it, and measures the result. A chain that looks
sensible on paper and removes nothing would pass a mocked test and fail here.

Some of these cases are faults a real sermon recording showed up: a noise
floor measured as digital silence rather than room tone, and every
measurement window landing inside the opening announcements.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
import cleanup as C  # noqa: E402

passed = failed = 0


def ok(name, cond, got=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {name}  {got}")


def near(a, b, tol):
    return abs(float(a) - float(b)) <= tol


# --- the measurements, on envelopes built by hand -------------------------
HOP = 0.02


def envelope(parts):
    """A level track in dB: (level, seconds) pairs."""
    out = []
    for level, seconds in parts:
        out.extend([level] * max(1, int(seconds / HOP)))
    return np.array(out, dtype=float)


# A service: dead air, then phrases over room tone, then dead air.
SERVICE = envelope([(-100.0, 60.0)]
                   + [(-20.0, 3.0), (-55.0, 1.2)] * 60
                   + [(-100.0, 60.0)])


def floor_of(levels, speech_db):
    """What `profile` does: measure the gaps between phrases."""
    gaps = C._gap_mask(levels, speech_db, HOP)
    return C._floor_of(levels[gaps], speech_db)


floor, steady, known = floor_of(SERVICE, -20.0)
ok("room tone is found, not the dead air", near(floor, -55.0, 1.5), floor)
ok("a steady room tone is seen to be steady", steady > 0.8, steady)
ok("a measured floor is trusted", known is True)
dead = float((SERVICE <= max(-85.0, -20.0 - 50.0)).mean())
ok("dead air is reported", near(dead, 120.0 / (120.0 + 60 * 4.2), 0.03), dead)

# A mixer that gates the mic: the gaps between phrases are digital silence,
# so there is no steady noise under the voice to subtract. This is what the
# church's own recording turned out to be, and the earlier whole-file
# estimate read it as a noisy room.
GATED = envelope([(-100.0, 30.0)] + [(-20.0, 3.0), (-100.0, 1.0)] * 40)
floor2, steady2, known2 = floor_of(GATED, -20.0)
ok("a gated channel is not mistaken for a noisy room", known2 is False, floor2)
ok("and the floor falls back well below the voice", floor2 <= -60.0, floor2)

# Noise that is only there some of the time: real, but not worth leaning on.
PATCHY = envelope([(-20.0, 3.0), (-55.0, 1.2)] * 20 + [(-20.0, 3.0), (-100.0, 1.2)] * 20)
floor3, steady3, known3 = floor_of(PATCHY, -20.0)
ok("noise that comes and goes is found", known3 and near(floor3, -55.0, 2.0), floor3)
ok("...but is reported as unsteady", 0.2 < steady3 < 0.8, steady3)

# Reverb, from decays built to a known slope.
dry = envelope([(-20.0, 1.0), (-60.0, 1.0)] * 12)
live = []
for _ in range(12):
    live.append((-20.0, 1.0))
    live.extend([(-20.0 - d, 0.04) for d in range(2, 42, 2)])   # 20 dB over 0.4 s
    live.append((-60.0, 0.6))
livelevels = envelope(live)
ok("a dry room measures short", C._reverb(dry, -60.0, -20.0) < 0.15, C._reverb(dry, -60.0, -20.0))
got_live = C._reverb(livelevels, -60.0, -20.0)
ok("a live room measures about its decay", 0.25 <= got_live <= 0.75, got_live)

# Quiet windows must not contain speech.
quiet = C._quiet_windows(SERVICE, -20.0, len(SERVICE) * HOP, 3)
ok("quiet windows are found", len(quiet) >= 1, quiet)
for at in quiet:
    frames = SERVICE[int(at / HOP):int((at + C.HUM_SECONDS) / HOP)]
    ok(f"the window at {at:.0f}s holds no speech", frames.max() < -40.0, frames.max())

# Speech windows must be spread across the whole file, not bunched at the top.
spread = C._speech_windows(SERVICE, -20.0, len(SERVICE) * HOP, 8)
ok("speech windows are found", len(spread) >= 4, spread)
ok("speech windows reach the far half of the file",
   bool(spread) and max(spread) > (len(SERVICE) * HOP) * 0.5, spread)


# --- the chain it builds --------------------------------------------------
def prof(**over):
    base = {"rate": 48000, "channels": 1, "codec": "pcm_s24le", "duration": 3600.0,
            "floor_db": -55.0, "hiss_db": -58.0, "floor_known": True, "dead_air": 0.1,
            "speech_db": -22.0, "peak_db": -8.0, "snr_db": 33.0, "clipping": 0.0,
            "hum": [], "reverb": 0.2, "tilt_db": -11.0, "steady": 1.0, "speech_at": [10.0],
            "quiet_at": [5.0], "model": ""}
    base.update(over)
    return base


def graph(**over):
    return ",".join(C.chain(prof(**over), over.pop("strength", "medium")))


ok("no hum found means no notch", "equalizer=f=50" not in graph())
humful = graph(hum=[{"freq": 50.0, "over": 24.0}, {"freq": 100.0, "over": 18.0}])
ok("hum is notched where it was heard",
   "equalizer=f=50:" in humful and "equalizer=f=100:" in humful, humful)
ok("the notch is deep but not unbounded", ":g=-30" in humful and ":g=-40" not in humful)

ok("clean recordings get a gentler denoise",
   float(graph(snr_db=50.0).split("afftdn=nr=")[1].split(":")[0])
   < float(graph(snr_db=12.0).split("afftdn=nr=")[1].split(":")[0]))
ok("the denoiser is told the hiss floor, not the overall floor",
   "nf=-55" in graph(hiss_db=-58.0, floor_db=-40.0), graph(hiss_db=-58.0, floor_db=-40.0))
ok("noise that comes and goes is denoised more gently than steady noise",
   float(graph(steady=0.2).split("afftdn=nr=")[1].split(":")[0])
   < float(graph(steady=1.0).split("afftdn=nr=")[1].split(":")[0]),
   (graph(steady=0.2), graph(steady=1.0)))
ok("an unmeasurable floor keeps the denoiser gentle",
   float(graph(floor_known=False, strength="voice").split("afftdn=nr=")[1].split(":")[0]) <= 6.0)

ok("a dry room is not gated", "agate" not in graph(reverb=0.1))
live_graph = graph(reverb=0.7)
ok("a live room is gated", "agate" in live_graph, live_graph)
gate_threshold = float(live_graph.split("agate=threshold=")[1].split(":")[0])
ok("the gate sits above the room tone", gate_threshold > C._lin(-55.0), gate_threshold)
ok("and well below speaking level", gate_threshold < C._lin(-22.0 - 6.0), gate_threshold)
ok("a room cannot be gated with no floor to gate against",
   "agate" not in graph(reverb=0.9, floor_known=False))
ok("dereverb can be forced on", "agate" in ",".join(C.chain(prof(reverb=0.05), "medium", dereverb=True)))
ok("dereverb can be forced off", "agate" not in ",".join(C.chain(prof(reverb=0.9), "medium", dereverb=False)))

ok("a dull mic has air added", "aexciter" in graph(tilt_db=-18.0))
ok("a bright mic does not", "aexciter" not in graph(tilt_db=-5.0))
ok("light touch adds no air at all", "aexciter" not in graph(tilt_db=-18.0, strength="light"))
ok("de-essing is gentle by default",
   float(graph().split("deesser=i=")[1].split(":")[0]) <= 0.25, graph())
ok("a bright mic is de-essed a little harder",
   float(graph(tilt_db=-5.0).split("deesser=i=")[1].split(":")[0])
   > float(graph(tilt_db=-18.0).split("deesser=i=")[1].split(":")[0]))

ok("clipping is repaired first", C.chain(prof(clipping=0.01))[0].startswith("adeclip"))
ok("rumble is always taken out", "highpass=f=80" in graph())
ok("levelling only on the strong settings",
   "speechnorm" not in graph() and "speechnorm" in graph(strength="voice"))
try:
    C.chain(prof(), "wild")
    ok("an unknown strength is refused", False)
except C.CleanupError:
    ok("an unknown strength is refused", True)

model_graph = ",".join(C.chain(prof(model="/tmp/a b/std.rnnn"), "medium"))
ok("a neural model replaces the spectral denoiser",
   "arnndn=m=" in model_graph and "afftdn" not in model_graph, model_graph)
ok("a path with spaces survives the filter graph", "/tmp/a b/std.rnnn" in model_graph)
ok("a colon in a model path is escaped", "a\\:b" in C._escape("a:b"))


# --- and now the sound itself --------------------------------------------
def build(path, rate=48000, seconds=48.0, hum=True, noise=True, room=False):
    """A recording with faults put in on purpose."""
    t = np.arange(int(rate * seconds)) / rate
    rng = np.random.default_rng(11)
    f0 = 115 + 25 * np.sin(2 * np.pi * 0.7 * t)
    voice = sum(np.sin(2 * np.pi * f0 * (k + 1) * t + k) / (k + 1.4) for k in range(14)) * 0.35
    voice += (np.convolve(rng.normal(0, 1, len(t)), np.ones(4) / 4, mode="same")
              * (np.sin(2 * np.pi * 3.1 * t) > 0.85) * 0.22)
    gate, edge = np.zeros_like(t), int(0.03 * rate)
    for start in np.arange(4.0, seconds - 3.0, 4.5):          # dead air at both ends
        a, b = int(start * rate), int((start + 2.8) * rate)
        shape = np.ones(b - a)
        shape[:edge] = np.linspace(0, 1, edge)
        shape[-edge:] = np.linspace(1, 0, edge)
        gate[a:b] = shape
    mix = voice * gate * 0.5
    if hum:
        mix = mix + (0.02 * np.sin(2 * np.pi * 50 * t) + 0.008 * np.sin(2 * np.pi * 100 * t)
                     + 0.004 * np.sin(2 * np.pi * 150 * t))
    if noise:
        mix = mix + rng.normal(0, 0.004, len(t))
        mix = mix + 0.03 * np.convolve(rng.normal(0, 1, len(t)), np.ones(600) / 600, mode="same")
    raw = (np.clip(mix, -1, 1) * (2 ** 23 - 1)).astype("<i4") << 8
    head = _wav_header(len(raw), rate)
    path.write_bytes(head + raw.astype("<i4").tobytes())
    if room:
        wet = path.with_name("wet-" + path.name)
        subprocess.run([C._ffmpeg(), "-v", "error", "-y", "-i", str(path), "-af",
                        "aecho=0.6:0.6:70|150|260:0.55|0.4|0.28", "-c:a", "pcm_s32le", str(wet)],
                       check=True)
        wet.replace(path)
    return path


def _wav_header(frames, rate):
    import struct
    data = frames * 4
    return (b"RIFF" + struct.pack("<I", 36 + data) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1,
            rate, rate * 4, 4, 32) + b"data" + struct.pack("<I", data))


def levels(path):
    lv, _hp, _clip = C._envelope(path)
    speech = float(np.percentile(lv, 95))
    floor, _steady, _known = C._floor_of(lv[C._gap_mask(lv, speech, HOP)], speech)
    return speech, floor


def tone_at(path, freq, at, seconds=1.8, rate=48000):
    x = np.concatenate(list(C._decode(path, rate, at, seconds)) or [np.zeros(1, "<f4")])
    mag = np.abs(np.fft.rfft(x * np.hanning(len(x)))) / max(1, len(x))
    fr = np.fft.rfftfreq(len(x), 1.0 / rate)
    band = (fr > freq - 3) & (fr < freq + 3)
    return C._db(mag[band].max()) if band.any() else -200.0


def loudness(path):
    res = subprocess.run([C._ffmpeg(), "-v", "info", "-hide_banner", "-nostats", "-i", str(path),
                          "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                         capture_output=True, text=True)
    out = {}
    for key in ("input_i", "input_tp"):
        for line in res.stderr.splitlines():
            if f'"{key}"' in line:
                out[key] = float(line.split('"')[3])
    return out


def duration(path):
    return C.probe(path)["duration"]


if not __import__("shutil").which("ffmpeg"):
    print("  (ffmpeg unavailable - the sound tests were skipped)")
else:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        noisy = build(tmp / "noisy.wav")

        p = C.profile(noisy)
        heard = [h["freq"] for h in p["hum"]]
        ok("the hum that was put in is the hum that is found",
           heard[:3] == [50.0, 100.0, 150.0], heard)
        ok("the room tone is measured near where it was set",
           near(p["floor_db"], -36.0, 4.0), p["floor_db"])
        ok("the hiss under the hum is measured lower still",
           p["hiss_db"] < p["floor_db"] - 5.0, (p["hiss_db"], p["floor_db"]))
        ok("a dry recording is not called live", p["reverb"] < 0.3, p["reverb"])
        ok("the profile reads as noisy", p["snr_db"] < 25.0, p["snr_db"])

        live = C.profile(build(tmp / "live.wav", room=True))
        ok("a recording with a tail on it is called live", live["reverb"] >= 0.3, live["reverb"])

        before_speech, before_floor = levels(noisy)
        before_hum = tone_at(noisy, 50.0, p["quiet_at"][0] if p["quiet_at"] else 3.0)

        for strength in ("light", "medium", "strong", "voice"):
            out = tmp / f"out-{strength}.wav"
            res = C.run(noisy, out, strength)
            ok(f"{strength}: a file is written", out.exists() and out.stat().st_size > 1000)
            ok(f"{strength}: nothing is lost off the length",
               near(duration(out), duration(noisy), 0.2), (duration(out), duration(noisy)))

            speech, floor = levels(out)
            quiet_at = res["profile"]["quiet_at"][0] if res["profile"]["quiet_at"] else 3.0
            ok(f"{strength}: the hum is gone",
               tone_at(out, 50.0, quiet_at) < before_hum - 20.0,
               (tone_at(out, 50.0, quiet_at), before_hum))
            ok(f"{strength}: the voice stands further above the noise",
               (speech - floor) > (before_speech - before_floor) + 8.0,
               (speech - floor, before_speech - before_floor))
            ok(f"{strength}: the voice is still there", speech > -40.0, speech)

            heard_loud = loudness(out)
            ok(f"{strength}: it lands near broadcast loudness",
               near(heard_loud.get("input_i", 0), C.TARGET_LUFS, 1.5), heard_loud)
            ok(f"{strength}: and leaves headroom at the peak",
               heard_loud.get("input_tp", 0) <= -1.0, heard_loud)

        # Sync: the filters delay what passes through them (about 30 ms through
        # this chain) and ffmpeg does not report it. If that is not taken back
        # off, the cleaned audio sits a frame late against the camera and every
        # cue timing moves with it.
        def lag_of(a, b, rate=48000, at=6.0, span=6.0):
            x = np.concatenate(list(C._decode(a, rate, at, span)) or [np.zeros(1, "<f4")])
            y = np.concatenate(list(C._decode(b, rate, at, span)) or [np.zeros(1, "<f4")])
            n = min(len(x), len(y))
            if n < rate:
                return 999
            x = x[:n].astype(float) - x[:n].astype(float).mean()
            y = y[:n].astype(float) - y[:n].astype(float).mean()
            edge = int(0.1 * rate)
            corr = np.correlate(y, x[edge:-edge], mode="valid")
            return int(np.argmax(np.abs(corr))) - edge

        for strength in ("light", "medium", "voice"):
            out = tmp / f"out-{strength}.wav"
            shift = lag_of(noisy, out)
            ok(f"{strength}: the cleaned audio still lines up with the original",
               abs(shift) <= 48, f"{shift} samples ({shift / 48.0:+.1f} ms)")
            ok(f"{strength}: and is exactly as long as it was",
               near(duration(out), duration(noisy), 0.005), (duration(out), duration(noisy)))

        # A recording with nothing wrong must come back with its voice intact.
        quiet_src = build(tmp / "quiet.wav", hum=False, noise=False)
        before_q, _ = levels(quiet_src)
        res_q = C.run(quiet_src, tmp / "quiet-out.wav", "medium")
        after_q, _ = levels(tmp / "quiet-out.wav")
        ok("an already-clean recording keeps its voice",
           after_q > before_q - 1.0 or res_q["gain_db"] > 0, (before_q, after_q))
        ok("and is handled gently, having no floor to measure",
           not res_q["profile"]["floor_known"] or res_q["profile"]["snr_db"] > 30,
           res_q["profile"]["snr_db"])

        # Stereo in, stereo out. Writing mono from a stereo recording cost
        # 3 LU against the target: R128 counts both channels of a dual-mono
        # pair, so the level was set from a measurement 3 dB louder than the
        # file being written, and the result sat at -19 LUFS.
        stereo = tmp / "stereo.wav"
        subprocess.run([C._ffmpeg(), "-v", "error", "-y", "-i", str(noisy),
                        "-af", "pan=stereo|c0=c0|c1=c0", "-c:a", "pcm_s24le",
                        str(stereo)], check=True)
        st_out = tmp / "stereo-clean.wav"
        C.run(stereo, st_out, "medium")
        ok("a stereo recording stays stereo", C.probe(st_out)["channels"] == 2,
           C.probe(st_out)["channels"])
        st_loud = loudness(st_out)
        ok("...and still lands on the loudness target",
           near(st_loud.get("input_i", 0), C.TARGET_LUFS, 1.5), st_loud)

        prev = C.preview(noisy, tmp / "pv", at=6.0, seconds=8.0, strength="medium")
        ok("a preview writes both halves",
           Path(prev["before"]).exists() and Path(prev["after"]).exists())
        ok("the preview is the length asked for", near(duration(prev["after"]), 8.0, 0.3),
           duration(prev["after"]))
        # The two halves are matched for loudness on purpose, so that what
        # you hear between them is the cleaning and not the level.
        b_loud = loudness(prev["before"]).get("input_i", 0)
        a_loud = loudness(prev["after"]).get("input_i", 0)
        ok("the two halves play at about the same loudness",
           near(b_loud, a_loud, 3.0), (b_loud, a_loud))
        _, before_gap = levels(Path(prev["before"]))
        _, after_gap = levels(Path(prev["after"]))
        ok("the before half still has its noise in it",
           after_gap < before_gap - 8.0, (before_gap, after_gap))
        ok("the before half is not cleaned, only levelled",
           abs(b_loud - C.TARGET_LUFS) < 6.0, b_loud)
        ok("a preview past the end of the file is pulled back inside",
           C.preview(noisy, tmp / "pv2", at=1e6, seconds=30.0)["at"] < duration(noisy))

print(f"\n  {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
