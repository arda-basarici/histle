"""Augment the event pool with the two facts the guess loop needs and Wikidata alone will not give.

Third stage of the histle dataset pipeline. It reads `data/raw/candidates.json` (for each
QID's Wikipedia page title) and `data/raw/enriched.json` (for the event pool — the items
Wikidata gave an event date), then writes `data/raw/augmented.json`: one record per event-pool
QID carrying `redirect_aliases`, `lat` and `lon`.

Why these two, and why here. The game has to accept what a player actually types, and it has
to place an event on a map. Neither is answered by the enrichment stage. Wikidata's
`skos:altLabel` aliases are curator-written and sparse — they are the names an editor thought
worth recording, not the names people search with. Wikipedia's redirects are the opposite:
every misremembering, translation, tabloid phrasing and honest synonym that ever sent a reader
to the right page, accumulated over two decades. That is precisely the vocabulary a guess box
must accept, and it lives in the MediaWiki Action API, not in Wikidata. Coordinates (P625) are
in Wikidata but were not asked for in the enrichment pass, and the region axis of the feedback
grid needs them: `countries` alone cannot say whether two events happened near each other.

The two jobs are scoped differently on purpose. Redirects are fetched only for **answer-pool
candidates** — event-pool items with at least 25 sitelinks, the famous-enough slice a hidden
answer could ever be drawn from — because the aliases exist to make the *answer* recognisable,
and fetching them for 10k obscure items would be nine tenths waste. Coordinates are fetched for
the **entire event pool**, because every guessable event needs a position on the region axis,
not just the answerable ones. So `redirect_aliases` is legitimately empty for most records, and
that is a scope fact, not a coverage failure.

Boundary. Input is two files plus the network; output is the per-batch cache under
`data/raw/augment/` and the merged `data/raw/augmented.json`. No curation, no scoring, no
alias ranking happens here — every event-pool QID appears in the output, coordinates included
or null, so the counts stay honest and the curation stage downstream owns every judgement call.

Gotchas worth knowing before editing:

- The redirects API answers with the page's *resolved* title, and it normalises what you asked
  for: `Attack_on_Pearl_Harbor` goes out, `Attack on Pearl Harbor` comes back. Worse, the
  `pages` array is not in request order. So the results cannot be joined back by position, and
  cannot be joined by string equality against the underscored title either. The response's
  `normalized` list is the join key: it maps each sent title to the form the pages array uses.
- Anonymous callers get 50 titles and 500 redirects per request. A batch of 50 famous events
  routinely blows past 500 (Pearl Harbor alone has ~40), so the `continue` object has to be
  followed until it stops appearing, merging as it goes. Ignoring it would silently truncate
  the tail of every large batch — and the loss would look like "those pages have few
  redirects", which is indistinguishable from the truth by inspection.
- Redirect titles are page keys, so they include namespaced pages ("Talk:...", "Portal:...").
  Anything with a colon is dropped: a namespace page is never something a player types.
  Case-only variants of the article title itself are dropped too — "Battle Of Hastings" is not
  a second name for the Battle of Hastings, it is the same name with the shift key held. Case
  variants of *each other* are deliberately kept ("Pearl Harbor attack" and "Pearl Harbor
  Attack" both survive): collapsing those is a normalisation policy the game's matcher should
  own, not a fetch-time judgement that throws data away.
- A P625 value is a WKT literal, `Point(lon lat)` — longitude first, which is the opposite of
  the order everything else here writes coordinates in, and a silent swap would drop every
  European battle into the Indian Ocean. Values on a non-Earth globe arrive prefixed with a
  globe URI in angle brackets; those are skipped rather than parsed.
- An item can carry several P625 values (a war fought in several places). The lexicographically
  first raw literal is kept, which is arbitrary but reproducible; the alternatives are lost.
- Batching is deterministic *for a given pair of input files*: QIDs are sorted by their numeric
  part, then sliced, so batch 0007 holds the same titles on every run and the cache resumes. If
  candidates.json or enriched.json changes, every boundary shifts and the cache is misaligned;
  a cache file whose stored QID list disagrees with the recomputed batch raises rather than
  being quietly reused. The remedy is crude on purpose: delete `data/raw/augment/` and re-run.
- Windows default encoding is not UTF-8 and these titles are full of non-ASCII names, so every
  `open()` here passes `encoding="utf-8"` and `main` reconfigures stdout.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import requests

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = (
    "histle-dataset-builder/0.1 "
    "(https://github.com/arda-basarici/histle; ardabasarici@gmail.com)"
)

ANSWER_POOL_MIN_SITELINKS = 25  # the famous-enough line; see the module docstring on scope

REDIRECT_BATCH_SIZE = 50  # the API's anonymous ceiling on `titles`
COORD_CHUNK_SIZE = 200
REDIRECT_SPACING_SECONDS = 0.2
QUERY_SPACING_SECONDS = 1.0
REDIRECT_TIMEOUT_SECONDS = 30
SPARQL_TIMEOUT_SECONDS = 90  # above WDQS's own 60s ceiling, so its timeout surfaces as a body
MAX_ATTEMPTS = 3
REDIRECT_BACKOFF_SECONDS = (1.0, 3.0)
SPARQL_BACKOFF_SECONDS = (5.0, 20.0)
PROGRESS_EVERY_BATCHES = 5
MAX_CONTINUATIONS = 40  # a `continue` that never clears must fail loudly, not spin forever

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_PATH = REPO_ROOT / "data" / "raw" / "candidates.json"
ENRICHED_PATH = REPO_ROOT / "data" / "raw" / "enriched.json"
CACHE_DIR = REPO_ROOT / "data" / "raw" / "augment"
AUGMENTED_PATH = REPO_ROOT / "data" / "raw" / "augmented.json"
CACHE_FILE_STEMS = {"redirects": "redirects-batch", "coords": "coords-chunk"}

POINT_PATTERN = re.compile(r"^Point\(\s*(-?[\d.eE+-]+)\s+(-?[\d.eE+-]+)\s*\)$")

COORD_QUERY = """
SELECT ?item ?coord WHERE {
  VALUES ?item { %s }
  ?item wdt:P625 ?coord .
}"""

# Report-only vocabulary: which classes get their own coverage row, which classes make an
# item a readable sample, and which words are too common to signal that an alias repeats the
# article's title. Nothing downstream depends on any of it and none of it is a taxonomy — the
# curation stage owns that.
TOP_CLASSES = 12
WATCHED_CLASSES = ("battle", "treaty", "peace treaty", "earthquake", "war", "aviation accident")
SAMPLE_CLASSES = frozenset(
    {
        "battle", "naval battle", "siege", "war", "armed conflict", "military operation",
        "treaty", "peace treaty", "earthquake", "disaster", "massacre", "terrorist attack",
        "revolution", "coup d'état",
    }
)
TITLE_STOPWORDS = frozenset({"a", "an", "and", "at", "de", "in", "of", "on", "the", "to"})
SAMPLE_COUNT = 5
BERLIN_WALL_QIDS = ("Q5086", "Q69163529")  # the wall itself, then its fall; see the spot-check


@dataclass
class BatchStats:
    """Tally of how one job spent its requests, for the acceptance report. Both jobs share the
    shape — a deterministic list of batches, each either restored from cache, fetched, or
    failed past its retries — so both report through the same record."""

    batches_total: int = 0
    batches_fetched: int = 0
    batches_cached: int = 0
    failed_batches: list[int] = field(default_factory=list)
    requests_sent: int = 0
    requests_retried: int = 0

    @property
    def batches_failed(self) -> int:
        return len(self.failed_batches)


@dataclass
class EventPool:
    """The slice of the enrichment this stage works on, read once so the two large input files
    are parsed exactly once.

    `qids` is the whole event pool in deterministic order; `answer_candidates` is the
    sitelink-filtered subset the redirect job runs over. The lookup dicts carry everything the
    report needs about an item, so no later function has to re-open an input file to print a
    label or a class."""

    qids: list[str]
    answer_candidates: list[str]
    titles: dict[str, str]
    labels: dict[str, str]
    sitelinks: dict[str, int]
    instance_of: dict[str, list[str]]


def load_json(path: Path, produced_by: str) -> dict[str, Any]:
    """One of the two input documents, with a missing file named as a missing pipeline stage
    rather than as a bare OSError — the fix is always "run the earlier stage", so the error
    says so."""
    if not path.is_file():
        raise RuntimeError(
            f"no file at {path}; expected the {produced_by} stage to have written it — "
            f"run pipeline/{produced_by}.py first"
        )
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_event_pool() -> EventPool:
    """The event pool joined to its Wikipedia page titles, ready for both jobs.

    The join is the point: the enrichment knows which QIDs are events and how famous they are,
    the harvest knows which page each QID came from, and the redirects API speaks only page
    titles. A pool QID with no harvested title cannot be asked about and would silently vanish
    from the redirect job, so it raises here instead — the two files are two views of one
    dataset and a gap between them means they are out of sync."""
    enriched = load_json(ENRICHED_PATH, "enrich")
    candidates = load_json(CANDIDATES_PATH, "harvest")

    items = enriched.get("items")
    if not isinstance(items, dict) or not items:
        raise RuntimeError(
            f"{ENRICHED_PATH} has no non-empty 'items' object; "
            f"expected one record per candidate QID, found keys: {sorted(enriched)}"
        )
    events = candidates.get("events")
    if not isinstance(events, list) or not events:
        raise RuntimeError(
            f"{CANDIDATES_PATH} has no non-empty 'events' list; "
            f"expected thousands of candidate records, found keys: {sorted(candidates)}"
        )

    titles = {event["qid"]: event["title"] for event in events}
    pool = {qid: record for qid, record in items.items() if record["has_event_date"]}

    untitled = sorted(qid for qid in pool if not titles.get(qid))
    if untitled:
        raise RuntimeError(
            f"{len(untitled)} event-pool QIDs have no page title in {CANDIDATES_PATH.name} "
            f"(first: {untitled[:5]}); expected every enriched QID to trace back to a "
            "harvested page — the two files are out of sync"
        )

    qids = sorted(pool, key=lambda qid: int(qid[1:]))
    return EventPool(
        qids=qids,
        answer_candidates=[
            qid for qid in qids if (pool[qid]["sitelinks"] or 0) >= ANSWER_POOL_MIN_SITELINKS
        ],
        titles={qid: titles[qid] for qid in qids},
        labels={qid: pool[qid]["label"] or "(no English label)" for qid in qids},
        sitelinks={qid: pool[qid]["sitelinks"] or 0 for qid in qids},
        instance_of={qid: pool[qid]["instance_of"] for qid in qids},
    )


def batched(qids: list[str], size: int) -> list[list[str]]:
    """The QID list cut into request-sized slices. Deterministic given the same input list,
    which is what makes the on-disk cache resumable — see the docstring's caveat about what
    happens when an input file changes underneath it."""
    return [qids[start : start + size] for start in range(0, len(qids), size)]


def display_form(title: str) -> str:
    """A page key rendered as a human reads it. Wikipedia's underscores are a URL artefact, and
    a player typing a guess types spaces."""
    return title.replace("_", " ").strip()


def is_case_variant(redirect: str, title: str) -> bool:
    """Whether a redirect differs from the article's own title by nothing but letter case.
    Wikipedia carries a lot of these ("Battle Of Hastings", "battle of hastings") because
    search is case-sensitive on the first letter only; none of them is a second *name* for the
    event, so keeping them would inflate every alias count with noise."""
    return redirect.lower() == title.lower()


def useful_aliases(redirect_titles: Iterable[str], page_title: str) -> list[str]:
    """The redirect titles worth offering a player, in display form and sorted.

    Two filters, both about junk rather than taste: a colon means a namespaced page
    ("Talk:...", "Wikipedia:...") that no one types as an event name, and a case-only variant
    of the article title carries no new name. Sorted and deduplicated so the written file is
    byte-stable across runs — the API's redirect order is page-id order, which is creation
    order, which is not meaning."""
    display_title = display_form(page_title)
    kept = {
        display_form(title)
        for title in redirect_titles
        if ":" not in title
    }
    return sorted(alias for alias in kept if alias and not is_case_variant(alias, display_title))


def build_session(accept: str) -> requests.Session:
    """A session carrying the identifying User-Agent both APIs ask anonymous clients for, and
    the Accept header that gets JSON rather than an HTML browser page."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": accept})
    return session


