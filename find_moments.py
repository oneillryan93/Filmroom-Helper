#!/usr/bin/env python3
"""Find big moments in a rugby match video from audio and camera movement.

No machine learning: scores every second of the match on
  - loudness spikes (crowd, bench, shouting)
  - the referee's whistle (a steady tone at the whistle's pitch, learned from the audio; every blast is logged)
  - camera movement (sudden pans/zooms by whoever is filming)
  - on-screen motion (bursts of action)
then picks the highest peaks and annotates the full video with them: open <out>/index.html to watch the
whole match with every moment marked on a timeline and listed alongside, one click (or N/P key) away.

Needs numpy and imageio-ffmpeg (which bundles ffmpeg; set IMAGEIO_FFMPEG_EXE to use your own build). uv installs
them from pyproject.toml. Decoding is the slow part, so video is decoded once, tiny (160x90 grey, 5 fps); pass
--hwaccel to decode on the GPU. The work is done by the modules in analyzer/.

Easiest: double-click "Match Analyzer.bat" (Windows) or "Match Analyzer.command" (macOS/Linux), which sets
everything up and runs this from a page.

Usage:
  uv run find_moments.py match.mp4
  uv run find_moments.py match.mp4 --chapters               # also write a copy with chapters for VLC etc.
  uv run find_moments.py match.mp4 --truth my_moments.txt   # check against your own list
"""

import argparse
import os
import sys
import time

from analyzer import moments as moment_rows
from analyzer.audio import read_audio_cues
from analyzer.ffmpeg import video_duration
from analyzer.outputs import (
    WHISTLE_FIELDS,
    default_results_folder,
    write_chapters_video,
    write_rows_csv,
    write_scores_csv,
)
from analyzer.review import load_review, read_times, report, report_against_review
from analyzer.scoring import combine, parse_weights, pick_peaks, same_length, smooth, spike_score
from analyzer.timecodes import format_time
from analyzer.video import read_video_cues
from analyzer.viewer import write_viewer_page


def build_parser():
    """The command-line options. The app builds its options form from these, so the help texts are its labels'
    descriptions."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add = parser.add_argument
    add("video", nargs="?", help="match video file (not needed with --learn-only)")
    add("--out", help="results folder (default: output/<video name>_moments in the project folder)")
    add("--max-moments", type=int, default=60, help="most moments to pick")
    add("--min-gap", type=int, default=20, help="seconds between moments")
    add("--min-score", type=float, default=2.0, help="how 'unusual' a second must be to count as a moment")
    add("--before", type=float, default=12, help="seconds of lead-in when jumping to a moment")
    add("--after", type=float, default=5, help="seconds after the peak a moment stays highlighted")
    add("--weights", help="e.g. loud=1,whistle=1.5,camera=0.5,motion=0.5")
    add("--hwaccel", action="store_true", help="decode video on the GPU if ffmpeg supports it")
    add("--chapters", action="store_true",
        help="also write <out>/match_chapters.mp4, a copy of the video with a chapter per moment")
    add("--whistle-db", type=float, default=32,
        help="how far (dB) a tone must stand above the background to log a whistle; lower catches more")
    add("--whistle-pitch", type=float, help="referee's whistle pitch in Hz (default: worked out from audio)")
    add("--whistle-before", type=float, default=6, help="seconds of lead-in when jumping to a whistle")
    add("--truth", help="text file of your own moment times (mm:ss or h:mm:ss), one per line")
    add("--tolerance", type=float, default=15, help="seconds for a --truth match")
    add("--whistle-truth", help="text file of real whistle times, one per line, to tune --whistle-db")
    ai = parser.add_argument_group("AI add-on (optional, runs locally)")
    ai.add_argument("--scoreboard", action="store_true",
                    help="read the on-screen score overlay: every score change becomes a labelled, named moment")
    ai.add_argument("--scoreboard-region", help="x,y,w,h of the score overlay, if it isn't found by itself")
    ai.add_argument("--learn", action="store_true",
                    help="learn from your reviews and rank moments that way when it tests better")
    ai.add_argument("--learn-only", action="store_true",
                    help="just relearn from your reviews and report how well it does (no video needed)")
    add("--review", help="review.json exported from the page: score this run against your false "
                         "positives and additions (default: <out>/review.json if it exists)")
    return parser


def log(message):
    print(message, file=sys.stderr, flush=True)


def read_cues(video, args):
    """Every per-second cue from the video, all the same length. Also returns the whistle blasts."""
    started = time.time()
    log("reading audio...")
    loudness, whistle, pitch, whistles = read_audio_cues(video, args.whistle_db, args.whistle_pitch)
    audio_done = time.time()
    log(f"audio: {audio_done - started:.1f}s (referee whistle ~{pitch:.0f} Hz)")
    log("reading video (the slow part)...")
    camera, motion = read_video_cues(video, args.hwaccel)
    log(f"video: {time.time() - audio_done:.1f}s")
    cues = same_length({"loud": loudness, "whistle": whistle, "camera": camera, "motion": motion})
    return cues, whistles


def score_seconds(cues, weights, out, args):
    """The per-second spikes and overall score (or the learned score, with --learn when it tests better)."""
    spikes = {name: smooth(spike_score(values)) for name, values in cues.items()}
    score = combine(spikes, weights)
    write_scores_csv(os.path.join(out, "scores.csv"), score, cues, spikes)
    if args.learn:
        from analyzer import learning
        model = learning.learn([out])
        if model.get("use"):
            score = learning.learned_score(model, os.path.join(out, "scores.csv"))
            log("ranking moments the way your reviews do (a moment needs 50%+ by default)")
    return spikes, score


def find_moments(video, duration, score, spikes, weights, out, args):
    """The moment rows, ranked, with the scoreboard's scoring plays attached (with --scoreboard)."""
    peaks = pick_peaks(score, args.max_moments, args.min_gap, args.min_score)
    moments = [moment_rows.make_moment(second, score, spikes, weights, args.before) for second in peaks]
    scoreboard = None
    if args.scoreboard:
        from analyzer.scoreboard import read_scoreboard
        region = tuple(int(v) for v in args.scoreboard_region.split(",")) if args.scoreboard_region else None
        scoreboard = read_scoreboard(video, duration, region, out_dir=out)
        if scoreboard:
            moment_rows.attach_scoreboard(moments, scoreboard, score, spikes, weights, args.before)
    moment_rows.rank_moments(moments)
    return moments, scoreboard


