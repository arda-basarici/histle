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
 *    `Math.random`, and a draw over a pool sorted by id, not by file order, so
 *    re-emitting `events.core.json` with rows in a different order cannot silently
 *    reshuffle the whole calendar. (Adding or removing a famous_candidate event does
 *    change future picks — pool membership is part of the input.)
 *
 *    The draw is a *cycle through a shuffled deck*, not an independent pick per day. An
 *    independent draw repeats: over 365 days it lands on only ~341 distinct events, and
 *    the calendar it produced contained a back-to-back pair — the same puzzle two
 *    mornings running, which reads as a bug to a player whatever the probability says.
 *    Shuffling the whole pool once per cycle and walking it in order makes a repeat
 *    impossible until every other event has had its turn. The shuffled deck then goes
 *    through a deterministic diversity repair (see `DIVERSITY`) which reorders it —
 *    never changes its contents — so a fortnight of the calendar does not read as all
 *    one kind of history.
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
 * Fixed salt mixed into the cycle number so cycle 0 is not simply "the order a
 * zero-seeded PRNG happens to emit". Any constant works; this one is the golden-ratio
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

/**
 * Ids come in two families: harvested Wikidata QIDs ("Q52418") and manual additions
 * ("M001"). Ordering on the number alone would make Q1 and M001 compare equal and leave
 * the sort at the mercy of file order, so the prefix leads and the number breaks the tie
 * — which still puts Q99 before Q100. The pipeline sorts its rows by the same key.
 */
const idOrder = (a, b) =>
  a.id[0] === b.id[0]
    ? Number(a.id.slice(1)) - Number(b.id.slice(1))
    : a.id[0] < b.id[0]
      ? -1
      : 1;

/**
 * @param {Array<{id: string, famous_candidate: boolean}>} events
 * @returns {Array} the answer pool, ascending by id — the canonical order the shuffle
 *   permutes. Sorting here rather than trusting the file is what makes the pick
 *   independent of how the pipeline happened to emit its rows.
 */
export const answerPool = (events) =>
  events.filter((event) => event.famous_candidate).sort(idOrder);

/**
 * @param {Array} pool - in canonical id order
 * @param {number} cycle - which pass through the pool this is
 * @returns {Array} a new array holding the same events in shuffled order
 *
 * Fisher-Yates, seeded by the cycle so every player of every day inside one cycle sees
 * the same deck. Walking the array downwards and swapping with an index at or below the
 * cursor is the unbiased form; the drifting-index variant that swaps with any position
 * is the classic mistake and skews short pools noticeably.
 *
 * Exported so the suite can measure the repair against its own input — "the calendar is
 * varied" is only a claim about the repair if the deck it was handed was not.
 */
export const shuffleForCycle = (pool, cycle) => {
  const deck = [...pool];
  const random = mulberry32((DAY_SALT ^ cycle) >>> 0);
  for (let i = deck.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    [deck[i], deck[j]] = [deck[j], deck[i]];
  }
  return deck;
};

/**
 * How varied a stretch of the calendar has to look. TUNABLE.
 *
 * A fair shuffle is not the same thing as a calendar that reads as varied, and the pool
 * is lopsided enough that the difference shows: "battle & war" is 32% of the 443 famous
 * events, so a raw shuffle hands out clusters — two sieges back to back, or a fortnight
 * that is almost all warfare with one treaty in it. Nothing is broken when that happens,
 * but a player meets the calendar sixteen days at a time and reads a cluster as the game
 * only knowing about wars.
 *
 * Two properties, chosen to be the weakest pair that kills the visible clustering:
 *   - no two consecutive days share a kind (the cluster you cannot miss)
 *   - any 16 consecutive days show at least 6 of the 8 kinds (the fortnight you notice)
 *
 * 6-of-8 is close to what the raw shuffle already produces on average, which is
 * deliberate: a demanding target would have the repair dragging rare kinds forward and
 * bankrupting the tail of the deck, trading a visible problem for a worse one.
 */
export const DIVERSITY = {
  windowDays: 16,
  minKindsPerWindow: 6,
};

