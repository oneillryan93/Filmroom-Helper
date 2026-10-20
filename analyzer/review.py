"""review.json: what you exported from the viewer, and checking a run against it.

The file holds, for "moments" and for "whistles":
    kept     times of detections you didn't mark as false
    false    times of detections you marked as false positives
    added    your own: [{"t", "name", "labels"}]
    labels   labels you gave detections: [{"t", "labels": [...]}]   (an empty list means "I removed them")
    names    names you gave detections:  [{"t", "name"}]           (an empty name means "back to the default")
plus "labelset", your list of labels in order. Detections are matched by time, within half a second.
"""

import json
import os
import sys

from .timecodes import format_time, parse_time

SAME_TIME = 0.5  # seconds


def load_review(path, required=False):
    """The review at `path`, or None if there isn't one (an error if you asked for that file explicitly)."""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if required:
        sys.exit(f"--review: no such file {path}")
    return None


def _entry_at(review, kind, field, seconds):
    for entry in ((review or {}).get(kind) or {}).get(field, []):
        if abs(float(entry.get("t", -1e9)) - seconds) < SAME_TIME:
            return entry
    return None


def review_labels(review, kind, seconds, automatic=""):
    """Labels you gave the detection at `seconds`, as "Try; Penalty" for the CSVs. An entry you made wins even
    when it's empty (you removed the automatic ones); otherwise the automatic labels stand."""
    entry = _entry_at(review, kind, "labels", seconds)
    return "; ".join(entry.get("labels", [])) if entry else automatic


def review_name(review, kind, seconds, automatic=""):
    """The name you gave the detection at `seconds`, else the automatic one."""
    entry = _entry_at(review, kind, "names", seconds)
    return entry.get("name", "") if entry else automatic


def read_times(path):
    """Times from a text file, one per line ("4:05" or "1:02:03"); lines starting with # are skipped."""
    with open(path) as f:
        return [parse_time(line) for line in f if line.strip() and not line.startswith("#")]


def report(kind, truth, false, detections, near):
    """Print how a run's detections line up with known real events (`truth`) and known false positives.
    `near(time, detection)` says whether a known time matches a detection."""
    caught = [t for t in truth if any(near(t, d) for d in detections)]
    real = [d for d in detections if any(near(t, d) for t in truth)]
    marked_false = [d for d in detections if d not in real and any(near(t, d) for t in false)]
    unreviewed = len(detections) - len(real) - len(marked_false)
    print(f"  {kind}: caught {len(caught)}/{len(truth)} of yours; of {len(detections)} detected, {len(real)} real, "
          f"{len(marked_false)} you marked false, {unreviewed} not reviewed")
    for t in truth:
        if t not in caught:
            print(f"    missed {format_time(t)}")


def report_against_review(review, moments, whistles, near_moment, near_whistle):
    for kind, detections, near in (("moments", moments, near_moment), ("whistles", whistles, near_whistle)):
        section = review.get(kind) or {}
        truth = list(section.get("kept", [])) + [added["t"] for added in section.get("added", [])]
        report(kind, truth, section.get("false", []), detections, near)
