"""Enrich the harvested candidate QIDs with the Wikidata facts that make typing possible.

Second stage of the histle dataset pipeline. It reads `data/raw/candidates.json`, asks
Wikidata's public SPARQL endpoint about every QID in it, and writes
`data/raw/enriched.json` — one record per QID carrying an English label, the item's own
event date (P585 point in time, falling back to P580 start time), the countries it
happened in and their continents, English aliases, sitelink count, and the instance-of
(P31) classes.

Why this stage exists. The harvest emits one candidate per page linked from an "On This
Day" blurb, and most of those pages are not the event: they are Bach, or NASA, or "4th
millennium BC", or "Kali Yuga" — the people, places and umbrellas a blurb name-checks
while describing something that happened. Nothing in the feed distinguishes them from the
event itself, and position in the link list does not either, so the fix has to come from
the items themselves.
The two fields that carry the signal are P31 (a person is `instance of: human`, an event
is `instance of: battle / treaty / disaster`) and the presence of an event date at all
(entities that merely *participated* in history usually have neither P585 nor P580).
This stage only *fetches* that evidence and reports what a filter on it would do; the
decision to drop anything belongs to the curation stage downstream.

Boundary. Input is `data/raw/candidates.json` plus the network; output is the per-chunk
cache under `data/raw/wikidata/` and the merged `data/raw/enriched.json`. No filtering,
no scoring, no category assignment happens here — every queried QID appears in the output,
including the ones Wikidata knows nothing about, so the counts stay honest.

Gotchas worth knowing before editing:

- Chunking is deterministic *for a given candidates.json*: QIDs are deduplicated, sorted
  by their numeric part, then sliced into fixed-size chunks, so chunk 0037 holds the same
  200 QIDs on every run and the cache can be resumed. A changed candidates.json reshuffles
  every chunk boundary and silently invalidates the cache. The remedy is deliberately
  crude: delete `data/raw/wikidata/` and re-run. A cache file whose stored QID list does
  not match the recomputed chunk raises rather than being quietly reused.
- WDQS punishes one big clever query far more than three small dull ones. GROUP_CONCAT and
  the `wikibase:label` SERVICE together make the optimizer produce plans that hit the 60s
  ceiling, so labels here come from explicit `rdfs:label` / `skos:altLabel` patterns with
  `FILTER(LANG(...)="en")`, and each chunk is asked four separate questions. Measured on a
  200-QID chunk: P31 alone 2.1s plus country/continent alone 4.1s, against 9.4s for the
  two joined into one query — splitting is both faster and immune to the P31 x country
  cross product.
- The endpoint's `wikibase:timeValue` is an `xsd:dateTime`, which uses ISO 8601 year
  numbering where year 0 exists and equals 1 BC. Wikipedia's years are historian's years
  with no year 0. So every BCE date here reads one year later than the harvest's: the
  Battle of Marathon is `-0489` to SPARQL and 490 BC to the feed. The harvest-vs-Wikidata
  cross-check in the report therefore flags only differences *greater than one year*, and
  a downstream consumer of `date_year` must decide which convention it wants.
- Time precision is an int on Wikidata's scale (11 = day, 10 = month, 9 = year, 8 = decade,
  7 = century, 6 = millennium, and coarser). The candidate set really does contain
  millennium- and century-precision items — that is what "4th millennium BC" is — so a
  consumer must not assume a usable day.
- An item can carry several P585 values (Marathon has three, one per competing scholarly
  date). The earliest is kept, which is arbitrary but deterministic; the alternatives are
  not preserved.
- All list fields are sorted alphabetically before being stored. SPARQL result order is
  not guaranteed, so alphabetical is the only order that survives a re-run unchanged.
- A QID that no longer exists on Wikidata still comes back as a row, because every pattern
  in the core query is OPTIONAL and `VALUES` binds the subject regardless. Presence in the
  result set therefore proves nothing; an item counts as "returned" only if it yielded at
  least one actual fact.
- Windows default encoding is not UTF-8 and these labels are full of non-ASCII names, so
  every `open()` here passes `encoding="utf-8"` and `main` reconfigures stdout.
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

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
USER_AGENT = (
    "histle-dataset-builder/0.1 "
    "(https://github.com/arda-basarici/histle; ardabasarici@gmail.com)"
)

CHUNK_SIZE = 200
REQUEST_TIMEOUT_SECONDS = 90  # above WDQS's own 60s ceiling, so its timeout surfaces as a body
QUERY_SPACING_SECONDS = 1.0
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (5.0, 20.0)
PROGRESS_EVERY_CHUNKS = 5

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_PATH = REPO_ROOT / "data" / "raw" / "candidates.json"
CACHE_DIR = REPO_ROOT / "data" / "raw" / "wikidata"
ENRICHED_PATH = REPO_ROOT / "data" / "raw" / "enriched.json"

QID_PATTERN = re.compile(r"^Q\d+$")
TIME_YEAR_PATTERN = re.compile(r"^([+-]?)(\d+)-")

SITELINKS_ABOVE = 50
# Only for picking a readable war sample in the report; nothing downstream depends on it,
# and it is deliberately not a taxonomy — the curation stage owns that.
CONFLICT_CLASSES = frozenset({"war", "battle", "armed conflict", "military operation"})
TOP_CLASSES = 15
YEAR_DISAGREEMENT_TOLERANCE = 1  # absorbs the BCE year-zero offset described in the docstring

# Four dull queries beat one clever one; see the module docstring. Each takes a
# space-separated block of `wd:Q...` terms as its single format argument.
CORE_QUERY = """
SELECT ?item ?label ?sitelinks ?prop ?time ?prec WHERE {
  VALUES ?item { %s }
  OPTIONAL { ?item rdfs:label ?label . FILTER(LANG(?label) = "en") }
  OPTIONAL { ?item wikibase:sitelinks ?sitelinks }
  OPTIONAL {
    VALUES (?p ?psv ?prop) { (p:P585 psv:P585 "P585") (p:P580 psv:P580 "P580") }
    ?item ?p ?statement .
    ?statement ?psv ?timevalue .
    ?timevalue wikibase:timeValue ?time ; wikibase:timePrecision ?prec .
  }
}"""

ALIAS_QUERY = """
SELECT ?item ?alias WHERE {
  VALUES ?item { %s }
  ?item skos:altLabel ?alias . FILTER(LANG(?alias) = "en")
}"""

TYPE_QUERY = """
SELECT ?item ?p31Label WHERE {
  VALUES ?item { %s }
  ?item wdt:P31 ?p31 .
  ?p31 rdfs:label ?p31Label . FILTER(LANG(?p31Label) = "en")
}"""

PLACE_QUERY = """
SELECT ?item ?countryLabel ?continentLabel WHERE {
  VALUES ?item { %s }
  ?item wdt:P17 ?country .
  ?country rdfs:label ?countryLabel . FILTER(LANG(?countryLabel) = "en")
  OPTIONAL {
    ?country wdt:P30 ?continent .
    ?continent rdfs:label ?continentLabel . FILTER(LANG(?continentLabel) = "en")
  }
}"""


@dataclass
class EnrichStats:
    """Tally of how the enrichment pass spent its queries, for the acceptance report."""

    chunks_total: int = 0
    chunks_fetched: int = 0
    chunks_cached: int = 0
    failed_chunks: list[int] = field(default_factory=list)
    queries_sent: int = 0
    queries_retried: int = 0

    @property
    def chunks_failed(self) -> int:
        return len(self.failed_chunks)


@dataclass
class Accumulator:
    """Mutable per-chunk gathering point, one instance per QID.

    SPARQL hands back one flat row per fact, with an item's label repeated on every row it
    appears in, so the rows have to be folded back into per-item shape before anything can
    be decided. Sets here, sorted lists at the end — a set both deduplicates the repeats
    and refuses to encode the endpoint's arbitrary row order as meaning.
    """

    label: str | None = None
    sitelinks: int | None = None
    dates: list[tuple[str, int]] = field(default_factory=list)
    countries: set[str] = field(default_factory=set)
    continents: set[str] = field(default_factory=set)
    aliases: set[str] = field(default_factory=set)
    instance_of: set[str] = field(default_factory=set)


@dataclass
class Candidates:
    """What this stage needs from the harvest: the QIDs to query, the year each was
    harvested with (for the cross-check), and which ones the feed flagged `selected` (for
    the filter verdict). Bundled so the 4.5 MB candidate file is parsed exactly once."""

    qids: list[str]
    harvest_years: dict[str, int]
    selected: set[str]


def load_candidates() -> Candidates:
    """The harvest's key set, read rather than re-derived. QIDs come back sorted by their
    numeric part, which is what makes chunking deterministic. A malformed QID is refused
    here rather than left to become a mystery 400 from WDQS."""
    if not CANDIDATES_PATH.is_file():
        raise RuntimeError(
            f"no candidate file at {CANDIDATES_PATH}; "
            "expected the harvest stage to have written it — run pipeline/harvest.py first"
        )

    with CANDIDATES_PATH.open(encoding="utf-8") as handle:
        document = json.load(handle)

    events = document.get("events")
    if not isinstance(events, list) or not events:
        raise RuntimeError(
            f"{CANDIDATES_PATH} has no non-empty 'events' list; "
            f"expected thousands of candidate records, found keys: {sorted(document)}"
        )

    harvest_years: dict[str, int] = {}
    selected: set[str] = set()
    for event in events:
        qid = event.get("qid", "")
        if not QID_PATTERN.match(qid):
            raise RuntimeError(
                f"candidate record carries qid {qid!r}; expected a Wikidata QID matching ^Q\\d+$"
            )
        harvest_years.setdefault(qid, event["year"])
        if event.get("selected"):
            selected.add(qid)

    return Candidates(
        qids=sorted(harvest_years, key=lambda qid: int(qid[1:])),
        harvest_years=harvest_years,
        selected=selected,
    )


def chunk_qids(qids: list[str]) -> list[list[str]]:
    """The QID list cut into query-sized slices. Deterministic given the same input list,
    which is what makes the on-disk cache resumable — see the docstring's caveat about
    what happens when candidates.json changes underneath it."""
    return [qids[start : start + CHUNK_SIZE] for start in range(0, len(qids), CHUNK_SIZE)]


def values_block(qids: Iterable[str]) -> str:
    """The QIDs as a SPARQL `VALUES` body."""
    return " ".join(f"wd:{qid}" for qid in qids)


def qid_of(binding: dict[str, Any]) -> str:
    """The bare QID from a result row's `?item` entity URI."""
    return binding["item"]["value"].rsplit("/", 1)[-1]


