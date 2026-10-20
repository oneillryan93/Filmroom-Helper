// The results page. It plays the whole match with every detected moment and whistle on a timeline and in a
// list, and lets you review them: mark false positives, add your own, label and rename them, and export all of
// that as review.json for the next run of find_moments.py.
//
// Sections, in order: data and helpers / your review / events / the list / filters / label picker / renaming /
// timeline / playback / adding your own / export and import / the pop-out video window / keyboard / start-up.

"use strict";

// ---------------------------------------------------------------- data and helpers

const DATA = JSON.parse(document.getElementById("match-data").textContent);
const $ = id => document.getElementById(id);

const video = $("video");
const timeline = $("timeline");
const timelineCanvas = $("timeline-canvas");
const playhead = $("playhead");
const hoverTime = $("hover-time");
const eventList = $("event-list");
video.src = DATA.video;

const KINDS = ["moments", "whistles"];
const SINGULAR = {moments: "moment", whistles: "whistle"};
const WHISTLE_COLOR = DATA.colors.whistle;
const YOURS_COLOR = "#eeeeee";
const DIMMED_COLOR = "#555555";  // false positives, and markers the filter hides
const PRESET_LABELS = ["Positive Play", "Negative Play", "Try - Our Team", "Try - Opponents",
  "Line break - Our Team", "Line break - Opponents", "Good Tackle", "Bad Tackle", "Turnover", "Penalty"];
const UNLABELLED = "\u0000none";  // the filter key for "no labels yet" (can't clash with a real label)
const SAME_TIME = 0.5;  // seconds: your review is matched to detections by time

function formatTime(seconds) {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total / 60) % 60;
  const secs = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
}

const HTML_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};
function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, char => HTML_ESCAPES[char]);
}

function randomId() {
  return Math.random().toString(36).slice(2, 10);
}

function videoLength() {
  return video.duration || DATA.duration;
}

function sameTime(a, b) {
  return Math.abs(a - b) < SAME_TIME;
}

// An element with a class and text, e.g. make("span", "event-time", "4:05")
function make(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

// Labels as a tidy list: strings, trimmed, no blanks, no repeats
function cleanLabels(list) {
  if (!Array.isArray(list)) return [];
  return [...new Set(list.map(label => String(label).trim()).filter(Boolean))];
}

// The detections from find_moments.py. Each gets three times: `at` is where its marker goes, `jump_s` where
// playback starts when you pick it, and `until` when its highlight ends.
const detections = {
  moments: DATA.moments.map(m => ({...m, kind: "moments", at: m.peak_s, until: m.peak_s + DATA.after})),
  whistles: DATA.whistles.map(w => ({...w, kind: "whistles", at: w.start_s, until: w.start_s + w.length_s + 2})),
};

// The colour of an event's dot and marker
function markerColor(event) {
  if (event.yours) return YOURS_COLOR;
  if (event.kind === "whistles") return WHISTLE_COLOR;
  if (event.source === "scoreboard") return DATA.colors.scoreboard;
  return DATA.colors[event.main_cue];
}

function renderLegend() {
  const cues = Object.entries(DATA.colors).filter(([cue]) => cue !== "scoreboard" || DATA.scoreboard);
  $("legend").innerHTML =
    cues.map(([cue, color]) => `<span><i class="dot" style="background:${color}"></i>${cue}</span>`).join("") +
    `<span><i class="dot yours"></i>yours</span><span>bottom lane: every whistle</span>`;
}

// ---------------------------------------------------------------- your review
// Everything you've told the page, per kind ("moments", "whistles"):
//   false    times of detections you marked as false positives
//   added    your own: {id, t, name, labels}
//   labels   labels you gave detections: {t, labels}   (an empty list means "I removed the automatic ones")
//   names    names you gave detections:  {t, name}     (an empty name means "back to the default")
// plus labelset: your labels, in order (the order decides the 1-9 keys). It's saved in this browser as you go;
// Export review downloads it as review.json, which find_moments.py reads on the next run.

const STORAGE_KEY = `match-review:${DATA.video_name}:${Math.round(DATA.duration)}`;

function cleanReview(raw) {
  const clean = {};
  for (const kind of KINDS) {
    const section = (raw && raw[kind]) || {};
    const list = field => (Array.isArray(section[field]) ? section[field] : []);
    const hasTime = entry => entry && Number.isFinite(+entry.t);
    clean[kind] = {
      false: list("false").map(Number).filter(Number.isFinite),
      added: list("added").filter(hasTime).map(added => ({
        id: added.id || randomId(),
        t: +added.t,
        note: String(added.note || ""),
        name: String(added.name || added.note || "").trim(),  // older reviews kept the typed text as a note
        labels: cleanLabels(added.labels),
      })),
      labels: list("labels").filter(hasTime).map(entry => ({t: +entry.t, labels: cleanLabels(entry.labels)})),
      names: list("names").filter(hasTime).map(entry => ({t: +entry.t, name: String(entry.name || "").trim()})),
    };
  }
  // Your label list keeps its order. Any label in use but missing from it (yours, or the scoreboard's
  // automatic ones) is added at the end.
  const usedByYou = KINDS.flatMap(kind => [...clean[kind].added, ...clean[kind].labels].flatMap(x => x.labels));
  const automatic = [...DATA.moments, ...DATA.whistles].flatMap(d => (d.auto ? d.auto.labels : []));
  const saved = cleanLabels(raw && raw.labelset);
  clean.labelset = cleanLabels([...(saved.length ? saved : PRESET_LABELS), ...usedByYou, ...automatic]);
  return clean;
}

function loadSavedReview() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved ? JSON.parse(saved) : null;
  } catch (error) {
    return null;  // storage blocked, or something unreadable saved
  }
}

