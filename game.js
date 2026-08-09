/**
 * Guess evaluation — the rules that turn a guess event into feedback.
 *
 * Pure module. Year direction/distance and category comparison are the real rules and
 * are expected to survive; the overall `closeness` colour weighs all three axes and is
 * the tunable part (see `closenessFor`). The per-guess score (see `SCORING`) and the
 * hint economy (see `HINTS`) live here too, because both are rules rather than
 * presentation — the shell only renders what they decide.
 *
 * Which event is hidden is not decided here — that is `daily.js`.
 *
 * There is no guess limit. The game is a triangulation puzzle over ~10k guessable
 * events, not a five-letter word: cutting a player off after six probes ends most
 * boards before the feedback has had a chance to converge, and the feedback is the
 * thing worth playing. Guess count became the score instead, which is why `HINTS`
 * prices a hint in the same currency.
 */

/**
 * Distance bands for |Δyear|, by inclusive upper bound. A guess falls in the first band
 * whose `max` it does not exceed, so the labels read as half-open ranges: 1968 vs 1919
 * is 49 years → "40–100y".
 *
 * Eight bands, deliberately narrow at the recent end. The answer pool skews modern, so
 * a coarse partition spends most of its resolution where almost nothing happens: an
 * earlier six-band cut lumped 100–500 years into one label, which told a player almost
 * nothing about a 20th-century answer. Retuning is one edit here — the colour
 * thresholds below read the same bounds, and nothing else in the codebase hardcodes a
 * band.
 */
export const YEAR_BUCKETS = [
  { max: 0, label: 'same year' },
  { max: 5, label: '1–5y' },
  { max: 15, label: '5–15y' },
  { max: 40, label: '15–40y' },
  { max: 100, label: '40–100y' },
  { max: 250, label: '100–250y' },
  { max: 600, label: '250–600y' },
  { max: Infinity, label: '600y+' },
];

/**
 * Band bounds the colour rule reads, named so the thresholds are not bare numbers.
 * Both are band edges from `YEAR_BUCKETS`: green reaches to the top of "5–15y", yellow
 * to the top of "40–100y".
 *
 * TUNABLE, and yellow has already moved once: at 250 years it covered five of the eight
 * bands, so a guess a quarter-millennium adrift still came back warm and the colour
 * stopped carrying information. 100 is the edge of the band where the pool's density
 * actually is.
 */
const GREEN_WITHIN_YEARS = 15;
const YELLOW_WITHIN_YEARS = 100;

/**
 * Inside this many years the year cell prints the exact distance ("7y") instead of the
 * band it falls in ("5–15y"). TUNABLE.
 *
 * Coarse at range, exact up close — a deliberate information schedule, not a leak. By
 * the time a guess is within a decade the band has already confined the answer to a
 * handful of years, so spending the last of the resolution buys a player the endgame
 * they have effectively already won instead of a run of probes that only rediscover
 * what the cell said. What it does not buy is the answer: the puzzle is naming the
 * event that happened in that year, and handing over the year leaves that untouched —
 * 1969 alone does not say moon landing rather than Woodstock.
 *
 * The exact label is a DISPLAY fact only. The band index behind it is unchanged, and
 * the score still reads the band (see `scoreFor`), so shrinking or widening this window
 * cannot move anybody's score.
 */
const EXACT_WITHIN_YEARS = 10;

const bucketFor = (yearDelta) => YEAR_BUCKETS.find((bucket) => yearDelta <= bucket.max);

/**
 * What the year cell prints: the exact distance up close, the band's label otherwise.
 * A delta of zero is not "0y" — it is the same year, which the band already says better
 * than a count of nothing does.
 */
const yearLabelFor = (yearDelta, bucket) =>
  yearDelta > 0 && yearDelta <= EXACT_WITHIN_YEARS ? `${yearDelta}y` : bucket.label;

/** Direction is stated from the answer's point of view: "the answer is later than your guess". */
const directionFor = (guessYear, answerYear) => {
  if (answerYear > guessYear) return 'later';
  if (answerYear < guessYear) return 'earlier';
  return 'equal';
};

/**
 * The region value meaning "this event spans continents" — a real answer in the data,
 * not a gap. World wars, pandemics and anything whose countries sit on two continents.
 */
const REGION_MULTI = 'multi';