def parse_time_year(raw: str) -> int | None:
    """The signed year from a Wikidata time string such as `-0489-08-07T00:00:00Z`, or
    None if the string does not start with one. Written as a regex over an arbitrary run
    of digits rather than a date parse because the values are neither guaranteed to be
    four-digit nor guaranteed to be real calendar dates — month and day are zero for
    coarse precisions, which every stdlib date parser rejects."""
    match = TIME_YEAR_PATTERN.match(raw)
    if match is None:
        return None
    sign, digits = match.groups()
    year = int(digits)
    return -year if sign == "-" else year


def earliest_date(dates: list[tuple[str, int]]) -> tuple[str, int] | None:
    """The earliest of an item's date values, or None if it has none. Items with rival
    scholarly datings carry several P585 values; picking the earliest is arbitrary but
    reproducible, and the raw string breaks ties so the choice cannot drift between runs."""
    if not dates:
        return None
    return min(dates, key=lambda entry: (parse_time_year(entry[0]) or 0, entry[0]))


def build_session() -> requests.Session:
    """A session carrying the identifying User-Agent WDQS asks anonymous clients for, and
    the Accept header that gets JSON rather than the HTML result browser."""
    session = requests.Session()
    session.headers.update(
        {"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"}
    )
    return session


def run_query(session: requests.Session, query: str, label: str, stats: EnrichStats) -> list[dict[str, Any]]:
    """The result rows of one SPARQL query, retrying transient failures with a backoff.
    WDQS routinely answers a perfectly valid query with 429 or 500 under load, so a single
    failure means nothing; three mean the chunk is not going to work today. Raises
    RuntimeError naming the query and the last error, and the caller decides that one dead
    chunk should not end the run. POST rather than GET because a 200-QID `VALUES` block
    overruns what the endpoint accepts in a URL."""
    last_error: Exception | str | None = None

    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            stats.queries_retried += 1
            time.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
        try:
            stats.queries_sent += 1
            response = session.post(
                SPARQL_ENDPOINT, data={"query": query}, timeout=REQUEST_TIMEOUT_SECONDS
            )
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                continue
            return response.json()["results"]["bindings"]
        except (requests.RequestException, json.JSONDecodeError, KeyError) as error:
            last_error = error

    raise RuntimeError(
        f"{label} query failed after {MAX_ATTEMPTS} attempts; "
        f"expected HTTP 200 with SPARQL JSON results from {SPARQL_ENDPOINT}; "
        f"last error: {last_error}"
    )


