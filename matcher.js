/**
 * Autocomplete matching over event names and aliases.
 *
 * Pure module — no DOM, no fetch. The shell (app.js) loads the data, calls
 * `buildSearchIndex` once, then `searchEvents` on every keystroke.
 *
 * The index is flat by *surface*: one entry per searchable string, so a single
 * event contributes its name plus one entry per alias. Ranking then collapses
 * back to one suggestion per event, keeping that event's best-scoring surface.
 * The alias set is large (~45k strings across ~10k events), so the normalized
 * form is computed once at build time and never recomputed per query.
 */

const COMBINING_MARKS = /\p{M}/gu;

/** Surface kinds, ordered: a hit on the event's own name outranks a hit on an alias. */
const SURFACE_NAME = 0;
const SURFACE_ALIAS = 1;

/**
 * Weight of a leading match, in the same units as `log1p(popularity)`.
 *
 * 1.5 is roughly the gap between a popularity of 20 and one of 100, so "the surface
 * starts with what you typed" outranks a moderate fame difference but not a large one.
 * Tunable — it is the one dial in the score.
 */
const PREFIX_BONUS = 1.5;

/**
 * The one place text is folded for comparison — upgrade locale handling here only.
 *
 * v1 is deliberately locale-independent: NFD → drop combining marks → toLowerCase().
 * That folds Turkish dotted/dotless i the "English" way (İ→i, I→i, not I→ı), which is
 * accepted while the only label file is English. Per-language folding arrives with the
 * language-file feature and must land in this function, not at its call sites.
 */
export const normalizeText = (text) =>
  text.normalize('NFD').replace(COMBINING_MARKS, '').toLowerCase();

/** Collapse runs of whitespace so a padded or double-spaced query still matches. */
const collapseSpaces = (text) => text.trim().replace(/\s+/g, ' ');

const makeEntry = (event, name, surface, surfaceKind) => ({
  id: event.id,
  name,
  surface,
  normSurface: normalizeText(surface),
  surfaceKind,
  popularity: event.popularity ?? 0,
});

/**
 * @param {Array<{id: string, popularity: number}>} events - from events.core.json
 * @param {Record<string, {name: string, aliases: string[]}>} labels - from labels.en.json
 * @returns {Array} flat surface entries; pass straight to `searchEvents`
 *
 * Events with no label entry are skipped rather than indexed under their id: an
 * unlabelled event is unguessable by name, so surfacing its raw QID helps nobody.
 */
export const buildSearchIndex = (events, labels) => {
  const entries = [];
  for (const event of events) {
    const label = labels[event.id];
    if (!label) continue;
    entries.push(makeEntry(event, label.name, label.name, SURFACE_NAME));
    for (const alias of label.aliases) {
      entries.push(makeEntry(event, label.name, alias, SURFACE_ALIAS));
    }
  }
  return entries;
};

/**
 * Every query token must appear somewhere in the surface, in any order.
 *
 * A whitespace-free query (CJK, or a single word) reduces to one token, which makes
 * this a plain substring test — the intended fallback for scripts that do not space
 * their words.
 */
const matchesAllTokens = (normSurface, tokens) =>
  tokens.every((token) => normSurface.includes(token));

/**
 * How good a hit is, within its tier: fame on a log scale, plus a bonus when the
 * surface *begins* with the whole query.
 *
 * Fame is logged rather than used raw because popularity is a sitelink count with a
 * long tail — linear popularity would let one famous event drown every plausible
 * alternative, while log1p keeps a pop-6 obscurity behind a pop-88 landmark without
 * making the pop-88 landmark unbeatable by a pop-40 exact-ish match.
 *
 * This replaces a stricter earlier scheme that ranked exact > prefix > substring as
 * hard tiers before ever looking at fame. That ordering is wrong for short queries:
 * "pearl" put the pop-6 "Pearl Continental hotel bombing" (a prefix hit) above the
 * pop-88 "Attack on Pearl Harbor" (a substring hit), which is not what anyone typing
 * five letters is after. Position is now a nudge, not a gate.
 */
const scoreFor = (normSurface, normQuery, popularity) =>
  Math.log1p(popularity) + (normSurface.startsWith(normQuery) ? PREFIX_BONUS : 0);

/**
 * Rank order: name over alias — the one surviving hard tier — then score desc.
 *
 * The tier is what keeps a query that names an event from being outbid by a more
 * famous event that merely lists it as an alias: "hiroshima" must land on the atomic
 * bombings, not on a bigger World-War-II article carrying "Hiroshima" among its
 * alternate labels. Fame cannot override "you typed this event's actual name".
 *
 * The trailing keys are pure determinism tiebreaks — shorter surface first (the plain
 * "Battle of Marathon" ahead of a longer variant), then id, so the same query always
 * produces the same list.
 */
const compareEntries = (a, b) =>
  a.surfaceKind - b.surfaceKind ||
  b.score - a.score ||
  a.surface.length - b.surface.length ||
  (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

/**
 * @param {Array} index - from `buildSearchIndex`
 * @param {string} query - raw user input
 * @param {number} [limit=8]
 * @returns {Array<{id: string, name: string, matchedAlias: string|null}>}
 *   One suggestion per event, best first. `matchedAlias` is the alias that caused the
 *   hit, or null when the event's own name matched — the UI shows it as "via: …" so a
 *   surprising suggestion explains itself.
 */
export const searchEvents = (index, query, limit = 8) => {
  const normQuery = collapseSpaces(normalizeText(query));
  if (!normQuery) return [];
  const tokens = normQuery.split(' ');

  const bestByEvent = new Map();
  for (const entry of index) {
    if (!matchesAllTokens(entry.normSurface, tokens)) continue;
    const scored = {
      ...entry,
      score: scoreFor(entry.normSurface, normQuery, entry.popularity),
    };
    const incumbent = bestByEvent.get(entry.id);
    if (!incumbent || compareEntries(scored, incumbent) < 0) {
      bestByEvent.set(entry.id, scored);
    }
  }

  return [...bestByEvent.values()]
    .sort(compareEntries)
    .slice(0, limit)
    .map(({ id, name, surface, surfaceKind }) => ({
      id,
      name,
      matchedAlias: surfaceKind === SURFACE_ALIAS ? surface : null,
    }));
};
