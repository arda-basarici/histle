/**
 * DOM shell for histle.
 *
 * Owns everything impure — fetching the dataset, holding play state, driving the
 * document, and talking to the clipboard. The rules live in `game.js`, the matching in
 * `matcher.js`, the day's answer in `daily.js`, the share text in `share.js`, and
 * persistence in `storage.js`; this file calls them and paints the result.
 *
 * The board is never the source of truth. Guesses are held as event ids and every
 * rendered row is derived from (id, answer) through `evaluateGuess` — which is why a
 * reload can restore a half-played day from ids alone.
 */

import { buildSearchIndex, searchEvents } from './matcher.js';
import { MAX_GUESSES, evaluateGuess } from './game.js';
import { answerForDay, dayNumberFor } from './daily.js';
import { buildShareText } from './share.js';
import { emptyStats, loadBoard, loadStats, saveBoard, saveStats, withResult } from './storage.js';

const SUGGESTION_LIMIT = 8;
const INPUT_DEBOUNCE_MS = 80;
const COPIED_LABEL_MS = 2000;

const REGION_CELL = {
  same: { symbol: '✓', title: 'Same region as the answer' },
  continent: { symbol: '~', title: 'Same continent, different country' },
  different: { symbol: '✗', title: 'Different region from the answer' },
  unknown: { symbol: '?', title: 'One of the two events has no region recorded' },
};

/** Wording tracks the thresholds in `game.js`; change both together. */
const CLOSENESS_CELL = {
  green: { text: 'Close', title: 'Near in time, and the region or the kind matches' },
  yellow: { text: 'Warm', title: 'Within a few centuries, or the region and kind both match' },
  grey: { text: 'Cold', title: 'Far in time, with neither region nor kind matching' },
};

const DIRECTION_ARROW = {
  later: { symbol: '▲', title: 'The answer happened after your guess' },
  earlier: { symbol: '▼', title: 'The answer happened before your guess' },
  equal: { symbol: '●', title: 'The answer happened in the same year as your guess' },
};

const el = {
  input: document.getElementById('guess-input'),
  suggestions: document.getElementById('suggestions'),
  status: document.getElementById('guess-status'),
  counter: document.getElementById('guess-counter'),
  outcome: document.getElementById('outcome'),
  guessList: document.getElementById('guess-list'),
  dayNumber: document.getElementById('day-number'),
};

const state = {
  status: 'loading',
  index: [],
  eventsById: new Map(),
  labels: {},
  dayNumber: 0,
  answer: null,
  guesses: [],
  stats: emptyStats(),
  suggestions: [],
  activeIndex: -1,
};

// ---------------------------------------------------------------- pure helpers

/** Negative years are BCE in the dataset; render that rather than a bare minus sign. */
const formatYear = (year) => (year < 0 ? `${Math.abs(year)} BC` : `${year}`);

const ordinal = (n) => {
  const teens = n % 100;
  if (teens >= 11 && teens <= 13) return `${n}th`;
  return `${n}${['th', 'st', 'nd', 'rd'][n % 10] ?? 'th'}`;
};

/**
 * "1789 → 18th c.", "-490 → 5th c. BC". The century a year belongs to counts from 1,
 * so 1800 is still the 18th century and 1801 opens the 19th.
 */
const centuryTag = (year) => {
  const century = Math.floor((Math.abs(year) - 1) / 100) + 1;
  return year < 0 ? `${ordinal(century)} c. BC` : `${ordinal(century)} c.`;
};

const debounce = (fn, delayMs) => {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delayMs);
  };
};

const nameOf = (id) => state.labels[id]?.name ?? id;

// ------------------------------------------------------------- element builders

const element = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

/**
 * A thumbnail, or a neutral block when the event has none (~14% of the dataset).
 * Images that fail to load are swapped for the same block, so a dead Wikimedia URL
 * degrades to the empty-slot look instead of a broken-image icon.
 */
