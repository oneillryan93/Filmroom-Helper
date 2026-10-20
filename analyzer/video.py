"""Video cues: how much the camera moves and how much moves on screen, per second.

The video is decoded once, tiny (160x90 grey, 5 frames a second), because decoding is the slow part.
"""

import numpy as np

from .ffmpeg import run_ffmpeg

WIDTH, HEIGHT, FPS = 160, 90, 5


def camera_shift(previous, current, window):
    """How far (pixels) the whole picture moved between two frames: a pan or zoom by whoever is filming.
    Found by phase correlation: the peak of the cross-power spectrum sits at the shift."""
    a, b = np.fft.fft2(previous * window), np.fft.fft2(current * window)
    cross = a * np.conj(b)
    cross /= np.abs(cross) + 1e-9
    correlation = np.abs(np.fft.ifft2(cross))
    dy, dx = np.unravel_index(np.argmax(correlation), correlation.shape)
    # the peak position wraps around: past halfway means a shift the other way
    if dy > previous.shape[0] // 2:
        dy -= previous.shape[0]
    if dx > previous.shape[1] // 2:
        dx -= previous.shape[1]
    return np.hypot(dx, dy)


def read_frames(path, hwaccel):
    args = ["-hwaccel", "auto"] if hwaccel else []
    args += ["-i", path, "-an", "-vf", f"fps={FPS},scale={WIDTH}:{HEIGHT},format=gray", "-f", "rawvideo"]
    raw = run_ffmpeg(args)
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, HEIGHT, WIDTH).astype(np.float32)


def per_second(values):
    """Average of each second's frames."""
    n_seconds = len(values) // FPS
    return values[: n_seconds * FPS].reshape(n_seconds, FPS).mean(axis=1)


def read_video_cues(path, hwaccel=False):
    """Returns (camera, motion): per-second camera movement (pixels per frame) and on-screen motion (mean change
    in brightness between frames)."""
    frames = read_frames(path, hwaccel)
    if len(frames) < 2:
        return np.zeros(0), np.zeros(0)
    window = np.outer(np.hanning(HEIGHT), np.hanning(WIDTH))  # tapers the edges so they don't fake a shift
    camera = np.zeros(len(frames))
    motion = np.zeros(len(frames))
    for i in range(1, len(frames)):
        camera[i] = camera_shift(frames[i - 1], frames[i], window)
        motion[i] = np.abs(frames[i] - frames[i - 1]).mean()
    return per_second(camera), per_second(motion)
