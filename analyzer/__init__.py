"""The pieces behind find_moments.py, one job per module:

    ffmpeg      running ffmpeg: decoding audio and frames, reading a video's length and size
    timecodes   "1:23:45" <-> seconds
    audio       loudness per second, and the referee's whistle: its pitch, a per-second cue, every blast
    video       camera movement and on-screen motion per second
    scoring     turning the cues into one "how unusual is this second" score, and picking the peaks
    moments     the rows for moments.csv / whistles.csv, ranking, attaching scoreboard events
    review      reading review.json (exported from the viewer) and scoring a run against it
    outputs     the CSV files and the copy of the video with chapters
    viewer      the results page (index.html), built from the files in viewer/
    scoreboard  optional: reading the on-screen score overlay (needs the `ai` extra)
    learning    optional: learning a better ranking from your reviews (numpy only)
"""
