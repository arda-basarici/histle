# DESIGN — histle

What is being built and why — the decisions and their reasoning, as a living snapshot
edited in place. The chronological journey lives in the stream's session log; the
feasibility evidence behind the founding decisions is recorded in the stream's
feasibility-spike record (2026-08-09).

*Design snapshot, playable build · last updated 2026-08-09.*

## Objective

A daily browser game: the player identifies a hidden famous historical event by
guessing other historical events; each guess returns distance feedback across several
axes. The distance-triangulation daily-puzzle shape (the Globle/Semantle family),
transposed to events.

The first build day's success criterion — a playable prototype sufficient to judge the
loop — was met, and play answered it: the loop works, and every felt weakness traced to
data quality rather than the mechanic. Virality remains the upside bet, not the bar;
polish beyond a shareable v1 stays gated on the game earning it.

A prior-art check (2026-08-09) found the history daily-game category saturated —
year-guessers, chronological-ordering games, letter-grid reskins, photo-dating games —
but no game where the guess itself is an event compared against a hidden event. That
structural gap is the niche; the multi-axis feedback below is what keeps the game in it.

## The loop

**Multi-axis feedback, not a bare year distance.** A single "years away" number
reduces the game to binary search on a timeline — the guessed events become mere date
probes, and the game blurs into the existing year-guesser genre. Each guess returns:

- **time**: direction (earlier/later) plus a bucketed distance — bands 0 · ≤5 · ≤15 ·
  ≤40 · ≤100 · ≤250 · ≤600 · >600 years, dense at the recent end because the answer
  pool skews modern (a wide 100–500 band said almost nothing about a 20th-century
  answer). Buckets are load-bearing ambiguity: direction plus an *exact* distance
  would solve the answer's year in one guess, so no displayed signal may be finer
  than its band.
- **region**: same / different / honest "multi" for events spanning continents (a
  world war has no continent — saying so is information, a "?" is a shrug) / unknown
  where the data has none.
- **kind**: match or no match over the eight-kind taxonomy below.
- a **row color** (green/yellow/grey) and a **0–100 score** summarizing closeness.
  The score is computed only from the information the cells already display (bucket
  index + region match + kind match, 100 reserved for the hit) — a score derived from
  raw year distance would let players reverse-engineer Δyear and silently repeal the
  bucket design. Legibility over mystique is the differentiator throughout: every
  signal is interpretable, unlike the embedding-similarity family's opaque numbers.

**Unlimited guesses, count as score.** The guess counter is the result ("solved
in 7"), shared and compared. *(Tombstone: the six-guess hard cap — cut 2026-08-09
after play. The cap belongs to Wordle's family, where guesses yield hard combinatorial
constraints over a small space; distance triangulation over 10k events is the
Globle/Semantle family, whose convergent design is unlimited guesses with
count-as-score. The genre had already discovered the right answer.)*

**Hints as purchases, give-up as the loss.** After six guesses a hint button offers,
in order: the answer's kind, its region, its century — each adding +2 to the shared
count, so help is priced in the number the player brags with. Give-up reveals the
answer and breaks the streak; losing is opt-in rather than imposed.

**The share text** — header ("histle #N — solved in 7"), hint count, and a spoiler-free
emoji trail of the last guesses. This is the distribution mechanism, so what it
encodes is a first-class design surface; changing its meaning post-launch resets
comparability between players and is avoided.

**Daily answer, deterministic and client-side.** A seeded permutation cycle over the
curated answer pool — every player gets the same puzzle, no server, no repeats within
a cycle. A deterministic repair pass enforces variety (no kind two days running; near
the full kind spread within any two-week window): the pool's natural composition is
war-heavy, and an unconstrained calendar reads as a military-history quiz. Casual
obfuscation only; a determined cheater is not the threat model.

## The taxonomy — one presented kind, many true labels

**Event-form and domain are different dimensions**, and a single-axis taxonomy
collides on exactly the famous cases (an assassination of a president is formally
violence and substantively politics). The resolution: one **primary kind** per event —
the game axis needs a single comparable value — with the full set of true kind labels
kept as **tags** underneath (the data captures them; game mechanics on tags are future
work below).

