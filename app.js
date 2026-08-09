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
 * reload can restore a half-played day from ids alone. The two things a reload cannot
 * re-derive are the hints bought and whether the player quit, so those are the only
 * extra facts the saved board carries.
 */

import { buildSearchIndex, searchEvents } from './matcher.js';
import { HINTS, effectiveGuessCount, evaluateGuess, scoreFor } from './game.js';
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

/**
 * What a guess spanning continents shows instead of the unknown "?".
 *
 * `evaluateGuess` collapses "no region recorded" and "region is multi" into the same
 * verdict, because neither can match a continent. On the board they must not read the
 * same: "?" says the dataset failed, and a player who guessed the Second World War
 * deserves to be told that the answer to *that* axis is "everywhere", not that we lost
 * the record. Same cell state, so the colour and the share grid are unaffected.
 */
const REGION_MULTI = 'multi';
const REGION_MULTI_CELL = {
  symbol: '🌐',
  title: 'Your guess spans continents, so the region axis gives no signal',
};

/** Wording tracks the thresholds in `game.js`; change both together. */
const CLOSENESS_CELL = {
  green: { text: 'Close', title: 'Near in time, and the region or the kind matches' },
  yellow: { text: 'Warm', title: 'Within a century, or the region and kind both match' },
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
  assist: document.getElementById('assist'),
  hintPanel: document.getElementById('hint-panel'),
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
  hintsUsed: 0,
  gaveUp: false,
  stats: emptyStats(),
  suggestions: [],
  activeIndex: -1,
};

/** The number the board, the stats and the share text all report. */
const effectiveCount = () => effectiveGuessCount(state.guesses.length, state.hintsUsed);

// ---------------------------------------------------------------- pure helpers

/** Negative years are BCE in the dataset; render that rather than a bare minus sign. */
const formatYear = (year) => (year < 0 ? `${Math.abs(year)} BC` : `${year}`);

const ordinal = (n) => {
  const teens = n % 100;
  if (teens >= 11 && teens <= 13) return `${n}th`;
  return `${n}${['th', 'st', 'nd', 'rd'][n % 10] ?? 'th'}`;
};

/**
 * The century a year belongs to, counting from 1 — so 1800 is still the 18th century
 * and 1801 opens the 19th.
 */
const centuryOf = (year) => Math.floor((Math.abs(year) - 1) / 100) + 1;

/** "1789 → 18th c.", "-490 → 5th c. BC" — the abbreviated form the tight suggestion rows use. */
const centuryTag = (year) =>
  year < 0 ? `${ordinal(centuryOf(year))} c. BC` : `${ordinal(centuryOf(year))} c.`;

/** "1789 → 18th century", "-490 → 5th century BC" — spelled out, for the hint panel. */
const centuryName = (year) =>
  `${ordinal(centuryOf(year))} century${year < 0 ? ' BC' : ''}`;

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
  const region =
    guessEvent.region === REGION_MULTI ? REGION_MULTI_CELL : REGION_CELL[feedback.regionMatch];
  const closeness = CLOSENESS_CELL[feedback.closeness];
  const cells = element('div', 'cells');
  cells.append(
    buildCell('Year', `${arrow.symbol} ${feedback.yearLabel}`, {
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

  // The score is a summary of the four cells below it and adds nothing to them (see
  // SCORING's leak-safety note) — it sits on the row so a player can rank their probes
  // at a glance instead of comparing four axes by eye.
  //
  // The "pts" suffix is there because a bare number in a corner reads as anything —
  // a year, a rank, a count of something. It is set small and quiet so the number stays
  // the thing the eye lands on and the unit only answers the question it raises.
  const score = scoreFor(feedback, { isAnswer });
  const scoreNode = element('span', 'guess__score', String(score));
  scoreNode.append(element('span', 'guess__score-unit', 'pts'));
  scoreNode.title = 'closeness score — 100 = the answer';

  const head = element('div', 'guess__head');
  head.append(heading, scoreNode);
  body.append(head, buildFeedbackCells(feedback, event));

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

/**
 * The counter counts UP and counts everything.
 *
 * There is no budget left to report, so the number on display is the one the player is
 * accumulating — and it includes what hints cost, which is what makes a hint feel like
 * a purchase at the moment of buying: the count visibly jumps by two.
 */
const updateCounter = () => {
  const spent = effectiveCount();
  if (state.status === 'playing') {
    el.counter.textContent = `Guess ${spent + 1}`;
    return;
  }
  el.counter.textContent =
    state.status === 'won' ? `Solved in ${spent}` : `Gave up — ${spent} spent`;
};

const lockInput = () => {
  el.input.disabled = true;
  el.input.value = '';
  el.input.placeholder = 'Come back tomorrow';
  closeSuggestions();
};

// ------------------------------------------------------------- hints + give up

const HINT_LABEL = { kind: 'Kind', region: 'Region', century: 'Century' };

/**
 * What each hint discloses about the answer, in `HINTS.order`.
 *
 * Region is stated honestly rather than hidden behind the board's "no signal" verdict:
 * `evaluateGuess` cannot compare 'multi' to a continent, but a player who has PAID for
 * the region has bought the fact, and "it spans continents" is that fact. Selling them
 * a shrug would be the worst of both — cost incurred, nothing learned.
 */
const HINT_VALUE = {
  kind: (answer) => answer.category,
  region: (answer) => {
    if (!answer.region) return 'not recorded for this event';
    return answer.region === REGION_MULTI ? 'multi — it spans continents' : answer.region;
  },
  century: (answer) => centuryName(answer.year),
};

const renderHints = () => {
  const revealed = HINTS.order.slice(0, state.hintsUsed);
  el.hintPanel.hidden = revealed.length === 0;
  el.hintPanel.replaceChildren(
    ...revealed.flatMap((hint) => [
      element('dt', null, HINT_LABEL[hint]),
      element('dd', null, HINT_VALUE[hint](state.answer)),
    ]),
  );
};

const buildHintButton = () => {
  const button = element(
    'button',
    'assist__hint',
    `Hint ${state.hintsUsed + 1} of ${HINTS.max} — costs ${HINTS.costInGuesses} guesses`,
  );
  button.type = 'button';
  button.addEventListener('click', takeHint);
  return button;
};

/**
 * Giving up is the one irreversible click on the page — the day cannot be replayed and
 * the streak is gone — so it asks twice. The confirmation lives in the button's own
 * label rather than a dialog: a modal for a quiet link would make the exit louder than
 * the game.
 */
const GIVE_UP_CONFIRM_MS = 4000;

const buildGiveUpButton = () => {
  const button = element('button', 'assist__giveup', 'Give up');
  button.type = 'button';
  let armed = false;
  let disarm;
  button.addEventListener('click', () => {
    if (armed) {
      clearTimeout(disarm);
      giveUp();
      return;
    }
    armed = true;
    button.textContent = 'Click again to give up';
    disarm = setTimeout(() => {
      armed = false;
      button.textContent = 'Give up';
    }, GIVE_UP_CONFIRM_MS);
  });
  return button;
};

/** Hints unlock on effort spent, not on hints remaining, so give up stays available after the third. */
const renderAssist = () => {
  const unlocked = state.status === 'playing' && state.guesses.length >= HINTS.unlockAfter;
  el.assist.hidden = !unlocked;
  if (!unlocked) {
    el.assist.replaceChildren();
    return;
  }
  el.assist.replaceChildren(
    ...(state.hintsUsed < HINTS.max ? [buildHintButton()] : []),
    buildGiveUpButton(),
  );
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
      hintsUsed: state.hintsUsed,
    });
    const copied = await copyToClipboard(text);
    button.textContent = copied ? 'Copied!' : 'Copy failed';
    setTimeout(() => {
      button.textContent = 'Copy result';
    }, COPIED_LABEL_MS);
  });
  return button;
};

