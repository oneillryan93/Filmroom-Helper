#!/usr/bin/env python3
"""Match Analyzer app: a local web page for running find_moments.py.

Start it with "Match Analyzer.bat" (Windows) or "Match Analyzer.command" (macOS/Linux), or `uv run app.py`
anywhere. uv sets up Python and the libraries in pyproject.toml (numpy, and imageio-ffmpeg, which bundles ffmpeg)
before this starts, so the page only has to check they're there. It serves on http://127.0.0.1 only, and from
there you can:
  - pick a video, set options and run the analysis, watching its progress
  - open the results viewer for any past run

Sections: settings / checking the environment / finding videos and past runs / jobs (one run at a time) /
desktop helpers / the web server / start-up.
"""

import argparse
import importlib.metadata
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------- settings

PROJECT = Path(__file__).resolve().parent
RESOURCES = PROJECT / "resources"
OUTPUT = PROJECT / "output"  # results folders (find_moments.py's default too)
SCRIPT = PROJECT / "find_moments.py"
PAGE = PROJECT / "app.html"
DEFAULT_PORT = 8765
MIN_PYTHON = (3, 10)
LIBRARIES = ("numpy", "imageio-ffmpeg")  # keep in step with pyproject.toml
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm", ".mts"}
START_HINT = 'start the app with "Match Analyzer.bat" / "Match Analyzer.command", or `uv run app.py`'

AI_MODULES = ("onnxruntime", "rapidocr_onnxruntime")  # what the pyproject.toml `ai` extra installs
AI_OPTIONS = ("scoreboard",)                           # options that need it (learning is numpy only)
AI_MARKER = PROJECT / ".models" / "ai-enabled"         # the launchers add `--extra ai` while this exists

OPEN_IN_BROWSER = True  # open the page and finished results by themselves (off with --no-browser)
TOKEN = secrets.token_urlsafe(24)  # API calls must carry this, so other websites can't drive the app
NO_CONSOLE_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: don't flash a console per command


def run_quietly(command, timeout=60):
    """Run a command and return (exit code, stdout, stderr), never raising."""
    try:
        result = subprocess.run(command, cwd=PROJECT, capture_output=True, text=True, timeout=timeout,
                                creationflags=NO_CONSOLE_WINDOW)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return -1, "", str(error)


# ---------------------------------------------------------------- checking the environment

def find_ffmpeg():
    """The ffmpeg find_moments.py will use: imageio-ffmpeg's bundled one, $IMAGEIO_FFMPEG_EXE, or PATH."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return None


def check_python():
    python_ok = sys.version_info >= MIN_PYTHON
    started_by_uv = bool(os.environ.get("UV"))  # uv run sets this for the processes it starts
    detail = f"Python {sys.version.split()[0]}" + (" set up by uv" if started_by_uv else f" ({sys.executable})")
    if not python_ok:
        detail += f": need {'.'.join(map(str, MIN_PYTHON))} or newer"
    return {"ok": python_ok, "detail": detail}


def check_libraries():
    installed, missing = [], []
    for library in LIBRARIES:
        if importlib.util.find_spec(library.replace("-", "_")):
            installed.append(f"{library} {importlib.metadata.version(library)}")
        else:
            missing.append(library)
    if missing:
        return {"ok": False, "detail": f"missing {', '.join(missing)}"}, missing
    return {"ok": True, "detail": ", ".join(installed)}, missing


def check_ffmpeg(missing_libraries):
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        return {"ok": True, "detail": ffmpeg}
    reason = "comes with imageio-ffmpeg" if missing_libraries else "imageio-ffmpeg couldn't find its ffmpeg"
    return {"ok": False, "detail": reason}


def status():
    """What the Environment card shows: a check per requirement, whether all are met, and the optional add-on."""
    libraries, missing = check_libraries()
    checks = {"python": check_python(), "libraries": libraries, "ffmpeg": check_ffmpeg(missing)}
    checks["ready"] = all(check["ok"] for check in checks.values())
    checks["ai"] = {"installed": ai_installed(), "uv": bool(find_uv())}  # optional, so not part of "ready"
    return checks


def ai_installed():
    return all(importlib.util.find_spec(module) for module in AI_MODULES)


def find_uv():
    """The uv that started us (uv run sets $UV), else the launcher's own copy, else one on PATH."""
    own_copy = PROJECT / ".tools" / "uv" / ("uv.exe" if os.name == "nt" else "uv")
    for candidate in (os.environ.get("UV"), str(own_copy), shutil.which("uv")):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