def get_json(
    session: requests.Session, params: dict[str, str], label: str, stats: BatchStats
) -> dict[str, Any]:
    """One MediaWiki Action API response, retrying transient failures with a short backoff.
    Raises RuntimeError naming the batch and the last error once the attempts are spent — the
    caller decides that one bad batch should not end the run."""
    last_error: Exception | str | None = None

    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            stats.requests_retried += 1
            time.sleep(REDIRECT_BACKOFF_SECONDS[min(attempt - 1, len(REDIRECT_BACKOFF_SECONDS) - 1)])
        try:
            stats.requests_sent += 1
            response = session.get(WIKIPEDIA_API, params=params, timeout=REDIRECT_TIMEOUT_SECONDS)
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                continue
            return response.json()
        except (requests.RequestException, json.JSONDecodeError) as error:
            last_error = error

    raise RuntimeError(
        f"{label} failed after {MAX_ATTEMPTS} attempts; "
        f"expected HTTP 200 with a JSON body from {WIKIPEDIA_API}; last error: {last_error}"
    )


def query_to_response_titles(payload: dict[str, Any]) -> dict[str, str]:
    """The response's `normalized` list inverted into "the title I sent" -> "the title the
    pages array uses". This is the only sound join key: underscores become spaces, the first
    letter may be capitalised, and the pages array is not in request order, so neither position
    nor string equality with the sent title can be trusted. Titles the API did not normalise
    are absent from the list and join to themselves, which the caller handles."""
    query = payload.get("query") or {}
    return {entry["from"]: entry["to"] for entry in query.get("normalized") or []}


