"""The moment and whistle rows that end up in moments.csv, whistles.csv and the viewer."""

import numpy as np

from .review import clean_scoring, review_false, review_labels, review_name, review_scoring
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
        "auto_labels": "",   # e.g. "Try"
        "_auto_scoring": None,  # the overlay's {"side", "play", "points"} (not written to moments.csv)
        # filled in by apply_scoring, from the scoreboard and the scoring you set in the page:
        "scoring": "",       # e.g. "NSRFC try +5"
        "scoreline": "",     # the score after it, e.g. "NSRFC 12–14 SSA"
        "auto_name": "",     # e.g. "NSRFC try (12–14)"
    }


def attach_scoreboard(moments, scoreboard, score, spikes, weights, lead_in):
    """Attach each score change to the moment that caused it: the biggest moment in the 40 s before the overlay
    changed, favouring ones closer to the change. If no picked moment is in that window, the biggest free second
    there becomes a new moment."""
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
        moment["_auto_scoring"] = {"side": event["side"], "play": event["play"], "points": event["points"]}
        moment["auto_labels"] = event["label"] or ""
    return moments


def apply_scoring(moments, review, scoreboard):
    """Who scored at each moment and the score after it, from the scoreboard's readings and the scoring plays you
    set in the page (yours win; your own added moments count too, false positives don't). Fills each moment's
    `scoring`, `scoreline` and automatic name, e.g. "NSRFC try (12–14)"."""
    teams, (home, away) = match_teams(review, scoreboard)
    plays = [(m["peak_s"], m, review_scoring(review, m["peak_s"], m["_auto_scoring"])) for m in moments
             if not review_false(review, "moments", m["peak_s"])]
    added = ((review or {}).get("moments") or {}).get("added", [])
    plays += [(float(a["t"]), None, clean_scoring(a.get("scoring"))) for a in added if "t" in a]
    for moment in moments:
        moment["scoring"] = moment["scoreline"] = moment["auto_name"] = ""
    for _, moment, scoring in sorted(plays, key=lambda play: play[0]):
        if not scoring:
            continue
        side = 0 if scoring["side"] == "home" else 1
        if side == 0:
            home += scoring["points"]
        else:
            away += scoring["points"]
        if moment is None:
            continue
        points = f'+{scoring["points"]}'
        what = points if scoring["play"] == "Score" else scoring["play"].lower()
        moment["scoring"] = f"{teams[side]} {what}" + ("" if what == points else f" {points}")
        moment["scoreline"] = f"{teams[0]} {home}–{away} {teams[1]}"
        moment["auto_name"] = f"{teams[side]} {what} ({home}–{away})"
    return moments


def match_teams(review, scoreboard):
    """The team names and the score when the video starts: yours from the page, else the scoreboard's."""
    teams = list(scoreboard["teams"]) if scoreboard else ["Home", "Away"]
    start = tuple(scoreboard["start"]) if scoreboard else (0, 0)
    yours = (review or {}).get("teams") or []
    if len(yours) == 2:
        teams = [str(name).strip() or default for name, default in zip(yours, teams, strict=True)]
    if len((review or {}).get("start") or []) == 2:
        start = tuple(int(score) for score in review["start"])
    return teams, start


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