READ_SCRIPT_OPTIONS = r"""
import argparse, json, find_moments
options = []
for action in find_moments.build_parser()._actions:
    if isinstance(action, argparse._HelpAction):
        continue
    if action.nargs == 0:
        kind = "flag"
    else:
        kind = {int: "int", float: "float"}.get(action.type, "str")
    options.append({"dest": action.dest, "flag": action.option_strings[0] if action.option_strings else None,
                    "default": action.default, "help": action.help or "", "kind": kind})
print(json.dumps(options))
"""


def script_options():
    """find_moments.py's own options, so the page's form always matches it. Read in a separate process so edits
    to find_moments.py show up without restarting the app."""
    exit_code, output, errors = run_quietly([sys.executable, "-c", READ_SCRIPT_OPTIONS])
    if exit_code != 0:
        if "ModuleNotFoundError" in errors:
            raise RuntimeError(f"couldn't read the script's options: {START_HINT}")
        raise RuntimeError(errors or "couldn't read the script's options")
    return json.loads(output)


# ---------------------------------------------------------------- finding videos and past runs

def find_videos():
    """Videos in resources/, the project folder, Videos and Downloads, newest first in each."""
    seen, videos = set(), []
    for folder in (RESOURCES, PROJECT, Path.home() / "Videos", Path.home() / "Downloads"):
        if not folder.is_dir():
            continue
        try:
            entries = sorted(folder.iterdir(), key=lambda p: -p.stat().st_mtime)
        except OSError:
            continue
        for path in entries:
            is_video = path.suffix.lower() in VIDEO_EXTENSIONS and path.is_file()
            if not is_video or path.resolve() in seen or path.name.endswith("_chapters.mp4"):
                continue
            seen.add(path.resolve())
            videos.append({"path": str(path), "name": path.name, "folder": str(path.parent),
                           "size_mb": round(path.stat().st_size / 1e6)})
    return videos


def count_rows(csv_path):
    try:
        with open(csv_path, encoding="utf-8") as f:
            return max(0, sum(1 for _ in f) - 1)
    except OSError:
        return None


def find_runs():
    """Results folders in output/ (anything holding the viewer's index.html and a moments.csv)."""
    runs = []
    for folder in OUTPUT.iterdir() if OUTPUT.is_dir() else ():
        if folder.is_dir() and (folder / "index.html").is_file() and (folder / "moments.csv").is_file():
            runs.append({"path": str(folder), "name": folder.name, "updated": (folder / "index.html").stat().st_mtime,
                         "moments": count_rows(folder / "moments.csv"),
                         "whistles": count_rows(folder / "whistles.csv"),
                         "review": (folder / "review.json").is_file(),
                         "chapters": (folder / "match_chapters.mp4").is_file()})
    return sorted(runs, key=lambda run: -run["updated"])


def default_results_folder(video):
    return OUTPUT / (Path(video).stem + "_moments")


# ---------------------------------------------------------------- jobs: one run at a time

class Job:
    """One run (an analysis, installing the add-on, relearning): its log, state, and the process doing it."""

    def __init__(self, kind, title):
        self.id = secrets.token_hex(6)
        self.kind = kind
        self.title = title
        self.lines = []
        self.state = "running"   # then "done", "failed" or "cancelled"
        self.stage = ""          # what it's doing now, for the progress line
        self.started = time.time()
        self.ended = None
        self.result = {}
        self.process = None
        self.cancelled = False
        self.lock = threading.Lock()

    def log(self, line):
        with self.lock:
            self.lines.append(line.rstrip("\r\n"))

    def set_stage(self, text):
        self.stage = text
        self.log(f"== {text}")

    def run(self, command, stage_from_output=False):
        """Run a command, logging its output line by line. With `stage_from_output`, lines ending in "..."
        ("reading video (the slow part)...") become the stage. Returns the exit code."""
        command = [str(part) for part in command]
        self.log("> " + subprocess.list2cmdline(command))
        self.process = subprocess.Popen(command, cwd=PROJECT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="replace",
                                        env=dict(os.environ, PYTHONUNBUFFERED="1"), creationflags=NO_CONSOLE_WINDOW)
        for line in self.process.stdout:
            self.log(line)
            if stage_from_output and line.strip().endswith("..."):
                self.stage = line.strip()
        return self.process.wait()

    def cancel(self):
        self.cancelled = True
        process = self.process
        if process and process.poll() is None:
            if os.name == "nt":  # take ffmpeg (a child of the script) down too
                run_quietly(["taskkill", "/F", "/T", "/PID", str(process.pid)])
            else:
                process.terminate()

    def finish(self, ok, message=""):
        if message:
            self.log(message)
        self.state = "cancelled" if self.cancelled else "done" if ok else "failed"
        self.ended = time.time()

    def snapshot(self, since=0):
        """The job as JSON for the page, with the log lines from `since` on."""
        with self.lock:
            return {"id": self.id, "kind": self.kind, "title": self.title, "state": self.state,
                    "stage": self.stage, "started": self.started, "ended": self.ended, "result": self.result,
                    "total": len(self.lines), "lines": self.lines[since:]}


