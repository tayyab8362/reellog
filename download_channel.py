"""
download_channel.py — Download YouTube channel playlists / single videos in max quality.

Features:
  * Fetches all playlists from a channel and shows a numbered menu
  * User picks which playlists to download (or ALL)
  * Files named: <upload_date> - <title>.mp4
  * Organised: downloads/<channel_name>/<playlist_name>/<date> - <title>.mp4
  * Thumbnail embedded in file + saved as .jpg alongside
  * Resumable — skips already downloaded files
  * Single video mode bypasses the menu

USAGE
  python3 download_channel.py "https://www.youtube.com/@channel_name"
  python3 download_channel.py "https://www.youtube.com/watch?v=VIDEO_ID" --video
  python3 download_channel.py "<url>" --browser firefox
  python3 download_channel.py "<url>" --format mp3
  python3 download_channel.py "<url>" --quality 1080

REQUIREMENTS
  pip install yt-dlp
  brew install ffmpeg   # macOS — needed for merging video+audio
"""

import argparse
import json
import os
import re
import subprocess
import sys


def ytdlp_auth(browser: str) -> list:
    return ["--cookies-from-browser", browser, "--remote-components", "ejs:github"]


def sanitize(name: str) -> str:
    """Remove filesystem-unsafe characters from a name."""
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()


def fetch_playlists(channel_url: str, auth: list) -> tuple:
    """
    Return (channel_name, playlists) where playlists is a list of dicts:
      {"id": ..., "title": ..., "count": ...}
    Also includes a virtual "All Videos" entry for the /videos tab.
    """
    print("Fetching channel playlists...", flush=True)

    # Get channel name from first video
    name_proc = subprocess.run([
        "yt-dlp", *auth,
        "--flat-playlist", "--playlist-items", "1",
        "--print", "%(channel,uploader)s",
        channel_url,
    ], capture_output=True, text=True)
    raw = name_proc.stdout.strip().splitlines()
    channel_name = sanitize(raw[0]) if raw and raw[0] not in ("", "NA") else "Unknown_Channel"

    # Fetch playlists tab
    base = channel_url.rstrip("/")
    playlists_url = f"{base}/playlists"
    pl_proc = subprocess.run([
        "yt-dlp", *auth,
        "--flat-playlist", "--dump-json",
        "--ignore-errors",
        playlists_url,
    ], capture_output=True, text=True)

    playlists = []
    seen = set()
    for line in pl_proc.stdout.splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        pl_id = e.get("id") or e.get("url", "")
        title = e.get("title") or e.get("playlist_title") or pl_id
        count = e.get("playlist_count") or "?"   # n_entries is capped at 10 by YouTube preview
        if pl_id and pl_id not in seen:
            seen.add(pl_id)
            playlists.append({
                "id": pl_id,
                "title": title,
                "count": count,
                "url": e.get("url") or f"https://www.youtube.com/playlist?list={pl_id}",
            })

    # Always add "All Videos" as an option
    playlists.insert(0, {
        "id": "__all_videos__",
        "title": "All Videos (channel /videos tab)",
        "count": "?",
        "url": f"{base}/videos",
    })

    return channel_name, playlists


def pick_playlists(playlists: list) -> list:
    """Show numbered menu, return list of selected playlist dicts."""
    print("\nAvailable playlists:\n")
    for i, pl in enumerate(playlists, 1):
        print(f"  [{i:2}] {pl['title']}  ({pl['count']} videos)")
    print(f"\n  [A ] Download ALL playlists")
    print(f"  [0 ] Cancel\n")

    while True:
        raw = input("Enter numbers separated by commas (e.g. 1,3,5) or A for all: ").strip()
        if raw == "0":
            print("Cancelled.")
            sys.exit(0)
        if raw.upper() == "A":
            return playlists
        try:
            indices = [int(x.strip()) for x in raw.split(",")]
            selected = []
            for idx in indices:
                if 1 <= idx <= len(playlists):
                    selected.append(playlists[idx - 1])
                else:
                    print(f"  Invalid number: {idx} — must be 1-{len(playlists)}")
                    selected = []
                    break
            if selected:
                return selected
        except ValueError:
            pass
        print("  Please enter valid numbers separated by commas, or A.")


