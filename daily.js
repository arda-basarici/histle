/**
 * The daily answer — which event today's puzzle hides.
 *
 * Pure module: give it the event list and a clock reading, get back a day number and
 * the event. No DOM, no storage, no network. The shell calls it once at boot.
 *
 * Two properties matter and both are load-bearing for the static contract (no backend,
 * so every player must derive the same puzzle from the date alone):
 *
 * 1. *Deterministic* — day number in, event out, always the same. A seeded PRNG, not
 *    `Math.random`, and an index draw over a pool sorted by id, not by file order, so
 *    re-emitting `events.core.json` with rows in a different order cannot silently
 *    reshuffle the whole calendar. (Adding or removing a famous_candidate event does
 *    change future picks — pool membership is part of the input.)
 * 2. *Local-midnight rollover* — the Wordle convention: the day number counts calendar
 *    days in the PLAYER'S OWN timezone. Consequence, accepted deliberately: players in
 *    different zones are on different puzzles for part of each day (Auckland starts
 *    #N while Los Angeles is still on #N-1), so a share grid can arrive before its
 *    recipient's puzzle rolls over. The alternative — a single UTC rollover — moves the
 *    surprise to 1am or 4pm for most of the world, which is worse for the daily habit
 *    the game is built around.
 *
 * On hiding the answer: it is not hidden. The pick is computed in the page from data
 * the page already downloaded, so anyone who opens devtools can read it in seconds.
 * What this module does buy is that the answer is not sitting in plain sight — it is
 * never logged and never written into the DOM before it is earned. That is the whole
 * threat model: casual shoulder-glances and accidental spoilers, not a determined
 * cheater, who cannot be stopped by any client-side scheme.
 */

/**
 * Day 0. Chosen as the project's launch-week Saturday; changing it renumbers every
 * puzzle, so it is frozen once the game is public.
 */
const EPOCH = { year: 2026, month: 8, day: 1 };

/**
 * Fixed salt mixed into the day number so puzzle #1 is not simply "the pool entry a
 * one-seeded PRNG happens to emit". Any constant works; this one is the golden-ratio
 * word used as a mixing constant all over hashing code.
 */
const DAY_SALT = 0x9e3779b9;

const MS_PER_DAY = 86_400_000;

/** Calendar date → a UTC timestamp, discarding the time of day. */
const atMidnightUtc = (year, month, day) => Date.UTC(year, month - 1, day);

/**
 * @param {Date} [now] - defaults to the current time
 * @returns {number} whole days from the epoch to `now`'s LOCAL calendar date
 *
 * Both sides are projected onto the UTC timeline before subtracting, so the arithmetic
 * never sees a daylight-saving jump: the operands are calendar dates, not instants.
 */
export const dayNumberFor = (now = new Date()) =>
  Math.floor(
    (atMidnightUtc(now.getFullYear(), now.getMonth() + 1, now.getDate()) -
      atMidnightUtc(EPOCH.year, EPOCH.month, EPOCH.day)) /
      MS_PER_DAY,
  );

/**
 * mulberry32 — a 32-bit PRNG small enough to read in one sitting and stable across
 * engines (all arithmetic is explicit 32-bit). Quality is far beyond what one draw per
 * day needs; determinism is the actual requirement.
 */
const mulberry32 = (seed) => () => {
  seed = (seed + 0x6d2b79f5) | 0;
  let t = seed;
  t = Math.imul(t ^ (t >>> 15), t | 1);
  t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
  return ((t ^ (t >>> 14)) >>> 0) / 4_294_967_296;
};

/** Ids are Wikidata QIDs ("Q52418"); sort by the number, so Q99 precedes Q100. */
const qidNumber = (id) => Number(id.slice(1));

/**
 * @param {Array<{id: string, famous_candidate: boolean}>} events
 * @returns {Array} the answer pool, ascending by id — the canonical order the draw
 *   indexes into. Sorting here rather than trusting the file is what makes the pick
 *   independent of how the pipeline happened to emit its rows.
 */
export const answerPool = (events) =>
  events
    .filter((event) => event.famous_candidate)
    .sort((a, b) => qidNumber(a.id) - qidNumber(b.id));

/**
 * @param {Array} events - every event, pool filtering happens here
 * @param {number} dayNumber - from `dayNumberFor`
 * @returns {object|undefined} the event to hide today; undefined only if the pool is empty
 */
export const answerForDay = (events, dayNumber) => {
  const pool = answerPool(events);
  if (pool.length === 0) return undefined;
  const random = mulberry32((dayNumber ^ DAY_SALT) >>> 0);
  return pool[Math.floor(random() * pool.length)];
};