def fetch_redirect_batch(
    session: requests.Session, qids: list[str], pool: EventPool, stats: BatchStats
) -> dict[str, list[str]]:
    """Every redirect pointing at one batch of up to 50 pages, folded back onto their QIDs.

    Continuation is the substance here: `rdlimit=max` caps at 500 redirects per response for an
    anonymous caller, and 50 famous events routinely exceed that, so the loop re-sends with
    whatever `continue` parameters came back and merges each page's redirects into the same
    accumulator until the object stops appearing. A `continue` that never clears would spin,
    so the loop is bounded and raises past the bound rather than hanging the run."""
    titles = [pool.titles[qid] for qid in qids]
    qids_by_title: dict[str, list[str]] = {}
    for qid in qids:
        qids_by_title.setdefault(pool.titles[qid], []).append(qid)

    base_params = {
        "action": "query",
        "prop": "redirects",
        "titles": "|".join(titles),
        "rdlimit": "max",
        "format": "json",
        "formatversion": "2",
    }
    label = f"redirect batch of {len(titles)} titles (first: {titles[0]})"

    redirects_by_response_title: dict[str, set[str]] = {}
    continuation: dict[str, str] = {}

    for round_number in range(MAX_CONTINUATIONS + 1):
        payload = get_json(session, {**base_params, **continuation}, label, stats)
        response_title_of = query_to_response_titles(payload)

        for page in (payload.get("query") or {}).get("pages") or []:
            if page.get("missing") or page.get("invalid"):
                continue
            bucket = redirects_by_response_title.setdefault(page["title"], set())
            bucket.update(entry["title"] for entry in page.get("redirects") or [])

        continuation = payload.get("continue") or {}
        if not continuation:
            break
        if round_number == MAX_CONTINUATIONS:
            raise RuntimeError(
                f"{label} still returned a continue object after {MAX_CONTINUATIONS} rounds; "
                "expected the redirect list to be exhausted long before that"
            )
        time.sleep(REDIRECT_SPACING_SECONDS)

    aliases: dict[str, list[str]] = {qid: [] for qid in qids}
    for query_title, owners in qids_by_title.items():
        response_title = response_title_of.get(query_title, query_title)
        found = redirects_by_response_title.get(response_title)
        if not found:
            continue
        kept = useful_aliases(found, query_title)
        for qid in owners:
            aliases[qid] = kept
    return aliases