Eight kinds, each with a stated boundary rather than a class list: **battle & war**
(organized armed conflict between comparable forces) · **revolution & uprising**
(bottom-up mass challenge to political order) · **attack & violence** (discrete acts —
terrorism, assassinations, massacres — outside conventional war) · **disaster &
accident** (natural disasters, accidents, epidemics, famines) · **politics &
diplomacy** (treaties, elections, declarations, state formation — and coups: an
elite seizure of government is politics, not uprising) · **science & exploration**
(inventions, discoveries, spaceflight, voyages) · **culture & religion** · **society &
economy**. Precedence for multi-class events runs violence > war > uprising >
disaster > science > culture > society > politics, politics doubling as the flagged
fallback.

**Three layers, never conflated:** the source ontology (whatever Wikidata says) → the
normalized primary-kind-plus-tags layer (this taxonomy) → the curated target
distribution (what the daily calendar actually serves, deliberately rebalanced by the
scheduler and the curation pass — source share never dictates target share).

## Input

**Autocomplete over a canonical vocabulary, not free text.** The player picks from
suggestions, so every input is a known entity by construction — no resolution errors
and no ML in the loop. Matching is a hand-rolled token matcher (~100 lines, no
dependency) over names plus aliases with locale-safe normalization isolated in one
function (the full Turkish-casing upgrade lands with the language-file feature). The
alias net comes from data, not semantics: Wikidata alternate labels plus Wikipedia
redirect titles — redirects are human-recorded alternate phrasings ("demolition of the
Berlin Wall" reaches the fall of the Berlin Wall because an editor once created that
redirect). Ranking: name matches above alias matches, then popularity-weighted with a
prefix bonus — guarded by a regression suite of famous-query expectations.

**Suggestions leak nothing**: name only — no year, no region — because a browsable
dropdown of years is a free timeline. The one bounded exception: identical names
(seven "Treaty of Paris") carry a century tag, scoped to same-name collisions.

**Free-text semantic matching — deferred, by design.** Embedding-based query→event
resolution would ship a model to every player or require a server; the game does not
need it at launch. It is the natural second layer *with its own measured evaluation*
(lexical vs. embedding recall on real missed queries) if the game earns further
investment — the queries autocomplete fails on are that evaluation set.

## The dataset

**Two pools.** The guessable pool is everything that survives cleaning (~10.3k
events); the answer pool is small and hand-curated (443 at the first cut: the
popularity prefill plus the manual icons, refined by the curation sheet). Wordle's
move — obscure guesses allowed, common answers only — applied to events.

**Harvest spine: Wikipedia's "On This Day" API** — 366 calendar-day calls yield every
editor-tagged event with summary text, year, and Wikidata QID. Every page a blurb
links becomes a candidate: the feed's link list has no canonical first element (the
lead link is often an umbrella — the Pearl Harbor blurb leads with World War II — so
first-link canonicalization provably loses icons). Sorting events from bystanders is
Wikidata's job, not position's.

**Enrichment and typing: batched Wikidata passes by QID** — dates, places, aliases,
instance-of classes, sitelink notability, coordinates, plus Wikipedia redirect
harvesting for the famous band. An entity is an *event* if it carries an event date
(point-in-time or start), refined by a small blocklist (people, places, products,
calendar years) with rescue classes for event-shaped umbrellas, and a year-gap sanity
check against the blurb year (an entity whose Wikidata date sits centuries from its
blurb is a taxon or an artifact, not the happening). The scored year is Wikidata's,
converted to historian years (no year zero).

**The manual-additions layer.** Some civilizational icons — the sealing of Magna
Carta, the invention of printing, the first powered flight — exist in Wikipedia only
as their *artifacts* (a charter, a product, an airplane) and structurally cannot pass
an event filter. A hand-curated manual file (~45 entries, provenance-marked) supplies
them, deduplicated against the harvested pool at merge. This is also what keeps the
science & exploration kind alive: the feed's composition (battles and plane crashes)
does not get to dictate the game's.

**Curation flows through one sheet.** `data/curation.csv` (regenerated by the
pipeline, human edits preserved and read back) rules answer-pool membership, kind
overrides, and outright drops. Region fill runs a documented chain: continent →
country lookup (transcontinental historical states resolved to their core, judgment
calls documented in the mapping) → coordinate fallback → null, with genuinely
multi-continent events marked "multi" rather than forced to an alphabetical continent.

**Events with duration** score against their start date — one stated rule, not
per-event judgment. **BCE events** stay in the guess pool (about sixty, historian
years); the answer pool remains almost entirely CE, since ancient chronology is
contested at bucket precision.

