"""Keeping Match Analyzer up to date from its GitHub releases.

On every start the app compares its own version (pyproject.toml's `version`) with the latest release on the
repository named in pyproject.toml (`[project.urls] Repository`). If the release is newer, its source is
downloaded and replaces this folder's files, and the app restarts into the new version (the launchers start it
again when it exits with RESTART_CODE).

Never touched: your results (output/), videos (resources/), the tools and models the launchers set up (.tools/,
.models/), the Python environment (.venv/), and anything else that isn't part of a release. The files an update
replaces or removes are first copied to .tools/backup/<old version>/; once an update has worked, only its
backup is kept and those from earlier updates are deleted. Nothing needs git: a release is
downloaded as a zip over HTTPS.

The check on start leaves a git checkout (a developer's copy) alone and only says a newer release exists. Running
this file directly (`uv run updater.py`) installs the latest release anyway, git checkout or not.

Turn the check on start off with `app.py --no-update`, or by setting MATCH_ANALYZER_NO_UPDATE=1.
"""

import io
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
PYPROJECT = PROJECT / "pyproject.toml"
TOOLS = PROJECT / ".tools"
MANIFEST = TOOLS / "installed-files.json"  # the files the last update installed, to know what a newer one removes
BACKUP = TOOLS / "backup"
RESTART_CODE = 75  # app.py exits with this after updating; the launchers then start it again
TIMEOUT = 5        # seconds: a slow or missing connection mustn't hold up the start
KEEP = {"output", "resources", ".tools", ".models", ".venv", ".git"}  # top-level folders an update never touches


