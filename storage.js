/**
 * Browser persistence — the day's board and the lifetime stats.
 *
 * Thin shell: two localStorage keys, read and written as JSON, plus the pure stats
 * transition that owns the shape being written. Nothing here knows the game's rules;
 * the caller decides what a finished game means and hands over the outcome.
 *
 * What is stored is deliberately minimal — the day number and the guessed event ids.
 * Feedback is *not* stored: it is a pure function of (guess, answer), so persisting it
 * would mean two sources of truth that a rules change could pull apart. Restoring
 * re-evaluates from the ids instead, which also means a tuned closeness formula
 * repaints an in-progress board correctly.
 *
 * Every read is defensive. localStorage is shared, user-editable, and survives across
 * versions of the game, so anything malformed is treated as absent rather than trusted.
 * The `-v1` key suffix is the escape hatch for a shape change: bump it and old state is
 * ignored, no migration code.
 */

const STATE_KEY = 'histle-state-v1';
const STATS_KEY = 'histle-stats-v1';

/**
 * Private storage access. A browser with storage disabled (Safari private mode, or a
 * user who blocked it) throws on access; the game must stay playable there, just
 * forgetful — so failures degrade to "nothing saved", never to a broken page.
 */
const readJson = (key) => {
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? null : JSON.parse(raw);
  } catch {
    return null;
  }
};

const writeJson = (key, value) => {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
};

const isPlainObject = (value) => typeof value === 'object' && value !== null;

// ------------------------------------------------------------------ the day's board

/**
 * @param {number} dayNumber - today's puzzle number
 * @returns {{dayNumber: number, guessIds: string[], finished: boolean, won: boolean}|null}
 *   the saved board, or null when there is none, it is malformed, or it belongs to an
 *   earlier day — a stale board is dropped rather than migrated, since yesterday's
 *   guesses mean nothing against today's answer.
 */
export const loadBoard = (dayNumber) => {
  const saved = readJson(STATE_KEY);
  if (!isPlainObject(saved) || saved.dayNumber !== dayNumber) return null;
  if (!Array.isArray(saved.guessIds) || !saved.guessIds.every((id) => typeof id === 'string')) {
    return null;
  }
  return {
    dayNumber,
    guessIds: saved.guessIds,
    finished: saved.finished === true,
    won: saved.won === true,
  };
};

/** @param {{dayNumber: number, guessIds: string[], finished: boolean, won: boolean}} board */
export const saveBoard = (board) => writeJson(STATE_KEY, board);

// ------------------------------------------------------------------------- the stats

/** @returns {{played: number, won: number, currentStreak: number, maxStreak: number, distribution: number[], lastRecordedDay: number|null}} */
export const emptyStats = () => ({
  played: 0,
  won: 0,
  currentStreak: 0,
  maxStreak: 0,
  distribution: [0, 0, 0, 0, 0, 0],
  lastRecordedDay: null,
});

export const loadStats = () => {
  const saved = readJson(STATS_KEY);
  if (!isPlainObject(saved)) return emptyStats();
  const blank = emptyStats();
  return {
    ...blank,
    ...saved,
    distribution: Array.isArray(saved.distribution) ? saved.distribution : blank.distribution,
  };
};

export const saveStats = (stats) => writeJson(STATS_KEY, stats);

/**
 * Pure stats transition: fold one finished game into the running totals.
 *
 * @param {object} stats - from `loadStats`
 * @param {{dayNumber: number, won: boolean, guessCount: number}} result
 * @returns {object} the updated stats, or `stats` unchanged when this day is already
 *   recorded — the double-count guard. A finished board is re-rendered on every reload,
 *   so "did we already count today?" has to be answered from durable state, and
 *   `lastRecordedDay` is that record.
 *
 * Streak rule: a win on the day after the last recorded day extends the streak; a win
 * after a gap starts a new one (a skipped day breaks it, whether or not it was lost);
 * a loss zeroes it.
 */
export const withResult = (stats, { dayNumber, won, guessCount }) => {
  if (stats.lastRecordedDay === dayNumber) return stats;

  const continues = won && stats.lastRecordedDay === dayNumber - 1;
  const currentStreak = won ? (continues ? stats.currentStreak + 1 : 1) : 0;
  const distribution = [...stats.distribution];
  if (won) distribution[guessCount - 1] = (distribution[guessCount - 1] ?? 0) + 1;

  return {
    played: stats.played + 1,
    won: stats.won + (won ? 1 : 0),
    currentStreak,
    maxStreak: Math.max(stats.maxStreak, currentStreak),
    distribution,
    lastRecordedDay: dayNumber,
  };
};