const buildThumb = (event, name) => {
  const slot = element('div', 'thumb');
  if (!event.thumb) {
    slot.classList.add('thumb--empty');
    return slot;
  }
  const img = element('img', 'thumb__img');
  img.src = event.thumb;
  img.alt = '';
  img.loading = 'lazy';
  img.decoding = 'async';
  img.referrerPolicy = 'no-referrer';
  img.addEventListener('error', () => {
    slot.replaceChildren();
    slot.classList.add('thumb--empty');
  });
  slot.append(img);
  slot.title = name;
  return slot;
};

const buildCell = (label, value, { state: cellState, title } = {}) => {
  const cell = element('div', 'cell');
  if (cellState) cell.dataset.state = cellState;
  if (title) cell.title = title;
  cell.append(element('span', 'cell__label', label), element('span', 'cell__value', value));
  return cell;
};

const buildFeedbackCells = (feedback, guessEvent) => {
  const arrow = DIRECTION_ARROW[feedback.yearDirection];
  const region = REGION_CELL[feedback.regionMatch];
  const closeness = CLOSENESS_CELL[feedback.closeness];
  const cells = element('div', 'cells');
  cells.append(
    buildCell('Year', `${arrow.symbol} ${feedback.yearBucket}`, {
      state: feedback.yearDirection,
      title: `${arrow.title} — ${formatYear(guessEvent.year)}`,
    }),
    buildCell('Region', `${region.symbol} ${guessEvent.region ?? 'unrecorded'}`, {
      state: feedback.regionMatch,
      title: region.title,
    }),
    buildCell('Kind', `${feedback.categoryMatch ? '✓' : '✗'} ${guessEvent.category}`, {
      state: feedback.categoryMatch ? 'same' : 'different',
      title: feedback.categoryMatch
        ? 'Same category as the answer'
        : 'Different category from the answer',
    }),
    buildCell('Overall', closeness.text, {
      state: feedback.closeness,
      title: closeness.title,
    }),
  );
  return cells;
};

const buildGuessRow = ({ event, name, feedback, isAnswer }) => {
  const row = element('li', 'guess');
  row.dataset.closeness = feedback.closeness;
  if (isAnswer) row.classList.add('guess--win');

  const body = element('div', 'guess__body');
  const heading = element('p', 'guess__name', name);
  // The guess's own year is shown, unlike in the suggestion list: the player has
  // already spent the turn, and triangulating along the timeline is impossible without
  // seeing where the earlier probes landed.
  heading.append(element('span', 'guess__year', ` · ${formatYear(event.year)}`));
  if (isAnswer) heading.append(element('span', 'guess__badge', 'solved'));
  body.append(heading, buildFeedbackCells(feedback, event));

  row.append(buildThumb(event, name), body);
  return row;
};

// ------------------------------------------------------------------ suggestions

const closeSuggestions = () => {
  state.suggestions = [];
  state.activeIndex = -1;
  el.suggestions.replaceChildren();
  el.suggestions.hidden = true;
  el.input.setAttribute('aria-expanded', 'false');
  el.input.removeAttribute('aria-activedescendant');
};

const paintActiveSuggestion = () => {
  [...el.suggestions.children].forEach((node, i) => {
    const active = i === state.activeIndex;
    node.classList.toggle('suggestion--active', active);
    node.setAttribute('aria-selected', String(active));
  });
  const active = el.suggestions.children[state.activeIndex];
  if (active) {
    el.input.setAttribute('aria-activedescendant', active.id);
    active.scrollIntoView({ block: 'nearest' });
  } else {
    el.input.removeAttribute('aria-activedescendant');
  }
};

/**
 * Tag the rows a player could not otherwise tell apart.
 *
 * 110 names in the dataset are shared by two or more events ("Siege of Constantinople"
 * by five), and a list showing three identical lines is unpickable. A century is the
 * coarsest thing that separates them, so same-name rows — and only those — carry one.
 *
 * This is a deliberate, bounded leak: it hands over roughly a 1-in-100-year window, but
 * only for the colliding rows, and only once a player has typed a query that collides.
 * Blanket year labels would leak the same thing on every row of every query, which is
 * the version the suggestion list refuses.
 */
