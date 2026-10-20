"""Audio cues: how loud each second is, and the referee's whistle.

The whistle is found by its sound: a steady, narrow tone at one pitch. We work out the referee's pitch from the
match itself, then measure, every ~32 ms, how far the strongest tone near that pitch stands above the background
noise. Measuring against the background (rather than against the total sound) is what stops a shout over the
top from drowning a whistle out.
"""

import numpy as np

from .ffmpeg import run_ffmpeg

SAMPLE_RATE = 16000          # Hz; plenty for whistles (2-4.5 kHz) and loudness
FFT_SIZE = 1024              # samples per spectrum frame (64 ms)
HOP = 512                    # samples between frames (32 ms)

WHISTLE_SEARCH = (2000, 4500)    # Hz where referee whistles sit
WHISTLE_HALF_BAND = 200          # Hz either side of the referee's pitch that still counts as the whistle
BACKGROUND_BAND = (1000, 6000)   # Hz used to measure the background noise level
WHISTLE_MIN_LENGTH = 0.2         # seconds a blast must last
WHISTLE_MERGE_GAP = 0.6          # blasts closer together than this are one whistle (pea-whistle trills break up)
DEFAULT_PITCH = 2400.0           # Hz, if the pitch can't be worked out


def frame_times(n_frames):
    """The time (seconds) at the centre of each spectrum frame."""
    return (np.arange(n_frames) * HOP + FFT_SIZE / 2) / SAMPLE_RATE


