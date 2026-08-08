# DESIGN — histle

What is being built and why — the decisions and their reasoning, as a living snapshot
edited in place. The chronological journey lives in the stream's session log; the
feasibility evidence behind the founding decisions is recorded in the stream's
feasibility-spike record (2026-08-09).

*Design snapshot, pre-build · last updated 2026-08-09.*

## Objective

A daily browser game: the player identifies a hidden famous historical event by
guessing other historical events; each guess returns distance feedback across several
axes. The Globle/Wordle daily-puzzle shape, transposed to events.

Success criterion for the first build day: a playable static prototype — real dataset,
working loop, shareable result grid — sufficient to judge whether the loop is fun.
Virality is the upside bet, not the bar; the build is priced accordingly (about a day,
plus polish only if the prototype convinces).

A prior-art check (2026-08-09) found the history daily-game category saturated —
year-guessers, chronological-ordering games, letter-grid reskins, photo-dating games —
but no game where the guess itself is an event compared against a hidden event. That
structural gap is the niche; the multi-axis feedback below is what keeps the game in
it.

## The loop

**Multi-axis feedback, not a bare year distance.** A single "years away" number
reduces the game to binary search on a timeline — the guessed events become mere date
probes, and the game blurs into the existing year-guesser genre. Each guess therefore
returns:

- **time**: direction (earlier/later) plus a bucketed distance — buckets roughly
  logarithmic (0 · ≤10 · ≤50 · ≤100 · ≤500 · >500 years) so early guesses stay
  informative and the endgame stays tight; direction is included because distance
  alone doubles the search space without adding fun;
- **region**: same region / same continent / different;
- **category**: match or no match, over a coarse taxonomy (6–8 buckets: war,
  politics, science-technology, culture, disaster, exploration, …) assigned during
  curation — external category vocabularies are too fine-grained and inconsistent to
  inherit;
- a **row color** (green/yellow/grey) summarizing overall closeness, which doubles as
  the share-grid cell.

What keeps the game a knowledge test rather than pure binary search: probes are
constrained by the player's own repertoire — one cannot "guess 1620" without knowing
an event near 1620. The feedback axes turn each guess into a deduction state
("close in time, wrong continent, right theme") rather than a progress bar.

**Six guesses, hard limit.** The Wordle convention; also what makes the share grid
compact and comparable between players. Hypothesis to test at the prototype: whether
six is right for this search space.

**The share grid** — one emoji row per guess encoding the axes, spoiler-free. This is
the distribution mechanism (Wordle's growth ran on it), so it is a first-class design
surface, not an afterthought.

**Daily answer, deterministic and client-side.** The answer derives from the date
(seeded pick from the answer pool), so every player gets the same puzzle with no
server. Obfuscation beyond casual (answer not in plaintext in the page source) is
sufficient; a determined cheater is not the threat model.

## Input

**Autocomplete over a canonical vocabulary, not free text.** The player picks from
suggestions, so every input is a known entity by construction — no resolution errors,
no "it didn't understand my guess" frustration, and no ML in the loop. Matching is
token-based fuzzy search (an off-the-shelf client-side library) over canonical names
*plus aliases*, so "destruction of the wall" surfaces "Fall of the Berlin Wall" via
the shared token. The alias net comes from data, not semantics: Wikidata alternate
labels plus Wikipedia redirect titles — redirects are human-recorded alternate
phrasings, i.e. the paraphrase collection already done by the crowd.

**Free-text semantic matching — deferred, by design.** Embedding-based query→event
resolution would either ship a model to every player or resurrect a backend; both
break the static contract for a capability the game does not need at launch. It is
the natural second layer *with its own measured evaluation* (lexical vs. embedding
recall on real missed queries) if the game earns further investment — the queries
autocomplete fails on are that evaluation set, collected for free.

## The dataset

**Two pools.** The guessable pool is large (order 5–10k events) so the player's
vocabulary is rarely rejected; the answer pool is small (order 100–365), famous, and
hand-curated, so the daily answer is always guessable in principle. Wordle's move
(obscure guesses allowed, common answers only), applied to events. Pool membership is
a data flag, never code.

**Harvest spine: Wikipedia's "On This Day" API** (verified live, 2026-08-09 spike).
366 calls — one per calendar day — return essentially every event editors have tagged
to day pages, each carrying summary text, year, and the Wikidata QID. The
`selected` type is a pre-curated famous subset: the answer-pool seed, courtesy of
Wikipedia editors. Deduplication happens on QID.

**Enrichment: one batched Wikidata pass by QID** — dates, country/location, alternate
labels, and sitelink count as the notability score. Joining by known QIDs sidesteps
the messy class-hierarchy problem of querying "all historical events" by type.
Pageviews (post-2015 only) may blend in as a secondary "looked-up-today" signal;
sitelink count remains the primary notability measure.

**Curation is a single manual pass** at the famous/not boundary, with the automatic
first cut done by the `selected` flag plus a sitelink threshold. Category labels are
assigned in this pass. The curated output (`data/events.json`) is committed — the
manual pass makes it non-regenerable; raw harvests regenerate from the APIs and stay
git-ignored.

**Events with duration** (wars, constructions): the dataset stores the start date and
scores distance against it — one rule, stated in the UI's help, rather than per-event
judgment calls.

**BCE events: a handful of iconic ones** (order 10–20) in the guess pool, answer pool
almost entirely CE. Ancient dates are fuzzy and the log buckets get strange at
millennium distances; a few icons (Caesar's assassination, Alexander, the pyramids)
buy the "you're 2000 years away" delight without staking daily answers on contested
chronology.

**Rejected sources, tombstoned.** *EventKG* — research-grade RDF, last real release
2020, multilingual entity-resolution machinery; overkill for a curated
500–1000-row game dataset (the 2026-08-09 spike's assessment). *DBpedia events* —
coverage skewed roughly half military conflicts and much of the rest sports; unusable
as a base. *Kaggle "World Important Events"* — no stated sourcing methodology;
at most a cross-check, never a source.

## Architecture

**A static page, no backend** — the whole game (HTML/JS/CSS + `data/events.json`)
serves from GitHub Pages. No accounts, no server state, no runtime API calls; streaks
and stats live in browser localStorage. This is the load-bearing simplicity: it makes
the day budget real, removes every operational cost from a project whose success mode
is a lottery, and loses nothing the game needs. A backend earns consideration only if
a capability that genuinely requires one (free-text matching, cross-device streaks)
earns *its* place first.

**Pipeline vs. game, one seam.** The Python pipeline (`pipeline/`) harvests, enriches,
and emits `data/events.json`; the frontend only reads that file. The dataset schema is
the contract between them: `{id (QID), name, aliases[], year, region, category,
famous_flag, popularity}` — exact field shapes settled during the build against real
harvested data.

## Scope & non-goals

- In: the daily game, the share grid, autocomplete with alias coverage, the curated
  dataset, localStorage streaks.
- Deliberately out (v1): free-text matching (deferred above) · any backend · accounts
  or cross-device sync · multi-language · archive/practice modes · custom domain and
  polish beyond screenshot-worthiness — all gated on the prototype proving the loop.

## Open questions

- Whether six guesses fits the search space — judged at the playable prototype.
- Exact bucket boundaries and the row-color formula — tuned in play, not argued in
  advance.
- The name: **histle** — working name, collision-checked (2026-08-09) against the
  crowded "-dle" namespace and found free; a rename before any public push is cheap.