function saveReview() {
  let saved = false;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(review));
    saved = true;
  } catch (error) {}
  $("saved-status").textContent = saved
    ? "Saved in this browser"
    : "Browser storage unavailable: export to keep your changes";
}

let review = cleanReview(loadSavedReview() || DATA.review);

// ---------------------------------------------------------------- events: the detections plus your own
// Rebuilt from the detections and your review after every change.

let events = {moments: [], whistles: []};

function buildEvents(kind) {
  const section = review[kind];
  const leadIn = kind === "moments" ? DATA.before : DATA.whistle_before;
  const highlightFor = kind === "moments" ? DATA.after : 3;
  for (const event of detections[kind]) {
    event.falsePositive = section.false.some(t => sameTime(t, event.at));
    const labelled = section.labels.find(entry => sameTime(entry.t, event.at));
    const named = section.names.find(entry => sameTime(entry.t, event.at));
    event.labels = labelled ? [...labelled.labels] : event.auto ? [...event.auto.labels] : [];
    event.name = named ? named.name : event.auto ? event.auto.name : "";
  }
  const yours = section.added.map(added => ({
    kind,
    yours: true,
    id: added.id,
    at: added.t,
    name: added.name,
    note: added.note,
    labels: [...added.labels],
    jump_s: Math.max(0, added.t - leadIn),
    until: added.t + highlightFor,
  }));
  return [...detections[kind], ...yours].sort((a, b) => a.at - b.at);
}

// Anything that has to outlive a rebuild (the picker's target, the last thing you picked) holds a key, and
// looks its event up again when needed.
function keyOf(event) {
  return {kind: event.kind, id: event.yours ? event.id : null, at: event.at};
}

function findEvent(key) {
  if (!key) return null;
  return events[key.kind].find(event => (key.id ? event.id === key.id : !event.yours && event.at === key.at));
}

let tab = "moments";       // which list is showing
let sortByScore = false;
let filter = new Set();    // labels to show (UNLABELLED for "no labels"); empty shows everything
let lastPicked = null;     // key of the event you last jumped to, labelled or renamed
let activeEvent = null;    // the event playing now, if any

function matchesFilter(event) {
  if (!filter.size) return true;
  return [...filter].some(label => (label === UNLABELLED ? !event.labels.length : event.labels.includes(label)));
}
const shownEvents = () => events[tab].filter(matchesFilter);
const playableEvents = () => shownEvents().filter(event => !event.falsePositive);

function jumpTo(event) {
  if (event.at !== undefined) lastPicked = keyOf(event);
  video.currentTime = event.jump_s;
  video.play();
}

// Changes to your review. Each one rebuilds and redraws everything.

function toggleFalsePositive(event) {
  const marked = review[event.kind].false;
  const index = marked.findIndex(t => sameTime(t, event.at));
  if (index >= 0) marked.splice(index, 1);
  else marked.push(event.at);
  refreshAll();
}

function removeYours(event) {
  review[event.kind].added = review[event.kind].added.filter(added => added.id !== event.id);
  refreshAll();
}

function setLabels(event, labels) {
  const section = review[event.kind];
  if (event.yours) {
    const added = section.added.find(a => a.id === event.id);
    if (added) added.labels = labels;
  } else {
    // keep an entry even when it's empty, so removing the scoreboard's automatic labels sticks
    section.labels = section.labels.filter(entry => !sameTime(entry.t, event.at));
    section.labels.push({t: event.at, labels});
  }
  refreshAll();
}

// Returns whether the label is now on
function toggleLabel(event, label) {
  const had = event.labels.includes(label);
  setLabels(event, had ? event.labels.filter(l => l !== label) : [...event.labels, label]);
  return !had;
}

function addToLabelList(name) {
  name = name.trim();
  if (name && !review.labelset.includes(name)) review.labelset.push(name);
  return name;
}

function defaultTitle(event) {
  if (event.yours) return `Your ${SINGULAR[event.kind]}`;
  return event.kind === "moments" ? `#${event.rank}` : `Whistle ${event.n}`;
}

function titleOf(event) {
  return event.name || defaultTitle(event);
}

function setName(event, name) {
  const section = review[event.kind];
  name = name.trim();
  if (name === defaultTitle(event)) name = "";  // saving the default text is the same as clearing it
  if (event.yours) {
    const added = section.added.find(a => a.id === event.id);
    if (added) added.name = name;
  } else {
    section.names = section.names.filter(entry => !sameTime(entry.t, event.at));
    // an empty entry is only needed to override an automatic (scoreboard) name
    if (name || (event.auto && event.auto.name)) section.names.push({t: event.at, name});
  }
  refreshAll();
}

