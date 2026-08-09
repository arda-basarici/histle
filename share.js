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
 *   histle #8 3/6
 *   ⬜⬆🌍✗
 *   🟨⬇🌍✓
 *   🟩🎯🌍✓
 *   <game url>
 */

/**
 * DEPLOY STEP: replace with the real hosting URL before the game goes public.
 * Left obviously fake so a placeholder that escapes into shared text is unmistakable
 * rather than quietly wrong.
 */
export const PLACEHOLDER_URL = 'https://example.invalid/histle';

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

/**
 * @param {{
 *   dayNumber: number,
 *   feedbacks: Array<{closeness: string, yearDirection: string, regionMatch: string, categoryMatch: boolean}>,
 *   won: boolean,
 *   maxGuesses: number,
 *   url?: string
 * }} result - `feedbacks` in play order, oldest first (the board shows newest first;
 *   the grid reads top-down like Wordle's)
 * @returns {string} the text to put on the clipboard, newline-separated, no trailing newline
 */
export const buildShareText = ({ dayNumber, feedbacks, won, maxGuesses, url = PLACEHOLDER_URL }) => {
  const score = won ? `${feedbacks.length}/${maxGuesses}` : `X/${maxGuesses}`;
  return [`histle #${dayNumber} ${score}`, ...feedbacks.map(rowFor), url].join('\n');
};