const withDisambiguation = (suggestions) => {
  const nameCounts = new Map();
  for (const suggestion of suggestions) {
    nameCounts.set(suggestion.name, (nameCounts.get(suggestion.name) ?? 0) + 1);
  }
  return suggestions.map((suggestion) => {
    if (nameCounts.get(suggestion.name) < 2) return suggestion;
    const year = state.eventsById.get(suggestion.id)?.year;
    return { ...suggestion, century: year === undefined ? null : centuryTag(year) };
  });
};

const buildSuggestionNode = (suggestion, i) => {
  const node = element('li', 'suggestion');
  node.id = `suggestion-${i}`;
  node.role = 'option';
  node.setAttribute('aria-selected', 'false');
  // Name only, by design: showing year/region/category here would hand the player
  // answer-adjacent facts they are supposed to spend a guess on. The century tag added
  // for same-name collisions is the one bounded exception (see `withDisambiguation`).
  const nameNode = element('span', 'suggestion__name', suggestion.name);
  if (suggestion.century) {
    nameNode.append(element('span', 'suggestion__century', ` · ${suggestion.century}`));
  }
  node.append(nameNode);
  if (suggestion.matchedAlias) {
    node.append(element('span', 'suggestion__via', `via: ${suggestion.matchedAlias}`));
  }
  node.addEventListener('mousedown', (event) => {
    event.preventDefault(); // keep focus in the input so blur does not race the pick
    submitGuess(suggestion.id);
  });
  return node;
};

const openSuggestions = (matches) => {
  const suggestions = withDisambiguation(matches);
  state.suggestions = suggestions;
  state.activeIndex = -1;
  el.suggestions.replaceChildren(...suggestions.map(buildSuggestionNode));
  el.suggestions.hidden = false;
  el.input.setAttribute('aria-expanded', 'true');
  paintActiveSuggestion();
};

const refreshSuggestions = () => {
  if (state.status !== 'playing') return;
  const query = el.input.value;
  if (!query.trim()) {
    closeSuggestions();
    setStatus('');
    return;
  }
  const suggestions = searchEvents(state.index, query, SUGGESTION_LIMIT);
  if (suggestions.length === 0) {
    closeSuggestions();
    setStatus('No event matches that — try fewer or different words.');
    return;
  }
  setStatus('');
  openSuggestions(suggestions);
};

const moveActive = (delta) => {
  if (state.suggestions.length === 0) return;
  const count = state.suggestions.length;
  const next = state.activeIndex + delta;
  state.activeIndex = next < 0 ? count - 1 : next >= count ? 0 : next;
  paintActiveSuggestion();
};

// ----------------------------------------------------------------- guess flow

const setStatus = (message) => {
  el.status.textContent = message;
};

const updateCounter = () => {
  const left = MAX_GUESSES - state.guesses.length;
  el.counter.textContent =
    state.status === 'playing'
      ? `${left} guess${left === 1 ? '' : 'es'} left`
      : `${state.guesses.length} of ${MAX_GUESSES} used`;
};

const lockInput = () => {
  el.input.disabled = true;
  el.input.value = '';
  el.input.placeholder = 'Come back tomorrow';
  closeSuggestions();
};

// ------------------------------------------------------------------- end state

/** Two decimals of win rate would be false precision on a handful of games. */
const buildStatsLine = () => {
  const { played, won, currentStreak } = state.stats;
  const winRate = played === 0 ? 0 : Math.round((won / played) * 100);
  return element(
    'p',
    'outcome__stats',
    `Played ${played} · ${winRate}% won · streak ${currentStreak}`,
  );
};