function refreshAll() {
  events = {moments: buildEvents("moments"), whistles: buildEvents("whistles")};
  renderCounts();
  saveReview();
  renderFilters();
  renderList();
  renderLabelPicker();
  placeLabelPicker();
  drawTimeline();
}

function renderCounts() {
  const count = (kind, test) => events[kind].filter(test).length;
  $("moments-count").textContent = count("moments", e => !e.falsePositive);
  $("whistles-count").textContent = count("whistles", e => !e.falsePositive);
  const falseMoments = count("moments", e => e.falsePositive), falseWhistles = count("whistles", e => e.falsePositive);
  const yourMoments = count("moments", e => e.yours), yourWhistles = count("whistles", e => e.yours);
  const labelled = count("moments", e => e.labels.length) + count("whistles", e => e.labels.length);
  $("review-summary").textContent =
    `Your review: ${falseMoments} moment and ${falseWhistles} whistle false positives, ` +
    `${yourMoments} moments and ${yourWhistles} whistles added, ${labelled} labelled`;
}

// ---------------------------------------------------------------- the list

function renderList() {
  let items = shownEvents();
  if (tab === "moments" && sortByScore) {
    // by rank, your own at the end
    const rank = event => (event.yours ? Infinity : event.rank);
    items = [...items].sort((a, b) => rank(a) - rank(b) || a.at - b.at);
  }
  eventList.innerHTML = "";
  if (!items.length) {
    eventList.append(make("li", "event empty", `Nothing ${filter.size ? "matches this filter" : "here yet"}.`));
  }
  for (const event of items) eventList.append(renderEventItem(event));
  activeEvent = null;
  updatePlayback();
}

function renderEventItem(event) {
  const item = make("li", "event");
  if (event.falsePositive) item.classList.add("false-positive");

  const dot = make("i", event.yours ? "dot yours" : "dot");
  if (!event.yours) dot.style.background = markerColor(event);

  const title = make("span", "event-title", titleOf(event));
  title.title = titleOf(event);
  const renameButton = make("button", "rename-button", "✎");
  renameButton.title = "Rename (R)";
  renameButton.onclick = click => { click.stopPropagation(); startRename(keyOf(event)); };
  const heading = make("span", "event-heading");
  heading.append(title, renameButton);

  const body = make("span", "event-body");
  body.append(heading, make("div", "event-detail", eventDetail(event)), renderLabelChips(event));

  const action = make("button", "event-action");
  if (event.yours) {
    action.textContent = "Remove";
    action.title = "Delete this one you added";
  } else {
    action.textContent = event.falsePositive ? "Undo" : "Remove";
    action.title = event.falsePositive ? "Count this detection again" : "Mark as a false positive";
  }
  action.onclick = click => {
    click.stopPropagation();
    event.yours ? removeYours(event) : toggleFalsePositive(event);
  };

  item.append(make("span", "event-time", formatTime(event.at)), dot, body, action);
  item.onclick = () => jumpTo(event);
  event.item = item;
  return item;
}

// The line under the title: what the detector saw. Once you rename a detection, its own name (#rank, Whistle n)
// moves here so it isn't lost.
function eventDetail(event) {
  if (event.yours) return "yours";
  const parts = [];
  if (event.name) parts.push(defaultTitle(event));
  if (event.kind === "whistles") parts.push(`${event.length_s.toFixed(1)}s`, `${event.peak_db} dB`);
  else if (event.source === "scoreboard") parts.push(event.scoreline);
  else parts.push(event.main_cue, `score ${event.score}`);
  if (event.falsePositive) parts.push("false positive");
  return parts.join(" · ");
}

function renderLabelChips(event) {
  const row = make("div", "event-labels");
  for (const label of event.labels) {
    const chip = make("button", "label-chip", label);
    chip.append(make("span", "remove-mark", "×"));
    chip.title = `Remove "${label}" from this ${SINGULAR[event.kind]}`;
    chip.onclick = click => { click.stopPropagation(); setLabels(event, event.labels.filter(l => l !== label)); };
    row.append(chip);
  }
  const addLabel = make("button", "add-label", event.labels.length ? "+" : "+ label");
  addLabel.title = "Label this (or press 1-9)";
  addLabel.onclick = click => { click.stopPropagation(); openLabelPicker(keyOf(event), addLabel); };
  row.append(addLabel);
  return row;
}

function switchTab(kind) {
  if (kind === tab) return;
  tab = kind;
  $("tab-moments").classList.toggle("selected", kind === "moments");
  $("tab-whistles").classList.toggle("selected", kind === "whistles");
  $("sort-toggle").style.visibility = kind === "moments" ? "visible" : "hidden";
  document.querySelectorAll(".kind-name").forEach(el => { el.textContent = SINGULAR[kind]; });
  closeLabelPicker();
  renderFilters();
  renderList();
}

$("tab-moments").onclick = () => switchTab("moments");
$("tab-whistles").onclick = () => switchTab("whistles");
$("sort-toggle").onclick = click => {
  sortByScore = !sortByScore;
  click.target.textContent = sortByScore ? "sort by time" : "sort by score";
  renderList();
};

// ---------------------------------------------------------------- filters: one chip per label in use, plus "Unlabelled"

