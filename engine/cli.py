#!/usr/bin/env python3
"""Sermon Studio command line.

    sermonstudio analyze sermon.mp4 --translation ESV
    sermonstudio corpus BSB
    sermonstudio show out/sermon.cues.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import align          # noqa: E402
import bible          # noqa: E402
import cards          # noqa: E402
import fcpxml         # noqa: E402
import fonts          # noqa: E402
import pipeline       # noqa: E402


def fmt_tc(seconds: float, fps: float = 25.0) -> str:
    frames = int(round(seconds * fps))
    h, rem = divmod(frames, int(fps * 3600))
    m, rem = divmod(rem, int(fps * 60))
    s, f = divmod(rem, int(fps))
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def cmd_analyze(args: argparse.Namespace) -> int:
    rep = pipeline.Reporter(as_json=args.json)
    try:
        payload = pipeline.analyze(
            args.media, translation=args.translation, corpus=args.corpus,
            model=args.model, fps=args.fps, transcript_path=args.transcript,
            reporter=rep, use_cache=not args.no_cache, out_path=args.out,
            audio=args.audio, audio_offset=args.audio_offset,
        )
    except Exception as exc:
        rep.fail(str(exc))
        return 1
    if not args.json:
        payload["_path"] = args.out or f"out/{Path(args.media).stem}.cues.json"
        _print_cues(payload)
    return 0


def _print_cues(payload: dict) -> None:
    fps = payload.get("fps", 25.0)
    cues = payload["cues"]
    print(f"\n{len(cues)} cues  ({payload['translation']}, {payload['corpus']} corpus)\n")
    for c in cues:
        mark = " " if c["enabled"] else "~"
        print(f"{mark} {fmt_tc(c['start'], fps)} → {fmt_tc(c['end'], fps)} "
              f"({c['duration']:5.1f}s)  {c['ref']:<22} {c['confidence']:.2f} "
              f"{c['source']:<12} {'; '.join(c['notes'])}")
        if c["text"]:
            body = c["text"][:96] + ("…" if len(c["text"]) > 96 else "")
            print(f"      {body}")
    if payload.get("_path"):
        print(f"\ncue file: {payload['_path']}")


def cmd_fonts(args: argparse.Namespace) -> int:
    """List installed fonts with the PostScript names After Effects needs."""
    families = fonts.families()
    if args.json:
        print(json.dumps(families))
        return 0
    for family in families:
        print(f"{family['family']}")
        for style, ps in sorted(family["styles"].items()):
            print(f"    {style:<22} {ps}")
    return 0


def cmd_key(args: argparse.Namespace) -> int:
    """Verify an API key, and save it only if it actually works."""
    env_var = {"esv": "SERMONSTUDIO_ESV_KEY",
               "nlt": "SERMONSTUDIO_NLT_KEY",
               "apibible": "SERMONSTUDIO_APIBIBLE_KEY"}[args.service]
    code = {"esv": "ESV", "nlt": "NLT", "apibible": "NIV"}[args.service]

    # Test through the environment so a bad key is never written to disk.
    os.environ[env_var] = args.value.strip()
    try:
        text, used = bible.passage(code, "John", 3, 16)
    except Exception as exc:
        print(f"that key did not work: {exc}", file=sys.stderr)
        return 1
    finally:
        os.environ.pop(env_var, None)

    if used.upper() != code:
        print(f"{code} is still not reachable (got {used})", file=sys.stderr)
        return 1

    path = Path(__file__).resolve().parent.parent / "config" / "keys.json"
    keys = {}
    if path.exists():
        try:
            keys = json.loads(path.read_text())
        except json.JSONDecodeError:
            keys = {}
    keys[args.service] = args.value.strip()
    path.write_text(json.dumps(keys, indent=2) + "\n")
    print(f"{code} works: {text[:80]}...")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Measure the offset between a camera file and a separate mic recording."""
    try:
        result = align.measure(args.video, args.audio,
                               progress=lambda m: print(f"  {m}", file=sys.stderr))
    except align.AlignError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return 0

    print()
    print(result.describe())
    print()
    print("In After Effects, with the video layer at its current position:")
    print(f"  set the audio layer's start time {result.offset:+.3f}s relative to it")
    if abs(result.drift_seconds) >= 0.08:
        print(f"  and its stretch to {result.stretch_percent:.4f}%")
    if result.anchors:
        print()
        print("  checked at:")
        for a in result.anchors:
            print(f"    {a.at/60:6.1f} min   offset {a.offset:+.3f}s   "
                  f"confidence {a.confidence:.1f}")
    return 0 if result.solid else 2