def absorb_core(rows: list[dict[str, Any]], accumulators: dict[str, Accumulator]) -> None:
    """Fold label, sitelink count and date rows into the accumulators. One row per date
    value, with label and sitelinks repeated across them, so the scalars are assigned
    idempotently and only the dates append."""
    for row in rows:
        item = accumulators[qid_of(row)]
        if "label" in row:
            item.label = row["label"]["value"]
        if "sitelinks" in row:
            item.sitelinks = int(row["sitelinks"]["value"])
        if "time" in row:
            item.dates.append((row["time"]["value"], int(row["prec"]["value"])))


def absorb_aliases(rows: list[dict[str, Any]], accumulators: dict[str, Accumulator]) -> None:
    """Fold English alias rows into the accumulators."""
    for row in rows:
        accumulators[qid_of(row)].aliases.add(row["alias"]["value"])


def absorb_types(rows: list[dict[str, Any]], accumulators: dict[str, Accumulator]) -> None:
    """Fold instance-of class labels into the accumulators — the typing evidence this whole
    stage exists to collect."""
    for row in rows:
        accumulators[qid_of(row)].instance_of.add(row["p31Label"]["value"])


def absorb_places(rows: list[dict[str, Any]], accumulators: dict[str, Accumulator]) -> None:
    """Fold country and continent labels into the accumulators. Continents arrive attached
    to a country and are flattened into their own set: a country can sit on two continents
    (Turkey, Russia) and an event can span several countries, so the two lists describe the
    same event at different granularities rather than lining up element by element."""
    for row in rows:
        item = accumulators[qid_of(row)]
        item.countries.add(row["countryLabel"]["value"])
        if "continentLabel" in row:
            item.continents.add(row["continentLabel"]["value"])