current_job = None
job_lock = threading.Lock()


def start_job(kind, title, work):
    """Start `work(job)` in the background, unless something is already running."""
    global current_job
    with job_lock:
        if current_job and current_job.state == "running":
            raise RuntimeError("something is already running; wait for it or cancel it")
        job = current_job = Job(kind, title)

    def run_in_background():
        try:
            work(job)
        except Exception as error:  # report anything unexpected in the log rather than dying silently
            job.finish(False, f"error: {error}")

    threading.Thread(target=run_in_background, daemon=True).start()
    return job


def install_ai_addon(job):
    """Install the ai extra into this app's own environment, and remember it for the next start."""
    uv = find_uv()
    if not uv:
        raise RuntimeError("the AI add-on is installed with uv: " + START_HINT)
    job.set_stage("Installing the AI add-on (first time only, about 100 MB)...")
    if job.run([uv, "sync", "--extra", "ai"]) != 0:
        raise RuntimeError("couldn't install the AI add-on (see the log); check your internet connection")
    importlib.invalidate_caches()
    AI_MARKER.parent.mkdir(exist_ok=True)
    AI_MARKER.write_text("The launchers start the app with `uv run --extra ai` while this file exists.\n")


def analysis(video, script_args, results_folder, needs_ai=False):
    """The work for an analysis job."""
    def work(job):
        if needs_ai and not ai_installed():
            install_ai_addon(job)
        job.set_stage("Starting")
        exit_code = job.run([sys.executable, "-u", SCRIPT, video] + script_args, stage_from_output=True)
        page = results_folder / "index.html"
        job.result = {"out": str(results_folder), "viewer": str(page)}
        ok = exit_code == 0 and page.is_file()
        job.stage = "Finished" if ok else "Failed"
        if ok:
            problem = ""
        elif exit_code != 0:
            problem = f"find_moments.py exited with code {exit_code}"
        else:
            problem = f"find_moments.py finished, but there's no results page at {page}"
        job.finish(ok, problem)
        if ok and OPEN_IN_BROWSER:
            open_path(page)
    return work


def relearning(job):
    """The work for a "Relearn from reviews" job."""
    job.set_stage("Learning from your reviews")
    exit_code = job.run([sys.executable, "-u", SCRIPT, "--learn-only"])
    job.finish(exit_code == 0, "" if exit_code == 0 else f"find_moments.py exited with code {exit_code}")


def form_to_args(form, options):
    """Turn the page's {option: value} into find_moments.py arguments, leaving out anything at its default."""
    by_name = {option["dest"]: option for option in options if option["flag"]}
    args = []
    for name, value in form.items():
        option = by_name.get(name)
        if not option or value in (None, ""):
            continue
        if option["kind"] == "flag":
            if value is True:
                args.append(option["flag"])
            continue
        if option["kind"] in ("int", "float"):
            value = (int if option["kind"] == "int" else float)(value)
            if option["default"] is not None and float(value) == float(option["default"]):
                continue
        elif value == option["default"]:
            continue
        args += [option["flag"], str(value)]
    return args


# ---------------------------------------------------------------- desktop helpers

def open_path(path):
    """Open a file or folder the way double-clicking it would."""
    path = str(path)
    if os.name == "nt":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


FILE_DIALOG = r"""
import sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
kind = sys.argv[1]
if kind == "video":
    path = filedialog.askopenfilename(title="Choose a match video",
        filetypes=[("Video", "*.mp4 *.mkv *.mov *.avi *.m4v *.webm *.mts"), ("All files", "*.*")])
elif kind == "folder":
    path = filedialog.askdirectory(title="Choose an output folder")
else:
    path = filedialog.askopenfilename(title="Choose a file",
        filetypes=[("JSON or text", "*.json *.txt"), ("All files", "*.*")])
print(path or "")
"""


def choose_path(kind):
    """A native file dialog ("video", "folder" or "file"), in its own process (Tk doesn't like being driven
    from a server thread). Returns the chosen path, or "" if cancelled."""
    exit_code, output, _ = run_quietly([sys.executable, "-c", FILE_DIALOG, kind], timeout=600)
    if exit_code != 0:
        raise RuntimeError("couldn't open a file dialog (tkinter missing?): type the path instead")
    return str(Path(output)) if output else ""


def may_open(target):
    """Only things inside the project folder or a results folder may be opened from the page."""
    allowed = [PROJECT] + [Path(run["path"]) for run in find_runs()]
    if current_job and current_job.result.get("out"):
        allowed.append(Path(current_job.result["out"]))
    return target.exists() and any(target.resolve().is_relative_to(folder.resolve()) for folder in allowed)


