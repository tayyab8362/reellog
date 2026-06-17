"""
app.py — Flask web UI for archive_channel.py and download_channel.py

Run:  python3 app.py
Then open: http://localhost:5050
"""

import json
import os
import queue
import re
import subprocess
import threading
import uuid

from flask import (Flask, Response, jsonify, render_template,
                   request, send_from_directory, stream_with_context)

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Active jobs: job_id -> {"proc": Popen, "log_q": Queue, "done": bool, "error": str|None}
JOBS: dict = {}
JOBS_LOCK = threading.Lock()


# ─────────────────────────── helpers ──────────────────────────────────────── #

def _stream_proc(proc, log_q: queue.Queue):
    """Read stdout+stderr from proc line by line, push to queue."""
    for line in iter(proc.stdout.readline, ""):
        log_q.put(line.rstrip("\n"))
    proc.wait()
    log_q.put(None)  # sentinel


def _launch(cmd: list, job_id: str):
    """Run cmd in background, stream output into JOBS[job_id]."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=BASE_DIR,
    )
    log_q: queue.Queue = queue.Queue()
    with JOBS_LOCK:
        JOBS[job_id] = {"proc": proc, "log_q": log_q, "done": False, "error": None}
    t = threading.Thread(target=_stream_proc, args=(proc, log_q), daemon=True)
    t.start()


def _output_tree(root: str) -> list:
    """Return sorted list of {path, name, size} for files under root."""
    result = []
    if not os.path.exists(root):
        return result
    for dirpath, _, filenames in os.walk(root):
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, BASE_DIR)
            size = os.path.getsize(full)
            result.append({"path": rel, "name": fname, "size": size})
    result.sort(key=lambda x: x["path"])
    return result


# ─────────────────────────── routes ───────────────────────────────────────── #

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.json or {}
    mode = data.get("mode")          # "transcript" | "download" | "playlists"
    url  = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    browser  = data.get("browser", "chrome")
    language = data.get("language", "en")
    single   = data.get("single_video", False)
    shots    = data.get("screenshots", False)
    threshold= data.get("scene_threshold", 0.02)
    no_whisp = data.get("no_whisper", False)
    w_model  = data.get("whisper_model", "base")
    fmt      = data.get("format", "video")
    quality  = data.get("quality")     # int or None

    job_id = str(uuid.uuid4())[:8]

    if mode == "transcript":
        cmd = ["python3", "archive_channel.py", url,
               "--browser", browser, "--lang", language,
               "--whisper-model", w_model]
        if single:
            cmd.append("--video")
        if shots:
            cmd += ["--screenshots", "--scene-threshold", str(threshold)]
        if no_whisp:
            cmd.append("--no-whisper")

    elif mode == "download":
        cmd = ["python3", "download_channel.py", url,
               "--browser", browser, "--format", fmt]
        if single:
            cmd.append("--video")
        if quality:
            cmd += ["--quality", str(quality)]

    elif mode == "playlists":
        # Just fetch playlist list — returned as JSON, not streamed
        auth = ["--cookies-from-browser", browser,
                "--remote-components", "ejs:github"]
        base = url.rstrip("/")
        proc = subprocess.run([
            "yt-dlp", *auth,
            "--flat-playlist", "--dump-json", "--ignore-errors",
            f"{base}/playlists",
        ], capture_output=True, text=True, cwd=BASE_DIR)
        playlists = [{"title": "All Videos (/videos tab)", "url": f"{base}/videos"}]
        seen = set()
        for line in proc.stdout.splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            pl_id = e.get("id", "")
            title = e.get("title") or pl_id
            pl_url = e.get("url") or f"https://www.youtube.com/playlist?list={pl_id}"
            if pl_id and pl_id not in seen:
                seen.add(pl_id)
                playlists.append({"title": title, "url": pl_url})
        return jsonify({"playlists": playlists})

    elif mode == "download_playlist":
        pl_url = (data.get("playlist_url") or "").strip()
        if not pl_url:
            return jsonify({"error": "playlist_url required"}), 400
        cmd = ["python3", "download_channel.py", pl_url,
               "--browser", browser, "--format", fmt]
        if quality:
            cmd += ["--quality", str(quality)]

    else:
        return jsonify({"error": f"Unknown mode: {mode}"}), 400

    _launch(cmd, job_id)
    return jsonify({"job_id": job_id})


@app.route("/api/stream/<job_id>")
def api_stream(job_id):
    """SSE endpoint — streams log lines for a running job."""
    def generate():
        while True:
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if job is None:
                yield "data: [Job not found]\n\n"
                return
            try:
                line = job["log_q"].get(timeout=30)
            except queue.Empty:
                yield "data: [waiting...]\n\n"
                continue
            if line is None:
                rc = job["proc"].returncode
                with JOBS_LOCK:
                    JOBS[job_id]["done"] = True
                yield f"data: __DONE__{rc}\n\n"
                return
            yield f"data: {line}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/cancel/<job_id>", methods=["POST"])
def api_cancel(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job and not job["done"]:
        job["proc"].terminate()
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Job not found or already done"})


@app.route("/api/outputs")
def api_outputs():
    root = request.args.get("root", "output")
    return jsonify(_output_tree(root))


@app.route("/api/file")
def api_file():
    """Serve any file under BASE_DIR for download."""
    rel = request.args.get("path", "")
    if not rel or ".." in rel:
        return "forbidden", 403
    directory = os.path.join(BASE_DIR, os.path.dirname(rel))
    filename  = os.path.basename(rel)
    return send_from_directory(directory, filename, as_attachment=True)


@app.route("/api/view")
def api_view():
    """Serve a file inline (images, markdown preview)."""
    rel = request.args.get("path", "")
    if not rel or ".." in rel:
        return "forbidden", 403
    directory = os.path.join(BASE_DIR, os.path.dirname(rel))
    filename  = os.path.basename(rel)
    return send_from_directory(directory, filename)


if __name__ == "__main__":
    os.makedirs(os.path.join(BASE_DIR, "templates"), exist_ok=True)
    app.run(host="0.0.0.0", port=5050, debug=False, threaded=True)