def check_against_known(args, review, review_path, moments, whistles):
    """Print how this run did against --truth / --whistle-truth files and your review.json."""
    def near_moment(t, moment):
        return abs(t - moment["peak_s"]) <= args.tolerance

    def near_whistle(t, whistle):  # times are written to the second, so allow a little slack around the blast
        return whistle["start_s"] - 1.5 <= t <= whistle["start_s"] + whistle["length_s"] + 1.5

    if args.truth:
        report("moments", read_times(args.truth), [], moments, near_moment)
    if args.whistle_truth:
        report("whistles", read_times(args.whistle_truth), [], whistles, near_whistle)
    if review:
        print(f"against your review ({review_path}):")
        report_against_review(review, moments, whistles, near_moment, near_whistle)


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.learn_only:
        from analyzer import learning
        learning.learn([args.out] if args.out else [])
        return
    if not args.video:
        parser.error("the video is required (unless you pass --learn-only)")
    if args.scoreboard:
        from analyzer.scoreboard import require
        require()

    weights = parse_weights(args.weights)
    out = args.out or default_results_folder(args.video)
    os.makedirs(out, exist_ok=True)
    duration = video_duration(args.video)

    cues, whistles = read_cues(args.video, args)
    spikes, score = score_seconds(cues, weights, out, args)
    moments, scoreboard = find_moments(args.video, duration, score, spikes, weights, out, args)

    review_path = args.review or os.path.join(out, "review.json")
    review = load_review(review_path, required=bool(args.review))
    moment_rows.apply_review(moments, review)
    whistle_rows = moment_rows.make_whistle_rows(whistles, args.whistle_before, review)

    if moments:
        write_rows_csv(os.path.join(out, "moments.csv"), moments, list(moments[0]))
    write_rows_csv(os.path.join(out, "whistles.csv"), whistle_rows, WHISTLE_FIELDS)
    settings = {"min_score": args.min_score, "before": args.before, "after": args.after,
                "whistle_before": args.whistle_before}
    write_viewer_page(out, args.video, moments, whistle_rows, score, duration, settings, review, scoreboard)
    if args.chapters:
        started = time.time()
        write_chapters_video(args.video, moments, duration, os.path.join(out, "match_chapters.mp4"))
        log(f"chapters: {time.time() - started:.1f}s")

    print(f"{len(moments)} moments, {len(whistle_rows)} whistles from {format_time(duration)} of video"
          f" -> open {os.path.join(out, 'index.html')}")
    check_against_known(args, review, review_path, moments, whistles)


if __name__ == "__main__":
    main()