def values_block(qids: Iterable[str]) -> str:
    """The QIDs as a SPARQL `VALUES` body."""
    return " ".join(f"wd:{qid}" for qid in qids)


def run_sparql(session: requests.Session, query: str, label: str, stats: BatchStats) -> list[dict[str, Any]]:
    """The result rows of one SPARQL query, retrying transient failures with a backoff. WDQS
    routinely answers a perfectly valid query with 429 or 500 under load, so a single failure
    means nothing; three mean the chunk is not going to work today. POST rather than GET
    because a 200-QID `VALUES` block overruns what the endpoint accepts in a URL."""
    last_error: Exception | str | None = None

    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            stats.requests_retried += 1
            time.sleep(SPARQL_BACKOFF_SECONDS[min(attempt - 1, len(SPARQL_BACKOFF_SECONDS) - 1)])
        try:
            stats.requests_sent += 1
            response = session.post(
                SPARQL_ENDPOINT, data={"query": query}, timeout=SPARQL_TIMEOUT_SECONDS
            )
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                continue
            return response.json()["results"]["bindings"]
        except (requests.RequestException, json.JSONDecodeError, KeyError) as error:
            last_error = error

    raise RuntimeError(
        f"{label} failed after {MAX_ATTEMPTS} attempts; "
        f"expected HTTP 200 with SPARQL JSON results from {SPARQL_ENDPOINT}; "
        f"last error: {last_error}"
    )


def parse_point(literal: str) -> tuple[float, float] | None:
    """A WKT point literal as (lat, lon), or None if it is not an Earth point.

    Two traps in one small function. The literal writes **longitude first** — `Point(13.4 52.5)`
    is Berlin, not a spot off Somalia — so the returned pair is deliberately reordered into the
    lat/lon convention the rest of the dataset uses. And a coordinate on another globe arrives
    as `<http://www.wikidata.org/entity/Q111> Point(...)`; those are Mars craters and the like,
    not places a history game can compare, so they read as absent rather than as coordinates."""
    match = POINT_PATTERN.match(literal.strip())
    if match is None:
        return None
    longitude, latitude = match.groups()
    return float(latitude), float(longitude)


def fetch_coord_chunk(
    session: requests.Session, qids: list[str], stats: BatchStats
) -> dict[str, dict[str, float | None]]:
    """The coordinates of one chunk of QIDs, as finished records. The only Wikidata-touching
    function. Items with no P625 yield no row at all — `wdt:P625` is not OPTIONAL here — so the
    accumulator is pre-filled with nulls and the query only ever fills some of it in. Where an
    item has several coordinates the lexicographically first literal wins, which is arbitrary
    but stable across runs."""
    literals: dict[str, str] = {}
    rows = run_sparql(session, COORD_QUERY % values_block(qids), f"coord chunk ({qids[0]}...)", stats)
    for row in rows:
        qid = row["item"]["value"].rsplit("/", 1)[-1]
        literal = row["coord"]["value"]
        if qid not in literals or literal < literals[qid]:
            literals[qid] = literal

    records: dict[str, dict[str, float | None]] = {qid: {"lat": None, "lon": None} for qid in qids}
    for qid, literal in literals.items():
        point = parse_point(literal)
        if point is None:
            continue
        records[qid] = {"lat": point[0], "lon": point[1]}
    return records


def cache_path(kind: str, index: int) -> Path:
    """Where one batch's parsed result lives. The job name is in the filename because both jobs
    cache side by side and their numbering is unrelated — job A slices the candidates by 50, job
    B slices the whole pool by 200, so index 0007 means two different things."""
    if kind not in CACHE_FILE_STEMS:
        raise RuntimeError(f"unknown cache kind {kind!r}; expected one of {sorted(CACHE_FILE_STEMS)}")
    return CACHE_DIR / f"{CACHE_FILE_STEMS[kind]}-{index:04d}.json"


