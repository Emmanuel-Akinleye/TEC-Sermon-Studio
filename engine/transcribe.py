"""Speech to timestamped words, locally.

Word-level timestamps are the whole point: cue accuracy is bounded by how
precisely we know when the book name was spoken, and segment-level timing
(a few seconds wide) is not good enough to key a graphic to.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "transcripts"

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".aif", ".aiff"}

# After Effects hands child processes a minimal PATH, so a Homebrew ffmpeg is
# invisible to anything the panel launches. Look where it actually lives.
FFMPEG_DIRS = ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin", "/usr/bin"]


def find_tool(name: str) -> str | None:
    """Locate ffmpeg/ffprobe whether or not PATH is any use."""
    found = shutil.which(name)
    if found:
        return found
    for folder in FFMPEG_DIRS:
        candidate = Path(folder) / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _fingerprint(path: Path, model: str) -> str:
    st = path.stat()
    raw = f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}|{model}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def extract_audio(video: Path, out_wav: Path, progress=None) -> Path:
    """16 kHz mono PCM — what Whisper wants, and small enough to be quick."""
    ffmpeg = find_tool("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError(
            "ffmpeg not found. Install it with:  brew install ffmpeg\n"
            "(looked on PATH and in " + ", ".join(FFMPEG_DIRS) + ")"
        )
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    if progress:
        progress("Extracting audio")
    cmd = [
        ffmpeg, "-nostdin", "-y", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        "-loglevel", "error", str(out_wav),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()[:400]}")
    return out_wav


def media_duration(path: Path) -> float:
    ffprobe = find_tool("ffprobe")
    if ffprobe is None:
        return 0.0
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


PARAKEET_MODEL = "mlx-community/parakeet-tdt-0.6b-v2"
DEFAULT_MODEL = "parakeet"


def _parakeet(audio: Path, total: float, progress=None) -> list[dict]:
    """Transcribe with NVIDIA Parakeet on the Mac's GPU.

    Chosen after testing every local option on real sermon readings scored
    against the ESV text: 90% against 80% for Whisper large-v3 and 73% for
    medium, in about 3 minutes a sermon instead of 20-60. It also runs on the
    Apple GPU, which faster-whisper cannot use.

    Parakeet reports sub-word pieces; a piece beginning with a space starts a
    new word, so the pieces are joined back into whole words with the start
    of the first and the end of the last.
    """
    try:
        from parakeet_mlx import from_pretrained
    except ImportError as exc:
        raise RuntimeError(
            "parakeet-mlx is not installed. Run 'TEC Sermon Studio'/setup.sh, or:\n"
            f"  {sys.executable} -m pip install parakeet-mlx"
        ) from exc

    if progress:
        progress("Loading Parakeet")
    model = from_pretrained(PARAKEET_MODEL)

    def report(done, whole):
        if progress and whole:
            frac = min(0.95, done / whole)
            progress(f"Transcribing {frac * 100:.0f}%", pct=frac)

    # Long sermons are read in overlapping two-minute chunks, which keeps
    # memory flat and lets progress be reported as it goes.
    result = model.transcribe(str(audio), chunk_duration=120.0,
                              overlap_duration=15.0, chunk_callback=report)

    words, current = [], None
    for sentence in result.sentences:
        for tok in sentence.tokens:
            piece = tok.text
            if current is None or piece.startswith(" "):
                if current and current["word"].strip():
                    words.append(current)
                current = {"word": piece.strip(), "start": float(tok.start),
                           "end": float(tok.end), "probs": [float(tok.confidence)]}
            else:
                current["word"] += piece
                current["end"] = float(tok.end)
                current["probs"].append(float(tok.confidence))
    if current and current["word"].strip():
        words.append(current)

    words = clean_seams(words)
    for w in words:
        probs = w.pop("probs")
        w["start"] = round(w["start"], 3)
        w["end"] = round(max(w["end"], w["start"] + 0.04), 3)
        # A word is only as certain as its least certain piece.
        w["prob"] = round(min(probs), 3)
    return words


def scripture_hotwords() -> str:
    """Bias the decoder toward book names for the whole sermon.

    `initial_prompt` only conditions the first window once
    `condition_on_previous_text` is off, which leaves the other eighty
    minutes unbiased. `hotwords` applies throughout, and book names are
    exactly the tokens a missed word costs a cue.
    """
    import books as B
    names = []
    for full in B.CANONICAL:
        stem = full.split(" ", 1)[1] if full[0].isdigit() else full
        if stem not in names:
            names.append(stem)
    return ", ".join(names)


def transcribe(media: Path, model_size: str = DEFAULT_MODEL, language: str = "en",
               progress=None, use_cache: bool = True,
               compute_type: str = "int8", beam_size: int = 5,
               initial_prompt: str | None = None) -> dict:
    """Return {"words": [...], "segments": [...], "duration": float}."""
    media = Path(media)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{media.stem}.{_fingerprint(media, model_size)}.json"
    if use_cache and cache.exists():
        if progress:
            progress("Using cached transcript")
        return json.loads(cache.read_text())

    if media.suffix.lower() in AUDIO_EXTS:
        audio = media
    else:
        audio = CACHE_DIR / f"{media.stem}.{_fingerprint(media, 'wav')}.wav"
        # Pulling audio out of a 90 GB camera file takes minutes, and the
        # fingerprint already covers size and mtime, so never do it twice.
        if audio.exists() and audio.stat().st_size > 1024:
            if progress:
                progress("Using extracted audio")
        else:
            extract_audio(media, audio, progress)

    total = media_duration(audio) or media_duration(media)

    if model_size.startswith("parakeet"):
        words = _parakeet(audio, total, progress)
        result = {"words": words, "segments": [],
                  "duration": total or (words[-1]["end"] if words else 0.0),
                  "model": model_size, "media": str(media)}
        cache.write_text(json.dumps(result))
        if progress:
            progress(f"Transcribed {len(words):,} words", pct=1.0)
        return result

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper is not installed. Run 'TEC Sermon Studio'/setup.sh, or:\n"
            f"  {sys.executable} -m pip install faster-whisper"
        ) from exc
    if progress:
        progress(f"Loading Whisper model ({model_size})")
    model = WhisperModel(model_size, device="cpu", compute_type=compute_type)

    prompt = initial_prompt or (
        "A Christian sermon, quoting the Bible by book, chapter and verse."
    )

    segments, _info = model.transcribe(
        str(audio), language=language, beam_size=beam_size,
        word_timestamps=True,
        vad_filter=True,
        # Preachers pause for effect. Splitting on short gaps fragments words
        # and costs timestamp accuracy at exactly the wrong moments.
        vad_parameters={"min_silence_duration_ms": 500,
                        "speech_pad_ms": 200},
        initial_prompt=prompt,
        hotwords=scripture_hotwords(),
        # Long sermons drift when each window is conditioned on the last.
        condition_on_previous_text=False,
        # Let it retry a window rather than emit a low-confidence guess.
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
    )

    words, segs = [], []
    for seg in segments:
        segs.append({"start": round(seg.start, 3), "end": round(seg.end, 3),
                     "text": seg.text.strip()})
        for w in (seg.words or []):
            words.append({"word": w.word.strip(),
                          "start": round(w.start, 3),
                          "end": round(w.end, 3),
                          "prob": round(getattr(w, "probability", 0.0), 3)})
        if progress and total:
            progress(f"Transcribing {seg.end/total*100:.0f}%",
                     pct=min(0.95, seg.end / total))

    result = {"words": words, "segments": segs,
              "duration": total or (words[-1]["end"] if words else 0.0),
              "model": model_size, "media": str(media)}
    cache.write_text(json.dumps(result))
    if progress:
        progress(f"Transcribed {len(words):,} words", pct=1.0)
    return result


def clean_seams(words: list[dict]) -> list[dict]:
    """Drop the words Parakeet repeats where its audio chunks join.

    Long recordings are read in overlapping two-minute pieces, and where two
    pieces are stitched together a word is now and then emitted twice, the
    second time out of order ("…what I'm saying? Paul." with "Paul" timed
    four seconds earlier). A word that starts before the one ahead of it has
    finished cannot have been said there, so it goes."""
    out: list[dict] = []
    for w in words:
        if out and float(w["start"]) < float(out[-1]["end"]) - 0.1:
            continue
        out.append(w)
    return out


def load_transcript(path: Path) -> dict:
    """Accept our own JSON, a Whisper JSON, or a WhisperX JSON."""
    data = json.loads(Path(path).read_text())
    if "words" in data and data["words"]:
        return data
    words = []
    for seg in data.get("segments", []):
        for w in seg.get("words", []):
            words.append({
                "word": (w.get("word") or w.get("text") or "").strip(),
                "start": float(w.get("start", seg.get("start", 0.0))),
                "end": float(w.get("end", seg.get("end", 0.0))),
            })
    if not words:
        raise RuntimeError(
            f"{path} has no word-level timestamps — Sermon Studio needs them. "
            "Re-run Whisper with word timestamps enabled."
        )
    data["words"] = words
    return data
