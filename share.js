/**
 * The share grid — a finished game rendered as spoiler-free text.
 *
 * Pure module: feedback in, string out. Clipboard access lives in the shell.
 *
 * The grid is the game's distribution mechanism, so it has one hard constraint: a
 * reader who has not played today must learn nothing about the answer from it. No event
 * names, no years, no regions or categories by name — only the per-axis verdicts, which
 * are meaningless without knowing what was guessed.
 *
 * Row shape, one guess per row: closeness · year direction · region · category.
 *
 *   histle #8 — solved in 7
 *   💡 1 hint
 *   …
 *   ⬜⬆🌍✗
 *   🟨⬇🌍✓
 *   🟩🎯🌍✓
 *   <game url>
 */

import { effectiveGuessCount } from './game.js';

/**
 * DEPLOY STEP: replace with the real hosting URL before the game goes public.
 * Left obviously fake so a placeholder that escapes into shared text is unmistakable
 * rather than quietly wrong.
 */
export const PLACEHOLDER_URL = 'https://example.invalid/histle';

/**
 * How many guess rows the trail shows before it starts eliding.
 *
 * Guessing is unlimited, so a full trail is unbounded and a thirty-row paste is not a
 * share, it is a wall someone scrolls past. Eight rows is roughly what fits a phone
 * message without collapsing, and the LAST eight are the ones kept: the endgame is
 * where the squares converge, and a reader's interest is in how the board closed, not
 * in the wide opening probes. The elided rows become a single "…" so the trail never
 * pretends to be complete.
 */
export const MAX_TRAIL_ROWS = 8;

const ELISION_ROW = '…';

const CLOSENESS_SQUARE = { green: '🟩', yellow: '🟨', grey: '⬜' };

/**
 * The year axis reads from the answer's point of view, matching the board's arrows.
 * `equal` renders as the bullseye: it means the guess landed on the answer's year,
 * which on the winning row is the win and on any other row is a same-year near miss —
 * the same fact either way, so one symbol carries it.
 */
const DIRECTION_MARK = { later: '⬆', earlier: '⬇', equal: '🎯' };

/**
 * `continent` folds into "different": the share grid deliberately runs coarser than the
 * board, since a third region tier costs a symbol readers would have to learn.
 */
const REGION_MARK = { same: '🌍', unknown: '◻' };
const REGION_MARK_DEFAULT = '🌐';

const rowFor = (feedback) =>
  CLOSENESS_SQUARE[feedback.closeness] +
  DIRECTION_MARK[feedback.yearDirection] +
  (REGION_MARK[feedback.regionMatch] ?? REGION_MARK_DEFAULT) +
  (feedback.categoryMatch ? '✓' : '✗');

const trailFor = (feedbacks) => {
  const rows = feedbacks.map(rowFor);
  if (rows.length <= MAX_TRAIL_ROWS) return rows;
  return [ELISION_ROW, ...rows.slice(-MAX_TRAIL_ROWS)];
};

/**
 * The count the header reports, and the only number in the text.
 *
 * It is the effective count — guesses plus what the hints cost — because that is the
 * figure two players can actually compare. A separate "💡 1 hint" line follows when
 * hints were bought, which is disclosure rather than arithmetic: the header already
 * charged for them, the line says where the charge came from.
 */
const headerFor = (dayNumber, won, effective) =>
  `histle #${dayNumber} — ${won ? `solved in ${effective}` : 'gave up'}`;

const hintLineFor = (hintsUsed) =>
  hintsUsed === 0 ? [] : [`💡 ${hintsUsed} hint${hintsUsed === 1 ? '' : 's'}`];

/**
 * @param {{
 *   dayNumber: number,
 *   feedbacks: Array<{closeness: string, yearDirection: string, regionMatch: string, categoryMatch: boolean}>,
 *   won: boolean,
 *   hintsUsed?: number,
 *   url?: string
 * }} result - `feedbacks` in play order, oldest first (the board shows newest first;
 *   the grid reads top-down like Wordle's). `won` is the whole outcome: there is no
 *   losing by running out of guesses any more, so a board that is not solved is one
 *   the player gave up on.
 * @returns {string} the text to put on the clipboard, newline-separated, no trailing newline
 */
export const buildShareText = ({
  dayNumber,
  feedbacks,
  won,
  hintsUsed = 0,
  url = PLACEHOLDER_URL,
}) => {
  const effective = effectiveGuessCount(feedbacks.length, hintsUsed);
  return [
    headerFor(dayNumber, won, effective),
    ...hintLineFor(hintsUsed),
    ...trailFor(feedbacks),
    url,
  ].join('\n');
};
