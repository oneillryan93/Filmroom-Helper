"""Reading the on-screen score overlay (e.g. "NSRFC 7 - SSA 14"), so every score change becomes a moment.

Optional: needs the `ai` extra (`uv run --extra ai ...`; the app installs it the first time you tick it), which
brings RapidOCR. Steps:
  1. find where the overlay is, by reading a dozen frames spread across the match
  2. read just that patch of the picture every 2 seconds
  3. clean up the misreads and turn the score changes into events (a try, a conversion, a penalty goal...)
"""

import csv
import os
import re
import subprocess
import sys

import numpy as np

from .ffmpeg import FFMPEG, grab_frame, video_size
from .timecodes import format_time

READ_EVERY = 2.0  # seconds between reads of the overlay

# Points -> the label for a score change of that size by one team
POINTS = {5: "Try", 2: "Conversion", 3: "Penalty", 7: "Try"}  # 7: a penalty try, or a try and conversion at once
CONVERSION_WINDOW = 180  # seconds after a try that a +2 counts as its conversion


def log(message):
    print(message, file=sys.stderr, flush=True)


def require():
    """Stop with instructions, not a traceback, when the ai extra isn't installed."""
    missing = []
    for module in ("onnxruntime", "rapidocr_onnxruntime"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        sys.exit("--scoreboard needs the ai add-on (missing " + ", ".join(missing) + "): run with "
                 "`uv run --extra ai find_moments.py ...`, or tick an AI option in the app to install it")


# ---------------------------------------------------------------- reading a score from text

# OCR often mistakes digits for look-alike letters ("NSRFC O - SSA O")
LOOKALIKE_DIGITS = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1", "L": "1",
                                  "|": "1", "S": "5", "Z": "2", "B": "8"})
_NUMBER = r"([0-9OoDQIlL|SZB]{1,3})"
_TEAM = r"([A-Z][A-Z.&']{1,9})"
_DASH = r"\s*[-–—:]\s*"
SCORE_LAYOUTS = [
    # "NSRFC 7 - SSA 14": home, home score, away, away score
    (re.compile(_TEAM + r"\s*" + _NUMBER + _DASH + _TEAM + r"\s*" + _NUMBER + r"(?![0-9A-Z])"), (0, 1, 2, 3)),
    # "NSRFC 7 - 14 SSA": home, home score, away score, away
    (re.compile(_TEAM + r"\s*" + _NUMBER + _DASH + _NUMBER + r"\s*" + _TEAM), (0, 1, 3, 2)),
]


def parse_score(text):
    """("NSRFC", 7, "SSA", 14) from text like 'NSRFC 7 - SSA 14', else None."""
    text = text.upper().replace("—", "-").replace("–", "-")
    for pattern, (home_at, home_score_at, away_at, away_score_at) in SCORE_LAYOUTS:
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groups()
        try:
            return (groups[home_at], int(groups[home_score_at].translate(LOOKALIKE_DIGITS)),
                    groups[away_at], int(groups[away_score_at].translate(LOOKALIKE_DIGITS)))
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- finding and reading the overlay

def text_boxes(ocr, image):
    """[(text, (x0, y0, x1, y1))] for an image: each box OCR found, then boxes on the same line joined left to
    right (an overlay can come back in pieces). Single boxes come first, so the overlay's own box wins over a
    line that also holds "1st Half" and the like."""
    found, _ = ocr(image)
    boxes = []
    for corners, text, _confidence in found or []:
        xs, ys = [p[0] for p in corners], [p[1] for p in corners]
        boxes.append((min(xs), min(ys), max(xs), max(ys), text))

    lines = []
    for box in sorted(boxes, key=lambda b: (b[1] + b[3]) / 2):
        middle = (box[1] + box[3]) / 2
        if lines and abs(lines[-1]["middle"] - middle) < (box[3] - box[1]) * 0.6:
            lines[-1]["boxes"].append(box)
        else:
            lines.append({"middle": middle, "boxes": [box]})

    results = [(box[4], box[:4]) for box in boxes]
    for line in lines:
        parts = sorted(line["boxes"])
        if len(parts) > 1:
            outline = (min(p[0] for p in parts), min(p[1] for p in parts),
                       max(p[2] for p in parts), max(p[3] for p in parts))
            results.append((" ".join(p[4] for p in parts), outline))
    return results


