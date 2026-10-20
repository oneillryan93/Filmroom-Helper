"""The files a run writes besides the viewer: scores.csv, moments.csv, whistles.csv and the chapters video."""

import csv
import os
import subprocess

from .ffmpeg import FFMPEG
from .timecodes import format_time

WHISTLE_FIELDS = ["n", "time", "start_s", "jump_s", "length_s", "peak_db", "name", "labels"]


def write_scores_csv(path, score, cues, spikes):
    """Every second of the match: the overall score, each cue's raw value and its spike score."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["second", "time", "score"] + [f"{name}_raw" for name in cues]
                        + [f"{name}_spike" for name in spikes])
        for second in range(len(score)):
            writer.writerow([second, format_time(second), f"{score[second]:.3f}"]
                            + [f"{cues[name][second]:.4f}" for name in cues]
                            + [f"{spikes[name][second]:.3f}" for name in spikes])


def write_rows_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_chapters_video(source, moments, duration, destination):
    """Copy the video (no re-encoding, so it's quick) with a chapter at each moment, so players like VLC can skip
    between them."""
    starts = [0.0] + [float(m["jump_s"]) for m in moments]
    titles = ["Kick-off"] + [f'#{m["rank"]} {m["peak_time"]} ({m["main_cue"]})' for m in moments]
    lines = [";FFMETADATA1"]
    for i, (start, title) in enumerate(zip(starts, titles, strict=True)):
        end = starts[i + 1] if i + 1 < len(starts) else duration
        if end <= start:
            continue
        escaped = title.replace("=", r"\=").replace(";", r"\;").replace("#", r"\#")
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(start * 1000)}", f"END={int(end * 1000)}",
                  "title=" + escaped]
    metadata_path = destination + ".ffmeta.txt"
    with open(metadata_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    try:
        subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-y", "-i", source, "-i", metadata_path,
                        "-map", "0", "-map_metadata", "0", "-map_chapters", "1", "-c", "copy", destination],
                       check=True)
    finally:
        os.remove(metadata_path)