function renderFilters() {
  const bar = $("filter-bar");
  const live = events[tab].filter(event => !event.falsePositive);
  const countWith = label => live.filter(e => (label === UNLABELLED ? !e.labels.length : e.labels.includes(label))).length;
  const labels = review.labelset.filter(label => countWith(label) || filter.has(label));
  bar.innerHTML = "";
  if (!labels.length && !filter.size) {
    bar.innerHTML = `<span class="dim">Label ${tab} with <b>+ label</b> or keys 1-9, then filter them here.</span>`;
    return;
  }
  const addChip = (text, value) => {
    const chip = make("button", "filter-chip" + (filter.has(value) ? " selected" : ""), text + " ");
    chip.append(make("span", "count", String(countWith(value))));
    chip.onclick = () => {
      filter.has(value) ? filter.delete(value) : filter.add(value);
      applyFilter();
    };
    bar.append(chip);
  };
  for (const label of labels) addChip(label, label);
  addChip("Unlabelled", UNLABELLED);
  if (filter.size) {
    const clear = make("button", "link-button", `showing ${live.filter(matchesFilter).length} of ${live.length} · clear`);
    clear.onclick = () => { filter.clear(); applyFilter(); };
    bar.append(clear);
  }
}

function applyFilter() {
  renderFilters();
  renderList();
  drawTimeline();
}

// ---------------------------------------------------------------- label picker: a small popup by "+ label"

let pickerTarget = null;  // key of the event being labelled

function openLabelPicker(key, anchor) {
  pickerTarget = key;
  lastPicked = key;
  renderLabelPicker();
  $("label-picker").classList.add("open");
  placeLabelPicker(anchor);
  $("new-label").value = "";
  $("new-label").focus();
}

function closeLabelPicker() {
  pickerTarget = null;
  $("label-picker").classList.remove("open");
}

// Sit by the event's "+ label" button. The list is re-rendered on every change and scrolls during playback, so
// the button is looked up again each time rather than remembered.
function placeLabelPicker(anchor) {
  if (!pickerTarget) return;
  if (!anchor || !anchor.isConnected) {
    const event = findEvent(pickerTarget);
    anchor = event && event.item && event.item.querySelector(".add-label");
  }
  if (!anchor || !anchor.isConnected) return;
  const picker = $("label-picker");
  const button = anchor.getBoundingClientRect();
  const width = picker.offsetWidth, height = picker.offsetHeight;
  const fitsBelow = button.bottom + height + 8 <= innerHeight;
  picker.style.left = `${Math.max(8, Math.min(button.left, innerWidth - width - 8))}px`;
  picker.style.top = `${fitsBelow ? button.bottom + 6 : Math.max(8, button.top - height - 6)}px`;
}

function renderLabelPicker() {
  if (!pickerTarget) return;
  const event = findEvent(pickerTarget);
  if (!event) return closeLabelPicker();
  const inUse = label => KINDS.some(kind => events[kind].some(e => e.labels.includes(label)));
  const what = event.name ? `"${event.name}"`
    : event.kind === "moments" ? (event.yours ? "Your moment" : `Moment #${event.rank}`) : defaultTitle(event);
  $("picker-title").textContent = `${what} at ${formatTime(event.at)}`;

  const box = $("picker-labels");
  box.innerHTML = "";
  review.labelset.forEach((label, index) => {
    const chip = make("button", "picker-label" + (event.labels.includes(label) ? " selected" : ""));
    if (index < 9) chip.append(make("kbd", "", String(index + 1)));
    chip.append(label);
    chip.onclick = () => toggleLabel(findEvent(pickerTarget), label);
    box.append(chip);
    if (!inUse(label) && !PRESET_LABELS.includes(label)) {
      // a label you made can be thrown away (a typo, say) while nothing uses it
      const remove = make("button", "picker-delete", "×");
      remove.title = `Delete the "${label}" label`;
      remove.onclick = () => {
        review.labelset = review.labelset.filter(l => l !== label);
        filter.delete(label);
        refreshAll();
      };
      box.append(remove);
    }
  });
}

$("new-label").addEventListener("keydown", press => {
  if (press.key === "Enter") {
    press.preventDefault();
    const name = addToLabelList(press.target.value);
    press.target.value = "";
    const event = findEvent(pickerTarget);
    if (name && event && !event.labels.includes(name)) toggleLabel(event, name);
    else refreshAll();
  } else if (press.key === "Escape") {
    press.preventDefault();
    closeLabelPicker();
  }
});
$("picker-done").onclick = closeLabelPicker;
document.addEventListener("mousedown", click => {
  const inside = $("label-picker").contains(click.target) || click.target.classList.contains("add-label");
  if (pickerTarget && !inside) closeLabelPicker();
});

// Number keys label whatever is playing now, else what you last jumped to or opened the picker for
function labelWithKey(number) {
  const label = review.labelset[number - 1];
  if (!label) return showToast(`No label ${number} yet: add one with + label`);
  const key = pickerTarget || (activeEvent ? keyOf(activeEvent) : lastPicked);
  const event = findEvent(key);
  if (!event) return showToast("Play or pick a moment or whistle first");
  const nowOn = toggleLabel(event, label);
  showToast(`${nowOn ? "Labelled" : "Removed"} "${label}"`);
}