def finalize(accumulator: Accumulator) -> dict[str, Any]:
    """One accumulator rendered as the output record. Lists are sorted here, once, so the
    written file is byte-stable across runs."""
    chosen = earliest_date(accumulator.dates)
    return {
        "label": accumulator.label,
        "date_raw": chosen[0] if chosen else None,
        "date_precision": chosen[1] if chosen else None,
        "date_year": parse_time_year(chosen[0]) if chosen else None,
        "has_event_date": chosen is not None,
        "countries": sorted(accumulator.countries),
        "continents": sorted(accumulator.continents),
        "aliases": sorted(accumulator.aliases),
        "instance_of": sorted(accumulator.instance_of),
        "sitelinks": accumulator.sitelinks,
    }


def empty_record() -> dict[str, Any]:
    """The record for a QID nothing is known about — an item Wikidata has dropped, or one
    whose chunk failed. Present in the output rather than absent, so downstream counts of
    "how many candidates do we have" never disagree with the harvest."""
    return finalize(Accumulator())


def fetch_chunk(session: requests.Session, qids: list[str], stats: EnrichStats) -> dict[str, Any]:
    """Every fact this stage wants about one chunk of QIDs, as finished records. The only
    function that touches the network. Four queries, spaced, in a fixed order; any of them
    raising aborts the whole chunk, because a chunk missing its P31 rows would look like a
    chunk of untyped items and quietly poison the filter verdict."""
    accumulators = {qid: Accumulator() for qid in qids}
    block = values_block(qids)

    for query, query_label, absorb in (
        (CORE_QUERY, "core", absorb_core),
        (ALIAS_QUERY, "alias", absorb_aliases),
        (TYPE_QUERY, "type", absorb_types),
        (PLACE_QUERY, "place", absorb_places),
    ):
        rows = run_query(session, query % block, query_label, stats)
        absorb(rows, accumulators)
        time.sleep(QUERY_SPACING_SECONDS)

    return {qid: finalize(accumulator) for qid, accumulator in accumulators.items()}