def cmd_fcpxml(args: argparse.Namespace) -> int:
    """Render the cards and write a Final Cut timeline that places them."""
    payload = json.loads(Path(args.cues).read_text())
    enabled = [c for c in payload["cues"] if c.get("enabled", True)]
    if not enabled:
        print("error: no cues are enabled", file=sys.stderr)
        return 1

    stem = Path(payload["media"]).stem
    out = Path(args.out) if args.out else Path("out") / f"{stem}.fcpxml"
    card_dir = Path(args.cards) if args.cards else out.parent / f"{stem} cards"

    size = tuple(payload.get("style", {}).get("reference", [1920, 1080]))
    print(f"Rendering {len(enabled)} cards into {card_dir}", file=sys.stderr)
    paths = cards.render_all(enabled, payload["style"], card_dir, size)

    written = fcpxml.build(payload, paths, out, offset=args.offset,
                           include_video=args.with_video, version=args.version)
    print(f"\n{written}")
    print(f"{len(paths)} cards in {card_dir}")
    print("\nIn Final Cut: File > Import > XML, then copy the connected")
    print("clips onto your own timeline.")
    return 0


def cmd_corpus(args: argparse.Namespace) -> int:
    try:
        n = bible.download_corpus(args.code, progress=print)
    except bible.BibleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{args.code}: {n:,} verses")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.cues).read_text())
    payload["_path"] = args.cues
    _print_cues(payload)
    return 0


def cmd_lookup(args: argparse.Namespace) -> int:
    """A typed reference ("Gen 2:24", "1 Cor 13 4-7") to its verse text, for
    fixing or adding a card by hand in the panel."""
    import refs
    import books as B
    words = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25}
             for i, w in enumerate(args.reference.replace(":", " : ").split())]
    # A confession or catechism, if that is what was typed.
    import documents
    for hit in documents.citations(words):
        if not hit.item:
            continue
        part = documents.find(hit.doc, hit.section, hit.item)
        if part:
            doc = documents.KNOWN[hit.doc]
            print(json.dumps({"ok": True, "ref": f"{doc.title}, {part[1]}", "book": doc.title,
                              "chapter": part[0], "verse": hit.item, "verse_end": None,
                              "text": part[2], "translation": "", "kind": "document"}))
            return 0
    found = [m for m in refs.detect(words, {"min_confidence": 0.0})
             if m.kind in ("full", "verse-only") and m.verse]
    if not found:
        print(json.dumps({"ok": False, "error": f"Could not read \u201c{args.reference}\u201d "
                          "as a reference - try e.g. John 3:16"}))
        return 1
    m = found[0]
    try:
        text, used = bible.passage(args.translation, m.book, m.chapter, m.verse,
                                   m.verse_end, fallback="BSB")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"No text for {m.ref}: {exc}"}))
        return 1
    print(json.dumps({"ok": True, "ref": B.display(m.book, m.chapter, m.verse, m.verse_end),
                      "book": m.book, "chapter": m.chapter, "verse": m.verse,
                      "verse_end": m.verse_end, "text": text, "translation": used}))
    return 0


def cmd_captions(args: argparse.Namespace) -> int:
    """YouTube subtitles and a readable transcript for an analysed sermon."""
    import captions
    try:
        payload = json.loads(Path(args.cues).read_text())
        edit_map = json.loads(args.map) if args.map else None
        out_dir = Path(args.out_dir) if args.out_dir else pipeline.OUT_DIR
        name = args.name or Path(payload["media"]).stem
        print(json.dumps(captions.make(payload, out_dir, name, edit_map)))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