def read_cached_batch(kind: str, index: int, qids: list[str]) -> dict[str, Any] | None:
    """The saved records for a batch, or None if it must be fetched. A truncated file from an
    interrupted run reads as absent — re-fetching is cheap, half a JSON document is not
    trustworthy. A file whose QID list has drifted from the recomputed batch raises: that means
    an input file changed and the whole cache is misaligned, which is worth stopping for rather
    than silently mixing two datasets."""
    path = cache_path(kind, index)
    if not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
    except json.JSONDecodeError:
        return None

    if document.get("qids") != qids:
        raise RuntimeError(
            f"cached {path.name} holds a different QID set than {kind} batch {index} now "
            "resolves to; expected candidates.json and enriched.json to be unchanged since the "
            f"cache was written — delete {CACHE_DIR} and re-run"
        )

    items = document.get("items")
    if items is None:
        return None
    return items


def write_cached_batch(kind: str, index: int, qids: list[str], items: dict[str, Any]) -> None:
    """Persist one batch's records, carrying the QID list that produced them so a later run can
    prove the cache still matches the inputs."""
    with cache_path(kind, index).open("w", encoding="utf-8") as handle:
        json.dump({"index": index, "qids": qids, "items": items}, handle, ensure_ascii=False)


def report_progress(kind: str, index: int, last: int, stats: BatchStats, note: str) -> None:
    """One progress line, printed every few fetched batches so a long run is watchable rather
    than silent."""
    print(
        f"  {kind} {index:04d}/{last} | "
        f"{stats.batches_fetched} fetched, {stats.batches_cached} cached, "
        f"{stats.batches_failed} failed | {note}",
        flush=True,
    )


def collect_redirects(pool: EventPool) -> tuple[dict[str, list[str]], BatchStats]:
    """Walk every redirect batch, from cache where possible and from Wikipedia otherwise, and
    return one alias list per answer-pool candidate. A batch that fails all its retries is
    recorded, filled with empty lists and stepped over — a long run must not be lost to one bad
    batch, and a re-run picks up where the cache stops."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    batches = batched(pool.answer_candidates, REDIRECT_BATCH_SIZE)
    stats = BatchStats(batches_total=len(batches))
    session = build_session("application/json")
    aliases: dict[str, list[str]] = {}

    for index, batch in enumerate(batches):
        cached = read_cached_batch("redirects", index, batch)
        if cached is not None:
            aliases.update(cached)
            stats.batches_cached += 1
            continue

        try:
            fetched = fetch_redirect_batch(session, batch, pool, stats)
        except RuntimeError as error:
            print(f"  FAILED redirect batch {index:04d}: {error}", flush=True)
            stats.failed_batches.append(index)
            aliases.update({qid: [] for qid in batch})
            continue

        write_cached_batch("redirects", index, batch, fetched)
        aliases.update(fetched)
        stats.batches_fetched += 1
        time.sleep(REDIRECT_SPACING_SECONDS)

        if stats.batches_fetched % PROGRESS_EVERY_BATCHES == 0:
            gained = sum(len(names) for names in aliases.values())
            report_progress("redirect batch", index, len(batches) - 1, stats, f"{gained} aliases so far")

    return aliases, stats


def collect_coordinates(pool: EventPool) -> tuple[dict[str, dict[str, float | None]], BatchStats]:
    """Walk every coordinate chunk, from cache where possible and from WDQS otherwise, and
    return one lat/lon record per event-pool QID. Same failure discipline as the redirect pass:
    a dead chunk becomes nulls and a line in the report, not the end of the run."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    chunks = batched(pool.qids, COORD_CHUNK_SIZE)
    stats = BatchStats(batches_total=len(chunks))
    session = build_session("application/sparql-results+json")
    coords: dict[str, dict[str, float | None]] = {}

    for index, chunk in enumerate(chunks):
        cached = read_cached_batch("coords", index, chunk)
        if cached is not None:
            coords.update(cached)
            stats.batches_cached += 1
            continue

        try:
            fetched = fetch_coord_chunk(session, chunk, stats)
        except RuntimeError as error:
            print(f"  FAILED coord chunk {index:04d}: {error}", flush=True)
            stats.failed_batches.append(index)
            coords.update({qid: {"lat": None, "lon": None} for qid in chunk})
            continue

        write_cached_batch("coords", index, chunk, fetched)
        coords.update(fetched)
        stats.batches_fetched += 1
        time.sleep(QUERY_SPACING_SECONDS)

        if stats.batches_fetched % PROGRESS_EVERY_BATCHES == 0:
            located = sum(1 for record in coords.values() if record["lat"] is not None)
            report_progress("coord chunk", index, len(chunks) - 1, stats, f"{located} located")

    return coords, stats