/**
 * Region comparison at the granularity the dataset currently supports.
 *
 * Two different things come back as 'unknown', and the distinction lives in the display
 * layer rather than here. An event with no region is a gap in the data. An event whose
 * region is 'multi' spans continents, so there is no single continent to agree or
 * disagree with — comparing it to Europe cannot honestly return either 'same' or
 * 'different'. Both yield no signal, which is what this function reports; `app.js` then
 * says "multi" instead of "?" on the board, because "we don't know" and "it was
 * everywhere" are different facts to hand a player.
 *
 * The 'continent' tier is reserved, not dead: regions are already continents, so a "near
 * miss" needs finer granularity than we have. When country-level comparison lands (the
 * data carries `country`), 'same' becomes same-country and 'continent' becomes
 * same-continent-different-country — this function is the only place that changes.
 */
const compareRegions = (guessRegion, answerRegion) => {
  if (!guessRegion || !answerRegion) return 'unknown';
  if (guessRegion === REGION_MULTI || answerRegion === REGION_MULTI) return 'unknown';
  return guessRegion === answerRegion ? 'same' : 'different';
};

/**
 * The overall verdict, weighing all three axes. TUNABLE — this is the dial the
 * prototype exists to calibrate; the bands and the thresholds above are the knobs.
 *
 *   green  = within 15 years AND (same region OR same category)
 *   yellow = within 100 years OR (same region AND same category)
 *   grey   = everything else
 *
 * The shape encodes one judgement: closeness in time is necessary but not sufficient
 * for green — fifteen years away in the wrong hemisphere about a different kind of
 * thing is a coincidence, not a near miss. The second yellow clause runs the other way: a guess that
 * reads the answer's *character* right (same continent, same kind) is warm even when it
 * is centuries off, because that is real progress on two of the three axes.
 *
 * Region 'unknown' never counts as a match, so those guesses lean on the category axis
 * alone. Deliberate, and it covers both readings of unknown: a missing region is not
 * evidence, and a 'multi' region is evidence of the wrong shape. Treating either as a
 * hit would make a gap look like a hint.
 */
const closenessFor = (yearDelta, regionMatch, categoryMatch) => {
  const sameRegion = regionMatch === 'same';
  if (yearDelta <= GREEN_WITHIN_YEARS && (sameRegion || categoryMatch)) return 'green';
  if (yearDelta <= YELLOW_WITHIN_YEARS || (sameRegion && categoryMatch)) return 'yellow';
  return 'grey';
};

/**
 * @param {{year: number, region: string|null, category: string}} guessEvent
 * @param {{year: number, region: string|null, category: string}} answerEvent
 * @returns {{
 *   yearDirection: 'earlier'|'later'|'equal',
 *   yearBucket: string,
 *   yearLabel: string,
 *   regionMatch: 'same'|'continent'|'different'|'unknown',
 *   categoryMatch: boolean,
 *   closeness: 'green'|'yellow'|'grey'
 * }}
 *   `yearDirection` points from the guess towards the answer. The year axis comes back
 *   twice on purpose: `yearBucket` is always a `YEAR_BUCKETS` label and is what the
 *   rules speak — the score keys off it — while `yearLabel` is what the cell prints,
 *   identical to the band except within `EXACT_WITHIN_YEARS`, where it is the exact
 *   distance. Keeping them apart is what lets the display get finer without the scoring
 *   table growing a row per year.
 *
 *   Years are plain integers with negatives meaning BCE, so the arithmetic needs no era
 *   handling — note this makes the 1 BCE → 1 CE gap two years, which is the
 *   astronomical convention and fine at these band widths.
 */
export const evaluateGuess = (guessEvent, answerEvent) => {
  const yearDelta = Math.abs(answerEvent.year - guessEvent.year);
  const bucket = bucketFor(yearDelta);
  const regionMatch = compareRegions(guessEvent.region, answerEvent.region);
  const categoryMatch = guessEvent.category === answerEvent.category;
  return {
    yearDirection: directionFor(guessEvent.year, answerEvent.year),
    yearBucket: bucket.label,
    yearLabel: yearLabelFor(yearDelta, bucket),
    regionMatch,
    categoryMatch,
    closeness: closenessFor(yearDelta, regionMatch, categoryMatch),
  };
};

// ------------------------------------------------------------------------ scoring