/**
 * @param {Array<{category: string}>} shuffled - one cycle's deck, from `shuffleForCycle`
 * @returns {{deck: Array, violations: number}} a repaired deck and the number of
 *   positions that could not be fixed
 *
 * Deterministic greedy repair. Walk the deck left to right; where the card at `i`
 * breaks a `DIVERSITY` rule, swap it with the NEAREST later card that does not, and
 * carry on. No randomness beyond the seeded shuffle that produced the input, so every
 * player of a given cycle repairs it to the same calendar.
 *
 * PERMUTATION PRESERVED: the only mutation is a swap of two positions, so the repaired
 * deck holds exactly the cards it was given — each pool member still appears exactly
 * once per cycle, which is the property that makes a repeat impossible before the deck
 * runs out. The repair reorders the cycle; it cannot change what is in it.
 *
 * NO EARLIER VIOLATION, by construction: positions below `i` are already settled and a
 * swap touches only `i` and some `j > i`, so nothing behind the cursor can move. The
 * displaced card lands at `j`, ahead of the walk, and is judged on its own when the
 * cursor reaches it.
 *
 * GRACEFUL TAIL: swaps only ever reach forward, so the last positions have nothing left
 * to trade with — a deck whose remaining cards are all one kind cannot be fixed. Those
 * positions are accepted and counted rather than retried, because the alternative is
 * either a backward swap (which re-breaks a settled position, and can cycle forever) or
 * a search that has no reason to terminate. A counted violation is a calendar that
 * looks slightly repetitive for a day; a hang is a blank page.
 */
export const repairDiversity = (shuffled) => {
  const deck = [...shuffled];
  const { windowDays, minKindsPerWindow } = DIVERSITY;

  /** Distinct kinds in the `windowDays`-long window ending at `i`, were `kind` placed there. */
  const kindsInWindowEndingAt = (i, kind) => {
    const kinds = new Set([kind]);
    for (let k = Math.max(0, i - windowDays + 1); k < i; k += 1) kinds.add(deck[k].category);
    return kinds.size;
  };

  /** Only full windows are judged: a 9-day prefix is not a 16-day window. */
  const fits = (i, card) => {
    if (i > 0 && deck[i - 1].category === card.category) return false;
    return (
      i < windowDays - 1 || kindsInWindowEndingAt(i, card.category) >= minKindsPerWindow
    );
  };

  let violations = 0;
  for (let i = 0; i < deck.length; i += 1) {
    if (fits(i, deck[i])) continue;
    const swapWith = deck.findIndex((card, at) => at > i && fits(i, card));
    if (swapWith === -1) {
      violations += 1;
      continue;
    }
    [deck[i], deck[swapWith]] = [deck[swapWith], deck[i]];
  }
  return { deck, violations };
};

/**
 * @param {Array} pool - in canonical id order
 * @param {number} cycle
 * @returns {Array} the cycle's calendar: shuffled, then repaired for diversity
 *
 * Self-contained per cycle — no cycle needs another one computed first, so any day
 * resolves from its own cycle number alone. The seam that buys is the one place the
 * `DIVERSITY` rules are not enforced: the last day of a cycle and the first of the next
 * come from independent shuffles and may share a kind. That is the same seam the id
 * draw already accepts (a repeat is impossible *within* a cycle, allowed across the
 * boundary), it lands once every 443 days, and closing it would make each cycle depend
 * on its predecessor — an unbounded regress backwards for a fortnight's cosmetics.
 */
export const scheduleForCycle = (pool, cycle) => repairDiversity(shuffleForCycle(pool, cycle)).deck;

/**
 * @param {Array} events - every event, pool filtering happens here
 * @param {number} dayNumber - from `dayNumberFor`
 * @returns {object|undefined} the event to hide today; undefined only if the pool is empty
 *
 * The day number splits into which pass through the deck we are on and how far into it,
 * so consecutive days are consecutive cards and no event returns until the deck runs
 * out. The remainder is folded back into range because `dayNumber` is negative for
 * anyone who opens the game before the epoch, and `%` in JavaScript keeps the sign.
 */
export const answerForDay = (events, dayNumber) => {
  const pool = answerPool(events);
  if (pool.length === 0) return undefined;
  const cycle = Math.floor(dayNumber / pool.length);
  const position = ((dayNumber % pool.length) + pool.length) % pool.length;
  return scheduleForCycle(pool, cycle)[position];
};