def merge_records(
    pool: EventPool, aliases: dict[str, list[str]], coords: dict[str, dict[str, float | None]]
) -> dict[str, dict[str, Any]]:
    """One output record per event-pool QID. Every pool member appears whether or not either
    job had anything to say about it, so a downstream count of "how many events do we have"
    can never disagree with the enrichment's."""
    return {
        qid: {
            "redirect_aliases": aliases.get(qid, []),
            "lat": coords.get(qid, {}).get("lat"),
            "lon": coords.get(qid, {}).get("lon"),
        }
        for qid in pool.qids
    }


def assert_coordinates_are_on_earth(records: dict[str, dict[str, Any]]) -> None:
    """Refuse to write a record whose coordinate is off the globe. A swapped lat/lon pair is the
    likely cause and it is invisible in aggregate counts — 52.5°N/13.4°E and 13.4°N/52.5°E are
    both perfectly plausible numbers — but any longitude beyond ±90 read as a latitude gives it
    away, so the range check is the cheapest guard against the failure that would silently
    scramble the region axis."""
    for qid, record in records.items():
        latitude, longitude = record["lat"], record["lon"]
        if latitude is None and longitude is None:
            continue
        if latitude is None or longitude is None:
            raise RuntimeError(
                f"{qid} has a half-filled coordinate (lat={latitude}, lon={longitude}); "
                "expected both or neither"
            )
        if not -90.0 <= latitude <= 90.0:
            raise RuntimeError(
                f"{qid} has latitude {latitude}; expected it within [-90, 90] — "
                "a lat/lon swap in parse_point is the usual cause"
            )
        if not -180.0 <= longitude <= 180.0:
            raise RuntimeError(
                f"{qid} has longitude {longitude}; expected it within [-180, 180]"
            )


def write_augmented(
    records: dict[str, dict[str, Any]],
    pool: EventPool,
    redirect_stats: BatchStats,
    coord_stats: BatchStats,
) -> None:
    """Persist the augmented records with the counts that produced them, so the file carries its
    own provenance and a reader need not re-run 80-odd requests to know what is missing."""
    AUGMENTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    alias_totals = [len(record["redirect_aliases"]) for record in records.values()]
    document = {
        "generated_from": "wikipedia redirects + wikidata P625 over the enriched event pool",
        "counts": {
            "event_pool": len(pool.qids),
            "answer_pool_candidates": len(pool.answer_candidates),
            "answer_pool_min_sitelinks": ANSWER_POOL_MIN_SITELINKS,
            "redirect_batches_total": redirect_stats.batches_total,
            "redirect_batches_fetched": redirect_stats.batches_fetched,
            "redirect_batches_cached": redirect_stats.batches_cached,
            "redirect_batches_failed": redirect_stats.batches_failed,
            "failed_redirect_batches": redirect_stats.failed_batches,
            "redirect_requests_sent": redirect_stats.requests_sent,
            "coord_chunks_total": coord_stats.batches_total,
            "coord_chunks_fetched": coord_stats.batches_fetched,
            "coord_chunks_cached": coord_stats.batches_cached,
            "coord_chunks_failed": coord_stats.batches_failed,
            "failed_coord_chunks": coord_stats.failed_batches,
            "coord_queries_sent": coord_stats.requests_sent,
            "qids_with_redirect_alias": sum(1 for count in alias_totals if count),
            "redirect_aliases_total": sum(alias_totals),
            "qids_with_coordinates": sum(1 for record in records.values() if record["lat"] is not None),
        },
        "items": records,
    }
    with AUGMENTED_PATH.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1)


def percent(count: int, total: int) -> str:
    """A share formatted for the report, safe when the denominator is zero."""
    if total == 0:
        return "  n/a"
    return f"{100 * count / total:5.1f}%"


def quantile(sorted_values: list[int], fraction: float) -> int:
    """The value at a fraction of the way through an already-sorted list, by nearest rank.
    Hand-rolled because this stage takes no dependency beyond `requests`, and a nearest-rank
    quantile on a few thousand integers needs no more than an index."""
    if not sorted_values:
        raise RuntimeError("quantile of an empty list; expected at least one value")
    index = min(len(sorted_values) - 1, int(fraction * len(sorted_values)))
    return sorted_values[index]


def content_words(text: str) -> set[str]:
    """The words of a title or alias that carry identity, lowercased. Articles and prepositions
    are dropped because they are shared by half of Wikipedia and would make every alias look
    like a restatement of its title."""
    return {word for word in display_form(text).lower().split() if word not in TITLE_STOPWORDS}


def colloquial_first(aliases: list[str], title: str) -> list[str]:
    """An item's aliases reordered so the interesting ones read first.

    "Interesting" is operationalised as *lexically distant from the article title, and shaped
    like something a person would type*: an alias sharing no content word with the title
    ("Hawaii Operation" for Attack on Pearl Harbor) is the colloquial name the whole job exists
    to catch, and among equally distant ones the wordier alphabetic phrase beats a numeric
    stub, so "World Trade Center attacks" surfaces ahead of "09/11/01". Purely a presentation
    order for the report — the stored list stays alphabetical."""
    title_words = content_words(title)

    def rank(alias: str) -> tuple[int, int, str]:
        words = display_form(alias).split()
        alphabetic = sum(1 for word in words if word.isalpha())
        return len(content_words(alias) & title_words), -min(alphabetic, 4), alias

    return sorted(aliases, key=rank)