def cache_path(index: int) -> Path:
    """Where one chunk's parsed result lives."""
    return CACHE_DIR / f"chunk-{index:04d}.json"


def read_cached_chunk(index: int, qids: list[str]) -> dict[str, Any] | None:
    """The saved records for a chunk, or None if it must be fetched. A truncated file from
    an interrupted run reads as absent — re-querying is cheap, half a JSON document is not
    trustworthy. A file whose QID list has drifted from the recomputed chunk raises: that
    means candidates.json changed and the entire cache is now misaligned, which is worth
    stopping for rather than silently mixing two datasets."""
    path = cache_path(index)
    if not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
    except json.JSONDecodeError:
        return None

    if document.get("qids") != qids:
        raise RuntimeError(
            f"cached {path.name} holds a different QID set than chunk {index} now resolves to; "
            "expected candidates.json to be unchanged since the cache was written — "
            f"delete {CACHE_DIR} and re-run"
        )

    items = document.get("items")
    if not items:
        return None
    return items


def write_cached_chunk(index: int, qids: list[str], items: dict[str, Any]) -> None:
    """Persist one chunk's records, carrying the QID list that produced them so a later run
    can prove the cache still matches the candidate set."""
    with cache_path(index).open("w", encoding="utf-8") as handle:
        json.dump({"index": index, "qids": qids, "items": items}, handle, ensure_ascii=False)