/**
 * Put text on the clipboard, preferring the async API but never waiting on it forever.
 *
 * `navigator.clipboard` needs a secure context and a permission a browser may refuse,
 * and — observed in Chrome while testing this build — its promise can simply never
 * settle when the document is not focused, with the permission reported as granted.
 * An unguarded `await` on that leaves the button silently stuck, so the API is raced
 * against a short deadline; whichever finishes first, the deprecated-but-universal
 * hidden-textarea + `execCommand` route runs if the API has not confirmed. Worst case
 * the text is copied twice, which is invisible.
 */
const CLIPBOARD_DEADLINE_MS = 800;

const copyToClipboard = async (text) => {
  const viaApi = navigator.clipboard
    ?.writeText(text)
    .then(() => true)
    .catch(() => false);
  const settled = await Promise.race([
    viaApi ?? Promise.resolve(false),
    new Promise((resolve) => setTimeout(() => resolve(false), CLIPBOARD_DEADLINE_MS)),
  ]);
  if (settled) return true;

  const scratch = element('textarea');
  scratch.value = text;
  scratch.setAttribute('readonly', '');
  scratch.style.position = 'fixed';
  scratch.style.opacity = '0';
  document.body.append(scratch);
  scratch.select();
  try {
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    scratch.remove();
  }
};

const buildShareButton = () => {
  const button = element('button', 'outcome__share', 'Copy result');
  button.type = 'button';
  button.addEventListener('click', async () => {
    const text = buildShareText({
      dayNumber: state.dayNumber,
      feedbacks: state.guesses.map((guess) => guess.feedback),
      won: state.status === 'won',
      maxGuesses: MAX_GUESSES,
    });
    const copied = await copyToClipboard(text);
    button.textContent = copied ? 'Copied!' : 'Copy failed';
    setTimeout(() => {
      button.textContent = 'Copy result';
    }, COPIED_LABEL_MS);
  });
  return button;
};

const showWin = () => {
  el.outcome.hidden = false;
  el.outcome.className = 'outcome outcome--win';
  el.outcome.replaceChildren(
    element('h2', 'outcome__title', 'Solved!'),
    element(
      'p',
      'outcome__line',
      `${nameOf(state.answer.id)} — ${formatYear(state.answer.year)}, in ${
        state.guesses.length
      } guess${state.guesses.length === 1 ? '' : 'es'}.`,
    ),
    buildStatsLine(),
    buildShareButton(),
  );
};

const showLoss = () => {
  const name = nameOf(state.answer.id);
  el.outcome.hidden = false;
  el.outcome.className = 'outcome outcome--loss';

  const reveal = element('div', 'reveal');
  reveal.append(buildThumb(state.answer, name));

  const text = element('div', 'reveal__text');
  text.append(
    element('p', 'reveal__name', name),
    element('p', 'reveal__year', formatYear(state.answer.year)),
    element('p', 'reveal__blurb', state.labels[state.answer.id]?.blurb ?? ''),
  );
  reveal.append(text);

  el.outcome.replaceChildren(
    element('h2', 'outcome__title', 'Out of guesses — the answer was:'),
    reveal,
    buildStatsLine(),
    buildShareButton(),
  );
};

// ----------------------------------------------------------------- guess flow

/** Render one guess and fold it into play state. Persistence is the caller's job. */
const appendGuess = (guessEvent) => {
  const feedback = evaluateGuess(guessEvent, state.answer);
  state.guesses.push({ id: guessEvent.id, feedback });
  el.guessList.prepend(
    // newest first: the latest feedback stays next to the input
    buildGuessRow({
      event: guessEvent,
      name: nameOf(guessEvent.id),
      feedback,
      isAnswer: guessEvent.id === state.answer.id,
    }),
  );
};

/** 'playing' until the last guess is the answer or the six are spent. */
const outcomeOf = (guesses) => {
  if (guesses.at(-1)?.id === state.answer.id) return 'won';
  return guesses.length >= MAX_GUESSES ? 'lost' : 'playing';
};

/**
 * Settle the board after guesses have been appended — shared by live play and by the
 * reload path, so a restored finished game looks exactly like one just played.
 *
 * Stats are written here rather than at the winning keystroke because reload replays
 * the same code; `withResult` refuses a day it has already recorded, which is what
 * keeps a refresh from inflating the streak.
 */
