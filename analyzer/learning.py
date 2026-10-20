"""Learning a better moment ranking from your reviews (numpy only, no download).

Every results folder with a review.json teaches it: the moments you kept or added are examples of real moments,
the ones you marked false are examples of what to skip, and a sample of quiet seconds far from anything you
confirmed count as weak "skip" examples. A small logistic regression learns from the cue spikes around each
example. It is only used if cross-validation shows it ranks moments better than the hand-set weights.
"""

import csv
import json
import sys
from pathlib import Path

import numpy as np

from .scoring import CUES

PROJECT = Path(__file__).resolve().parent.parent
MODELS = PROJECT / ".models"
LEARNED = MODELS / "learned.json"

WINDOW = 5            # seconds either side of an example that its features look at
MIN_REAL = 15         # examples needed before learning anything
MIN_FALSE = 5
WEAK_NEGATIVE_WEIGHT = 0.2
MUST_BEAT_BY = 0.02   # average precision the learned ranking must gain over the current one to be used


def log(message):
    print(message, file=sys.stderr, flush=True)


def window_features(scores_csv):
    """Per second: the max and mean of each cue's spike over +-5 s, the spikes themselves, and the score.
    Returns (features, score)."""
    with open(scores_csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    spikes = np.array([[float(row[f"{cue}_spike"]) for cue in CUES] for row in rows], np.float32)
    score = np.array([float(row["score"]) for row in rows], np.float32)
    padded = np.pad(spikes, ((WINDOW, WINDOW), (0, 0)), mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * WINDOW + 1, axis=0)[:len(rows)]
    features = np.concatenate([windows.max(axis=2), windows.mean(axis=2), spikes, score[:, None]], axis=1)
    return features, score


def reviewed_folders(extra_dirs=()):
    """Every results folder with a review.json and scores.csv: those in the project folder, plus any given."""
    folders = {Path(d).resolve() for d in extra_dirs if d}
    folders |= {d.resolve() for d in PROJECT.iterdir() if d.is_dir() and (d / "review.json").is_file()}
    return sorted(d for d in folders if (d / "review.json").is_file() and (d / "scores.csv").is_file())


class Examples:
    """The training set: features, label (1 real / 0 skip), weight, which match it came from, current score."""

    def __init__(self):
        self.features, self.labels, self.weights, self.match, self.current = [], [], [], [], []

    def add(self, features, label, weight, match, current):
        self.features.append(features)
        self.labels.append(label)
        self.weights.append(weight)
        self.match.append(match)
        self.current.append(current)

    def arrays(self):
        return (np.array(self.features, np.float32), np.array(self.labels, np.float32),
                np.array(self.weights, np.float32), np.array(self.match), np.array(self.current, np.float32))


def collect_examples(folders, rng):
    examples = Examples()
    for match, folder in enumerate(folders):
        review = json.loads((folder / "review.json").read_text(encoding="utf-8"))
        features, score = window_features(folder / "scores.csv")
        n_seconds = len(score)

        def second(t, n_seconds=n_seconds):  # the row for time t, kept inside the match
            return int(min(n_seconds - 1, max(0, round(t))))

        moments = review.get("moments") or {}
        real = [float(t) for t in moments.get("kept", [])] + [float(a["t"]) for a in moments.get("added", [])]
        false = [float(t) for t in moments.get("false", [])]
        for t in real:
            examples.add(features[second(t)], 1, 1.0, match, score[second(t)])
        for t in false:
            examples.add(features[second(t)], 0, 1.0, match, score[second(t)])

        # quiet seconds well away (30 s+) from anything you confirmed count as weak "skip" examples
        near_reviewed = np.zeros(n_seconds, bool)
        for t in real + false:
            near_reviewed[max(0, second(t) - 30): second(t) + 31] = True
        free = np.flatnonzero(~near_reviewed)
        if len(free):
            for s in rng.choice(free, size=min(len(free), 3 * max(1, len(real))), replace=False):
                examples.add(features[s], 0, WEAK_NEGATIVE_WEIGHT, match, score[s])
    return examples.arrays()


def fit_logistic(features, labels, weights, l2=1e-2, iterations=800, step=0.5):
    """Weighted, L2-regularised logistic regression by gradient descent on standardised features."""
    mean, std = features.mean(axis=0), features.std(axis=0) + 1e-6
    z = (features - mean) / std
    coef, bias = np.zeros(z.shape[1]), 0.0
    for _ in range(iterations):
        probability = 1 / (1 + np.exp(-(z @ coef + bias)))
        gradient = (probability - labels) * weights
        coef -= step * (z.T @ gradient / weights.sum() + l2 * coef)
        bias -= step * gradient.sum() / weights.sum()
    return {"mean": mean.tolist(), "std": std.tolist(), "coef": coef.tolist(), "bias": float(bias)}


def predict(model, features):
    z = (features - np.array(model["mean"])) / np.array(model["std"])
    return 1 / (1 + np.exp(-(z @ np.array(model["coef"]) + model["bias"])))


def average_precision(scores, labels):
    """How well `scores` rank the real examples (1.0 = every real one first)."""
    ordered = labels[np.argsort(-scores)]
    if ordered.sum() == 0:
        return 0.0
    precision_at_each = np.cumsum(ordered) / np.arange(1, len(ordered) + 1)
    return float(precision_at_each[ordered == 1].mean())


def cross_validate(features, labels, weights, match, current, rng):
    """Average precision of the learned ranking and of the current one on held-out examples: by match when there
    are 3+ reviewed matches, else 5 random folds."""
    folds = match if len(set(match.tolist())) >= 3 else rng.integers(0, 5, len(labels))
    learned_scores, current_scores = [], []
    for fold in sorted(set(folds.tolist())):
        test = folds == fold
        if labels[test].sum() == 0 or (1 - labels[test]).sum() == 0:
            continue  # needs both kinds of example to score
        model = fit_logistic(features[~test], labels[~test], weights[~test])
        learned_scores.append(average_precision(predict(model, features[test]), labels[test]))
        current_scores.append(average_precision(current[test], labels[test]))
    learned = float(np.mean(learned_scores)) if learned_scores else 0.0
    now = float(np.mean(current_scores)) if current_scores else 0.0
    return learned, now


def learn(extra_dirs=()):
    """Train on your reviews, compare against the current scoring, save to .models/learned.json, return it."""
    folders = reviewed_folders(extra_dirs)
    rng = np.random.default_rng(0)
    features, labels, weights, match, current = collect_examples(folders, rng)
    n_real = int(labels.sum())
    n_false = int(((labels == 0) & (weights == 1.0)).sum())
    log(f"learning from {len(folders)} reviewed match(es): {n_real} real moments, {n_false} false positives")

    model = {"version": 1, "use": False}
    if n_real < MIN_REAL or n_false < MIN_FALSE:
        log(f"  not enough to learn from yet: need {MIN_REAL}+ kept or added moments and {MIN_FALSE}+ marked false "
            "(review more in the viewer, Export review, and save review.json in the results folder)")
    else:
        learned, now = cross_validate(features, labels, weights, match, current, rng)
        model.update(fit_logistic(features, labels, weights))
        model["use"] = learned > now + MUST_BEAT_BY
        model["cv"] = {"learned": round(learned, 3), "current": round(now, 3)}
        verdict = "using the learned ranking" if model["use"] else "keeping the current weights"
        log(f"  cross-validated ranking quality (average precision): learned {learned:.2f} vs current weights "
            f"{now:.2f} -> {verdict}")
    MODELS.mkdir(exist_ok=True)
    LEARNED.write_text(json.dumps(model), encoding="utf-8")
    return model


def learned_score(model, scores_csv):
    """The learned probability per second, scaled so 0.5 lands on the default moment threshold (2.0)."""
    features, _ = window_features(scores_csv)
    return predict(model, features) * 4.0