# ---------------------------------------------------------------- the web server

class Handler(BaseHTTPRequestHandler):
    server_version = "MatchAnalyzer"

    def log_message(self, format, *args):  # keep the console quiet; the page shows what matters
        pass

    def addressed_to_us(self):
        # refuse requests addressed to another hostname (DNS rebinding)
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ("127.0.0.1", "localhost")

    def has_token(self):
        if self.headers.get("X-Token") != TOKEN:
            self.send(403, {"error": "bad token: reload the page"})
            return False
        return True

    def send(self, code, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---- GET

    def do_GET(self):
        if not self.addressed_to_us():
            return self.send(403, {"error": "forbidden"})
        path, _, query = self.path.partition("?")
        if path == "/":
            page = PAGE.read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
            return self.send(200, page.encode(), "text/html; charset=utf-8")
        if path == "/api/ping":
            return self.send(200, {"app": "match-analyzer"})
        if not self.has_token():
            return
        route = self.GET_ROUTES.get(path)
        if not route:
            return self.send(404, {"error": "not found"})
        try:
            self.send(200, route(self, query))
        except RuntimeError as error:
            self.send(409, {"error": str(error)})

    def get_status(self, query):
        return {**status(), "home": str(PROJECT), "output": str(OUTPUT), "os": os.name}

    def get_options(self, query):
        return {"options": script_options()}

    def get_videos(self, query):
        return {"videos": find_videos()}

    def get_runs(self, query):
        return {"runs": find_runs()}

    def get_job(self, query):
        params = dict(pair.split("=", 1) for pair in query.split("&") if "=" in pair)
        since = int(params.get("since", 0) or 0)
        return {"job": current_job.snapshot(since) if current_job else None}

    GET_ROUTES = {"/api/status": get_status, "/api/options": get_options, "/api/videos": get_videos,
                  "/api/runs": get_runs, "/api/job": get_job}

    # ---- POST

    def do_POST(self):
        if not self.addressed_to_us() or not self.has_token():
            return
        route = self.POST_ROUTES.get(self.path)
        try:
            if not route:
                raise RuntimeError("unknown action")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            self.send(200, route(self, body))
        except (RuntimeError, ValueError, OSError) as error:
            self.send(400, {"error": str(error)})

    def post_run(self, body):
        video = body.get("video", "").strip().strip('"')
        if not video or not Path(video).is_file():
            raise RuntimeError(f"video not found: {video or '(none chosen)'}")
        if not status()["ready"]:
            raise RuntimeError(START_HINT)
        form = body.get("options") or {}
        script_args = form_to_args(form, script_options())
        results_folder = Path(form["out"]) if form.get("out") else default_results_folder(video)
        if not form.get("out"):  # say where, so the app always looks for the results where the script puts them
            script_args += ["--out", str(results_folder)]
        needs_ai = any(form.get(option) for option in AI_OPTIONS)
        work = analysis(video, script_args, results_folder, needs_ai)
        return {"job": start_job("run", f"Analysing {Path(video).name}", work).snapshot()}

    def post_learn(self, body):
        return {"job": start_job("learn", "Learning from your reviews", relearning).snapshot()}

    def post_cancel(self, body):
        if current_job and current_job.state == "running":
            current_job.cancel()
        return {"ok": True}

    def post_pick(self, body):
        return {"path": choose_path(body.get("kind", "video"))}

    def post_open(self, body):
        target = Path(body.get("path", ""))
        if body.get("viewer"):
            target = target / "index.html"
        if not may_open(target):
            raise RuntimeError("can only open results from this app")
        open_path(target)
        return {"ok": True}

    POST_ROUTES = {"/api/run": post_run, "/api/learn": post_learn, "/api/cancel": post_cancel,
                   "/api/pick": post_pick, "/api/open": post_open}


# ---------------------------------------------------------------- start-up

def already_running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as response:
            return json.load(response).get("app") == "match-analyzer"
    except (OSError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description="Match Analyzer app")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true",
                        help="don't open the page (or finished results) automatically")
    args = parser.parse_args()
    global OPEN_IN_BROWSER
    OPEN_IN_BROWSER = not args.no_browser

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        if already_running(args.port):
            print(f"Match Analyzer is already running: opening http://127.0.0.1:{args.port}/")
            if OPEN_IN_BROWSER:
                webbrowser.open(f"http://127.0.0.1:{args.port}/")
            return
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)  # port taken by something else: any free one
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Match Analyzer is running at {url}\n"
          "Keep this window open while you use it; close it (or Ctrl+C) to stop.")
    if OPEN_IN_BROWSER:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if current_job and current_job.state == "running":
            current_job.cancel()


if __name__ == "__main__":
    main()