def enrich_all(qids: list[str]) -> tuple[dict[str, dict[str, Any]], EnrichStats]:
    """Walk every chunk, from cache where possible and from WDQS otherwise, and return one
    record per queried QID. A chunk that fails all its retries is recorded, filled with
    empty records and stepped over — an hour of successful work must not be lost to one
    bad chunk, and a re-run will pick it up from where the cache stops."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    chunks = chunk_qids(qids)
    stats = EnrichStats(chunks_total=len(chunks))
    session = build_session()
    items: dict[str, dict[str, Any]] = {}

    for index, chunk in enumerate(chunks):
        cached = read_cached_chunk(index, chunk)
        if cached is not None:
            items.update(cached)
            stats.chunks_cached += 1
            continue

        try:
            fetched = fetch_chunk(session, chunk, stats)
        except RuntimeError as error:
            print(f"  FAILED chunk {index:04d}: {error}", flush=True)
            stats.failed_chunks.append(index)
            items.update({qid: empty_record() for qid in chunk})
            continue

        write_cached_chunk(index, chunk, fetched)
        items.update(fetched)
        stats.chunks_fetched += 1

        if stats.chunks_fetched % PROGRESS_EVERY_CHUNKS == 0:
            dated = sum(1 for record in items.values() if record["has_event_date"])
            print(
                f"  chunk {index:04d}/{len(chunks) - 1} | "
                f"{stats.chunks_fetched} fetched, {stats.chunks_cached} cached, "
                f"{stats.chunks_failed} failed | {len(items)} items, {dated} dated",
                flush=True,
            )

    return items, stats


def write_enriched(items: dict[str, dict[str, Any]], stats: EnrichStats) -> None:
    """Persist the enriched records with the counts that produced them, so the file carries
    its own provenance and a reader need not re-run 300-odd queries to know what is missing."""
    ENRICHED_PATH.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "generated_from": "wikidata enrichment of candidates.json",
        "counts": {
            "chunks_total": stats.chunks_total,
            "chunks_fetched": stats.chunks_fetched,
            "chunks_cached": stats.chunks_cached,
            "chunks_failed": stats.chunks_failed,
            "failed_chunk_indices": stats.failed_chunks,
            "queries_sent": stats.queries_sent,
            "queries_retried": stats.queries_retried,
            "qids_queried": len(items),
            "qids_returned": sum(1 for record in items.values() if not is_blank(record)),
            "qids_missing": sum(1 for record in items.values() if is_blank(record)),
            "with_event_date": sum(1 for record in items.values() if record["has_event_date"]),
        },
        "items": items,
    }
    with ENRICHED_PATH.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1)


def is_blank(record: dict[str, Any]) -> bool:
    """Whether Wikidata returned nothing about this QID. Checked over the finished record
    rather than the accumulator so it also covers records restored from the cache."""
    return not (
        record["label"]
        or record["sitelinks"] is not None
        or record["has_event_date"]
        or record["countries"]
        or record["aliases"]
        or record["instance_of"]
    )


def percent(count: int, total: int) -> str:
    """A share formatted for the report, safe when the denominator is zero."""
    if total == 0:
        return "  n/a"
    return f"{100 * count / total:5.1f}%"


def quantile(sorted_values: list[int], fraction: float) -> int:
    """The value at a fraction of the way through an already-sorted list, by nearest rank.
    Hand-rolled because this stage takes no dependency beyond `requests`, and a nearest-rank
    quantile on 16k integers needs no more than an index."""
    if not sorted_values:
        raise RuntimeError("quantile of an empty list; expected at least one value")
    index = min(len(sorted_values) - 1, int(fraction * len(sorted_values)))
    return sorted_values[index]


def class_counts(items: dict[str, dict[str, Any]], has_date: bool) -> Counter[str]:
    """How often each P31 class appears on one side of the has-event-date split. Each item
    contributes at most once per distinct class, so the numbers read as "items of this
    class" rather than "statements"."""
    counter: Counter[str] = Counter()
    for record in items.values():
        if record["has_event_date"] is has_date:
            counter.update(set(record["instance_of"]))
    return counter


def year_disagreements(
    items: dict[str, dict[str, Any]], harvest_years: dict[str, int]
) -> list[tuple[int, str, int, int, str]]:
    """Every QID whose Wikidata year is further than the tolerance from the harvested year,
    worst first, as (gap, qid, harvest year, wikidata year, label). This is the honesty
    check on the harvest: the feed's year is scraped prose, and a large gap usually means
    the blurb's canonical page is a different thing than the blurb's event."""
    gaps: list[tuple[int, str, int, int, str]] = []
    for qid, record in items.items():
        wikidata_year = record["date_year"]
        harvest_year = harvest_years.get(qid)
        if wikidata_year is None or harvest_year is None:
            continue
        gap = abs(wikidata_year - harvest_year)
        if gap > YEAR_DISAGREEMENT_TOLERANCE:
            gaps.append((gap, qid, harvest_year, wikidata_year, record["label"] or "(no label)"))
    return sorted(gaps, reverse=True)