def read_samples(path):
    """The whole soundtrack as mono floats in -1..1."""
    raw = run_ffmpeg(["-i", path, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le"])
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def loudness_per_second(samples):
    """dB per whole second (RMS)."""
    n_seconds = len(samples) // SAMPLE_RATE
    seconds = samples[: n_seconds * SAMPLE_RATE].reshape(n_seconds, SAMPLE_RATE)
    return 20 * np.log10(np.sqrt((seconds ** 2).mean(axis=1)) + 1e-6)


def whistle_spectrum(samples):
    """Short-time spectrum, worked out in chunks because a full match won't fit in memory at once.

    Returns (power, background, freqs): the power in the whistle range for every frame (frames x bins), each
    frame's background level (median power over 1-6 kHz), and the frequency of each kept bin."""
    freqs = np.fft.rfftfreq(FFT_SIZE, 1 / SAMPLE_RATE)
    keep = (freqs >= WHISTLE_SEARCH[0] - WHISTLE_HALF_BAND) & (freqs <= WHISTLE_SEARCH[1] + WHISTLE_HALF_BAND)
    background_bins = (freqs >= BACKGROUND_BAND[0]) & (freqs <= BACKGROUND_BAND[1])
    n_frames = max(0, (len(samples) - FFT_SIZE) // HOP)
    window = np.hanning(FFT_SIZE).astype(np.float32)

    power = np.zeros((n_frames, keep.sum()), np.float32)
    background = np.zeros(n_frames, np.float32)
    chunk = 8192  # frames at a time
    for first in range(0, n_frames, chunk):
        frames = np.arange(first, min(first + chunk, n_frames))
        sample_index = frames[:, None] * HOP + np.arange(FFT_SIZE)[None, :]
        spectrum = np.abs(np.fft.rfft(samples[sample_index] * window, axis=1)) ** 2 + 1e-12
        power[frames] = spectrum[:, keep]
        background[frames] = np.median(spectrum[:, background_bins], axis=1)
    return power, background, freqs[keep]


def referee_pitch(power, background, freqs):
    """The referee's whistle is the most common strong, steady tone in the whistle range. Shouts sit lower (their
    harmonics cluster around 1.5-1.8 kHz) and wander in pitch, so they don't win this vote."""
    inside = (freqs >= WHISTLE_SEARCH[0]) & (freqs <= WHISTLE_SEARCH[1])
    prominence_db = 10 * np.log10(power[:, inside].max(axis=1) / background)
    peak_freq = freqs[inside][power[:, inside].argmax(axis=1)]
    steady = np.abs(np.diff(peak_freq, prepend=peak_freq[:1])) <= 120
    strong = steady & (prominence_db >= np.percentile(prominence_db, 99))
    if strong.sum() < 10:
        return None
    counts, edges = np.histogram(peak_freq[strong], bins=np.arange(WHISTLE_SEARCH[0], WHISTLE_SEARCH[1] + 1, 50))
    return float(edges[np.argmax(counts)] + 25)  # the middle of the most common 50 Hz bin


def whistle_track(power, background, freqs, pitch):
    """Per frame: how far (dB) the strongest tone near the referee's pitch stands above the background, and the
    frequency of that tone."""
    near = np.abs(freqs - pitch) <= WHISTLE_HALF_BAND
    prominence_db = 10 * np.log10(power[:, near].max(axis=1) / background)
    peak_freq = freqs[near][power[:, near].argmax(axis=1)]
    return prominence_db, peak_freq


def whistle_cue_per_second(prominence_db, peak_freq, n_seconds, hold_frames=4):
    """The strongest whistle-like tone in each second, counting only tones that hold for `hold_frames` (~0.13 s)
    at a steady pitch, so a brief squeak doesn't count."""
    held = np.lib.stride_tricks.sliding_window_view(prominence_db, hold_frames).min(axis=1)
    pitch_spread = np.ptp(np.lib.stride_tricks.sliding_window_view(peak_freq, hold_frames), axis=1)
    sustained = np.where(pitch_spread <= 150, held, 0)
    pad_left, pad_right = hold_frames // 2, hold_frames - 1 - hold_frames // 2
    sustained = np.concatenate([np.zeros(pad_left), sustained, np.zeros(pad_right)])

    second_of_frame = np.minimum(frame_times(len(prominence_db)).astype(int), n_seconds - 1)
    cue = np.zeros(n_seconds)
    np.maximum.at(cue, second_of_frame, np.maximum(sustained, 0))
    return cue


def find_whistles(prominence_db, threshold_db, hold_db=8, max_gap=2, min_loud_frames=3):
    """Every whistle blast, as {"start_s", "length_s", "peak_db"}.

    A blast starts where the tone reaches `threshold_db` above the background and carries on while it stays
    within `hold_db` of that, bridging up to `max_gap` quieter frames. A faint whistle under a shout flickers, and
    needing it to stay above the threshold the whole time would break it into pieces too short to count."""
    times = frame_times(len(prominence_db))
    loud = prominence_db >= threshold_db
    blasts = []
    i = 0
    while i < len(prominence_db):
        if not loud[i]:
            i += 1
            continue
        end, quiet_run = i, 0
        while end + 1 < len(prominence_db):
            if prominence_db[end + 1] >= threshold_db - hold_db:
                quiet_run = 0
            elif quiet_run < max_gap:
                quiet_run += 1
            else:
                break
            end += 1
        end -= quiet_run  # don't count the quiet frames the blast trailed off with
        long_enough = times[end] - times[i] >= WHISTLE_MIN_LENGTH
        if long_enough and loud[i:end + 1].sum() >= min_loud_frames:
            blasts.append([times[i], times[end], float(prominence_db[i:end + 1].max())])
        i = end + 1

    merged = []
    for start, stop, peak in blasts:
        if merged and start - merged[-1][1] < WHISTLE_MERGE_GAP:
            merged[-1][1] = stop
            merged[-1][2] = max(merged[-1][2], peak)
        else:
            merged.append([start, stop, peak])
    return [{"start_s": start, "length_s": stop - start, "peak_db": peak} for start, stop, peak in merged]


def read_audio_cues(path, whistle_db, pitch=None):
    """Everything we take from the soundtrack.

    Returns (loudness, whistle_cue, pitch, whistles): per-second loudness in dB, the per-second whistle cue, the
    referee's whistle pitch in Hz, and every whistle blast."""
    samples = read_samples(path)
    n_seconds = len(samples) // SAMPLE_RATE
    if n_seconds == 0:
        return np.zeros(0), np.zeros(0), pitch, []
    loudness = loudness_per_second(samples)

    power, background, freqs = whistle_spectrum(samples)
    if len(power) < 4:
        return loudness, np.zeros(n_seconds), pitch, []
    pitch = pitch or referee_pitch(power, background, freqs) or DEFAULT_PITCH
    prominence_db, peak_freq = whistle_track(power, background, freqs, pitch)
    whistle_cue = whistle_cue_per_second(prominence_db, peak_freq, n_seconds)
    return loudness, whistle_cue, pitch, find_whistles(prominence_db, whistle_db)