def cmd_snippet_suggest(args: argparse.Namespace) -> int:
    import snippets
    try:
        payload = json.loads(Path(args.cues).read_text())
        edit_map = json.loads(args.map) if args.map else None
        print(json.dumps({"ok": True, "moments": snippets.suggest(payload, edit_map, args.count)}))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


def cmd_snippet(args: argparse.Namespace) -> int:
    """Captions and pastor-following for one clip, ready for After Effects."""
    import snippets
    rep = pipeline.Reporter(as_json=True)
    try:
        payload = json.loads(Path(args.cues).read_text())
        edit_map = json.loads(args.map) if args.map else None
        style = pipeline.load_config("style")
        rep.stage_start("snippet", "Preparing the snippet")
        data = snippets.prepare(payload, edit_map, float(args.start), float(args.end), style,
                                progress=lambda m: rep.emit(m), follow=not args.no_follow)
        data["style"] = style
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data))
        rep.result({"path": str(out), "captions": len(data["captions"]),
                    "follow": len(data["follow"])})
        return 0
    except Exception as exc:
        rep.fail(str(exc))
        return 1


def cmd_furniture(args: argparse.Namespace) -> int:
    """Draw the slide layout's still parts once, for the build to use."""
    import furniture
    try:
        style = pipeline.load_config("style")
        print(json.dumps({"ok": True, "path": furniture.make(style, args.width, args.height,
                                                             force=args.force, deck=args.deck)}))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


def cmd_documents(args: argparse.Namespace) -> int:
    """Confessions and catechisms: download one, or list what is stored."""
    import documents
    try:
        if args.action == "add":
            if args.name not in documents.KNOWN:
                raise SystemExit(f"Don't know {args.name}. Known: {', '.join(documents.KNOWN)}")
            n = documents.download(args.name, progress=lambda m: print(m, file=sys.stderr))
            print(json.dumps({"ok": True, "document": args.name, "articles": n}))
        else:
            have = {k: documents.have(k) for k in documents.KNOWN}
            print(json.dumps({"ok": True, "documents": [
                {"id": k, "title": documents.KNOWN[k].title, "articles": v} for k, v in have.items()]}))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


def cmd_clean_profile(args: argparse.Namespace) -> int:
    """Listen to a recording and report what is wrong with it."""
    import cleanup
    try:
        prof = cleanup.profile(args.audio)
        spec_notes = cleanup.describe(prof)
        if args.json:
            print(json.dumps({"ok": True, "profile": prof, "notes": spec_notes,
                              "strengths": list(cleanup.STRENGTHS)}))
            return 0
        print(f"\n{Path(args.audio).name}  "
              f"{prof['duration']/60:.1f} min, {prof['rate']} Hz, {prof['channels']} ch\n")
        for note in spec_notes:
            print(f"  - {note}")
        print(f"\n  noise floor {prof['floor_db']:.1f} dB, hiss {prof['hiss_db']:.1f} dB, "
              f"voice {prof['speech_db']:.1f} dB, peak {prof['peak_db']:.1f} dB")
        print("\n  filters it would use:")
        for f in cleanup.chain(prof, args.strength):
            print(f"      {f}")
        return 0
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_clean_preview(args: argparse.Namespace) -> int:
    """A few seconds before and after, to judge the settings by ear."""
    import cleanup
    rep = pipeline.Reporter(as_json=True)
    try:
        rep.stage_start("clean", "Preparing the preview")
        res = cleanup.preview(args.audio, args.out_dir, at=args.at, seconds=args.seconds,
                              strength=args.strength, dereverb=_dereverb(args),
                              progress=lambda m, pct=None: rep.emit(m, pct))
        rep.result({"ok": True, **res})
        return 0
    except Exception as exc:
        rep.fail(str(exc))
        return 1


