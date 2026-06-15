# YouTube Channel Transcript Archiver

Transcribes an **entire YouTube channel** into a single clean Markdown file — every video, with title, URL, upload date, duration, and full timestamped transcript.

```
## Video 3: How to LEARN so FAST it feels ILLEGAL

* URL: https://www.youtube.com/watch?v=...
* Upload Date: 2024-11-10
* Duration: 06:42

### Transcript

[00:00] There's a method top students use...
[00:04] that most people have never heard of.
...
```

---

## Requirements

- Python 3.8+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) (installed via pip, **not** the system package)
- A browser with a YouTube login (Chrome, Firefox, Safari, or Edge)
- *(Optional)* [ffmpeg](https://ffmpeg.org/) + `openai-whisper` for offline Whisper fallback

```bash
pip install -r requirements.txt
```

> **Firefox / Safari / Edge users:** pass `--browser firefox` (or `safari`/`edge`) — see flags below.

---

## Quick Start

```bash
python3 archive_channel.py "https://www.youtube.com/@channel_name"
```

Output is written to `channel_archive.md` in the current directory. The run is **resumable** — if interrupted, re-run the same command and it skips already-completed videos.

---

## All Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--browser` | `chrome` | Browser to pull cookies from: `chrome`, `firefox`, `safari`, `edge` |
| `--lang` | `en` | Preferred caption language code |
| `--no-whisper` | off | Skip Whisper offline fallback (faster) |
| `--whisper-model` | `base` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large` |

```bash
# Firefox user
python3 archive_channel.py "https://www.youtube.com/@channel_name" --browser firefox

# Non-English channel
python3 archive_channel.py "https://www.youtube.com/@channel_name" --lang ja

# Faster, no Whisper
python3 archive_channel.py "https://www.youtube.com/@channel_name" --no-whisper
```

---

## Why Browser Cookies Are Needed

YouTube detects and blocks automated transcript/subtitle requests from IP addresses without a valid session. Passing `--cookies-from-browser` lets yt-dlp borrow your existing logged-in YouTube session from your browser — **no passwords are read or stored**, only the session cookie.

This is a standard yt-dlp feature. See: [yt-dlp FAQ on cookies](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)

---

## How It Works — Transcript Priority

For each video the script tries three methods in order, stopping at the first success:

1. **yt-dlp subtitle download** — downloads the `.vtt` caption file directly (works for most videos with auto-captions or manual subtitles)
2. **youtube-transcript-api** — Python library fallback
3. **Whisper** *(optional)* — downloads audio and transcribes offline using OpenAI's Whisper model

---

## Whisper Fallback (Optional)

For videos with no captions at all, Whisper can transcribe the audio locally. Requires ffmpeg:

```bash
# macOS
brew install ffmpeg

# then install whisper
pip install openai-whisper
```

Then run without `--no-whisper` and optionally pick a larger model for better accuracy:

```bash
python3 archive_channel.py "https://www.youtube.com/@channel_name" --whisper-model small
```

> `small` is a good balance of speed vs accuracy. `large` is best quality but slow.

---

## Bugs Fixed (Development Notes)

This section documents the issues discovered and fixed during development, so you don't have to deal with them.

### 1. `youtube-transcript-api` v1.x API change
**Problem:** The old code used `YouTubeTranscriptApi.list_transcripts(video_id)` (static class method from v0.x). Version 1.x changed to an instance-based API: `YouTubeTranscriptApi().list(video_id)`.  
**Symptom:** All transcripts returned `None` silently.  
**Fix:** Changed to instantiate the class first, then call `.list()`.

### 2. YouTube IP blocking (`RequestBlocked`)
**Problem:** `youtube-transcript-api` makes direct HTTP requests to YouTube. YouTube blocks these from IPs that look automated (no valid session).  
**Symptom:** `RequestBlocked` exception — "Sign in to confirm you're not a bot."  
**Fix:** Switched primary transcript method to **yt-dlp subtitle download**, which uses browser cookies to authenticate and bypasses the block. The API is kept as a secondary fallback.

### 3. VTT output path — `yt-dlp -o` pattern
**Problem:** The original `-o` template was `video_id + ".%(ext)s"` which yt-dlp interpreted as a literal filename prefix with no directory, causing files to be saved with unexpected names.  
**Fix:** Changed to `-o workdir/%(id)s.%(ext)s` so yt-dlp uses the video ID as the filename and the VTT search by `startswith(video_id)` works correctly.

### 4. Repeating/duplicate transcript lines (the big one)
**Problem:** Every line in the output appeared **3 times** — e.g.:
```
[03:54] days anymore. Which one of these hacks
[03:55] is new for you and which one are you
[03:54] days anymore. Which one of these hacks
[03:55] is new for you and which one are you
[03:54] days anymore. Which one of these hacks
```
**Root cause:** YouTube's auto-caption VTT format uses a **rolling/sliding window**. Each sentence is split across three cue blocks:
- Block 1: new words with inline `<HH:MM:SS><c>` word-timing tags
- Block 2: plain-text recap of block 1 (no new words — pure duplicate)
- Block 3: tail of block 1 as prefix + new words with timing tags

The old `parse_vtt` collected all text lines from all blocks, producing 3 copies of everything.  
**Fix:** Complete rewrite of `parse_vtt`:
1. Parse file into cue blocks (blank-line separated)
2. Only keep lines containing `<HH:MM:SS>` timing tags — these are the "growing" lines with new content; plain-text recap lines are discarded
3. Strip all HTML/timing tags
4. Deduplicate consecutive identical cues
5. Word-overlap pass: YouTube carries the tail of the previous cue as a prefix of the next — find the longest word-boundary overlap and strip it

### 5. Channel name always `"Unknown"`
**Problem:** The flat-playlist enumeration doesn't return `channel`/`uploader` fields. The script only looked at the first video's metadata, which sometimes also lacked these fields.  
**Fix:** Priority chain: metadata `channel` → metadata `uploader` → URL handle (e.g. `@hugh_knows` → `hugh_knows`) → last path segment of URL.

### 6. `python` command not found on macOS
**Problem:** macOS uses `python3`, not `python`.  
**Fix:** Use `python3` in all commands.

### 7. `--browser` hardcoded to Chrome
**Problem:** `--cookies-from-browser chrome` was hardcoded, breaking the script for Firefox/Safari/Edge users.  
**Fix:** Added `--browser` CLI flag (default: `chrome`).
