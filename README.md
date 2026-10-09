# TEC Sermon Studio

Sermon post-production for After Effects. Point it at a sermon and it
transcribes the mic recording on this Mac, then builds:

* **Verse cards** — every passage the preacher cites or reads, with the verse
  text, timed to appear as he says the reference and leave as he moves on.
  Either as lower thirds over your edit, or as the slide layout with the
  camera inset beside the verse.
* **Subtitles and a transcript** — a YouTube-ready `.srt` and a readable
  transcript with the scriptures listed by timestamp, saved beside your
  project.
* **Snippets** — vertical clips for social media, with big captions and a
  frame that follows the pastor as he moves.

No typing. No scrubbing the timeline looking for "turn with me to…".

---

## Install

```bash
cd "TEC Sermon Studio" && ./setup.sh
```

That installs the Python packages, downloads an offline Bible, enables
unsigned extensions in After Effects, and links the panel.
Then restart After Effects and open **Window → Extensions → TEC Sermon Studio**.

Requires macOS, Python 3.10+, ffmpeg (`brew install ffmpeg`) and
After Effects 2020 or newer.

## Use

1. Open your sermon comp with the camera footage on the timeline.
2. Open the TEC Sermon Studio panel. It reads the comp and finds the video layer.
3. **Analyze sermon.** First run downloads the speech model once (~2.5 GB);
   after that a full sermon takes about 3 minutes.
4. Review the list. Each row shows the reference, the timecode, the verse text
   and how it was found. Click a timecode to jump the playhead there.
   Untick anything you do not want.
5. **Build in After Effects.**

You get a comp called *<your comp> — Verse Captions* layered on top of your
edit, containing native shape and text layers — one controller null per verse,
with slide and fade keyframes. Everything is editable. Delete a card, retime
it, restyle it, nothing is baked.

Re-running Build wipes that captions comp and rebuilds it, so iterate freely.

## What gets analyzed, and where the cards land

SermonStudio analyzes **the recorded video file**, not the comp. Cue times are
relative to the original recording, start to finish, regardless of what you
have done in the edit.

The comp is only consulted to answer a second question: where did each of
those moments end up? It finds every piece of that recording on the timeline
— trimmed, split into several pieces, time-stretched or sitting inside a
precomp — and maps each cue through the piece that contains it.

That means:

* Trim the first fifteen minutes of pre-service and the cards still line up.
* Cut the footage into six pieces and every card lands on the right frame.
* Cut out a section where he read a verse, and **no card is built for it**.
  The panel tells you how many were skipped for that reason.
* A card that starts before a cut ends at the cut rather than running over it.

To pick a specific recording, select its layer in the timeline before you hit
Analyze. Otherwise the longest piece of footage in the comp is used, which on
a sermon timeline is the camera. **Choose file…** analyzes any file on disk;
if it is not on the timeline, cards are built at raw recording times.

## How it decides what to put on screen

**Spoken references.** The transcript is scanned for citations in the forms
preachers actually use: *"Judges chapter sixteen verse twenty"*,
*"first Corinthians thirteen four through seven"*, *"John three sixteen"*,
*"James tells us in chapter one verse fourteen"*. Book names are matched
through an alias table and fuzzy-matched when Whisper mishears them.

**Context carry-over.** This is the part that matters. Once a passage is
announced, a preacher walks through it by verse number alone — *"now look at
verse twenty one"*, *"verse twenty two"*. SermonStudio remembers the current
book and chapter and resolves those, which is where most of the cues in a
typical sermon come from.

**Readings.** Every sentence is also compared against all 31,000 verses. That
catches passages quoted without a reference, and more importantly it tells the
timing engine how long he actually stays in the verse. Matching tolerates a
different translation — it scores rare shared words ("Philistines", "awoke",
"Nazirite") and checks they arrive in the verse's own order.

**False positives.** "Mark said to me last Sunday" is not Mark's gospel, and
"Judges sixteen. Twenty years later…" is not Judges 16:20. Ambiguous book
names need a number close behind; verse numbers that cannot exist are dropped;
readings with no spoken reference must clear a much higher bar before they get
a card of their own. Anything borderline still appears in the panel, unticked
and badged **check**, rather than being silently dropped.