const settle = () => {
  state.status = outcomeOf(state.guesses);
  if (state.status === 'playing') {
    updateCounter();
    return;
  }
  const updated = withResult(state.stats, {
    dayNumber: state.dayNumber,
    won: state.status === 'won',
    guessCount: state.guesses.length,
  });
  if (updated !== state.stats) {
    state.stats = updated;
    saveStats(updated);
  }
  lockInput();
  if (state.status === 'won') showWin();
  else showLoss();
  updateCounter();
};

const persistBoard = () => {
  saveBoard({
    dayNumber: state.dayNumber,
    guessIds: state.guesses.map((guess) => guess.id),
    finished: state.status !== 'playing',
    won: state.status === 'won',
  });
};

const submitGuess = (eventId) => {
  if (state.status !== 'playing') return;
  if (state.guesses.some((guess) => guess.id === eventId)) {
    setStatus(`You have already guessed ${nameOf(eventId)}.`);
    return;
  }
  const guessEvent = state.eventsById.get(eventId);
  if (!guessEvent) {
    setStatus('That event is missing from the dataset — please pick another.');
    return;
  }
  el.input.value = '';
  closeSuggestions();
  setStatus('');
  appendGuess(guessEvent);
  settle();
  persistBoard();
};

/** Enter only commits a highlighted suggestion — a mistyped guess should not cost a turn. */
const handleKeydown = (event) => {
  switch (event.key) {
    case 'ArrowDown':
      event.preventDefault();
      moveActive(1);
      break;
    case 'ArrowUp':
      event.preventDefault();
      moveActive(-1);
      break;
    case 'Enter': {
      event.preventDefault();
      const picked = state.suggestions[state.activeIndex];
      if (picked) submitGuess(picked.id);
      else if (el.input.value.trim()) setStatus('Pick an event from the suggestions.');
      break;
    }
    case 'Escape':
      closeSuggestions();
      break;
    default:
  }
};

// ------------------------------------------------------------------------ init

const loadData = async () => {
  const [core, labelFile] = await Promise.all([
    fetch('./data/events.core.json').then((res) => res.json()),
    fetch('./data/labels.en.json').then((res) => res.json()),
  ]);
  return { events: core.events, labels: labelFile.labels };
};

/**
 * Replay a saved day onto a fresh board.
 *
 * Only ids are stored, so every row is re-derived here. Ids that no longer resolve —
 * a dataset rebuild can drop an event — are skipped rather than fatal: a shortened
 * board is a better failure than a blank page.
 */
const restoreBoard = () => {
  const saved = loadBoard(state.dayNumber);
  if (!saved) return;
  for (const id of saved.guessIds) {
    const guessEvent = state.eventsById.get(id);
    if (guessEvent) appendGuess(guessEvent);
  }
};

const init = async () => {
  setStatus('Loading events…');
  el.input.disabled = true;
  try {
    const { events, labels } = await loadData();
    state.labels = labels;
    state.eventsById = new Map(events.map((event) => [event.id, event]));
    state.index = buildSearchIndex(events, labels);
    state.dayNumber = dayNumberFor();
    state.answer = answerForDay(events, state.dayNumber);
    if (!state.answer) throw new Error('the answer pool is empty');
  } catch (error) {
    setStatus('Could not load the event data. Reload to try again.');
    throw error;
  }

  el.dayNumber.textContent = `#${state.dayNumber}`;
  state.stats = loadStats();
  state.status = 'playing';
  el.input.disabled = false;
  setStatus('');

  restoreBoard();
  settle(); // no-op on a fresh day; re-locks and re-shows the panel on a finished one

  el.input.addEventListener('input', debounce(refreshSuggestions, INPUT_DEBOUNCE_MS));
  el.input.addEventListener('keydown', handleKeydown);
  el.input.addEventListener('blur', () => closeSuggestions());
  el.input.addEventListener('focus', refreshSuggestions);
};

init();