def redirect_samples(pool: EventPool, records: dict[str, dict[str, Any]]) -> list[str]:
    """Five answer-pool candidates whose redirects are worth reading: the most-linked items that
    are recognisably *events* and did gain aliases, famous first.

    Ranking the whole subset by alias count instead returns year articles and disaster pages
    whose redirects are victims' names — technically the richest, evidentially useless. The
    class filter is the cheap way to reach the events a reader can judge on sight. Deterministic
    by construction, so two runs over the same cache print the same five and a diff means the
    data moved."""
    readable = [
        qid
        for qid in pool.answer_candidates
        if records[qid]["redirect_aliases"] and SAMPLE_CLASSES & set(pool.instance_of[qid])
    ]
    ranked = sorted(readable, key=lambda qid: (-pool.sitelinks[qid], int(qid[1:])))
    return ranked[:SAMPLE_COUNT]


def coordinate_samples(pool: EventPool, records: dict[str, dict[str, Any]]) -> list[str]:
    """Five located events spread across the globe — the northernmost, southernmost, easternmost
    and westernmost of the pool plus one near the origin. Extremes rather than a random draw
    because an off-by-a-hemisphere parse bug hides in the middle of a distribution and stands
    out at its edges."""
    located = [qid for qid in pool.qids if records[qid]["lat"] is not None]
    if not located:
        return []

    picks: list[str] = []
    for key in (
        lambda qid: -records[qid]["lat"],
        lambda qid: records[qid]["lat"],
        lambda qid: -records[qid]["lon"],
        lambda qid: records[qid]["lon"],
        lambda qid: abs(records[qid]["lat"]) + abs(records[qid]["lon"]),
    ):
        for qid in sorted(located, key=lambda qid: (key(qid), int(qid[1:]))):
            if qid not in picks:
                picks.append(qid)
                break
    return picks


def coverage_by_class(
    pool: EventPool, records: dict[str, dict[str, Any]], class_name: str
) -> tuple[int, int]:
    """How many pool items of one P31 class have a coordinate, and how many there are. An item
    with several classes counts under each of them, so the rows are "items of this class" and
    do not sum to the pool."""
    members = [qid for qid in pool.qids if class_name in pool.instance_of[qid]]
    located = sum(1 for qid in members if records[qid]["lat"] is not None)
    return located, len(members)


def print_berlin_wall_check(pool: EventPool, records: dict[str, dict[str, Any]]) -> None:
    """The named spot-check, run over both Berlin Wall items: the wall and its fall.

    Two QIDs rather than one because the obvious one is the wrong one, and that is the finding.
    A spot-check that printed nothing when its subject turned out to be absent would hide
    exactly the fact worth knowing, so each QID reports its own status — absent from the pool,
    present but below the sitelink line, or present with the aliases it gained."""
    candidates = set(pool.answer_candidates)
    for qid in BERLIN_WALL_QIDS:
        if qid not in records:
            print(
                f"  {qid} is NOT in the event pool — Wikidata gives it no P585/P580 date, "
                "so it never reached this stage."
            )
            continue
        if qid not in candidates:
            print(
                f"  {qid} ({pool.labels[qid]}) is in the event pool but has "
                f"{pool.sitelinks[qid]} sitelinks, below the {ANSWER_POOL_MIN_SITELINKS} line — "
                "no redirects were fetched for it."
            )
            continue
        aliases = records[qid]["redirect_aliases"]
        print(f"  {qid} ({pool.labels[qid]}) gained {len(aliases)} redirect aliases:")
        for alias in colloquial_first(aliases, pool.titles[qid])[:12]:
            print(f"      {alias}")