def sample_records(items: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
    """Five records worth reading with your own eyes, each with the reason it was picked:
    a war, an item typed as a person, a BCE item, an item with no event date at all, and a
    high-sitelink item. Chosen by scanning QIDs in order rather than at random, so two runs
    over the same data print the same five and a diff means the data moved."""
    wanted: list[tuple[str, Any]] = [
        ("war", lambda record: bool(CONFLICT_CLASSES & set(record["instance_of"]))),
        ("person-typed", lambda record: "human" in record["instance_of"]),
        ("BCE", lambda record: record["date_year"] is not None and record["date_year"] < 0),
        ("no event date", lambda record: not record["has_event_date"] and bool(record["label"])),
        ("high sitelinks", lambda record: (record["sitelinks"] or 0) > 150),
    ]

    chosen: list[tuple[str, str]] = []
    taken: set[str] = set()
    for reason, matches in wanted:
        for qid in sorted(items, key=lambda key: int(key[1:])):
            if qid in taken or not matches(items[qid]):
                continue
            chosen.append((reason, qid))
            taken.add(qid)
            break
    return chosen


def print_sample(reason: str, qid: str, record: dict[str, Any], harvest_year: int | None) -> None:
    """One enriched record laid out for reading, fields on their own lines."""
    print(f"  [{reason}] {qid} — {record['label'] or '(no English label)'}")
    print(
        f"      date        : {record['date_raw']} (precision {record['date_precision']}, "
        f"year {record['date_year']}, has_event_date={record['has_event_date']}); "
        f"harvest year {harvest_year}"
    )
    print(f"      instance_of : {', '.join(record['instance_of']) or '—'}")
    print(f"      countries   : {', '.join(record['countries']) or '—'}")
    print(f"      continents  : {', '.join(record['continents']) or '—'}")
    aliases = record["aliases"]
    shown = ", ".join(aliases[:6]) + (f" (+{len(aliases) - 6} more)" if len(aliases) > 6 else "")
    print(f"      aliases     : {shown or '—'}")
    print(f"      sitelinks   : {record['sitelinks']}")


def print_report(
    items: dict[str, dict[str, Any]], stats: EnrichStats, candidates: Candidates
) -> None:
    """The acceptance evidence: what was queried, how well each field is covered, what a
    has-event-date filter would keep and drop, whether the harvest's years hold up, and
    five records to read. Numbers first — the filter verdict is the question this run was
    launched to answer."""
    total = len(items)
    returned = [record for record in items.values() if not is_blank(record)]
    dated = [record for record in items.values() if record["has_event_date"]]
    alias_counts = [len(record["aliases"]) for record in items.values()]
    sitelinks = sorted(
        record["sitelinks"] for record in items.values() if record["sitelinks"] is not None
    )

    print("\n" + "=" * 78)
    print("ENRICH REPORT — candidates x Wikidata")
    print("=" * 78)

    print("\n1. RUN")
    print(f"  chunks total        : {stats.chunks_total}")
    print(f"  chunks fetched      : {stats.chunks_fetched}")
    print(f"  chunks from cache   : {stats.chunks_cached}")
    print(f"  chunks failed       : {stats.chunks_failed} {stats.failed_chunks or ''}")
    print(f"  queries sent        : {stats.queries_sent} ({stats.queries_retried} were retries)")
    print(f"  QIDs queried        : {total}")
    print(f"  QIDs returned       : {len(returned)}  {percent(len(returned), total)}")
    print(f"  QIDs missing        : {total - len(returned)}  {percent(total - len(returned), total)}")

    print("\n2. FIELD COVERAGE (share of all queried QIDs)")
    coverage = (
        ("label", sum(1 for record in items.values() if record["label"])),
        ("event date (P585|P580)", len(dated)),
        ("country", sum(1 for record in items.values() if record["countries"])),
        ("continent", sum(1 for record in items.values() if record["continents"])),
        ("aliases", sum(1 for record in items.values() if record["aliases"])),
        ("instance_of (P31)", sum(1 for record in items.values() if record["instance_of"])),
        ("sitelinks", sum(1 for record in items.values() if record["sitelinks"] is not None)),
    )
    for name, count in coverage:
        print(f"  {name:<24} {count:>6}  {percent(count, total)}")
    print(f"  {'aliases: mean count':<24} {sum(alias_counts) / total:>6.2f}")

    print("\n3. FILTER VERDICT — what keeping only has_event_date would do")
    print(f"  overall   : {len(dated):>6} / {total} kept  {percent(len(dated), total)}")
    selected_total = sum(1 for qid in items if qid in candidates.selected)
    selected_dated = sum(
        1
        for qid, record in items.items()
        if qid in candidates.selected and record["has_event_date"]
    )
    print(
        f"  selected  : {selected_dated:>6} / {selected_total} kept  "
        f"{percent(selected_dated, selected_total)}"
    )

    kept_classes = class_counts(items, has_date=True)
    dropped_classes = class_counts(items, has_date=False)
    print(f"\n  top {TOP_CLASSES} P31 classes KEPT (has_event_date = true):")
    for name, count in kept_classes.most_common(TOP_CLASSES):
        print(f"      {name:<40} {count:>6}  {percent(count, len(dated))}")
    print(f"\n  top {TOP_CLASSES} P31 classes DROPPED (has_event_date = false):")
    for name, count in dropped_classes.most_common(TOP_CLASSES):
        print(f"      {name:<40} {count:>6}  {percent(count, total - len(dated))}")

    print("\n4. CROSS-CHECK — harvest year vs Wikidata year")
    comparable = sum(
        1
        for qid, record in items.items()
        if record["date_year"] is not None and qid in candidates.harvest_years
    )
    gaps = year_disagreements(items, candidates.harvest_years)
    print(f"  comparable items    : {comparable}")
    print(
        f"  differ by > {YEAR_DISAGREEMENT_TOLERANCE} year : {len(gaps)}  "
        f"{percent(len(gaps), comparable)}"
    )
    for gap, qid, harvest_year, wikidata_year, label in gaps[:5]:
        print(f"      {gap:>6}y  {qid:<12} harvest {harvest_year:>6} vs wikidata {wikidata_year:>6}  {label}")

    print("\n5. SITELINKS")
    if sitelinks:
        print(f"  median              : {quantile(sitelinks, 0.5)}")
        print(f"  p90                 : {quantile(sitelinks, 0.9)}")
        print(f"  max                 : {sitelinks[-1]}")
        above = sum(1 for value in sitelinks if value > SITELINKS_ABOVE)
        print(f"  above {SITELINKS_ABOVE:<3}           : {above}  {percent(above, len(sitelinks))}")
    else:
        print("  (no sitelink values)")

    print("\n6. SAMPLES")
    for reason, qid in sample_records(items):
        print_sample(reason, qid, items[qid], candidates.harvest_years.get(qid))

    print(f"\nWROTE {ENRICHED_PATH}")
    print("=" * 78, flush=True)


def main() -> int:
    """Load, enrich, persist, report. Returns non-zero when no QID came back enriched,
    which means the run is not evidence of anything."""
    # Labels carry accents and non-Latin scripts; a redirected stdout on Windows defaults
    # to the ANSI codepage and would raise UnicodeEncodeError on them.
    sys.stdout.reconfigure(encoding="utf-8")

    candidates = load_candidates()
    chunk_count = len(chunk_qids(candidates.qids))
    print(
        f"Enriching {len(candidates.qids)} QIDs from Wikidata in {chunk_count} chunks "
        f"of up to {CHUNK_SIZE} (4 queries each)...",
        flush=True,
    )

    items, stats = enrich_all(candidates.qids)
    write_enriched(items, stats)
    print_report(items, stats, candidates)

    if not any(not is_blank(record) for record in items.values()):
        print("ERROR: every QID came back empty; expected thousands enriched.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
