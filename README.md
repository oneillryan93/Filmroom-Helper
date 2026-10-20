# Match Analyzer

Finds the key moments and every referee whistle in a full match video. Currently is geared heavily only towards rugby. It builds a results page where you can
watch the whole match, jump between moments, and review them: label, rename, mark false positives, add your own.

## Getting started

Double-click **Match Analyzer.bat** (Windows) or **Match Analyzer.command** (macOS/Linux). The first start sets
everything up by itself, with no prompts: it installs [uv](https://docs.astral.sh/uv/) into `.tools/uv`, along with
Python and the libraries it needs. ffmpeg comes bundled through `imageio-ffmpeg`. The app then opens in your browser:
pick a video, choose options and press Analyse.

From a terminal:

```
uv run find_moments.py "match.mp4"                 # analyse a match
uv run --extra ai find_moments.py "match.mp4" --scoreboard --learn
uv run find_moments.py --help                      # every option
```

## Project layout

```
find_moments.py        the command line: options, then main() as named steps
app.py / app.html      the local app (setup checks, options form, runs)
analyzer/
  ffmpeg.py            running ffmpeg: decoding, length, frame size
  timecodes.py         Timecode reformatting helpers
  audio.py             loudness and the referee's whistle         
  video.py             camera movement and on-screen motion        
  scoring.py           cues -> one score -> the peaks              
  learning.py          learning from your reviews                 
  moments.py           the moment and whistle rows, ranking, scoreboard events
  review.py            reading review.json, scoring a run against it
  outputs.py           the CSVs and the chapters video
  scoreboard.py        the optional score-overlay reader
  viewer.py            builds index.html from viewer/page.html, style.css and viewer.js
output/                every run's results: output/<video name>_moments/ (not in git)
```

---

# How it works

The analyzer never "watches" the match the way a person does. It turns the video into a few numbers for every
second of play, then looks for seconds where those numbers suddenly jump. Chapters 1 and 2 cover how those numbers
are made, Chapter 3 covers how they're combined, and Chapter 4 covers how your reviews can improve the result.

## Chapter 1: The audio component ([analyzer/audio.py](analyzer/audio.py))

The soundtrack gives two cues, **loudness** and **whistle**. It also produces the list of every whistle blast.

### 1.1 Getting the sound
ffmpeg turns the soundtrack into one long list of numbers: mono, 16,000 samples per second. That rate is plenty,
because the highest sound we care about (the whistle) sits below 4.5 kHz.

### 1.2 Loudness
For each whole second, the program takes the average strength of the sound (its RMS) and converts it to decibels.
Crowd roars, cheers and shouting all show up as loud seconds.

### 1.3 Breaking sound into pitches
The whistle can't be found by loudness alone, because a shout can be louder. The program splits the sound into
pitches with a spectrum analysis:
- It cuts the sound into short overlapping slices, about 64 ms long and starting every 32 ms.
- For each slice it measures how much energy sits at each pitch, like a graphic equaliser display.
- A full match is too big to do in one go, so it works in chunks.

### 1.4 Finding the referee's pitch
Every whistle has its own pitch, so the program learns this referee's pitch from the match itself:
- It looks only between 2 and 4.5 kHz, where whistles live.
- In every slice it finds the strongest tone in that range and checks it is **steady** (barely changed from the
  slice before).
- It keeps only the strongest 1% of steady tones and builds a tally of their pitches in 50 Hz bins.
- The most common bin is the referee's pitch.

Shouts are delineated vs. whistles because their pitch wanders and mostly sits lower, around 1.5–1.8 kHz. If there aren't
enough strong tones to decide it falls back to 2.4 kHz. You can also set the pitch yourself with `--whistle-pitch`.

### 1.5 How much the whistle stands out
For every 32 ms slice, the program asks how far the strongest tone within ±200 Hz of the referee's pitch stands
above the background noise:
- The background is the median energy across 1–6 kHz, roughly how noisy everything else is at that instant.
- The result is the whistle's **prominence** in dB.

Comparing against the background is the key trick. A shout raises everything at once, so the background rises with
it. A whistle raises one narrow pitch, so it still stands out even under a shout.

### 1.6 The per-second whistle cue
For scoring, a tone only counts if it holds for about 0.13 s (4 slices) and its pitch stays within 150 Hz the whole
time. The whistle cue for a second is the strongest sustained tone in that second. Brief squeaks and clatters are
ignored.

### 1.7 Finding each whistle blast (whistles.csv)
Separately, the program logs every blast using "start high, allowed to dip":
- **Start:** a blast starts when prominence reaches the threshold (32 dB by default, set with `--whistle-db`).
- **Continue:** it carries on while prominence stays within 8 dB of the threshold, and it may bridge up to 2 quieter
  slices (about 64 ms). This stops a faint whistle under crowd noise, which flickers, from breaking into fragments.
- **Keep:** a blast is kept only if it lasts at least 0.2 s and has at least 3 slices above the threshold.
- **Merge:** blasts less than 0.6 s apart are joined into one, because a whistle's trill naturally breaks up.

Each blast records its start time, length and peak dB.

---

## Chapter 2: The video component ([analyzer/video.py](analyzer/video.py))

The picture gives two cues, **camera** and **motion**.

### 2.1 Getting the frames
Decoding video is the slowest part of the whole run, so ffmpeg decodes it once at a tiny size: 160×90 pixels,
greyscale, 5 frames per second. That keeps the detail that matters (big movements) and throws away the rest.
`--hwaccel` lets the graphics card do the decoding.

### 2.2 Camera movement (pan and zoom)
When something big happens, whoever is filming usually swings the camera to follow it. To measure how far the whole
picture shifted between two frames, the program uses **phase correlation**:
1. It converts both frames into patterns of repeating waves (a 2-D Fourier transform).
2. A shift of the picture shows up as a consistent change between the two patterns. Combining them and converting
   back gives an image with one bright spot, and that spot's position is the shift (e.g. 6 pixels left, 1 up).
3. The edges of each frame are faded out first, so the hard border doesn't create a fake "no movement" answer.

The cue is the shift distance in pixels per frame, averaged over each second.

### 2.3 On-screen motion
This is simpler: the average change in brightness of every pixel from one frame to the next. Players sprinting, a
ruck collapsing or a fast pan all raise it, while a stoppage keeps it low. It's averaged per second as well.

---

## Chapter 3: How the cues become moments ([analyzer/scoring.py](analyzer/scoring.py))

This step sits between the components above and the learning below.

### 3.1 Lining up
Audio and video can differ by a second in length, so all four cues are padded or trimmed to the same number of
seconds.

### 3.2 Unusual rather than loud
A loud second only matters if it's louder than *usual for that part of the match*. For each cue and each second,
the program compares the value with the surrounding minute (61 seconds):
- **Typical level:** the median of that minute.
- **Typical variation:** the median absolute deviation (how far values usually stray from that median).
- **Spike score:** how many of those typical variations this second sits above the median.

Medians are used because they aren't thrown off by the spikes themselves. Only rises count, not drops. Spikes are
capped at 8 so one freak second can't dominate, then lightly smoothed over 3 seconds.

### 3.3 Combining
The four spike scores are blended with weights: loud 1.0, whistle 1.0, camera 0.7, motion 0.5 (you can change them
with `--weights`). The total is scaled so a typical real moment lands around 2. This per-second **score** is the
curve on the viewer's timeline and the `score` column in scores.csv. The cue that contributed most to a moment is
its **main cue**.

### 3.4 Picking the moments
The program repeatedly takes the highest-scoring second and blocks out 20 seconds either side of it, so you don't
get five moments for one play. It stops when the score falls below 2.0 or it has 60 moments. Each moment's playback
starts 12 seconds before the peak so you see the build-up. These limits are `--min-score`, `--max-moments`,
`--min-gap` and `--before`.

---

## Chapter 4: The learning component ([analyzer/learning.py](analyzer/learning.py))

The hand-set weights in 3.3 are a guess. Learning swaps that guess for a ranking based on your reviews, but only
when it can prove it does better.

### 4.1 Where it learns from
It uses every results folder in `output/` that has both a **review.json** (exported from the viewer) and a
**scores.csv**. Your reviews become examples:

| Your review | Treated as | Weight |
|---|---|---|
| Moments you kept, or added yourself | "real moment" | full |
| Moments you marked false positive | "skip" | full |
| Random quiet seconds 30 s+ from anything you reviewed | "probably skip" | 0.2 |

The random quiet seconds matter because you mostly confirm good moments. Without some "probably nothing here"
examples, the model would never see what ordinary play looks like. They get a low weight because a few of them
might actually be interesting.

### 4.2 What it looks at
For each example second, it reads the 4 spike scores from scores.csv for the 5 seconds on either side and builds 13
numbers:
- the highest of each cue's spikes in that window (4)
- the average of each cue's spikes in that window (4)
- each cue's spike at that exact second (4)
- the current overall score (1)

This lets it learn patterns such as "a whistle shortly after a roar matters, a roar on its own doesn't".

### 4.3 The model
It's a **logistic regression**, about the simplest learning model there is:
- It gives each of the 13 numbers a weight, adds them up and squashes the total into a probability from 0 to 1 that
  this is a real moment.
- Training nudges the weights 800 times to fit your examples better.
- It includes a small penalty that keeps the weights modest, so it doesn't over-fit a few examples.

It uses plain numpy. Nothing is downloaded, and it trains in well under a second.

### 4.4 Proving it's better before using it
1. **Enough data:** it needs at least 15 real moments and 5 false positives. Otherwise it tells you what's missing
   and changes nothing.
2. **A fair test:** it trains on part of your examples and tests on the part it hasn't seen (cross-validation):
   - With 3 or more reviewed matches, it holds out one whole match at a time. This is the honest test: does it
     work on a match it has never seen?
   - With fewer, it splits the examples into 5 random groups.
3. **Scoring the test:** both the learned ranking and the current weights are scored by **average precision**, a
   measure of how near the top the real moments land. 1.0 means every real moment comes before every false one.
4. **Decision:** the learned model is used only if it beats the current weights by at least 0.02. Either way, the
   result is saved to `.models/learned.json` and the run's log shows both numbers.

### 4.5 How it's used
With `--learn`, after scores.csv is written, the learned probability (×4) replaces the score for picking moments. A
50% chance of being real lands exactly on the usual 2.0 threshold, so `--min-score` behaves the same. The "Relearn
from reviews" button in the app runs only the training and the test (`--learn-only`).

### 4.6 What it can't do
It can only re-weigh the four cues. It can't see a try that made no noise, no camera movement and no motion. That
gap is somewhat mitigated by the scoreboard reader ([analyzer/scoreboard.py](analyzer/scoreboard.py), `--scoreboard`) fills, by
placing a moment wherever the score on screen changes. This only occurs of course if the video already has that info on the screen, so it is optional.


## Built with

- [FFmpeg](https://ffmpeg.org/) for video and audio decoding. It is pulled in as a dependency or used as a separate program if found on your local system and isn't bundled here.
- [NumPy](https://numpy.org/) for the logistic regression and many data tranformation actions.
- The camera-pan cue uses phase correlation: C. D. Kuglin and D. C. Hines, "The phase correlation image alignment method", *Proc. IEEE Int. Conf. on Cybernetics and Society*, 163–165 (1975).

## Privacy

Match footage, clips and moment lists should be kept private if you are forking this repository. Don't publish footage of players, *especially* of those under 18, without their consent.

## License

MIT. See [LICENSE](LICENSE).
