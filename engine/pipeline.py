"""Video in, cue list out."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import align as aligner
import bible
import quotes
import refs
import timing
import transcribe as tx

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
OUT_DIR = ROOT / "out"


def load_config(name: str, overrides: dict | None = None) -> dict:
    path = CONFIG_DIR / f"{name}.json"
    cfg = json.loads(path.read_text()) if path.exists() else {}
    if overrides:
        cfg = {**cfg, **{k: v for k, v in overrides.items() if v is not None}}
    return cfg


class Reporter:
    """Progress as JSON lines for the panel, or plain text for a terminal."""

    def __init__(self, as_json: bool = False):
        self.as_json = as_json
        self.stage = "start"
        self.t0 = time.time()

    def stage_start(self, stage: str, label: str) -> None:
        self.stage = stage
        self.emit(label, pct=0.0)

    def emit(self, message: str, pct: float | None = None, **extra) -> None:
        if self.as_json:
            payload = {"event": "progress", "stage": self.stage,
                       "message": message, "elapsed": round(time.time() - self.t0, 1)}
            if pct is not None:
                payload["pct"] = round(pct, 3)
            payload.update(extra)
            print(json.dumps(payload), flush=True)
        else:
            bar = f" [{pct*100:3.0f}%]" if pct is not None else ""
            print(f"  {self.stage:<11}{bar} {message}", file=sys.stderr, flush=True)

    def result(self, payload: dict) -> None:
        if self.as_json:
            print(json.dumps({"event": "done", **payload}), flush=True)

    def fail(self, message: str) -> None:
        if self.as_json:
            print(json.dumps({"event": "error", "message": message}), flush=True)
        else:
            print(f"error: {message}", file=sys.stderr, flush=True)


def analyze(media: str | Path, translation: str = "ESV", corpus: str = "BSB",
            model: str = "parakeet", fps: float = 25.0,
            transcript_path: str | None = None, reporter: Reporter | None = None,
            detect_overrides: dict | None = None,
            timing_overrides: dict | None = None,
            use_cache: bool = True, out_path: str | None = None,
            audio: str | Path | None = None,
            audio_offset: float | None = None,
            allow_camera_audio: bool = False) -> dict:
    rep = reporter or Reporter()
    media = Path(media)
    if not media.exists():
        raise FileNotFoundError(f"No such file: {media}")
    if not audio and not transcript_path and not allow_camera_audio:
        raise RuntimeError("Choose the mic recording first - Sermon Studio transcribes "
                           "the external mic, not the camera's sound.")
    audio = Path(audio) if audio else None
    if audio and not audio.exists():
        raise FileNotFoundError(f"No such file: {audio}")

    # 1. corpus -----------------------------------------------------------
    rep.stage_start("corpus", f"Preparing {corpus} corpus")
    bible.ensure_corpus(corpus, progress=lambda m: rep.emit(m))

    # 2. sync ---------------------------------------------------------------
    # The mic recording is far cleaner than camera audio, so transcribe that
    # when it is offered - but every cue has to come back on the camera's
    # timeline, which means measuring the offset between the two first.
    alignment = None
    if audio:
        rep.stage_start("sync", "Matching mic recording to camera")
        if audio_offset is not None:
            alignment = aligner.Alignment(
                offset=float(audio_offset), confidence=99.0, drift_ppm=0.0,
                drift_seconds=0.0, stretch_percent=100.0, overlap=0.0,
                anchors=[], a0=float(audio_offset), a1=0.0)
            rep.emit(f"Using the offset you supplied ({audio_offset:+.3f}s)", pct=1.0)
        else:
            try:
                alignment = aligner.measure_cached(media, audio, progress=lambda m: rep.emit(m))
            except aligner.AlignError as exc:
                alignment, why = None, str(exc)
            else:
                why = None
                rep.emit(alignment.describe().split("\n")[0], pct=1.0,
                         offset=alignment.offset, confidence=alignment.confidence,
                         solid=alignment.solid)
                if not alignment.solid:
                    why = (f"it does not line up with this video (sync confidence "
                           f"{alignment.confidence:.0f})")
            if why:
                # Timings from a mic file that does not match the video would
                # all be wrong, and camera sound is not good enough to use
                # instead. Stop and say so.
                raise RuntimeError(
                    f"The mic file {audio.name} does not match this video: {why}. "
                    "Pick the mic recording from this service and Analyze again.")

    # 3. transcript -------------------------------------------------------
    source = audio or media
    rep.stage_start("transcribe",
                    "Transcribing mic recording" if audio else "Transcribing sermon audio")
    if transcript_path:
        data = tx.load_transcript(Path(transcript_path))
        rep.emit(f"Loaded {len(data['words']):,} words from transcript", pct=1.0)
    else:
        data = tx.transcribe(source, model_size=model, progress=rep.emit,
                             use_cache=use_cache)
    words = tx.clean_seams(data["words"])
    if not words:
        raise RuntimeError("Transcription produced no words")

    if alignment is not None:
        # From here on every time is camera time, so nothing downstream has to
        # know the mic file existed.
        words = [{**w,
                  "start": alignment.video_time(float(w["start"])),
                  "end": alignment.video_time(float(w["end"]))}
                 for w in words]
        _warn_uncovered(media, audio, alignment, data, rep)
        data["words"] = words
        data["duration"] = tx.media_duration(media) or float(data.get("duration") or 0.0)

    # 4. spoken references ------------------------------------------------
    rep.stage_start("detect", "Finding spoken references")
    detect_cfg = load_config("detect", detect_overrides)
    mentions = refs.detect(words, detect_cfg)
    rep.emit(f"{len(mentions)} references spoken", pct=1.0,
             mentions=len(mentions))

    # 5. readings ---------------------------------------------------------
    rep.stage_start("quotes", "Matching passages read aloud")
    index = quotes.load_index(corpus)
    hits = index.find(words, detect_cfg.get("quotes", {}))
    rep.emit(f"{len(hits)} passages read aloud", pct=1.0, quotes=len(hits))

    # 6. cues -------------------------------------------------------------
    rep.stage_start("cues", "Building cue timings")
    timing_cfg = {**load_config("timing", timing_overrides), "fps": fps}
    cues = timing.build_cues(mentions, hits, words, translation=translation,
                             corpus=corpus, cfg=timing_cfg, index=index)
    # Confessions and catechisms the church reads from, if any are downloaded.
    try:
        import documents
        doc_cues = documents.cues(words, {**timing_cfg, "quotes": detect_cfg.get("quotes", {})},
                                  index=index)
        if doc_cues:
            cues = documents.merge(cues, doc_cues)
            rep.emit(f"{len(doc_cues)} from the confessions", pct=1.0)
    except Exception as exc:
        rep.emit(f"Confession lookup skipped: {exc}", pct=1.0)

    substituted = sorted({c.translation for c in cues
                          if c.text and c.translation.upper() != translation.upper()})
    if substituted:
        rep.emit(
            f"{translation.upper()} is not available - cards show "
            f"{', '.join(substituted)} instead. Add an API key to fix this.",
            pct=1.0, warning="translation", requested=translation.upper(),
            using=substituted[0],
        )
    rep.emit(f"{len(cues)} cues", pct=1.0)

    # 7. write ------------------------------------------------------------
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(out_path) if out_path else cue_path_for(media)
    style = load_config("style")
    payload = {
        "version": 1,
        "media": str(media.resolve()),
        "media_identity": media_identity(media),
        "duration": data.get("duration", 0.0),
        "fps": fps,
        "translation": translation,
        "corpus": corpus,
        "audio": str(audio.resolve()) if audio else None,
        "alignment": alignment.to_dict() if alignment else None,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "style": style,
        "cues": [c.to_dict() for c in cues],
        "mentions": [m.to_dict() for m in mentions],
    }
    out.write_text(json.dumps(payload, indent=2))
    rep.result({"cues_path": str(out), "cue_count": len(cues),
                "enabled": sum(1 for c in cues if c.enabled),
                "alignment": alignment.to_dict() if alignment else None})
    return payload


def reanalyze(cues_path: str | Path, reporter: Reporter | None = None,
              detect_overrides: dict | None = None,
              timing_overrides: dict | None = None) -> dict:
    """Run today's analysis over a sermon already transcribed.

    The recording itself may be long since archived; the transcript, the
    measured sync and the frame rate are all in the cue file from last time,
    which is everything the rest of the work needs. Every improvement to the
    detection since can be applied without restoring 97 GB of footage.
    """
    import captions
    import documents
    rep = reporter or Reporter()
    path = Path(cues_path)
    payload = json.loads(path.read_text())
    rep.stage_start("corpus", "Preparing the corpus")
    corpus = payload.get("corpus", "BSB")
    bible.ensure_corpus(corpus, progress=lambda m: rep.emit(m))

    rep.stage_start("transcribe", "Reading the saved transcript")
    words = tx.clean_seams(captions._transcript_for(payload)["words"])
    alignment = payload.get("alignment")
    if alignment:
        words = [{**w,
                  "start": w["start"] + alignment["a0"] + alignment["a1"] * w["start"],
                  "end": w["end"] + alignment["a0"] + alignment["a1"] * w["end"]}
                 for w in words]
    rep.emit(f"{len(words):,} words", pct=1.0)

    rep.stage_start("detect", "Finding spoken references")
    detect_cfg = load_config("detect", detect_overrides)
    mentions = refs.detect(words, detect_cfg)
    index = quotes.load_index(corpus)
    hits = index.find(words, detect_cfg.get("quotes", {}))
    rep.stage_start("cues", "Building cue timings")
    timing_cfg = {**load_config("timing", timing_overrides), "fps": payload.get("fps", 25.0)}
    cues = timing.build_cues(mentions, hits, words, translation=payload.get("translation", "ESV"),
                             corpus=corpus, cfg=timing_cfg, index=index)
    doc_cues = documents.cues(words, {**timing_cfg, "quotes": detect_cfg.get("quotes", {})},
                              index=index)
    if doc_cues:
        cues = documents.merge(cues, doc_cues)

    payload["cues"] = [c.to_dict() for c in cues]
    payload["mentions"] = [m.to_dict() for m in mentions]
    payload["style"] = load_config("style")
    payload["generated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(json.dumps(payload, indent=2))
    rep.result({"cues_path": str(path), "cue_count": len(cues),
                "enabled": sum(1 for c in cues if c.enabled),
                "alignment": alignment})
    return payload


def media_identity(media: Path) -> str:
    """Name, size and modification time: tells two recordings apart even when
    the camera gives them the same file name."""
    st = Path(media).stat()
    return f"{Path(media).name}|{st.st_size}|{int(st.st_mtime)}"


def cue_path_for(media: Path) -> Path:
    """Where this recording's cue file lives.

    Normally out/<name>.cues.json. If that name already belongs to a
    different recording (cameras reuse names like C0001.MP4), the new one
    gets its own file instead of overwriting the old sermon's analysis.
    """
    media = Path(media)
    ident = media_identity(media)
    n = 1
    while True:
        out = OUT_DIR / (f"{media.stem}.cues.json" if n == 1 else f"{media.stem} ({n}).cues.json")
        if not out.exists():
            return out
        try:
            head = json.loads(out.read_text())
        except Exception:
            return out
        same = head.get("media_identity") == ident or (
            "media_identity" not in head and head.get("media") == str(media.resolve()))
        if same:
            return out
        n += 1


def _warn_uncovered(media, audio, alignment, data, rep) -> None:
    """Say so if the mic recording does not cover all of the video.

    Only the mic is transcribed, so a stretch it missed (started late, stopped
    early) can have no verses. That is worth knowing before building.
    """
    video_len = tx.media_duration(media) or 0.0
    mic_len = float(data.get("duration") or 0.0) or tx.media_duration(audio) or 0.0
    if not video_len or not mic_len:
        return
    covered_from = max(0.0, alignment.video_time(0.0))
    covered_to = min(video_len, alignment.video_time(mic_len))
    missing = [(a, b) for a, b in ((0.0, covered_from), (covered_to, video_len)) if b - a > 20.0]
    if not missing:
        return
    parts = [f"{int(a // 60)}:{int(a % 60):02d}-{int(b // 60)}:{int(b % 60):02d}" for a, b in missing]
    rep.emit(f"The mic recording does not cover {' and '.join(parts)} of the video, "
             "so no verses can be found there.", pct=1.0, warning="coverage")