/**
 * The 0–100 score a guess earns. TUNABLE — every constant the score depends on is in
 * this one object, so recalibrating is a single edit here.
 *
 * LEAK-SAFE BY CONSTRUCTION — the constraint this shape exists to honour.
 * -----------------------------------------------------------------------
 * `scoreFor` reads NOTHING but the fields the guess row already displays: the year
 * band label, whether the region cell said "same", whether the kind cell said "✓", and
 * whether the row is the winning one. Two guesses that paint identical cells therefore
 * score identically, so the number cannot narrow the answer beyond what a player can
 * already see. This matters because the score is far more compressible than the board
 * — a number is easy to binary-search against, and a score built from the raw year
 * delta (say) would hand over a decade of resolution the "40–100y" cell deliberately
 * withholds. Anything added here must pass the same test: is it on the row?
 *
 * The score reads `yearBucket`, the band, and not `yearLabel`, what the cell prints.
 * Those differ within ten years, where the cell prints the exact distance — and the
 * band is derivable from that number, so scoring off the band stays strictly inside
 * what the row shows. Scoring off the *label* instead would need a base per year and
 * would spread the exactness into a second channel for no gain.
 *
 * Weighting: time carries the score and the two categorical axes are small bonuses.
 * That is the game's own shape — the year band is the axis a player actually
 * triangulates on, while region and kind are one-bit confirmations that narrow the
 * field without locating it.
 *
 * `nonHitCap` is not cosmetic. The top band plus both bonuses is 90 + 6 + 4 = 100, so
 * a same-year guess matching region and kind — a genuinely common near miss, since
 * near misses are exactly the guesses that agree on everything but identity — would
 * otherwise print the same 100 as the answer itself and read as a solved board that
 * refuses to end. Capping non-hits at 99 reserves 100 for the hit alone.
 */
export const SCORING = {
  /** Keys are `YEAR_BUCKETS` labels; every label must appear (asserted in the suite). */
  yearBase: {
    'same year': 90,
    '1–5y': 80,
    '5–15y': 70,
    '15–40y': 55,
    '40–100y': 40,
    '100–250y': 25,
    '250–600y': 15,
    '600y+': 5,
  },
  regionSameBonus: 6,
  kindMatchBonus: 4,
  exact: 100,
  nonHitCap: 99,
};

/**
 * @param {{yearBucket: string, regionMatch: string, categoryMatch: boolean}} feedback
 *   from `evaluateGuess` — displayed feedback only, see the leak-safety note above
 * @param {{isAnswer?: boolean}} [options] - `isAnswer` is the winning row, itself
 *   visible on the board (it carries the "solved" badge)
 * @returns {number} 0–100; exactly 100 only when `isAnswer`
 */
export const scoreFor = (feedback, { isAnswer = false } = {}) => {
  if (isAnswer) return SCORING.exact;
  const base = SCORING.yearBase[feedback.yearBucket];
  if (base === undefined) {
    throw new Error(
      `scoreFor: no base score for year bucket "${feedback.yearBucket}" — SCORING.yearBase must cover every YEAR_BUCKETS label`,
    );
  }
  const total =
    base +
    (feedback.regionMatch === 'same' ? SCORING.regionSameBonus : 0) +
    (feedback.categoryMatch ? SCORING.kindMatchBonus : 0);
  return Math.min(total, SCORING.nonHitCap);
};

// -------------------------------------------------------------------------- hints

/** Guesses a player must spend before the hint controls unlock. The old `MAX_GUESSES`. */
export const HINT_UNLOCK_AT = 6;

/**
 * The hint economy. TUNABLE.
 *
 * Hints are PURCHASES, not gifts, and the price is paid in the same currency as the
 * score: taking one adds `costInGuesses` to the count a player reports. That keeps a
 * single number comparable between a player who ground it out and one who bought
 * their way in — "solved in 11" means the same effort either way — and it is why
 * nothing here needs a separate "hinted" asterisk in the stats.
 *
 * `order` is fixed rather than player-chosen, weakest first: kind narrows the field to
 * roughly an eighth, region to a continent, century to a hundred years. A player who
 * buys all three has spent six guesses and still has to name the event.
 */
export const HINTS = {
  unlockAfter: HINT_UNLOCK_AT,
  max: 3,
  costInGuesses: 2,
  order: ['kind', 'region', 'century'],
};

/**
 * @param {number} guessCount - guesses actually submitted
 * @param {number} hintsUsed - 0..`HINTS.max`
 * @returns {number} the count the board, the stats and the share text all report
 */
export const effectiveGuessCount = (guessCount, hintsUsed) =>
  guessCount + hintsUsed * HINTS.costInGuesses;