def build_fmt(fmt: str, quality) -> list:
    if fmt == "mp3":
        return ["--extract-audio", "--audio-format", "mp3", "--audio-quality", "0"]
    if fmt == "m4a":
        return ["--extract-audio", "--audio-format", "m4a", "--audio-quality", "0"]
    # video
    if quality:
        return [
            "-f",
            f"bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]"
            f"/bestvideo[height<={quality}]+bestaudio/best[height<={quality}]",
            "--merge-output-format", "mp4",
        ]
    return [
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
    ]


def download(url: str, out_tpl: str, fmt_flags: list,
             thumb_flags: list, auth: list):
    cmd = [
        "yt-dlp", *auth,
        *fmt_flags,
        "--embed-metadata",
        "--embed-chapters",
        "--add-metadata",
        *thumb_flags,
        "--no-overwrites",
        "--continue",
        "--ignore-errors",
        "--progress",
        "-o", out_tpl,
        url,
    ]
    return subprocess.run(cmd).returncode


def main():
    ap = argparse.ArgumentParser(
        description="Download YouTube channel playlists or a single video in max quality."
    )
    ap.add_argument("url", help="YouTube channel URL or single video URL")
    ap.add_argument("--video", action="store_true",
                    help="single video mode — skips playlist menu")
    ap.add_argument("--browser", default="chrome",
                    help="browser for cookies: chrome|firefox|safari|edge (default: chrome)")
    ap.add_argument("--format", default="video", choices=["video", "mp3", "m4a"],
                    help="output format: video (default), mp3, m4a")
    ap.add_argument("--quality", type=int, default=None,
                    help="max video height e.g. 1080, 720 (default: best available)")
    ap.add_argument("--out-dir", default="downloads",
                    help="root output directory (default: downloads/)")
    ap.add_argument("--no-thumbnails", action="store_true",
                    help="skip saving thumbnail images")
    args = ap.parse_args()

    auth = ytdlp_auth(args.browser)
    fmt_flags = build_fmt(args.format, args.quality)
    thumb_flags = [] if args.no_thumbnails else [
        "--write-thumbnail", "--convert-thumbnails", "jpg", "--embed-thumbnail",
    ]

    # ------------------------------------------------------------------ #
    # Single video mode
    # ------------------------------------------------------------------ #
    if args.video:
        vid_match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", args.url)
        if not vid_match:
            print("ERROR: could not extract video ID from URL.")
            sys.exit(1)

        # Get channel name for folder
        name_proc = subprocess.run([
            "yt-dlp", *auth, "--playlist-items", "1",
            "--print", "%(channel,uploader)s", args.url,
        ], capture_output=True, text=True)
        raw = name_proc.stdout.strip().splitlines()
        channel_name = sanitize(raw[0]) if raw and raw[0] not in ("", "NA") else "Unknown_Channel"

        out_dir = os.path.join(args.out_dir, channel_name)
        os.makedirs(out_dir, exist_ok=True)
        out_tpl = os.path.join(out_dir, "%(upload_date>%Y-%m-%d)s - %(title)s.%(ext)s")

        print(f"Downloading to: {out_dir}")
        rc = download(args.url, out_tpl, fmt_flags, thumb_flags, auth)
        print(f"\nDone -> {out_dir}" if rc == 0 else f"\nFinished with errors -> {out_dir}")
        return

    # ------------------------------------------------------------------ #
    # Channel mode — fetch playlists and show menu
    # ------------------------------------------------------------------ #
    channel_name, playlists = fetch_playlists(args.url, auth)

    if not playlists:
        print("No playlists found. Try passing a direct playlist URL or use --video for a single video.")
        sys.exit(1)

    selected = pick_playlists(playlists)

    print(f"\nWill download {len(selected)} playlist(s) to: {args.out_dir}/{channel_name}/\n")

    for pl in selected:
        pl_title = sanitize(pl["title"])
        out_dir = os.path.join(args.out_dir, channel_name, pl_title)
        os.makedirs(out_dir, exist_ok=True)
        out_tpl = os.path.join(out_dir, "%(upload_date>%Y-%m-%d)s - %(title)s.%(ext)s")

        print(f"\n{'='*60}")
        print(f"Playlist : {pl['title']}")
        print(f"Folder   : {out_dir}")
        print(f"{'='*60}\n")

        rc = download(pl["url"], out_tpl, fmt_flags, thumb_flags, auth)
        status = "Done" if rc == 0 else f"Finished with errors (rc={rc})"
        print(f"\n{status} -> {out_dir}")

    print(f"\nAll done -> {args.out_dir}/{channel_name}/")


if __name__ == "__main__":
    main()