// ---------------------------------------------------------------- renaming, in place
// Enter or clicking away saves, Esc cancels, and an empty name goes back to the default.

function startRename(key) {
  const event = findEvent(key);
  if (!event || !event.item || !event.item.isConnected) return showToast("Play or pick a moment or whistle first");
  closeLabelPicker();
  lastPicked = key;
  const input = make("input", "rename-input");
  input.value = titleOf(event);
  input.placeholder = defaultTitle(event);
  input.maxLength = 80;
  input.spellcheck = false;
  let finished = false;
  const finish = save => {
    if (finished) return;
    finished = true;
    if (save) setName(findEvent(key), input.value);
    else renderList();
  };
  input.onkeydown = press => {
    if (press.key === "Enter") { press.preventDefault(); finish(true); }
    else if (press.key === "Escape") { press.preventDefault(); finish(false); }
    press.stopPropagation();  // typing a name mustn't trigger the shortcut keys
  };
  input.onblur = () => finish(true);
  input.onclick = click => click.stopPropagation();
  event.item.querySelector(".event-title").replaceWith(input);
  input.focus();
  input.select();
}

// ---------------------------------------------------------------- timeline
// The overall score as a grey curve, the moment threshold as a dashed line, a marker per moment, notches where
// the score changed, and a lane along the bottom with every whistle.

const WHISTLE_LANE = 12;  // px

function drawTimeline() {
  const ratio = window.devicePixelRatio || 1;
  const width = timelineCanvas.clientWidth * ratio, height = timelineCanvas.clientHeight * ratio;
  if (!width || !height) return;
  timelineCanvas.width = width;
  timelineCanvas.height = height;
  const area = {
    ctx: timelineCanvas.getContext("2d"),
    width,
    ratio,
    graphHeight: height - WHISTLE_LANE * ratio,
    laneHeight: WHISTLE_LANE * ratio,
    x: seconds => (seconds / videoLength()) * width,
  };
  const top = Math.max(...DATA.score, DATA.min_score * 1.5);
  area.y = value => area.graphHeight - (value / top) * (area.graphHeight - 6 * ratio);
  drawScoreCurve(area);
  drawMomentMarkers(area);
  drawScoreChanges(area);
  drawWhistleLane(area);
}

function drawScoreCurve({ctx, width, ratio, graphHeight, x, y}) {
  ctx.fillStyle = "#3a3a3a";
  ctx.beginPath();
  ctx.moveTo(0, graphHeight);
  DATA.score.forEach((value, second) => ctx.lineTo(x(second), y(value)));
  ctx.lineTo(width, graphHeight);
  ctx.fill();
  // the moment threshold
  ctx.strokeStyle = "#555";
  ctx.setLineDash([4 * ratio, 4 * ratio]);
  ctx.beginPath();
  ctx.moveTo(0, y(DATA.min_score));
  ctx.lineTo(width, y(DATA.min_score));
  ctx.stroke();
  ctx.setLineDash([]);
}

function drawMomentMarkers({ctx, ratio, graphHeight, x}) {
  for (const moment of events.moments) {
    const left = x(moment.at);
    const dimmed = moment.falsePositive || !matchesFilter(moment);  // half height and grey
    ctx.fillStyle = dimmed ? DIMMED_COLOR : markerColor(moment);
    ctx.fillRect(left - ratio, dimmed ? graphHeight / 2 : 0, 2 * ratio, dimmed ? graphHeight / 2 : graphHeight);
    if (dimmed) continue;
    ctx.beginPath();  // a square head for yours, a round one for detections
    if (moment.yours) ctx.rect(left - 4 * ratio, ratio, 8 * ratio, 8 * ratio);
    else ctx.arc(left, 5 * ratio, 4 * ratio, 0, 7);
    ctx.fill();
  }
}

function drawScoreChanges({ctx, ratio, x}) {
  if (!DATA.scoreboard) return;
  ctx.fillStyle = DATA.colors.scoreboard;
  for (const change of DATA.scoreboard.changes) {
    const left = x(change.t);  // a small downward notch along the top edge
    ctx.beginPath();
    ctx.moveTo(left - 4 * ratio, 0);
    ctx.lineTo(left + 4 * ratio, 0);
    ctx.lineTo(left, 6 * ratio);
    ctx.fill();
  }
}

function drawWhistleLane({ctx, width, ratio, graphHeight, laneHeight, x}) {
  ctx.fillStyle = "#151515";
  ctx.fillRect(0, graphHeight, width, laneHeight);
  for (const whistle of events.whistles) {
    const dimmed = whistle.falsePositive || !matchesFilter(whistle);
    ctx.fillStyle = dimmed ? DIMMED_COLOR : markerColor(whistle);
    const length = whistle.yours ? 2 * ratio : Math.max(ratio, x(whistle.length_s));
    ctx.fillRect(x(whistle.at) - ratio / 2, graphHeight + 2 * ratio, length, laneHeight - 4 * ratio);
  }
}

// Clicking near a marker jumps to that event's lead-in (in the bottom lane: whistles); elsewhere seeks there
function timeAtPointer(pointer) {
  const box = timeline.getBoundingClientRect();
  const fraction = Math.min(1, Math.max(0, (pointer.clientX - box.left) / box.width));
  return fraction * videoLength();
}

