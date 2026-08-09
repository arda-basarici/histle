/**
 * Browser persistence — the day's board and the lifetime stats.
 *
 * Thin shell: two localStorage keys, read and written as JSON, plus the pure stats
 * transition that owns the shape being written. Nothing here knows the game's rules;
 * the caller decides what a finished game means and hands over the outcome.
 *
 * What is stored is deliberately minimal — the day number, the guessed event ids, and
 * the two facts a reload cannot re-derive (how many hints were bought, whether the
 * player gave up). Feedback is *not* stored: it is a pure function of (guess, answer),
 * so persisting it would mean two sources of truth that a rules change could pull
 * apart. Restoring re-evaluates from the ids instead, which also means a tuned
 * closeness formula or a retuned score repaints an in-progress board correctly.
 *
 * Every read is defensive. localStorage is shared, user-editable, and survives across
 * versions of the game, so anything malformed is treated as absent rather than trusted.
 *
 * The `-v2` suffix marks the unlimited-guesses shape. v1 keys are read once, folded in
 * by `migrateStats`, and deleted — see `loadStats`.
 */

import { HINTS } from './game.js';

const STATE_KEY = 'histle-state-v2';
const STATS_KEY = 'histle-stats-v2';

const LEGACY_STATE_KEY = 'histle-state-v1';
const LEGACY_STATS_KEY = 'histle-stats-v1';

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

const removeKey = (key) => {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // storage is unavailable; there is nothing to clean up
  }
};

const isPlainObject = (value) => typeof value === 'object' && value !== null;

const clampInteger = (value, min, max) =>
  Number.isInteger(value) ? Math.min(max, Math.max(min, value)) : min;

// ------------------------------------------------------------------ the day's board

/**
 * @param {number} day - today's puzzle number
 * @returns {{day: number, guessIds: string[], hintsUsed: number, gaveUp: boolean, finished: boolean, won: boolean}|null}
 *   the saved board, or null when there is none, it is malformed, or it belongs to an
 *   earlier day — a stale board is dropped rather than migrated, since yesterday's
 *   guesses mean nothing against today's answer.
 *
 * `hintsUsed` is clamped into range rather than rejected: a hand-edited 99 should cost
 * the player the maximum three hints' worth of score, not wipe their board.
 */
export const loadBoard = (day) => {
  const saved = readJson(STATE_KEY);
  if (!isPlainObject(saved) || saved.day !== day) return null;
  if (!Array.isArray(saved.guessIds) || !saved.guessIds.every((id) => typeof id === 'string')) {
    return null;
  }
  return {
    day,
    guessIds: saved.guessIds,
    hintsUsed: clampInteger(saved.hintsUsed, 0, HINTS.max),
    gaveUp: saved.gaveUp === true,
    finished: saved.finished === true,
    won: saved.won === true,
  };
};

/** @param {{day: number, guessIds: string[], hintsUsed: number, gaveUp: boolean, finished: boolean, won: boolean}} board */
export const saveBoard = (board) => writeJson(STATE_KEY, board);

// ------------------------------------------------------------------------- the stats

/**
 * Win-distribution buckets, keyed by EFFECTIVE count (guesses + hint cost).
 *
 * Individual columns while a board is still a Wordle-sized achievement, then two
 * catch-all bands. Unlimited guessing has no upper bound, so the alternative is a
 * histogram whose axis stretches to whatever the unluckiest day cost — which squashes
 * the part a player actually reads. "7-9" and "10+" are labels, not numbers, and the
 * array is positional: index, not value, is what `distributionIndexFor` returns.
 */
export const DISTRIBUTION_BUCKETS = [1, 2, 3, 4, 5, 6, '7-9', '10+'];

/**
 * @param {number} effectiveCount - guesses + hint cost, see `effectiveGuessCount`
 * @returns {number} index into `DISTRIBUTION_BUCKETS`; counts below 1 clamp to the
 *   first bucket, which cannot arise from a real win but keeps a corrupt read in range
 */
export const distributionIndexFor = (effectiveCount) => {
  if (effectiveCount <= 6) return Math.max(0, effectiveCount - 1);
  return effectiveCount <= 9 ? 6 : 7;
};