## Transcription

SermonStudio uses **NVIDIA Parakeet** (`parakeet-tdt-0.6b-v2`), running on the
Mac's GPU. It was chosen by transcribing two real sermon readings with every
local option and scoring each against the ESV text actually being read:

| model | accuracy | time per sermon |
|---|---|---|
| Whisper small | 27% | ~9 min |
| Whisper large-v3-turbo | 67% | — |
| Whisper medium | 73% | ~20 min |
| Whisper large-v3 | 80% | ~1 hour |
| **Parakeet** | **90%** | **~3 min** |

Whisper models dropped whole stretches of scripture reading — seven verses of
Hosea 2 in one test — which no amount of downstream checking can recover.

## Reviewing a sermon

Every card is checked against what was actually said while it is on screen,
and the list tells you which ones need a human:

| badge | meaning | what to do |
|---|---|---|
| **look** | something about this card is doubtful — the reason is shown under it | read the reason, check it |
| **switched off** | a loose match nobody read; already unticked | nothing, unless it was right |
| **cited** | named by the preacher but paraphrased, not read | usually fine |
| *(translation)* | confirmed: the verse's words were heard | nothing |

Click **Flagged** to see only the cards that need a look. On the sermons it has
been tested on, that is zero to one card per service.

What it handles on its own:

* **Reading on past the citation.** "From verse 8" is followed into 9, 10, 11
  for as long as the words match.
* **Skipped verses.** A verse the preacher jumped over is removed, and the
  reading either side closes the gap.
* **Self-corrections.** "Verse 28 — no, let's go back to 15" flags 28.
* **Loose matches.** A verse guessed from a couple of shared words is switched
  off rather than shown.
* **Missing transcript.** Where the recording or transcription dropped out,
  cards are left alone rather than judged on nothing.

`run-tests.sh` replays real sermons and checks the things that once went wrong
on them still come out right.

## When cards enter and leave

The out point is evidence-driven, the way a live operator works:

| Signal | Effect |
|---|---|
| Reference spoken | card in, 0.15 s early |
| Passage still being read | held, up to 45 s |
| Reading finished | out 1.6 s later |
| Next reference cited | out just before it |
| Long pause (3 s+) | out at the pause |
| Nothing else | out after 4.5–15 s |

The same verse cited twice within 25 seconds becomes one continuous card
rather than two flashes. Cards never overlap.

Tune any of it in `config/timing.json`.

## Translations

KJV, BSB and ASV work offline with no key. Copyrighted translations need a
free key in `config/keys.json`:

| Translation | Where to get a key |
|---|---|
| ESV | api.esv.org — free for church use |
| NLT | api.nlt.to — free |
| NIV, NKJV, NASB, CSB, AMP | scripture.api.bible — free tier |

```json
{ "esv": "your-token-here" }
```

Verse text is cached permanently after the first fetch, so repeat builds and
offline edit sessions work fine. If a key is missing, the cue keeps its timing
and falls back to the offline translation so you are never blocked.

The **corpus** (used for matching, never shown on screen) stays BSB regardless
— it only needs to be complete and local.

## Styling

`config/style.json` controls everything visual: the red reference chip, the
cream text bar, fonts, padding, corner radius, slide distance and animation
timing. Defaults match the on-screen look you're already using. Sizes are in
1920×1080 units and scale automatically to the comp.

Fonts are given as a preference list; the first one installed wins, so a comp
opened on another machine still builds.

## Final Cut Pro

The analysis is editor-agnostic — only the last step knows what an editor is.
For Final Cut that step writes an FCPXML:

```bash
python3 engine/cli.py fcpxml out/sermon.cues.json
python3 engine/cli.py clean-profile mic.wav
python3 engine/cli.py clean mic.wav --out "mic (cleaned).wav" --strength medium
python3 engine/cli.py clean-preview mic.wav --out-dir out/cleanup --at 1800
python3 engine/cli.py deck import "Reformation.pdf"
python3 engine/cli.py deck place <deck id> --cues out/sermon.cues.json
```

