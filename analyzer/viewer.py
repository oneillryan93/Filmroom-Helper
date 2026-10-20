"""The results page: index.html in the results folder.

It's built from viewer/page.html, viewer/style.css and viewer/viewer.js, with this run's data dropped in as a
JSON block, into one self-contained file that opens straight from disk and plays the original video.
"""

import json
import os
import pathlib
import urllib.parse

VIEWER_FILES = pathlib.Path(__file__).resolve().parent / "viewer"

CUE_COLORS = {"loud": "#f59e0b", "whistle": "#38bdf8", "camera": "#a78bfa", "motion": "#34d399",
              "scoreboard": "#f472b6"}


def video_url(video, out_dir):
    """How index.html in `out_dir` should refer to the video: relative if possible, else a file:// URI."""
    try:
        relative = os.path.relpath(os.path.abspath(video), os.path.abspath(out_dir))
        return urllib.parse.quote(relative.replace(os.sep, "/"))
    except ValueError:  # on another drive (Windows)
        return pathlib.Path(os.path.abspath(video)).as_uri()


def automatic_values(moment):
    """The scoreboard's name and labels for a moment, which the page uses until you change them."""
    labels = [label for label in moment.get("auto_labels", "").split("; ") if label]
    if not labels and not moment.get("auto_name"):
        return None
    return {"labels": labels, "name": moment.get("auto_name", "")}


def page_data(out_dir, video, moments, whistles, score, duration, settings, review, scoreboard):
    """Everything the page's script needs, as plain JSON."""
    return {
        "video": video_url(video, out_dir),
        "video_name": os.path.basename(video),
        "duration": duration,
        "colors": CUE_COLORS,
        "score": [round(float(s), 2) for s in score],         # the overall score per second, for the timeline
        "min_score": settings["min_score"],                    # the moment threshold, drawn as a dashed line
        "before": settings["before"],                          # seconds of lead-in when jumping to a moment
        "after": settings["after"],                            # seconds a moment stays highlighted after its peak
        "whistle_before": settings["whistle_before"],          # seconds of lead-in when jumping to a whistle
        "review": review,  # your last exported review, so a re-run's page starts from it
        "scoreboard": {"teams": list(scoreboard["teams"]), "start": list(scoreboard["start"]),
                       "changes": scoreboard["changes"]} if scoreboard else None,
        "moments": [{"rank": m["rank"], "peak_s": m["peak_s"], "jump_s": float(m["jump_s"]),
                     "peak_time": m["peak_time"], "score": m["score"], "main_cue": m["main_cue"],
                     "source": m.get("source", ""), "scoreline": m.get("scoreline", ""),
                     "auto": automatic_values(m)} for m in moments],
        "whistles": [{"n": w["n"], "time": w["time"], "start_s": float(w["start_s"]),
                      "jump_s": float(w["jump_s"]), "length_s": float(w["length_s"]), "peak_db": w["peak_db"]}
                     for w in whistles],
    }


def write_viewer_page(out_dir, video, moments, whistles, score, duration, settings, review, scoreboard=None):
    data = page_data(out_dir, video, moments, whistles, score, duration, settings, review, scoreboard)
    page = (VIEWER_FILES / "page.html").read_text(encoding="utf-8")
    style = (VIEWER_FILES / "style.css").read_text(encoding="utf-8")
    script = (VIEWER_FILES / "viewer.js").read_text(encoding="utf-8")
    # "</" inside the data would end the <script> block early
    data_json = json.dumps(data).replace("</", "<\\/")
    page = page.replace("/*STYLE*/", style).replace("/*SCRIPT*/", script).replace("{/*DATA*/}", data_json)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
