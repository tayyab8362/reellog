
"""
archive_channel.py — Transcribe an entire YouTube channel into one Markdown file.

Implements the spec:
  * Enumerate every public video (long-form, Shorts, playlists) via yt-dlp.
  * Oldest -> newest ordering.
  * Per video: title, URL, upload date, duration, full timestamped transcript.
  * Transcript priority: (1) official/manual captions, (2) auto-generated
    captions, (3) Whisper fallback on downloaded audio.
  * Incremental append to channel_archive.md (resumable, dedup-safe).
  * Failed videos logged at the end.

USAGE
  python3 archive_channel.py "https://www.youtube.com/@channel_name"
  python3 archive_channel.py "<url>" --no-whisper          # skip Whisper fallback
  python3 archive_channel.py "<url>" --lang en              # preferred caption lang
  python3 archive_channel.py "<url>" --whisper-model small
  python3 archive_channel.py "<url>" --browser firefox      # if not using Chrome

REQUIREMENTS
  pip install yt-dlp youtube-transcript-api
  # Whisper fallback (optional, large): pip install openai-whisper  + ffmpeg installed
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

OUT_FILE = "channel_archive.md"
STATE_FILE = ".archive_state.json"   # tracks completed video IDs for resume/dedup


# ----------------------------- helpers -------------------------------------- #

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def fmt_ts(seconds: float) -> str:
    """Seconds -> [MM:SS] (or [H:MM:SS] past an hour)."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"[{h}:{m:02d}:{s:02d}]"
    return f"[{m:02d}:{s:02d}]"


def fmt_date(yyyymmdd: str) -> str:
    if not yyyymmdd or len(yyyymmdd) != 8:
        return "Unknown"
    try:
        return datetime.strptime(yyyymmdd, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return yyyymmdd


def fmt_duration(seconds) -> str:
    if not seconds:
        return "Unknown"
    return fmt_ts(seconds).strip("[]")


def load_state() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f).get("done", []))
    return set()


def save_state(done: set):
    with open(STATE_FILE, "w") as f:
        json.dump({"done": sorted(done)}, f)


# ----------------------- channel enumeration -------------------------------- #

def ytdlp_auth(browser: str) -> list:
    return ["--cookies-from-browser", browser, "--remote-components", "ejs:github"]


def enumerate_videos(channel_url: str, browser: str):
    """Return list of dicts (flat) for every video, oldest first.
    Pulls /videos, /shorts, and /streams tabs so nothing is missed."""
    base = channel_url.rstrip("/")
    # If a bare channel URL is given, expand to its content tabs.
    tabs = [base]
    if not re.search(r"/(videos|shorts|streams|playlist)", base):
        tabs = [f"{base}/videos", f"{base}/shorts", f"{base}/streams"]

    seen, items = set(), []
    for tab in tabs:
        proc = run([
            "yt-dlp", *ytdlp_auth(browser), "--flat-playlist", "--ignore-errors",
            "--dump-json", tab,
        ])
        for line in proc.stdout.splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            vid = e.get("id")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            items.append(e)
        if proc.returncode != 0 and not proc.stdout.strip():
            print(f"  (tab not available or empty: {tab})", file=sys.stderr)

    return items


def fetch_metadata(video_id: str, browser: str):
    """Full metadata for one video (title, date, duration)."""
    proc = run([
        "yt-dlp", *ytdlp_auth(browser), "--no-warnings", "--skip-download", "--dump-json",
        f"https://www.youtube.com/watch?v={video_id}",
    ])
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


# ----------------------- transcript strategies ------------------------------ #

