"""Turning the per-second cues into one score and picking the moments.

Each cue is turned into a "spike" score: how unusual this second is compared with the minute around it. The
spikes are then blended with the cue weights into the overall score, and the highest, well-separated peaks
become the moments.
"""

import numpy as np

CUES = ("loud", "whistle", "camera", "motion")

# How much each cue counts towards the combined score. Tune these against your --truth list or review.json.
DEFAULT_WEIGHTS = {"loud": 1.0, "whistle": 1.0, "camera": 0.7, "motion": 0.5}

SPIKE_CAP = 8  # a spike score never goes above this, so one freak second can't dominate


def parse_weights(text):
    """"loud=1,whistle=1.5" -> the default weights with those two changed."""
    weights = dict(DEFAULT_WEIGHTS)
    if text:
        for pair in text.split(","):
            cue, value = pair.split("=")
            weights[cue.strip()] = float(value)
    return weights


def rolling_median(values, width):
    padded = np.pad(values, width // 2, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, width)
    return np.median(windows, axis=1)[: len(values)]


def spike_score(values, baseline_seconds=61):
    """How unusual each second is versus the surrounding minute: a robust z-score (median and median absolute
    deviation, so the spikes themselves don't skew it), positive side only."""
    if len(values) == 0:
        return values
    width = min(baseline_seconds, len(values) | 1)  # an odd width that fits
    residual = values - rolling_median(values, width)
    spread = np.median(np.abs(residual - np.median(residual))) * 1.4826 + 1e-9
    return np.clip(residual / spread, 0, SPIKE_CAP)


def smooth(values, width=3):
    return np.convolve(values, np.ones(width) / width, mode="same") if len(values) else values


def same_length(cues):
    """Pad or trim every cue to the longest one (audio and video can differ by a second)."""
    n = max(len(values) for values in cues.values())
    return {name: (np.pad(values, (0, n - len(values))) if len(values) < n else values[:n])
            for name, values in cues.items()}


def combine(spikes, weights):
    """The weighted average of the cue spikes, scaled so a typical moment lands around 2."""
    total_weight = sum(weights.values())
    weighted = sum(weights[cue] * spikes[cue] for cue in spikes)
    return weighted / total_weight * len(spikes) / 2


def main_cue(spikes, weights, second):
    """Which cue contributed most at this second."""
    return max(spikes, key=lambda cue: weights[cue] * spikes[cue][second])


def pick_peaks(score, max_count, min_gap, min_score):
    """The highest seconds, at least `min_gap` seconds apart, down to `min_score`, in time order."""
    remaining = score.copy()
    peaks = []
    while len(peaks) < max_count:
        best = int(np.argmax(remaining))
        if remaining[best] < min_score:
            break
        peaks.append(best)
        remaining[max(0, best - min_gap): best + min_gap + 1] = -np.inf
    return sorted(peaks)
