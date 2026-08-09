/**
 * Guess evaluation — the rules that turn a guess event into feedback.
 *
 * Pure module. Year direction/distance and category comparison are the real rules and
 * are expected to survive; the overall `closeness` colour weighs all three axes and is
 * the tunable part (see `closenessFor`).
 *
 * Which event is hidden is not decided here — that is `daily.js`.
 */

export const MAX_GUESSES = 6;

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
 * to the top of "100–250y".
 */
const GREEN_WITHIN_YEARS = 15;
const YELLOW_WITHIN_YEARS = 250;

const bucketFor = (yearDelta) => YEAR_BUCKETS.find((bucket) => yearDelta <= bucket.max);

/** Direction is stated from the answer's point of view: "the answer is later than your guess". */
const directionFor = (guessYear, answerYear) => {
  if (answerYear > guessYear) return 'later';
  if (answerYear < guessYear) return 'earlier';
  return 'equal';
};

/**
 * Region comparison at the granularity the dataset currently supports.
 *
 * ~25% of events carry no region, so 'unknown' is a common and honest answer rather
 * than an error case. The 'continent' tier is reserved, not dead: regions are already
 * continents, so a "near miss" needs finer granularity than we have. When country-level
 * comparison lands (the data carries `country`), 'same' becomes same-country and
 * 'continent' becomes same-continent-different-country — this function is the only
 * place that changes.
 */
const compareRegions = (guessRegion, answerRegion) => {
  if (!guessRegion || !answerRegion) return 'unknown';
  return guessRegion === answerRegion ? 'same' : 'different';
};

/**
 * The overall verdict, weighing all three axes. TUNABLE — this is the dial the
 * prototype exists to calibrate; the bands and the thresholds above are the knobs.
 *
 *   green  = within 15 years AND (same region OR same category)
 *   yellow = within 250 years OR (same region AND same category)
 *   grey   = everything else
 *
 * The shape encodes one judgement: closeness in time is necessary but not sufficient
 * for green — fifteen years away in the wrong hemisphere about a different kind of
 * thing is a coincidence, not a near miss. The second yellow clause runs the other way: a guess that
 * reads the answer's *character* right (same continent, same kind) is warm even when it
 * is centuries off, because that is real progress on two of the three axes.
 *
 * Region 'unknown' — about a quarter of the dataset carries no region — never counts as
 * a match, so those guesses lean on the category axis alone. Deliberate: an unknown is
 * not evidence, and reading it as one would make missing data look like a hint.
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
 *   regionMatch: 'same'|'continent'|'different'|'unknown',
 *   categoryMatch: boolean,
 *   closeness: 'green'|'yellow'|'grey'
 * }}
 *   `yearDirection` points from the guess towards the answer; `yearBucket` is a label
 *   from `YEAR_BUCKETS`. Years are plain integers with negatives meaning BCE, so the
 *   arithmetic needs no era handling — note this makes the 1 BCE → 1 CE gap two years,
 *   which is the astronomical convention and fine at these band widths.
 */
export const evaluateGuess = (guessEvent, answerEvent) => {
  const yearDelta = Math.abs(answerEvent.year - guessEvent.year);
  const regionMatch = compareRegions(guessEvent.region, answerEvent.region);
  const categoryMatch = guessEvent.category === answerEvent.category;
  return {
    yearDirection: directionFor(guessEvent.year, answerEvent.year),
    yearBucket: bucketFor(yearDelta).label,
    regionMatch,
    categoryMatch,
    closeness: closenessFor(yearDelta, regionMatch, categoryMatch),
  };
};