def transcript_via_api(video_id: str, lang: str):
    """Priority 1 & 2: manual then auto-generated captions via youtube-transcript-api."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None

    try:
        api = YouTubeTranscriptApi()
        listing = api.list(video_id)
        order = []
        try:
            order.append(listing.find_manually_created_transcript([lang]))
        except Exception:
            pass
        try:
            order.append(listing.find_generated_transcript([lang]))
        except Exception:
            pass
        for t in listing:
            order.append(t)

        seen_t = set()
        for t in order:
            if id(t) in seen_t:
                continue
            seen_t.add(id(t))
            try:
                data = t.fetch()
                if data:
                    normalized = [
                        {"text": (s.text if hasattr(s, "text") else s["text"]),
                         "start": (s.start if hasattr(s, "start") else s["start"])}
                        for s in data
                    ]
                    return normalized if normalized else None
            except Exception:
                continue
    except Exception:
        return None
    return None


def transcript_via_ytdlp_subs(video_id: str, lang: str, workdir: str, browser: str):
    """Primary: let yt-dlp download caption files (vtt) and parse."""
    run([
        "yt-dlp", *ytdlp_auth(browser), "--skip-download", "--write-subs", "--write-auto-subs",
        "--sub-langs", f"{lang}.*,en.*", "--sub-format", "vtt",
        "-o", os.path.join(workdir, "%(id)s.%(ext)s"),
        f"https://www.youtube.com/watch?v={video_id}",
    ])
    vtt = next(
        (os.path.join(workdir, f) for f in os.listdir(workdir)
         if f.startswith(video_id) and f.endswith(".vtt")),
        None,
    )
    if not vtt:
        return None
    return parse_vtt(vtt)


def parse_vtt(path: str):
    """Parse a YouTube .vtt file into clean [{text, start}] cues.

    YouTube auto-caption VTT blocks look like this (two text lines per block):

        00:00:00.400 --> 00:00:02.470 align:start position:0%
         
        So,<00:00:00.719><c> you're</c><00:00:00.960><c> ambitious.</c>...

        00:00:02.470 --> 00:00:02.480 align:start position:0%
        So, you're ambitious. You have big
         

        00:00:02.480 --> 00:00:04.390 align:start position:0%
        So, you're ambitious. You have big
        dreams.<00:00:03.120><c> You</c><00:00:03.280><c> tell</c>...

    Per block: the LAST non-blank text line that contains '<c>' or timing tags
    is the line with NEW words.  Plain-text-only lines are rolling duplicates.

    After collecting tagged lines we strip all XML/timing tags, then remove
    word-level overlap between consecutive cues (YouTube carries the tail of
    the previous cue as a prefix of the next one).
    """
    ts_re = re.compile(r"(\d{2}):(\d{2}):(\d{2})\.\d{3}\s*-->")
    tag_re = re.compile(r"<[^>]+>")
    has_timing = re.compile(r"<\d{2}:\d{2}:\d{2}")

    # Split file into cue blocks (separated by blank lines)
    with open(path, encoding="utf-8") as f:
        content = f.read()

    blocks = re.split(r"\n\n+", content.strip())
    raw_cues = []   # (start_seconds, tagged_line)

    for block in blocks:
        lines = block.splitlines()
        # Find the timestamp line
        ts_line = next((l for l in lines if ts_re.match(l.strip())), None)
        if not ts_line:
            continue
        m = ts_re.match(ts_line.strip())
        h, mn, s = map(int, m.groups())
        start = h * 3600 + mn * 60 + s

        # Collect text lines (everything after the timestamp)
        ts_idx = lines.index(ts_line)
        text_lines = [l for l in lines[ts_idx + 1:] if l.strip()]

        # The tagged line (with new words) is the one containing timing tags.
        # If multiple tagged lines exist, take the last one (most complete).
        tagged = next((l for l in reversed(text_lines) if has_timing.search(l)), None)
        if tagged:
            raw_cues.append((start, tagged))

    # Strip all tags and build cue list, deduplicating identical consecutive text
    cues = []
    for start, line in raw_cues:
        text = tag_re.sub("", line).strip()
        if not text:
            continue
        if cues and cues[-1]["text"] == text:
            continue
        cues.append({"start": start, "text": text})

    # Remove word-level overlap: YouTube prefixes each cue with the tail of
    # the previous cue. Find longest suffix(prev)==prefix(cur) and strip it.
    cleaned = []
    for i, cue in enumerate(cues):
        if i == 0:
            cleaned.append(cue)
            continue
        prev_words = cleaned[-1]["text"].split()
        cur_words = cue["text"].split()
        overlap = 0
        for length in range(min(len(prev_words), len(cur_words)), 0, -1):
            if prev_words[-length:] == cur_words[:length]:
                overlap = length
                break
        new_words = cur_words[overlap:]
        if new_words:
            cleaned.append({"start": cue["start"], "text": " ".join(new_words)})

    return cleaned or None


def transcript_via_whisper(video_id: str, workdir: str, model_name: str, browser: str):
    """Priority 3: download audio, transcribe locally with Whisper."""
    try:
        import whisper
    except ImportError:
        return None

    audio = os.path.join(workdir, f"{video_id}.m4a")
    dl = run([
        "yt-dlp", *ytdlp_auth(browser), "-f", "bestaudio", "-x", "--audio-format", "m4a",
        "-o", os.path.join(workdir, f"{video_id}.%(ext)s"),
        f"https://www.youtube.com/watch?v={video_id}",
    ])
    if dl.returncode != 0 or not os.path.exists(audio):
        # yt-dlp may have produced a different ext
        audio = next(
            (os.path.join(workdir, f) for f in os.listdir(workdir)
             if f.startswith(video_id) and f.rsplit(".", 1)[-1]
             in ("m4a", "webm", "mp3", "opus", "wav")),
            None,
        )
        if not audio:
            return None
    try:
        model = whisper.load_model(model_name)
        result = model.transcribe(audio, verbose=False)
        return [{"start": seg["start"], "text": seg["text"].strip()}
                for seg in result.get("segments", [])]
    except Exception:
        return None


def get_transcript(video_id, lang, workdir, use_whisper, whisper_model, browser):
    """Apply the full priority order, return (cues, source) or (None, reason)."""
    cues = transcript_via_ytdlp_subs(video_id, lang, workdir, browser)
    if cues:
        return cues, "captions (yt-dlp)"
    cues = transcript_via_api(video_id, lang)
    if cues:
        return cues, "captions (api)"
    if use_whisper:
        cues = transcript_via_whisper(video_id, workdir, whisper_model, browser)
        if cues:
            return cues, "whisper"
    return None, "no transcript available"


# ----------------------------- markdown ------------------------------------- #

def render_transcript(cues) -> str:
    lines = []
    for c in cues:
        start = c.get("start", 0)
        text = (c.get("text") or "").replace("\n", " ").strip()
        if text:
            lines.append(f"{fmt_ts(start)} {text}")
    return "\n".join(lines) if lines else "Transcript unavailable"


def append_video(index, meta, transcript_md):
    url = f"https://www.youtube.com/watch?v={meta['id']}"
    block = (
        f"\n## Video {index}: {meta.get('title', 'Untitled')}\n\n"
        f"* URL: {url}\n"
        f"* Upload Date: {fmt_date(meta.get('upload_date', ''))}\n"
        f"* Duration: {fmt_duration(meta.get('duration'))}\n\n"
        f"### Transcript\n\n"
        f"{transcript_md}\n"
    )
    with open(OUT_FILE, "a", encoding="utf-8") as f:
        f.write(block)


def write_header(channel_name, total):
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(f"# Channel: {channel_name}\n\n")
        f.write(f"**Total Videos:** {total}\n")


def append_failures(failed):
    with open(OUT_FILE, "a", encoding="utf-8") as f:
        f.write("\n\n## Failed Videos\n\n")
        if not failed:
            f.write("None.\n")
        for url, reason in failed:
            f.write(f"* {url} : {reason}\n")


# ------------------------------- main --------------------------------------- #

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("channel_url")
    ap.add_argument("--lang", default="en", help="preferred caption language")
    ap.add_argument("--no-whisper", action="store_true",
                    help="disable the Whisper fallback")
    ap.add_argument("--whisper-model", default="base",
                    help="tiny|base|small|medium|large")
    ap.add_argument("--browser", default="chrome",
                    help="browser to pull cookies from: chrome|firefox|safari|edge (default: chrome)")
    args = ap.parse_args()

    workdir = ".archive_tmp"
    os.makedirs(workdir, exist_ok=True)

    print("Enumerating channel videos...", file=sys.stderr)
    items = enumerate_videos(args.channel_url, args.browser)
    if not items:
        print("ERROR: no videos found (check the URL and network access to "
              "youtube.com).", file=sys.stderr)
        sys.exit(1)

    # Resolve full metadata so we can sort oldest->newest by upload_date.
    print(f"Found {len(items)} videos. Fetching metadata...", file=sys.stderr)
    metas = []
    for it in items:
        m = fetch_metadata(it["id"], args.browser) or it
        m.setdefault("id", it["id"])
        metas.append(m)
    metas.sort(key=lambda m: m.get("upload_date", "00000000"))

    url_handle = re.search(r"/@([^/?&]+)", args.channel_url)
    channel_name = (
        metas[0].get("channel")
        or metas[0].get("uploader")
        or (url_handle.group(1) if url_handle else None)
        or args.channel_url.rstrip("/").split("/")[-1]
    )

    done = load_state()
    # Fresh run if header absent; otherwise append (resume).
    if not os.path.exists(OUT_FILE):
        write_header(channel_name, len(metas))

    failed = []
    for i, meta in enumerate(metas, 1):
        vid = meta["id"]
        url = f"https://www.youtube.com/watch?v={vid}"
        if vid in done:
            print(f"[{i}/{len(metas)}] skip (already done) {vid}", file=sys.stderr)
            continue
        print(f"[{i}/{len(metas)}] {meta.get('title', vid)}", file=sys.stderr)
        try:
            cues, source = get_transcript(
                vid, args.lang, workdir,
                use_whisper=not args.no_whisper,
                whisper_model=args.whisper_model,
                browser=args.browser,
            )
            transcript_md = render_transcript(cues) if cues else "Transcript unavailable"
            append_video(i, meta, transcript_md)
            if not cues:
                failed.append((url, "Transcript unavailable"))
            else:
                print(f"      via {source}", file=sys.stderr)
            done.add(vid)
            save_state(done)          # incremental: safe to Ctrl-C and resume
        except Exception as e:        # never let one video kill the run
            failed.append((url, f"error: {e}"))
            print(f"      FAILED: {e}", file=sys.stderr)

    append_failures(failed)
    print(f"\nDone -> {OUT_FILE}", file=sys.stderr)


if __name__ == "__main__":
    main()