def cmd_clean(args: argparse.Namespace) -> int:
    """Clean a whole recording and write a WAV for After Effects."""
    import cleanup
    rep = pipeline.Reporter(as_json=args.json)
    try:
        out = args.out or str(Path(args.audio).with_name(
            Path(args.audio).stem + " (cleaned).wav"))
        res = cleanup.run(args.audio, out, strength=args.strength,
                          dereverb=_dereverb(args),
                          progress=lambda m, pct=None: rep.emit(m, pct))
        if args.json:
            rep.result({"ok": True, **res})
        else:
            print(f"\n{res['path']}")
            for note in res["notes"]:
                print(f"  - {note}")
            print(f"\n  level set {res['gain_db']:+.1f} dB to reach "
                  f"{res['target_lufs']:.0f} LUFS")
        return 0
    except Exception as exc:
        rep.fail(str(exc))
        return 1


def _dereverb(args: argparse.Namespace):
    """None means "decide from the measured room"."""
    if getattr(args, "dereverb", False):
        return True
    if getattr(args, "no_dereverb", False):
        return False
    return None


def cmd_deck(args: argparse.Namespace) -> int:
    """Convert a presentation into slide pictures and the words on them."""
    import deck as D
    rep = pipeline.Reporter(as_json=args.json)
    try:
        if args.action == "place":
            if not args.cues:
                raise D.DeckError("Give the sermon's cue file as well: "
                                  "deck place <deck> --cues out/<name>.cues.json")
            rep.stage_start("deck", "Placing the slides")
            done = D.place(args.cues, args.path, progress=lambda m: rep.emit(m))
            if args.json:
                rep.result({"ok": True, **done})
            else:
                print(f"\n{done['slides']} slides placed beside {done['verses']} verses "
                      f"({done['guessed']} put between their neighbours)")
                print(f"cue file: {done['cues_path']}")
            return 0
        if args.action == "import":
            if not args.path:
                raise D.DeckError("Give the deck to import: a PDF, a folder of "
                                  "pictures, or a .pptx")
            rep.stage_start("deck", "Importing the slides")
            info = D.load(args.path, progress=lambda m: rep.emit(m), force=args.force)
        else:
            info = D.read(args.path)
        if args.json:
            rep.result({"ok": True, **info})
            return 0
        print(f"\n{info['count']} slides from {Path(info['source']).name}  "
              f"(deck {info['id']})\n")
        for slide in info["slides"]:
            read = f"[{slide['read']}]" if slide.get("read") else ""
            print(f"  {slide['index']:3d}. {read:6} {slide['title'] or '(no text)'}")
        print(f"\npictures in data/decks/{info['id']}/")
        return 0
    except Exception as exc:
        rep.fail(str(exc))
        return 1


def cmd_reanalyze(args: argparse.Namespace) -> int:
    """Re-run the analysis from a saved transcript, with the footage archived."""
    rep = pipeline.Reporter(as_json=args.json)
    try:
        payload = pipeline.reanalyze(args.cues, reporter=rep)
    except Exception as exc:
        rep.fail(str(exc))
        return 1
    if not args.json:
        payload["_path"] = args.cues
        _print_cues(payload)
    return 0