It renders each verse card as a transparent PNG using the same
`config/style.json` the After Effects build uses, then writes a timeline that
places them at the right frames with fade and slide keyframes. In Final Cut:
**File → Import → XML**, then copy the connected clips onto your own edit.

`--with-video` puts the sermon clip on the timeline too, so you can check the
placement before copying. `--offset` shifts every card.

The difference from the After Effects build is that the card text is baked
into the PNG rather than staying editable. For auto-generated verse cards that
is usually the right trade; if a verse is wrong, untick it and rebuild.

Why Final Cut might be worth it: After Effects composites every frame through
the whole layer stack, so an 85-minute sermon is hours of rendering before any
encoding. Final Cut leaves untouched frames alone and hands encoding to the
Apple silicon media engine.

## Separately recorded mic audio

Church audio is usually captured on a desk or lapel mic, not the camera, and
the two recordings start at different moments because two different people hit
record. SermonStudio transcribes the clean mic track and puts the cues on the
camera's timeline:

```bash
python3 engine/cli.py analyze sermon.mp4 --audio "mic recording.wav"
```

The offset is measured by cross-correlating the camera's own scratch audio
against the mic recording — the same acoustic events in both files, matched to
about a millisecond. Far tighter than lining up waveforms by eye, and tighter
than matching transcripts, where a word boundary is only good to a couple of
hundred milliseconds.

To just get the number, without any verse detection:

```bash
python3 engine/cli.py sync sermon.mp4 "mic recording.wav"
```

```
offset -29.999s (the mic started 29.999s before the camera)
confidence 44.1 (solid)
```

Set the audio layer's start time by that offset and the two are locked.

It also measures **drift**. Two recorders on different clocks diverge slowly;
20 parts per million is a second and a half across an 85-minute sermon, which
starts in sync and ends visibly out. When drift is significant it reports the
stretch percentage that cancels it.

Confidence below 8 means it could not find a convincing match — usually the
wrong pair of files, or camera audio too poor to compare. It says so rather
than quietly returning a wrong number.

## Presentation slides

Some sessions are not carried by Scripture. A talk on the history of the
Reformation used a handful of verses and a 35-slide deck, so the deck takes
the panel the way a verse does: one slide at a time beside the speaker,
fading in and out, changing when he moves on.

In the panel's **Slides** section, **Choose deck…** takes a PDF or a .pptx,
**Folder…** takes a folder of exported pictures, and **Place from transcript**
works out when each slide is being talked about. The slides then appear in the
review list with the verses, ticked, timed and with their own thumbnails, and
**Build** puts them in the comp.

What to export: a **PDF** or **PNGs**. Both come out of Canva, Keynote,
PowerPoint and Google Slides in one step and keep the design exactly. A .pptx
works when its slides are full-bleed pictures, which is what the design tools
produce; one built from text and shapes cannot be drawn here, because that
needs PowerPoint, Keynote or LibreOffice, and none are installed. Keynote
files say the same. The conversion itself is Apple's own: `engine/deck.swift`
renders pages through PDFKit and reads the words with Vision, so nothing has
to be installed - there is no pdftoppm, poppler or PyObjC on this machine.

**The words on each slide** are what place it. The text layer is used when it
is sound and Vision reads the slide when it is not - a deck whose font had no
usable character map gave "3 Octobe 5" for "31 October 1517", which is long
enough to look like text and useless to match on, so both are scored and the
better one wins.

**When each slide goes up** comes from the transcript. Every word is weighted
by how rare it is *in that talk*, since the words that matter in a session on
the Reformation are not the ones that matter in a session on Samson; a slide
scores best where its own words start, not merely where they appear, because
scoring by plain coverage put every slide up to a window - 75 seconds - early
and truncated the slide before it. The deck is then aligned as a whole, in
order and never going backwards, the same way a passage of verses is.