def log(message):
    print(message, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- versions

def read_pyproject(text):
    """(version, repository URL) from pyproject.toml's text. A regex rather than tomllib, which needs 3.11."""
    version = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    repository = re.search(r'^Repository\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return (version.group(1) if version else None), (repository.group(1) if repository else None)


def current_version():
    return read_pyproject(PYPROJECT.read_text(encoding="utf-8"))[0]


def version_key(version):
    """"v1.2.10" -> (1, 2, 10), so versions compare as numbers. None for anything else (e.g. "1.0-beta")."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", (version or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(candidate, current):
    candidate_key, current_key = version_key(candidate), version_key(current)
    return bool(candidate_key and current_key and candidate_key > current_key)


# ---------------------------------------------------------------- GitHub

def api_url(repository):
    """https://github.com/owner/name -> https://api.github.com/repos/owner/name"""
    match = re.fullmatch(r"https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?", repository or "")
    if not match:
        raise ValueError(f"not a GitHub repository URL: {repository!r}")
    return f"https://api.github.com/repos/{match.group(1)}/{match.group(2)}"


def fetch(url, timeout=TIMEOUT, accept="application/vnd.github+json"):
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "match-analyzer-updater"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def latest_release(repository):
    """The newest published release (not drafts or pre-releases): {"version", "tag", "zip", "page"}, or None if
    the repository has none yet. The zip is the one the Release workflow attaches (.github/workflows/release.yml),
    else GitHub's own zip of the tagged source."""
    try:
        release = json.loads(fetch(api_url(repository) + "/releases/latest"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise
    tag = release["tag_name"]
    attached = [asset["browser_download_url"] for asset in release.get("assets", [])
                if re.fullmatch(r"Match-Analyzer-v[\d.]+\.zip", asset.get("name", ""))]
    return {"version": tag.lstrip("v"), "tag": tag, "zip": attached[0] if attached else release["zipball_url"],
            "page": release["html_url"]}


# ---------------------------------------------------------------- installing a release

def release_files(archive):
    """{relative path: zip entry name} for the release's files. GitHub puts everything in one top folder
    ("owner-name-1a2b3c/"), which is dropped. Refuses paths that would land outside this folder."""
    files = {}
    for entry in archive.infolist():
        if entry.is_dir():
            continue
        parts = entry.filename.split("/")[1:]
        if not parts or any(part in ("", ".", "..") for part in parts) or ":" in entry.filename:
            raise ValueError(f"unexpected path in the release: {entry.filename}")
        files["/".join(parts)] = entry.filename
    if "pyproject.toml" not in files or "app.py" not in files:
        raise ValueError("the download doesn't look like Match Analyzer")
    return files


def write_atomically(path, data):
    """Write a file by renaming a finished temporary file over it, so nothing ever sees it half-written. (A
    script that's running, like the macOS/Linux launcher, also keeps reading its old copy.)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".new")
    try:
        with os.fdopen(handle, "wb") as f:
            f.write(data)
        if path.exists():
            shutil.copymode(path, temporary)
        elif path.suffix in (".command", ".sh"):
            os.chmod(temporary, 0o755)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def install(archive_bytes, expected_version, old_version):
    """Replace this folder's files with the release's. Returns how many files changed."""
    archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    files = release_files(archive)
    new_version = read_pyproject(archive.read(files["pyproject.toml"]).decode("utf-8"))[0]
    if new_version != expected_version:
        raise ValueError(f"the release is tagged {expected_version} but its pyproject.toml says {new_version}")
    files = {path: entry for path, entry in files.items() if path.split("/")[0] not in KEEP}

    previous = set(json.loads(MANIFEST.read_text(encoding="utf-8"))) if MANIFEST.is_file() else set()
    removed = sorted(path for path in previous - set(files) if path.split("/")[0] not in KEEP)
    changed = [path for path, entry in files.items()
               if not (PROJECT / path).is_file() or (PROJECT / path).read_bytes() != archive.read(entry)]

    backup = BACKUP / old_version
    shutil.rmtree(backup, ignore_errors=True)
    for path in changed + removed:
        if (PROJECT / path).is_file():
            (backup / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PROJECT / path, backup / path)

    # pyproject.toml last: until it's in, the folder still says it's the old version, so an interrupted update is
    # simply tried again on the next start
    for path in sorted(changed, key=lambda path: path == "pyproject.toml"):
        write_atomically(PROJECT / path, archive.read(files[path]))
    for path in removed:
        (PROJECT / path).unlink(missing_ok=True)
    TOOLS.mkdir(exist_ok=True)
    MANIFEST.write_text(json.dumps(sorted(files), indent=1), encoding="utf-8")
    # it worked, so only this update's backup is worth keeping: older ones go
    for older in BACKUP.iterdir():
        if older.is_dir() and older != backup:
            shutil.rmtree(older, ignore_errors=True)
    return len(changed) + len(removed)


# ---------------------------------------------------------------- on start-up

def check_for_update(asked=False):
    """Check GitHub and, when a newer release exists, install it: on start (`asked` False) only when this isn't a
    git checkout and checks aren't turned off; when you ask for it directly (`asked` True), always. Returns what
    happened, for the app's page: {"version", "latest", "state", "message"}, where state is one of
    "off", "up-to-date", "updated", "available" (newer, but not installed) or "unknown" (couldn't check)."""
    version, repository = read_pyproject(PYPROJECT.read_text(encoding="utf-8"))
    result = {"version": version, "latest": None, "state": "unknown", "message": ""}
    if os.environ.get("MATCH_ANALYZER_NO_UPDATE") and not asked:
        return {**result, "state": "off", "message": "update checks are turned off"}
    try:
        release = latest_release(repository)
    except (OSError, ValueError, KeyError) as error:
        return {**result, "message": f"couldn't check for updates ({error})"}
    if not release or not is_newer(release["version"], version):
        latest = release["version"] if release else version
        return {**result, "latest": latest, "state": "up-to-date", "message": "up to date"}

    result["latest"] = release["version"]
    if (PROJECT / ".git").exists() and not asked:
        return {**result, "state": "available", "message": f"v{release['version']} is available (this folder is "
                f"a git checkout, so `git pull`, or `uv run updater.py` to install the release): {release['page']}"}
    log(f"updating Match Analyzer from v{version} to v{release['version']}...")
    try:
        archive = fetch(release["zip"], timeout=120, accept="application/octet-stream")
        count = install(archive, release["version"], version)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        return {**result, "state": "available",
                "message": f"v{release['version']} is available but the update failed ({error}): {release['page']}"}
    return {**result, "state": "updated",
            "message": f"updated to v{release['version']} ({count} files; the old ones are in {BACKUP / version})"}


if __name__ == "__main__":
    # `uv run updater.py`: install the latest release now, even in a git checkout
    outcome = check_for_update(asked=True)
    print(f"Match Analyzer v{outcome['version']}: {outcome['message']}")
    if outcome["state"] == "updated":
        print("If the app is running, close it and start it again to use the new version.")