function nearestEvent(list, seconds) {
  let best = null;
  for (const event of list) {
    if (!best || Math.abs(event.at - seconds) < Math.abs(best.at - seconds)) best = event;
  }
  return best;
}

timeline.onclick = click => {
  const seconds = timeAtPointer(click);
  const tolerance = videoLength() * 6 / timeline.clientWidth;  // 6 px either side
  const inWhistleLane = click.clientY > timeline.getBoundingClientRect().bottom - WHISTLE_LANE - 4;
  const kind = inWhistleLane ? "whistles" : "moments";
  const hit = nearestEvent(events[kind], seconds);
  if (hit && Math.abs(hit.at - seconds) <= tolerance) {
    switchTab(kind);
    jumpTo(hit);
  } else {
    video.currentTime = seconds;
  }
};
timeline.onmousemove = pointer => {
  hoverTime.style.display = "block";
  hoverTime.style.left = `${pointer.clientX - timeline.getBoundingClientRect().left}px`;
  hoverTime.textContent = formatTime(timeAtPointer(pointer));
};
timeline.onmouseleave = () => { hoverTime.style.display = "none"; };

// ---------------------------------------------------------------- playback: what's playing now

// "NSRFC 7–14 SSA" at this point of the match, or "" without a scoreboard
function scoreAtTime(seconds) {
  const scoreboard = DATA.scoreboard;
  if (!scoreboard) return "";
  let [home, away] = scoreboard.start;
  for (const change of scoreboard.changes) {
    if (change.t <= seconds) { home = change.home; away = change.away; }
  }
  return `${scoreboard.teams[0]} ${home}–${away} ${scoreboard.teams[1]}`;
}

function describeForNowPlaying(event) {
  if (!event) return "";
  if (event.name) return event.name;
  if (event.yours) return `your ${SINGULAR[event.kind]}`;
  return event.kind === "moments" ? `moment #${event.rank}` : `whistle ${event.n}`;
}

function updatePlayback() {
  const now = video.currentTime, length = videoLength();
  playhead.style.left = `${(now / length) * 100}%`;

  let playing = null;  // the last shown event whose window covers now
  for (const event of shownEvents()) {
    if (event.jump_s <= now + 0.25 && now <= event.until) playing = event;
  }
  if (playing !== activeEvent) {
    if (activeEvent && activeEvent.item) activeEvent.item.classList.remove("active");
    if (playing && playing.item) {
      playing.item.classList.add("active");
      playing.item.scrollIntoView({block: "nearest"});
    }
    activeEvent = playing;
  }
  const parts = [`${formatTime(now)} / ${formatTime(length)}`, describeForNowPlaying(playing), scoreAtTime(now)];
  $("now-playing").textContent = parts.filter(Boolean).join(" · ");
  if (caption) renderCaption(playing, now, length);
}

function jumpToNext() {
  const next = playableEvents().find(event => event.jump_s > video.currentTime + 0.5);
  if (next) jumpTo(next);
}

function jumpToPrevious() {
  // more than 3 s back, so pressing it right after a jump goes to the one before
  const previous = [...playableEvents()].reverse().find(event => event.jump_s < video.currentTime - 3);
  jumpTo(previous || {jump_s: 0});
}

function togglePlay() {
  video.paused ? video.play() : video.pause();
}

$("next").onclick = jumpToNext;
$("previous").onclick = jumpToPrevious;

let toastTimer = null;
function showToast(message) {
  const toasts = [$("toast"), videoWindow && videoWindow.document.getElementById("toast")].filter(Boolean);
  for (const toast of toasts) {
    toast.textContent = message;
    toast.classList.add("show");
  }
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toasts.forEach(toast => toast.classList.remove("show")), 1600);
}

// ---------------------------------------------------------------- adding your own

let addingAt = 0;

function openAddForm() {
  video.pause();
  closeLabelPicker();
  addingAt = video.currentTime;
  $("add-time").textContent = formatTime(addingAt);
  $("add-kind").textContent = SINGULAR[tab];
  $("add-name").value = "";
  $("add-form").classList.add("open");
  $("add-name").focus();
}

function closeAddForm() {
  $("add-form").classList.remove("open");
}

// Added to the tab you're on: your moment, or your whistle
function saveAddForm() {
  const id = randomId();
  const name = $("add-name").value.trim();
  review[tab].added.push({id, t: Math.round(addingAt * 10) / 10, note: "", name, labels: []});
  lastPicked = {kind: tab, id, at: null};
  closeAddForm();
  refreshAll();
}

$("add").onclick = openAddForm;
$("add-save").onclick = saveAddForm;
$("add-cancel").onclick = closeAddForm;
$("add-form").addEventListener("keydown", press => {
  if (press.key === "Enter") { press.preventDefault(); saveAddForm(); }
  else if (press.key === "Escape") { press.preventDefault(); closeAddForm(); }
});

// ---------------------------------------------------------------- export and import