def print_report(
    pool: EventPool,
    records: dict[str, dict[str, Any]],
    redirect_stats: BatchStats,
    coord_stats: BatchStats,
) -> None:
    """The acceptance evidence: what each job cost, how much vocabulary the redirects bought,
    how well coordinates cover the pool and which classes they favour, and samples to read with
    your own eyes."""
    candidates = pool.answer_candidates
    alias_counts = sorted(len(records[qid]["redirect_aliases"]) for qid in candidates)
    with_alias = sum(1 for count in alias_counts if count)
    located = [qid for qid in pool.qids if records[qid]["lat"] is not None]

    print("\n" + "=" * 78)
    print("AUGMENT REPORT — wikipedia redirects + wikidata coordinates")
    print("=" * 78)

    print("\n1. RUN")
    print(f"  event pool                  : {len(pool.qids)}")
    print(
        f"  answer-pool candidates      : {len(candidates)} "
        f"(event pool AND sitelinks >= {ANSWER_POOL_MIN_SITELINKS})"
    )
    print(
        f"  redirect batches            : {redirect_stats.batches_total} total, "
        f"{redirect_stats.batches_fetched} fetched, {redirect_stats.batches_cached} cached, "
        f"{redirect_stats.batches_failed} failed {redirect_stats.failed_batches or ''}"
    )
    print(
        f"  redirect requests sent      : {redirect_stats.requests_sent} "
        f"({redirect_stats.requests_retried} were retries; the excess over one per batch is "
        "continuation)"
    )
    print(
        f"  coord chunks                : {coord_stats.batches_total} total, "
        f"{coord_stats.batches_fetched} fetched, {coord_stats.batches_cached} cached, "
        f"{coord_stats.batches_failed} failed {coord_stats.failed_batches or ''}"
    )
    print(
        f"  coord queries sent          : {coord_stats.requests_sent} "
        f"({coord_stats.requests_retried} were retries)"
    )

    print("\n2. REDIRECT ALIASES (over the answer-pool candidates)")
    print(f"  QIDs with >= 1 alias        : {with_alias}  {percent(with_alias, len(candidates))}")
    print(f"  aliases gained (total)      : {sum(alias_counts)}")
    print(f"  mean per candidate QID      : {sum(alias_counts) / max(len(candidates), 1):.2f}")
    if alias_counts:
        print(f"  distribution p50 / p90 / max: {quantile(alias_counts, 0.5)} / "
              f"{quantile(alias_counts, 0.9)} / {alias_counts[-1]}")

    print(
        "\n  samples (most-linked event-shaped candidates; "
        "each item's most colloquial-looking aliases first):"
    )
    for qid in redirect_samples(pool, records):
        aliases = records[qid]["redirect_aliases"]
        print(f"      {qid} — {pool.labels[qid]}  ({len(aliases)} aliases, "
              f"{pool.sitelinks[qid]} sitelinks)")
        for alias in colloquial_first(aliases, pool.titles[qid])[:6]:
            print(f"          {alias}")

    print("\n  named spot-check — the Berlin Wall:")
    print_berlin_wall_check(pool, records)

    print("\n3. COORDINATES (over the whole event pool)")
    print(f"  located                     : {len(located)}  {percent(len(located), len(pool.qids))}")
    print(f"  no coordinate               : {len(pool.qids) - len(located)}  "
          f"{percent(len(pool.qids) - len(located), len(pool.qids))}")

    class_totals: Counter[str] = Counter()
    for qid in pool.qids:
        class_totals.update(set(pool.instance_of[qid]))
    shown = [name for name, _ in class_totals.most_common(TOP_CLASSES)]
    shown += [name for name in WATCHED_CLASSES if name in class_totals and name not in shown]

    print("\n  coverage by P31 class (an item counts under each of its classes):")
    for name in shown:
        located_count, total = coverage_by_class(pool, records, name)
        print(f"      {name:<24} {located_count:>5} / {total:<5}  {percent(located_count, total)}")

    print("\n  samples (the pool's four compass extremes plus one near the origin):")
    for qid in coordinate_samples(pool, records):
        record = records[qid]
        print(f"      {qid:<12} lat {record['lat']:>10.4f}  lon {record['lon']:>11.4f}  "
              f"{pool.labels[qid]}")

    print("\n4. SANITY")
    print(f"  coordinates checked         : {len(located)} — all within "
          "[-90, 90] latitude and [-180, 180] longitude (asserted before writing)")
    half_filled = sum(
        1
        for record in records.values()
        if (record["lat"] is None) != (record["lon"] is None)
    )
    print(f"  half-filled coordinates     : {half_filled}")
    print(f"  records written             : {len(records)} (= event pool)")

    print(f"\nWROTE {AUGMENTED_PATH}")
    print("=" * 78, flush=True)


def main() -> int:
    """Load, fetch both jobs, check, persist, report. Returns non-zero when neither job
    produced anything, which means the run is not evidence of anything."""
    # Titles and labels carry accents and non-Latin scripts; a redirected stdout on Windows
    # defaults to the ANSI codepage and would raise UnicodeEncodeError on them.
    sys.stdout.reconfigure(encoding="utf-8")

    pool = load_event_pool()
    print(
        f"Event pool {len(pool.qids)} QIDs; {len(pool.answer_candidates)} answer-pool candidates "
        f"(sitelinks >= {ANSWER_POOL_MIN_SITELINKS}).",
        flush=True,
    )

    print(
        f"Job A — redirects for {len(pool.answer_candidates)} titles in "
        f"{len(batched(pool.answer_candidates, REDIRECT_BATCH_SIZE))} batches of up to "
        f"{REDIRECT_BATCH_SIZE}...",
        flush=True,
    )
    aliases, redirect_stats = collect_redirects(pool)

    print(
        f"Job B — P625 for {len(pool.qids)} QIDs in "
        f"{len(batched(pool.qids, COORD_CHUNK_SIZE))} chunks of up to {COORD_CHUNK_SIZE}...",
        flush=True,
    )
    coords, coord_stats = collect_coordinates(pool)

    records = merge_records(pool, aliases, coords)
    assert_coordinates_are_on_earth(records)
    write_augmented(records, pool, redirect_stats, coord_stats)
    print_report(pool, records, redirect_stats, coord_stats)

    if not any(record["redirect_aliases"] or record["lat"] is not None for record in records.values()):
        print("ERROR: no QID gained an alias or a coordinate; expected thousands.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