def find_overlay(path, duration, ocr, samples=12):
    """(x, y, width, height) of the score overlay, or None: the typical box that reads as a score across a dozen
    frames spread over the match."""
    width, height = video_size(path)
    hits = []
    for i in range(samples):
        seconds = duration * (0.03 + 0.94 * i / max(1, samples - 1))
        image = np.frombuffer(grab_frame(path, seconds), np.uint8).reshape(height, width, 3)
        for text, box in text_boxes(ocr, image):
            if parse_score(text):
                hits.append(box)
                break
    if len(hits) < 3:
        return None
    x0, y0 = np.median([b[0] for b in hits]), np.median([b[1] for b in hits])
    x1, y1 = np.median([b[2] for b in hits]), np.median([b[3] for b in hits])
    # pad generously: the box grows when a score goes to two digits
    pad_x, pad_y = (x1 - x0) * 0.2 + 20, (y1 - y0) * 0.5 + 8
    x0, y0 = max(0, int(x0 - pad_x)), max(0, int(y0 - pad_y))
    x1, y1 = min(width, int(x1 + pad_x)), min(height, int(y1 + pad_y))
    return x0, y0, (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2  # even sizes, which ffmpeg's crop prefers


def read_overlay(path, duration, ocr, region, every=READ_EVERY):
    """Read the overlay every few seconds. Returns one read per frame:
    {"t", "home", "home_score", "away", "away_score", "text"}, with the team fields None when it didn't read."""
    x, y, width, height = region
    command = [FFMPEG, "-v", "error", "-nostdin", "-i", path, "-an",
               "-vf", f"fps=1/{every},crop={width}:{height}:{x}:{y}",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    frame_bytes = width * height * 3
    expected = int(duration / every) + 1
    reads, last_reported = [], -10
    with subprocess.Popen(command, stdout=subprocess.PIPE) as ffmpeg:
        while True:
            buffer = ffmpeg.stdout.read(frame_bytes)
            if len(buffer) < frame_bytes:
                break
            image = np.frombuffer(buffer, np.uint8).reshape(height, width, 3)
            reads.append(read_one(ocr, image, seconds=len(reads) * every))
            percent = 100 * len(reads) // expected
            if percent >= last_reported + 10:
                log(f"  scoreboard: {percent}%")
                last_reported = percent
    return reads


def read_one(ocr, image, seconds):
    # The crop is one line of text, so read it straight off (~10 ms). Only look for text boxes (~0.5 s) when
    # that doesn't give a score.
    recognised, _ = ocr(image, use_det=False, use_cls=False)
    text = recognised[0][0] if recognised else ""
    parsed = parse_score(text)
    if not parsed:
        for candidate, _box in text_boxes(ocr, image):
            parsed = parse_score(candidate)
            if parsed:
                text = candidate
                break
    if not parsed:
        return {"t": seconds, "home": None, "home_score": None, "away": None, "away_score": None, "text": ""}
    home, home_score, away, away_score = parsed
    return {"t": seconds, "home": home, "home_score": home_score, "away": away, "away_score": away_score,
            "text": text}


# ---------------------------------------------------------------- from reads to score changes

def score_changes(reads, confirm=2, hold_seconds=30):
    """Turn noisy reads into the moments the score changed.

    A new score must be read `confirm` times in a row. A normal change (+2, +3, +5 or +7 for one team) counts
    then; anything else (an OCR slip, or two scores between reads) only once it has held for `hold_seconds`.
    Scores never go down: a held decrease is a correction by whoever runs the overlay, so we just follow it.
    Returns (changes, teams)."""
    current = None                              # the score we believe now
    candidate, seen, first_seen = None, 0, None  # a different score we've started reading
    changes, teams = [], None
    for read in reads:
        if read["home"] is None:
            continue
        teams = teams or (read["home"], read["away"])
        score = (read["home_score"], read["away_score"])
        if score == current:
            candidate = None
            continue
        if score == candidate:
            seen += 1
        else:
            candidate, seen, first_seen = score, 1, read["t"]
        if current is None:
            if seen >= confirm:
                current = score  # the score when the footage starts, not a change
            continue

        home_gain, away_gain = score[0] - current[0], score[1] - current[1]
        one_team = (home_gain == 0) != (away_gain == 0)
        points = home_gain or away_gain
        normal = home_gain >= 0 and away_gain >= 0 and one_team and points in POINTS
        held = read["t"] - first_seen >= hold_seconds and seen >= 3
        if (normal and seen >= confirm) or held:
            if home_gain >= 0 and away_gain >= 0 and (home_gain or away_gain):
                changes.append({"t": first_seen, "team": teams[0] if home_gain else teams[1],
                                "side": "home" if home_gain else "away",
                                "points": points if one_team else home_gain + away_gain,
                                "before": current, "after": score, "normal": normal})
            current, candidate = score, None
    return changes, teams


def scoring_events(changes):
    """Name and label each change, folding a conversion into the try just before it."""
    events = []
    for change in changes:
        previous = events[-1] if events else None
        is_conversion_of_previous = (
            change["points"] == 2 and change["normal"] and previous and previous["label"] == "Try"
            and previous["team"] == change["team"] and not previous.get("converted")
            and change["t"] - previous["t_change"] <= CONVERSION_WINDOW)
        home_score, away_score = change["after"]
        if is_conversion_of_previous:
            previous["converted"] = True
            previous["after"] = change["after"]
            previous["name"] = f'{change["team"]} try + conversion ({home_score}–{away_score})'
            continue
        label = POINTS.get(change["points"]) if change["normal"] else None
        if label == "Try":
            what = "try" if change["points"] == 5 else "converted try"
        else:
            what = {"Conversion": "conversion", "Penalty": "penalty goal"}.get(label, f'+{change["points"]}')
        events.append({"t_change": change["t"], "team": change["team"], "side": change["side"], "label": label,
                       "after": change["after"], "name": f'{change["team"]} {what} ({home_score}–{away_score})'})
    return events


def write_scoreboard_csv(path, reads):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["second", "time", "home", "home_score", "away", "away_score", "ocr_text"])
        for read in reads:
            writer.writerow([f'{read["t"]:.0f}', format_time(read["t"]), read["home"] or "",
                             "" if read["home_score"] is None else read["home_score"], read["away"] or "",
                             "" if read["away_score"] is None else read["away_score"], read["text"]])


def read_scoreboard(path, duration, region=None, out_dir=None):
    """Everything the overlay tells us, ready to merge into the moments, or None if there's no readable overlay:
    {"region", "teams", "events", "changes", "start"}. Also writes scoreboard.csv (every read) to `out_dir`."""
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    if region is None:
        log("finding the scoreboard...")
        region = find_overlay(path, duration, ocr)
        if region is None:
            log("  no score overlay found (pass --scoreboard-region x,y,w,h if there is one)")
            return None
    log(f"reading the scoreboard at {region}...")
    reads = read_overlay(path, duration, ocr, region)
    changes, teams = score_changes(reads)
    if out_dir:
        write_scoreboard_csv(os.path.join(out_dir, "scoreboard.csv"), reads)
    readable = sum(read["home"] is not None for read in reads)
    log(f"  read the score in {readable}/{len(reads)} frames; {len(changes)} score changes")
    if not teams:
        return None
    first_read = next((r for r in reads if r["home"] is not None), None)
    return {
        "region": region,
        "teams": teams,
        "events": scoring_events(changes),
        "changes": [{"t": c["t"], "home": c["after"][0], "away": c["after"][1]} for c in changes],
        "start": (first_read["home_score"], first_read["away_score"]) if first_read else (0, 0),
    }