def cmd_follow(args: argparse.Namespace) -> int:
    """Framing that keeps the preacher centred in the slide layout's camera."""
    import track
    rep = pipeline.Reporter(as_json=True)
    try:
        windows = json.loads(args.windows)          # [[start, end], …] in comp time
        edit_map = json.loads(args.map) if args.map else None
        ranges, back = [], []
        for start, end in windows:
            piece = None
            if edit_map:
                for m in edit_map:
                    if m["tMin"] - 0.01 <= start <= m["tMax"] + 0.01:
                        piece = m
                        break
                if piece is None:
                    continue
                ranges.append((((start - piece["offset"]) / piece["scale"]),
                               ((min(end, piece["tMax"]) - piece["offset"]) / piece["scale"])))
            else:
                ranges.append((start, end))
            back.append((start, end, piece))
        rep.stage_start("follow", "Following the preacher")
        paths = track.follow(Path(args.media), ranges, float(args.crop_width),
                             identity=args.media, progress=lambda m: rep.emit(m))
        out = []
        for (start, end, piece), path in zip(back, paths):
            keys = [[(piece["offset"] + t * piece["scale"]) if piece else t, x]
                    for t, x in path["keys"]]
            out.append({"start": start, "end": end, "keys": keys})
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({"windows": out}))
        rep.result({"path": args.out, "sections": len(out),
                    "keys": sum(len(w["keys"]) for w in out)})
        return 0
    except Exception as exc:
        rep.fail(str(exc))
        return 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sermonstudio", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("analyze", help="find verses in a sermon video")
    a.add_argument("media", help="video or audio file")
    a.add_argument("--translation", default="ESV", help="on-screen translation")
    a.add_argument("--corpus", default="BSB", help="offline translation used for matching")
    a.add_argument("--model", default="parakeet", help="transcription model (parakeet)")
    a.add_argument("--fps", type=float, default=25.0)
    a.add_argument("--audio", help="separate mic recording to transcribe instead "
                                   "(auto-synced to the video)")
    a.add_argument("--audio-offset", type=float,
                   help="skip sync measurement and use this offset in seconds")
    a.add_argument("--transcript", help="reuse an existing word-level transcript")
    a.add_argument("--out", help="where to write the cue JSON")
    a.add_argument("--no-cache", action="store_true")
    a.add_argument("--json", action="store_true", help="machine-readable progress")
    a.set_defaults(func=cmd_analyze)

    y = sub.add_parser("sync", help="measure the offset between camera and mic")
    y.add_argument("video", help="camera file (must have its own scratch audio)")
    y.add_argument("audio", help="separately recorded mic file")
    y.add_argument("--json", action="store_true")
    y.set_defaults(func=cmd_sync)

    f = sub.add_parser("fcpxml", help="build a Final Cut timeline from a cue file")
    f.add_argument("cues", help="a cue file produced by analyze")
    f.add_argument("--out", help="where to write the .fcpxml")
    f.add_argument("--cards", help="folder for the rendered card PNGs")
    f.add_argument("--offset", type=float, default=0.0,
                   help="shift every card by this many seconds")
    f.add_argument("--with-video", action="store_true",
                   help="include the sermon clip on the timeline as well")
    f.add_argument("--version", default=fcpxml.FCPXML_VERSION,
                   help="FCPXML version to declare (default %(default)s)")
    f.set_defaults(func=cmd_fcpxml)

    ft = sub.add_parser("fonts", help="list installed fonts and their PostScript names")
    ft.add_argument("--json", action="store_true")
    ft.set_defaults(func=cmd_fonts)

    k = sub.add_parser("key", help="save and test an API key")
    k.add_argument("service", choices=["esv", "nlt", "apibible"])
    k.add_argument("value")
    k.set_defaults(func=cmd_key)

    c = sub.add_parser("corpus", help="download an offline translation")
    c.add_argument("code", help="e.g. BSB, KJV, ASV")
    c.set_defaults(func=cmd_corpus)

    lk = sub.add_parser("lookup", help="a typed reference to its verse text (JSON)")
    lk.add_argument("reference")
    lk.add_argument("--translation", default="ESV")
    lk.set_defaults(func=cmd_lookup)

    cp = sub.add_parser("captions", help="YouTube subtitles (.srt) and a readable transcript")
    cp.add_argument("cues", help="a cue file produced by analyze")
    cp.add_argument("--map", help="JSON list of how the recording sits in the edit")
    cp.add_argument("--out-dir", help="folder to write into (default: out/)")
    cp.add_argument("--name", help="file name to use (default: the video's name)")
    cp.set_defaults(func=cmd_captions)

    sg = sub.add_parser("snippet-suggest", help="moments that would make good short clips")
    sg.add_argument("cues")
    sg.add_argument("--map", help="JSON list of how the recording sits in the edit")
    sg.add_argument("--count", type=int, default=5)
    sg.set_defaults(func=cmd_snippet_suggest)

    sn = sub.add_parser("snippet", help="prepare one vertical clip for After Effects")
    sn.add_argument("cues")
    sn.add_argument("--from", dest="start", required=True, help="start, seconds into the edit")
    sn.add_argument("--to", dest="end", required=True, help="end, seconds into the edit")
    sn.add_argument("--map", help="JSON list of how the recording sits in the edit")
    sn.add_argument("--out", required=True, help="where to write the clip's JSON")
    sn.add_argument("--no-follow", action="store_true", help="fixed framing, no tracking")
    sn.set_defaults(func=cmd_snippet)

    fn = sub.add_parser("furniture", help="pre-draw the slide backdrop, panel, frame and logo")
    fn.add_argument("--width", type=int, required=True)
    fn.add_argument("--height", type=int, required=True)
    fn.add_argument("--force", action="store_true")
    fn.add_argument("--deck", action="store_true",
                    help="the arrangement for a session built on a presentation")
    fn.set_defaults(func=cmd_furniture)

    dc = sub.add_parser("documents", help="confessions and catechisms to show on screen")
    dc.add_argument("action", choices=["add", "list"])
    dc.add_argument("name", nargs="?", default="", help="e.g. dort")
    dc.set_defaults(func=cmd_documents)

    cl = sub.add_parser("clean", help="clean up a mic recording (noise, hum, room, level)")
    cl.add_argument("audio", help="the mic recording")
    cl.add_argument("--out", help="where to write the cleaned WAV")
    cl.add_argument("--strength", default="medium", choices=["light", "medium", "strong", "voice"])
    cl.add_argument("--dereverb", action="store_true", help="always pull the room down")
    cl.add_argument("--no-dereverb", action="store_true", help="leave the room alone")
    cl.add_argument("--json", action="store_true")
    cl.set_defaults(func=cmd_clean)

    cv = sub.add_parser("clean-preview", help="the same seconds before and after cleanup")
    cv.add_argument("audio")
    cv.add_argument("--out-dir", required=True)
    cv.add_argument("--at", type=float, help="seconds in (default: a speech-heavy spot)")
    cv.add_argument("--seconds", type=float, default=20.0)
    cv.add_argument("--strength", default="medium", choices=["light", "medium", "strong", "voice"])
    cv.add_argument("--dereverb", action="store_true")
    cv.add_argument("--no-dereverb", action="store_true")
    cv.set_defaults(func=cmd_clean_preview)

    cq = sub.add_parser("clean-profile", help="what is wrong with a recording")
    cq.add_argument("audio")
    cq.add_argument("--strength", default="medium", choices=["light", "medium", "strong", "voice"])
    cq.add_argument("--json", action="store_true")
    cq.set_defaults(func=cmd_clean_profile)

    dk = sub.add_parser("deck", help="presentation slides to show beside the speaker")
    dk.add_argument("action", choices=["import", "place", "show"])
    dk.add_argument("path", nargs="?", default="",
                    help="the deck (PDF, folder of pictures, or .pptx), or a deck id for show")
    dk.add_argument("--cues", help="the analysed sermon to place the slides in")
    dk.add_argument("--force", action="store_true", help="convert again, ignoring the cache")
    dk.add_argument("--json", action="store_true")
    dk.set_defaults(func=cmd_deck)

    ra = sub.add_parser("reanalyze", help="re-run the analysis from a saved transcript")
    ra.add_argument("cues", help="a cue file produced by analyze")
    ra.add_argument("--json", action="store_true")
    ra.set_defaults(func=cmd_reanalyze)

    fl = sub.add_parser("follow", help="keep the preacher centred in the slide camera")
    fl.add_argument("--media", required=True)
    fl.add_argument("--windows", required=True, help="JSON [[start, end], …] in comp time")
    fl.add_argument("--map", help="JSON list of how the recording sits in the edit")
    fl.add_argument("--crop-width", required=True, help="how much of the width the inset shows")
    fl.add_argument("--out", required=True)
    fl.set_defaults(func=cmd_follow)

    s = sub.add_parser("show", help="print a cue file")
    s.add_argument("cues")
    s.set_defaults(func=cmd_show)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