/** @returns {{played: number, won: number, currentStreak: number, maxStreak: number, distribution: number[], lastRecordedDay: number|null}} */
export const emptyStats = () => ({
  played: 0,
  won: 0,
  currentStreak: 0,
  maxStreak: 0,
  distribution: DISTRIBUTION_BUCKETS.map(() => 0),
  lastRecordedDay: null,
});

/**
 * Pure v1 → v2 stats transition.
 *
 * @param {object|null} legacy - whatever sat under the v1 key
 * @returns {object} v2 stats
 *
 * The four lifetime counters carry over: they are the numbers a player has watched
 * accumulate, and resetting them to punish a format change would be the change costing
 * the player something real. The distribution does not — v1 counted six columns keyed
 * by raw guess count, v2 counts eight keyed by effective count, and there is no honest
 * way to re-key history that never recorded hints. It starts empty.
 *
 * `lastRecordedDay` carries too, though it is neither a counter nor a distribution.
 * Dropping it would re-open the double-count guard on a day already recorded under v1
 * (a migrating player's in-progress day would be counted twice) and would break the
 * streak's continuity test, silently restarting a long streak at 1 on the next win.
 * Its shape is unchanged, so there is nothing to migrate — only a reason not to lose it.
 */
export const migrateStats = (legacy) => {
  const blank = emptyStats();
  if (!isPlainObject(legacy)) return blank;
  const carry = (key) => (Number.isFinite(legacy[key]) ? legacy[key] : blank[key]);
  return {
    ...blank,
    played: carry('played'),
    won: carry('won'),
    currentStreak: carry('currentStreak'),
    maxStreak: carry('maxStreak'),
    lastRecordedDay: Number.isFinite(legacy.lastRecordedDay) ? legacy.lastRecordedDay : null,
  };
};

/**
 * Read the v2 stats, migrating and retiring v1 on first run after the upgrade.
 *
 * The migration writes v2 and removes both v1 keys immediately, so it happens exactly
 * once per browser: a second call finds v2 present and never looks at v1 again. The v1
 * *board* is dropped rather than converted — it belongs to a single day, under rules
 * that no longer apply, and today's board will be written fresh within a guess.
 */
export const loadStats = () => {
  const saved = readJson(STATS_KEY);
  if (isPlainObject(saved)) {
    const blank = emptyStats();
    return {
      ...blank,
      ...saved,
      distribution:
        Array.isArray(saved.distribution) && saved.distribution.length === blank.distribution.length
          ? saved.distribution
          : blank.distribution,
    };
  }

  const migrated = migrateStats(readJson(LEGACY_STATS_KEY));
  writeJson(STATS_KEY, migrated);
  removeKey(LEGACY_STATS_KEY);
  removeKey(LEGACY_STATE_KEY);
  return migrated;
};

export const saveStats = (stats) => writeJson(STATS_KEY, stats);

/**
 * Pure stats transition: fold one finished game into the running totals.
 *
 * @param {object} stats - from `loadStats`
 * @param {{day: number, won: boolean, effectiveCount: number}} result - `effectiveCount`
 *   is what the player reports (guesses plus hint cost), so a bought hint costs a
 *   distribution column exactly as two guesses would
 * @returns {object} the updated stats, or `stats` unchanged when this day is already
 *   recorded — the double-count guard. A finished board is re-rendered on every reload,
 *   so "did we already count today?" has to be answered from durable state, and
 *   `lastRecordedDay` is that record.
 *
 * Streak rule: a win on the day after the last recorded day extends the streak; a win
 * after a gap starts a new one (a skipped day breaks it, whether or not it was lost);
 * a loss zeroes it. Giving up is a loss and reaches here as one — it counts as played,
 * breaks the streak, and adds no distribution entry, because the distribution answers
 * "how many guesses does a solve take" and an unsolved board has no answer to give.
 */
export const withResult = (stats, { day, won, effectiveCount }) => {
  if (stats.lastRecordedDay === day) return stats;

  const continues = won && stats.lastRecordedDay === day - 1;
  const currentStreak = won ? (continues ? stats.currentStreak + 1 : 1) : 0;
  const distribution = [...stats.distribution];
  if (won) {
    const at = distributionIndexFor(effectiveCount);
    distribution[at] = (distribution[at] ?? 0) + 1;
  }

  return {
    played: stats.played + 1,
    won: stats.won + (won ? 1 : 0),
    currentStreak,
    maxStreak: Math.max(stats.maxStreak, currentStreak),
    distribution,
    lastRecordedDay: day,
  };
};