/** Spells out what the count was made of, so a hinted solve does not look like a pure one. */
const solveLine = () => {
  const spent = effectiveCount();
  const breakdown =
    state.hintsUsed === 0
      ? ''
      : ` (${state.guesses.length} guess${state.guesses.length === 1 ? '' : 'es'} + ${
          state.hintsUsed
        } hint${state.hintsUsed === 1 ? '' : 's'})`;
  return `${nameOf(state.answer.id)} — ${formatYear(state.answer.year)}, in ${spent}${breakdown}.`;
};

const showWin = () => {
  el.outcome.hidden = false;
  el.outcome.className = 'outcome outcome--win';
  el.outcome.replaceChildren(
    element('h2', 'outcome__title', 'Solved!'),
    element('p', 'outcome__line', solveLine()),
    buildStatsLine(),
    buildShareButton(),
  );
};

/** The only loss left is giving up — running out of guesses is no longer possible. */
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
    element('h2', 'outcome__title', 'Gave up — the answer was:'),
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

/** 'playing' until the answer is guessed or the player quits — there is no third exit. */
const outcomeOf = (guesses) => {
  if (guesses.at(-1)?.id === state.answer.id) return 'won';
  return state.gaveUp ? 'lost' : 'playing';
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
  renderHints();
  if (state.status === 'playing') {
    updateCounter();
    renderAssist();
    return;
  }
  const updated = withResult(state.stats, {
    day: state.dayNumber,
    won: state.status === 'won',
    effectiveCount: effectiveCount(),
  });
  if (updated !== state.stats) {
    state.stats = updated;
    saveStats(updated);
  }
  lockInput();
  renderAssist();
  if (state.status === 'won') showWin();
  else showLoss();
  updateCounter();
};

const persistBoard = () => {
  saveBoard({
    day: state.dayNumber,
    guessIds: state.guesses.map((guess) => guess.id),
    hintsUsed: state.hintsUsed,
    gaveUp: state.gaveUp,
    finished: state.status !== 'playing',
    won: state.status === 'won',
  });
};

const takeHint = () => {
  if (state.status !== 'playing' || state.hintsUsed >= HINTS.max) return;
  state.hintsUsed += 1;
  setStatus(
    `Hint bought — it cost you ${HINTS.costInGuesses} guesses. ${
      HINTS.max - state.hintsUsed
    } left.`,
  );
  settle(); // repaints the panel, the counter and the button; the game stays in play
  persistBoard();
};

const giveUp = () => {
  if (state.status !== 'playing') return;
  state.gaveUp = true;
  setStatus('');
  settle();
  persistBoard();
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
  state.hintsUsed = saved.hintsUsed;
  state.gaveUp = saved.gaveUp;
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
