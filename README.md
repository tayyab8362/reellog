# YouTube Channel Transcript Archiver

Transcribes a **YouTube channel or single video** into a clean Markdown file — with title, URL, upload date, duration, full timestamped transcript, and optional keyframe screenshots.

```
## Video 3: How to LEARN so FAST it feels ILLEGAL

* URL: https://www.youtube.com/watch?v=...
* Upload Date: 2024-11-10
* Duration: 06:42

### Screenshots (8 keyframes)
![00:14](screenshots/abc123/00-14.jpg)
...

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
- [ffmpeg](https://ffmpeg.org/) — required for screenshots (`brew install ffmpeg` on macOS)
- *(Optional)* `openai-whisper` for offline transcription of videos with no captions

```bash
pip install -r requirements.txt
```

---

## All Commands

### Entire channel — transcript only
```bash
python3 archive_channel.py "https://www.youtube.com/@channel_name"
```

### Entire channel — transcript + screenshots
```bash
python3 archive_channel.py "https://www.youtube.com/@channel_name" --screenshots
```

### Single video — transcript only
```bash
python3 archive_channel.py "https://www.youtube.com/watch?v=VIDEO_ID" --video
```

### Single video — transcript + screenshots
```bash
python3 archive_channel.py "https://www.youtube.com/watch?v=VIDEO_ID" --video --screenshots
```

### Firefox / Safari / Edge (not Chrome)
```bash
python3 archive_channel.py "https://www.youtube.com/@channel_name" --browser firefox
python3 archive_channel.py "https://www.youtube.com/@channel_name" --browser safari
python3 archive_channel.py "https://www.youtube.com/@channel_name" --browser edge
```

### Non-English channel
```bash
python3 archive_channel.py "https://www.youtube.com/@channel_name" --lang ja
```

### Skip Whisper fallback (faster, no audio download)
```bash
python3 archive_channel.py "https://www.youtube.com/@channel_name" --no-whisper
```

### Control screenshot density
```bash
# Default — catches most scene changes (~100-150 frames per 8min video)
python3 archive_channel.py "https://www.youtube.com/@channel_name" --screenshots

# More frames — catches subtle visual shifts
python3 archive_channel.py "https://www.youtube.com/@channel_name" --screenshots --scene-threshold 0.02

# Fewer frames — only major scene cuts
python3 archive_channel.py "https://www.youtube.com/@channel_name" --screenshots --scene-threshold 0.2
```

### Better Whisper accuracy (for videos with no captions)
```bash
python3 archive_channel.py "https://www.youtube.com/@channel_name" --whisper-model small
```

---

## All Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--video` | off | Treat the URL as a single video instead of a channel |
| `--screenshots` | off | Capture unique keyframe screenshots per video |
| `--scene-threshold` | `0.05` | ffmpeg scene sensitivity `0.0`–`1.0` (lower = more frames) |
| `--browser` | `chrome` | Browser for cookies: `chrome`, `firefox`, `safari`, `edge` |
| `--lang` | `en` | Preferred caption language code |
| `--no-whisper` | off | Skip Whisper offline fallback |
| `--whisper-model` | `base` | Whisper model: `tiny`, `base`, `small`, `medium`, `large` |

---

## Output Structure

Each run saves into its own folder under `output/` so runs never overwrite each other:

```
output/
  channel__Hugh_Knows/          ← full channel run
    archive.md                  ← all transcripts
    screenshots/
      <video_id>/               ← one folder per video
        00-14.jpg               ← keyframe at 0m 14s
        01-32.jpg
        ...

  video__PcC3OvlPDcE/           ← single video run
    archive.md
    screenshots/
      PcC3OvlPDcE/
        00-08.jpg
        ...
```

Screenshots are **embedded inline in `archive.md`** at the exact timestamp where the scene changed, right above the matching transcript line:

```
![Scene change at [00:14]](output/video__PcC3OvlPDcE/screenshots/PcC3OvlPDcE/00-14.jpg)
[00:14] Here's what most people get wrong...
```

Run is **resumable** — if interrupted, re-run the same command and it skips already-done videos.

---

## Why Browser Cookies Are Needed

YouTube blocks automated requests without a valid session. The script passes `--cookies-from-browser` to yt-dlp, which borrows your existing logged-in YouTube session — **no passwords are read or stored**, only the session cookie.

See: [yt-dlp FAQ on cookies](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)

---

## How Transcripts Are Fetched (Priority Order)

1. **yt-dlp subtitle download** — downloads `.vtt` caption file directly (works for most videos)
2. **youtube-transcript-api** — Python library fallback
3. **Whisper** *(optional)* — downloads audio and transcribes offline

---

## How Screenshots Work

ffmpeg's `select=gt(scene,threshold)` filter detects scene changes and extracts only frames where the visual content changes significantly. Falls back to 1 frame every 30 seconds if no scene changes are detected. Downloaded video is deleted after frame extraction to save disk space.

---

## Whisper Setup (Optional)

Only needed for videos with no captions at all:

```bash
brew install ffmpeg          # macOS
pip install openai-whisper
```

> `--whisper-model small` is a good balance of speed vs accuracy. `large` is best but slow.

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