**Rejected sources, tombstoned.** *EventKG* — research-grade RDF, stale since 2020;
overkill for a game-scale curated dataset (though its idea returns, purpose-built and
small, in the relation-graph future work). *DBpedia events* — half military, much of
the rest sports. *Kaggle "World Important Events"* — no sourcing methodology.

## Architecture

**Static-first as invariant, host as choice.** The game requires no server to play:
static files, client-side daily selection, localStorage streaks. That invariant keeps
the game portable, free to host, and immune to any box's downtime — and it survives a
move from GitHub Pages to the portfolio VPS unchanged, since serving static files is
the one thing every host does. Optional server-side additions (first candidate: a tiny
failed-query logging endpoint, which accrues the semantic-matcher evaluation set) must
degrade gracefully when absent, preserving the invariant. Accounts, cross-device sync,
and server-authoritative answers remain out until a capability genuinely requires
them.

**The split schema is the multilanguage seam.** `data/events.core.json` carries only
language-independent fields (`{id, year, region, country, lat, lon, category, tags[],
famous_candidate, popularity, thumb, manual}`); `data/labels.en.json` carries names,
aliases, and blurbs for one language. Supporting another wiki language is additive —
generate its label file, add it to a picker — with no change to game code or the core
file. Thumbnails hotlink Wikimedia's CDN (bundling 10k images is not an option); this
is an asset fetch, not an API call, and the one stated softening of "no runtime
requests."

**Pipeline vs. game, one seam.** The Python pipeline (`pipeline/`: harvest → enrich →
augment → assemble) emits the data files; the frontend only reads them. The curated
outputs are committed — the manual passes make them non-regenerable — while raw
harvests are git-ignored and regenerate from the APIs.

## Scope & non-goals

- In (v1): the daily game with unlimited guesses, hints, score, share text;
  autocomplete with alias coverage; the curated dataset; localStorage streaks; row
  thumbnails.
- Deliberately out (v1), gated on the game proving itself: free-text matching · any
  required backend · accounts/sync · additional languages · archive and practice
  modes · the map and constellation visuals · polish beyond screenshot-worthiness.

## Future work (curated)

- **Tag namespaces and mechanics** — typed tags (form/domain from bounded
  vocabularies; family from Wikidata part-of chains), a tri-state kind cell (primary
  match / tag overlap / miss) with matched tags named in the row detail; tag *count*
  never surfaces (it measures tagging density, not closeness). Revisit when the tag
  data has a first mechanic worth its curation cost.
- **The typed relation graph** — events as nodes, named edges (part-of families,
  shared figures): interpretable relatedness ("related because both sit inside WWII")
  as against embedding similarity. Small, mechanically harvestable, and the data
  substrate for the two entries below. Build when a feature needs edges to *say why*.
- **One canonical distance function** — a single composite over year/geo/tags from
  which score, closeness color, rank indicators, related-events, and hint selection
  all derive; "close" must mean one thing everywhere. Settle before any
  distance-consuming polish feature.
- **The constellation visual** — the pool as a navigable graph laid out on
  interpretable axes (time as spine, families as clusters), guesses glowing with
  their closeness colors, structure visible but identities unlabeled until earned;
  the win blooms the neighborhood. The flagship of the filmable tier; depends on the
  three entries above. The world-map pin view is its cheap precursor.
- **Closeness rank** ("among the 20 nearest events in the pool") — endgame
  accelerant, mostly leak-safe; needs the canonical distance first.
- **Turkish edition** — mechanically: a `labels.tr.json` pass plus the locale-correct
  matcher upgrade; editorially: a per-language answer calendar, since fame is
  culture-relative. The distribution bet: the Turkish daily-game niche looks
  underserved. Trigger: the game showing life.
- **Failed-query logging** — the first server-side addition (VPS endpoint, graceful
  absence); accrues the free-text matcher's evaluation set from real players.
- **Answer-space QA** — offline density maps of the answer calendar over the
  era/region/kind space, checking the 443 spread before promotion pushes.

## Open questions

- Score constants, hint pricing, and the closeness formula — tunable by design;
  revisit after real players.
- The name: **histle** — working name, collision-checked (2026-08-09) and free; a
  rename before any public push is cheap.
- Hosting at launch: GitHub Pages (zero ops today) vs. the portfolio VPS under
  `ardabasarici.dev` (better long-term home; enables the logging endpoint) — decided
  at the deploy step; the static-first invariant makes the choice reversible.