function exportReview() {
  const exported = {video: DATA.video_name, duration: DATA.duration, exported: new Date().toISOString(),
                    labelset: review.labelset};
  for (const kind of KINDS) {
    const section = review[kind];
    exported[kind] = {
      // detections you didn't mark as false, so the next run can be scored against them
      kept: detections[kind].filter(d => !d.falsePositive).map(d => d.at),
      false: section.false,
      labels: section.labels,
      names: section.names,
      added: section.added.map(({t, note, name, labels}) => ({t, note, name, labels})),
    };
  }
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([JSON.stringify(exported, null, 2)], {type: "application/json"}));
  link.download = "review.json";
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

$("export").onclick = exportReview;
$("import").onclick = () => $("import-file").click();
$("import-file").onchange = async change => {
  const file = change.target.files[0];
  change.target.value = "";
  if (!file) return;
  try {
    review = cleanReview(JSON.parse(await file.text()));
    filter.clear();
    refreshAll();
  } catch (error) {
    alert("That file isn't a review.json from this page.");
  }
};

// ---------------------------------------------------------------- the pop-out video window
// The video can play in its own window, to drag to another screen or make fullscreen, while the list and
// timeline stay here (and get the room the video used). It's the same <video> element moved across, so
// playback, seeking and every key keep working from either window.

let videoWindow = null;      // the pop-out window while it's open
let caption = null;          // its caption bar (time, name, labels, score)
let captionHidden = false;
const placeholder = $("video-placeholder");

const VIDEO_WINDOW_STYLE = `
  :root { color-scheme: dark; }
  html, body { margin: 0; height: 100%; background: #000; color: #eee; font: 16px/1.4 system-ui, sans-serif; }
  body { display: flex; flex-direction: column; }
  video { flex: 1; min-height: 0; width: 100%; background: #000; object-fit: contain; }
  .caption { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; padding: 10px 16px;
             background: #111; border-top: 1px solid #2c2c2c; font-size: 20px; }
  .caption.hidden { display: none; }
  .caption-time { min-width: 5.5em; color: #999; font-variant-numeric: tabular-nums; }
  .caption-name { font-weight: 700; }
  .caption-dot { width: 12px; height: 12px; border-radius: 50%; flex: none; }
  .caption-labels { display: flex; flex-wrap: wrap; gap: 6px; }
  .caption-label { padding: 1px 10px; border-radius: 999px; background: #2d2d2d; border: 1px solid #3a3a3a;
                   font-size: 15px; }
  .caption-score { margin-left: auto; color: #f472b6; font-weight: 600; }
  .caption-buttons { display: flex; gap: 6px; }
  .caption button { padding: 5px 10px; background: #1b1b1b; color: #eee; border: 1px solid #2c2c2c;
                    border-radius: 6px; font: inherit; font-size: 14px; cursor: pointer; }
  .caption button:hover { border-color: #999; }
  .toast { position: fixed; left: 50%; bottom: 80px; transform: translateX(-50%); padding: 8px 14px;
           background: #000; color: #eee; border: 1px solid #444; border-radius: 6px; font-size: 16px;
           opacity: 0; pointer-events: none; transition: opacity 0.15s; }
  .toast.show { opacity: 1; }`;

const VIDEO_WINDOW_BODY = `
  <div class="caption">
    <span class="caption-time"></span><span class="caption-dot"></span><span class="caption-name"></span>
    <span class="caption-labels"></span><span class="caption-score"></span>
    <span class="caption-buttons">
      <button data-action="previous" title="Previous (P)">&#9664; Prev</button>
      <button data-action="next" title="Next (N)">Next &#9654;</button>
      <button data-action="fullscreen" title="Fullscreen (F)">Fullscreen</button>
      <button data-action="back" title="Back to the main window (V)">Back</button>
    </span>
  </div>
  <div class="toast" id="toast"></div>`;

// Moving the element between windows can make the browser reset it (always when its window is closing), so
// note where it was and put it back there. Only a reset to the start is undone, never a seek you made since.
function moveVideoKeepingPlace(move) {
  const position = video.currentTime, wasPlaying = !video.paused;
  let seekedSince = false;
  const noteSeek = () => { seekedSince = true; };
  move();
  video.addEventListener("seeking", noteSeek, {once: true});
  const restore = () => {
    video.removeEventListener("seeking", noteSeek);
    if (!seekedSince && video.currentTime < 0.5 && position > 0.5) video.currentTime = position;
    if (wasPlaying) video.play().catch(() => {});
  };
  if (video.readyState >= 1) restore();
  else video.addEventListener("loadedmetadata", restore, {once: true});
}

function popOutVideo() {
  if (videoWindow && !videoWindow.closed) { videoWindow.focus(); return; }
  const opened = window.open("", "match-video", "popup,width=1280,height=800");
  if (!opened) return showToast("The browser blocked the video window: allow pop-ups for this page");
  videoWindow = opened;
  const doc = opened.document;
  doc.title = `${DATA.video_name} (video)`;
  doc.head.innerHTML = `<meta charset="utf-8"><style>${VIDEO_WINDOW_STYLE}</style>`;
  doc.body.innerHTML = VIDEO_WINDOW_BODY;
  moveVideoKeepingPlace(() => doc.body.prepend(video));

  caption = doc.querySelector(".caption");
  caption.classList.toggle("hidden", captionHidden);
  const buttonActions = {previous: jumpToPrevious, next: jumpToNext, fullscreen: toggleFullscreen, back: bringVideoBack};
  caption.querySelector(".caption-buttons").onclick = click => {
    const action = buttonActions[click.target.dataset.action];
    if (action) action();
  };
  doc.addEventListener("keydown", handleKey);
  video.ondblclick = toggleFullscreen;
  opened.addEventListener("pagehide", () => { if (videoWindow === opened) bringVideoBack(); });

  placeholder.classList.remove("hidden");
  document.body.classList.add("video-popped-out");
  $("pop-out").innerHTML = "&#10697; Bring video back";
  requestAnimationFrame(() => { drawTimeline(); placeLabelPicker(); });  // the layout has changed
  updatePlayback();
}