A slide nothing was said about - a portrait, a map, a quotation he reads
without comment - is placed between its neighbours and flagged for a look
rather than passed off as matched. If no slide in the deck can be matched at
all, the deck is spread evenly across the talk so it is all there to adjust.
Each slide holds until the next one starts, so the panel is never left empty,
and the last one stays up to the end of the talk.

Placing a deck leaves the verses exactly as they are, including what you
ticked and unticked, and placing it again replaces the slides rather than
stacking a second copy. Slides need the **slide panel** layout; a lower third
has nowhere to put them, and a build says so rather than leaving them out
quietly.

## Audio cleanup

The **Audio cleanup** section does locally what Adobe Podcast does in a
browser: noise, hum, room, tone and level, with nothing uploaded anywhere.
After Effects itself has no noise reduction, and Enhance Speech is a Premiere
feature, so this runs on ffmpeg.

It listens to the recording before deciding what to do, which is the part a
fixed preset gets wrong:

- **Noise floor** measured in the gaps *between phrases*, not across the whole
  file. The recorder runs before and after the service, and that dead air is
  digital silence — measuring it rated a noisy sermon as quiet. Our mixer also
  gates the mic, so the level is read off the gaps that are not silent, while
  how *often* they are quiet decides how hard the denoiser works. A gated
  channel has nothing steady to subtract and leaning on it only risks the
  voice.
- **Mains hum** found by looking for narrow peaks at 50 or 60 Hz and their
  harmonics, and notched only where one is really there. Above 200 Hz a notch
  starts to sit in the voice, so it takes clearer evidence.
- **The room**, as a decay slope at the end of loud phrases, quoted as the
  RT20 it implies. Over 0.3s the tails between words are pulled down. This is
  not true dereverb — nothing local does that — but it closes a live hall up.
- **Brightness**, as 6–10 kHz against 1–4 kHz over speaking frames, which
  decides whether air is added. It is deliberately not a harshness detector:
  real speech has consonants 15–20 dB above the voice body, and the first
  attempt rated a real sermon worse than audio with harshness put in on
  purpose.
- **Level**, set to about −16 LUFS and −1.1 dBTP, which is what YouTube
  expects. Our recordings come in around −27 dBFS peak, so this is most of
  what makes the result sound finished. Three things had to be right for it to
  land: the loudness is measured over the whole processed recording in a pass
  of its own (sampling speech-heavy spots read a 75-minute sermon 4.7 LU too
  loud); the voice is brought to a working level *before* the gate and
  compressor, which work to absolute thresholds and never engaged at all on a
  recording sitting at −43 dBFS; and the cleaned file keeps the source's
  channel count, because measuring a stereo chain and writing mono threw the
  target out by another 3 LU. The last stage is a measured gain and a limiter
  rather than loudnorm's own normaliser — loudnorm lands slightly closer, but
  how much it delays the audio depends on which mode it picks, and sync
  matters more here than half a LU.

Two passes over the audio, then — about a minute each for a 75-minute sermon,
so a couple of minutes in all.

**Preview** plays twenty seconds before and after in the panel. Both halves
are matched for loudness on purpose — against a raw original the cleaned
version just sounds louder, which tells you nothing.

**Add to After Effects** imports the cleaned WAV, gives it the mic layer's
position, stretch and trims, and mutes the original. That is safe because the
cleaned file is the same length and lines up sample for sample: the filters
delay what passes through them — about 30 ms through this chain, roughly a
frame — and ffmpeg does not report it, so the engine measures the delay on the
recording itself and takes it back off. Left uncorrected it would shift sync
and every cue timing with it.

**Use for analysis** transcribes the cleaned file instead of the original.
Re-run Analyze afterwards; a sync offset already measured still holds.

Strengths are Light, Medium, Strong and Voice only. On a well-recorded sermon
the honest gain is level and tone rather than denoise. For noise the ceiling
here is spectral subtraction; a neural model does better, and the chain will
use one automatically if an RNNoise `.rnnn` file is put in `data/models`.

## Command line

The panel is a front end for this:

```bash
python3 engine/cli.py analyze sermon.mp4 --translation ESV
python3 engine/cli.py show out/sermon.cues.json
python3 engine/cli.py corpus KJV
python3 engine/cli.py sync sermon.mp4 mic.wav
python3 engine/cli.py fcpxml out/sermon.cues.json
python3 engine/cli.py clean-profile mic.wav
python3 engine/cli.py clean mic.wav --out "mic (cleaned).wav" --strength medium
python3 engine/cli.py clean-preview mic.wav --out-dir out/cleanup --at 1800
python3 engine/cli.py deck import "Reformation.pdf"
python3 engine/cli.py deck place <deck id> --cues out/sermon.cues.json
```

`analyze` writes `out/<name>.cues.json`, which the AE script reads. Useful for
batching a whole Sunday overnight. `--transcript` reuses an existing
word-level transcript instead of re-running Whisper.

## Render times

Measured on this machine, from a ProRes master, per 20 seconds of 4K footage
and extrapolated to an 85-minute sermon:

| path | 20s | 85 min |
|---|---|---|
| 4K H.264, software (After Effects' own encoder) | 113.7s | ~8 hours |
| 4K H.264, hardware (media engine) | 13.9s | ~59 min |
| 4K HEVC, hardware | 15.0s | ~64 min |
| 1080p H.264, hardware | 5.9s | ~25 min |
| decode ProRes only (the floor) | 5.6s | ~24 min |

Software H.264 is roughly **8x slower** than hardware at 4K, and it is usually
the single largest cost in an After Effects export — larger than compositing.

So do not export H.264 from After Effects. Render a ProRes master from AE's own
render queue, then:

```bash
tools/encode.sh master.mov          # 1080p
tools/encode.sh master.mov --4k     # keep 4K
tools/encode.sh master.mov --hevc   # HEVC instead
```

Media Encoder can do the same job if *Performance: Hardware Encoding* is
ticked — it is the same underlying encoder. What does not help is sending the
comp to Media Encoder over Dynamic Link: the encode gets faster but the render
gets slower, because frames come from a headless After Effects.

Two other things worth checking:

* **Multi-Frame Rendering** — Preferences > Memory & Performance. Off means
  rendering on roughly one core.
* **The camera codec.** A 10-bit 4:2:2 H.264 master has no hardware decoder on
  Apple silicon; decoding alone measured 2.2x realtime with 5.6 cores busy, and
  After Effects pays that on every frame. Transcoding to ProRes decoded 2.2x
  faster, at the cost of roughly 185 GB per sermon.

## If something goes wrong

**Panel is blank or missing from the Extensions menu.** Re-run `./setup.sh`
and fully quit After Effects (⌘Q, not just closing the project).

**"engine folder not found".** The panel is linked but cannot see the Python
side — re-run `./setup.sh`, which rewrites `cep/engine-path.txt`.

**"Could not start Python".** Put the full path to your python3 in the panel's
settings (the ⚙ button).

**Verse text is empty.** That translation needs an API key in
`config/keys.json`. The timing is still correct — add the key and rebuild.

**A card is on screen too long or too short.** Its out point is a keyframed
null like any other layer. Drag it. Or change `config/timing.json` and rebuild
to fix the pattern everywhere.

**A book name is wrong.** Usually fixed on its own: when the verses cited
don't match what is read, SermonStudio checks every book for that chapter and
verse and uses the one the reading matches ("in 1966" -> 1 Timothy 6). The card
notes when this happened.

## Layout

```
engine/      Python: transcribe, detect, match, time, fetch verse text
  books.py     66 books, aliases, fuzzy matching
  spoken_numbers.py  spoken numbers ("one hundred and nineteen" -> 119)
  refs.py      citation detection and context carry-over
  quotes.py    whole-Bible reading matcher
  timing.py    cue in/out logic
  bible.py     translation sources and local cache
  align.py     camera/mic sync by cross-correlation
  cards.py     verse cards rendered to PNG (for Final Cut)
  fcpxml.py    Final Cut timeline writer
cep/         The After Effects panel
  jsx/SermonStudio.jsx   builds the layers
config/      style, detection and timing settings; API keys
data/        Bible database, transcript cache (generated)
out/         cue files (generated)
```
