"""Running ffmpeg. The binary comes with imageio-ffmpeg (or $IMAGEIO_FFMPEG_EXE, or ffmpeg on PATH)."""

import os
import re
import subprocess
import sys

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args):
    """Run ffmpeg with `args` (inputs, filters, output format) and return what it writes to stdout."""
    command = [FFMPEG, "-v", "error", "-nostdin"] + args + ["pipe:1"]
    return subprocess.run(command, stdout=subprocess.PIPE, check=True).stdout


def describe_input(path):
    """The text ffmpeg prints about a file it's given (there's no ffprobe in imageio-ffmpeg, so we read this)."""
    if not os.path.isfile(path):
        sys.exit(f"video not found: {path}")
    result = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-i", path],
                            capture_output=True, text=True, errors="replace")
    return result.stderr


def video_duration(path):
    """Length in seconds, from the "Duration: 00:49:51.89" line."""
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", describe_input(path))
    if not match:
        sys.exit(f"couldn't read the length of {path}; is it a video file?")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def video_size(path):
    """(width, height) in pixels, from the "Video: h264 ..., 1920x1080" line."""
    match = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", describe_input(path))
    if not match:
        sys.exit(f"couldn't read the frame size of {path}")
    return int(match.group(1)), int(match.group(2))


def grab_frame(path, seconds, video_filter=None):
    """One frame at `seconds` as raw RGB bytes. Seeking before the input (-ss first) is fast and, in current
    ffmpeg, frame-accurate."""
    args = ["-ss", f"{max(0.0, seconds):.2f}", "-i", path, "-frames:v", "1", "-an"]
    if video_filter:
        args += ["-vf", video_filter]
    return run_ffmpeg(args + ["-f", "rawvideo", "-pix_fmt", "rgb24"])