function bringVideoBack() {
  if (!videoWindow) return;
  const closing = videoWindow;
  videoWindow = null;
  caption = null;
  if (closing.document.fullscreenElement) closing.document.exitFullscreen().catch(() => {});
  moveVideoKeepingPlace(() => placeholder.before(video));
  placeholder.classList.add("hidden");
  document.body.classList.remove("video-popped-out");
  $("pop-out").innerHTML = "&#10697; Pop out video";
  video.ondblclick = null;
  try { closing.close(); } catch (error) {}
  drawTimeline();
  updatePlayback();
}

function togglePopOut() {
  videoWindow ? bringVideoBack() : popOutVideo();
}

// In the video window, fullscreen the whole window so the caption stays visible; here, just the video
function toggleFullscreen() {
  const doc = videoWindow ? videoWindow.document : document;
  const target = videoWindow ? doc.documentElement : video;
  if (doc.fullscreenElement) doc.exitFullscreen().catch(() => {});
  else target.requestFullscreen().catch(() => showToast("Fullscreen isn't available here"));
}

function toggleCaption() {
  captionHidden = !captionHidden;
  if (caption) caption.classList.toggle("hidden", captionHidden);
}

function renderCaption(event, now, length) {
  caption.querySelector(".caption-time").textContent = `${formatTime(now)} / ${formatTime(length)}`;
  caption.querySelector(".caption-name").textContent = event ? titleOf(event) : "";
  caption.querySelector(".caption-dot").style.background = event ? markerColor(event) : "transparent";
  const labels = caption.querySelector(".caption-labels");
  labels.innerHTML = "";
  if (event) for (const label of event.labels) labels.append(make("span", "caption-label", label));
  caption.querySelector(".caption-score").textContent = scoreAtTime(now);
}

async function moveVideoToOtherScreen() {
  if (!videoWindow) popOutVideo();
  if (!videoWindow) return;
  if (!("getScreenDetails" in window)) {
    return showToast("This browser can't place windows: drag the video window across instead");
  }
  try {
    const screens = await window.getScreenDetails();  // asks for permission the first time
    const other = screens.screens.find(screen => screen !== screens.currentScreen);
    if (!other) return showToast("Only one screen found");
    videoWindow.moveTo(other.availLeft, other.availTop);
    videoWindow.resizeTo(other.availWidth, other.availHeight);
    videoWindow.focus();
  } catch (error) {
    showToast("Not allowed to place windows: drag the video window across instead");
  }
}

$("pop-out").onclick = togglePopOut;
$("video-back").onclick = bringVideoBack;
$("video-other-screen").onclick = moveVideoToOtherScreen;

// ---------------------------------------------------------------- keyboard
// The same keys work in this window and in the video window. An action returns false when the key doesn't
// apply right now, so the browser can do its usual thing with it.

const KEY_ACTIONS = {
  v: togglePopOut,
  f: toggleFullscreen,
  c: () => (videoWindow ? toggleCaption() : false),
  n: jumpToNext,
  p: jumpToPrevious,
  m: () => switchTab("moments"),
  w: () => switchTab("whistles"),
  a: openAddForm,
  r: () => startRename(activeEvent ? keyOf(activeEvent) : lastPicked),
  x: () => {
    if (activeEvent) activeEvent.yours ? removeYours(activeEvent) : toggleFalsePositive(activeEvent);
  },
  j: () => { video.currentTime -= 10; },
  l: () => { video.currentTime += 10; },
  k: togglePlay,
  " ": press => (press.target === video ? false : togglePlay()),  // on the video itself, its own controls handle it
  escape: () => (pickerTarget ? closeLabelPicker() : false),
};

function handleKey(press) {
  const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(press.target.tagName);
  if (typing || press.ctrlKey || press.metaKey || press.altKey) return;
  const name = press.key.toLowerCase();
  let handled;
  if (/^[1-9]$/.test(name)) handled = labelWithKey(Number(name));
  else if (name in KEY_ACTIONS) handled = KEY_ACTIONS[name](press);
  else return;
  if (handled !== false) press.preventDefault();
}

document.addEventListener("keydown", handleKey);

// ---------------------------------------------------------------- start-up

video.addEventListener("timeupdate", updatePlayback);
video.addEventListener("seeked", updatePlayback);
video.addEventListener("loadedmetadata", () => { drawTimeline(); updatePlayback(); });
window.addEventListener("resize", () => { drawTimeline(); placeLabelPicker(); });
window.addEventListener("pagehide", () => { if (videoWindow) videoWindow.close(); });
eventList.addEventListener("scroll", () => placeLabelPicker());
renderLegend();
refreshAll();
