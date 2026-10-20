"""The moment and whistle rows that end up in moments.csv, whistles.csv and the viewer."""

import numpy as np

from .review import review_labels, review_name
from .scoring import main_cue
from .timecodes import format_time

SCORE_LOOKBACK = 40  # seconds: the overlay changes within seconds of a try, so the play is in the 40 s before it


def make_moment(second, score, spikes, weights, lead_in, cue=None):
    """One row of moments.csv for the peak at `second`. `lead_in` is how far before the peak playback starts."""
    jump = max(0.0, second - lead_in)
    return {
        "rank": 0,  # set by rank_moments once every moment is known
        "peak_s": int(second),
        "peak_time": format_time(second),
        "jump_s": f"{jump:.1f}",
        "jump_time": format_time(jump),
        "score": f"{score[second]:.2f}",
        "main_cue": cue or main_cue(spikes, weights, second),
        **{f"{name}_spike": f"{spikes[name][second]:.2f}" for name in spikes},
        # filled in when the scoreboard is read:
        "source": "",        # "scoreboard" for a scoring play
        "scoreline": "",     # e.g. "NSRFC 12–14 SSA"
        "auto_name": "",     # e.g. "NSRFC try (12–14)"
        "auto_labels": "",   # e.g. "Try"
    }


def attach_scoreboard(moments, scoreboard, score, spikes, weights, lead_in):
    """Attach each score change to the moment that caused it: the biggest moment in the 40 s before the overlay
    changed, favouring ones closer to the change. If no picked moment is in that window, the biggest free second
    there becomes a new moment."""
    home, away = scoreboard["teams"]
    for event in scoreboard["events"]:
        window_end = int(min(len(score) - 1, event["t_change"] + 2))
        window_start = max(0, window_end - SCORE_LOOKBACK)

        def recency(second, window_end=window_end):  # 1 at the change, 0.5 at the start of the window
            return 1.0 - 0.5 * (window_end - second) / SCORE_LOOKBACK

        candidates = [m for m in moments if window_start <= m["peak_s"] <= window_end and not m["source"]]
        if candidates:
            moment = max(candidates, key=lambda m: float(m["score"]) * recency(m["peak_s"]))
        else:
            second = biggest_free_second(moments, score, window_start, window_end, recency)
            moment = make_moment(second, score, spikes, weights, lead_in, cue="scoreboard")
            moments.append(moment)
        moment["source"] = "scoreboard"
        moment["scoreline"] = f'{home} {event["after"][0]}–{event["after"][1]} {away}'
        moment["auto_name"] = event["name"]
        moment["auto_labels"] = event["label"] or ""
    return moments


def biggest_free_second(moments, score, start, end, recency):
    """The highest-scoring second in start..end that isn't within 10 s of an existing moment (or, if all of them
    are, the highest-scoring second regardless)."""
    taken = np.zeros(end - start + 1, bool)
    for m in moments:
        if start - 10 <= m["peak_s"] <= end + 10:
            taken[max(0, m["peak_s"] - start - 10): m["peak_s"] - start + 11] = True
    weighted = np.where(taken, -np.inf, score[start:end + 1] * recency(np.arange(start, end + 1)))
    best = weighted if np.isfinite(weighted).any() else score[start:end + 1]
    return start + int(np.argmax(best))


def rank_moments(moments):
    """Rank 1, 2, 3... with scoring plays first, then by score; then put the list back in time order."""
    by_importance = sorted(moments, key=lambda m: (m["source"] != "scoreboard", -float(m["score"])))
    for rank, moment in enumerate(by_importance, 1):
        moment["rank"] = rank
    moments.sort(key=lambda m: m["peak_s"])
    return moments


def apply_review(moments, review):
    """Fill in each moment's name and labels: yours from review.json, else the automatic (scoreboard) ones."""
    for moment in moments:
        moment["name"] = review_name(review, "moments", moment["peak_s"], moment["auto_name"])
        moment["labels"] = review_labels(review, "moments", moment["peak_s"], moment["auto_labels"])
    return moments


def make_whistle_rows(whistles, lead_in, review):
    """One row of whistles.csv per blast, numbered from 1."""
    rows = []
    for number, whistle in enumerate(whistles, 1):
        start = float(f'{whistle["start_s"]:.2f}')  # the time as written, so review.json matches it
        rows.append({
            "n": number,
            "time": format_time(whistle["start_s"]),
            "start_s": f'{whistle["start_s"]:.2f}',
            "jump_s": f'{max(0.0, whistle["start_s"] - lead_in):.1f}',
            "length_s": f'{whistle["length_s"]:.2f}',
            "peak_db": f'{whistle["peak_db"]:.1f}',
            "name": review_name(review, "whistles", start),
            "labels": review_labels(review, "whistles", start),
        })
    return rows
