"""Harvest Wikipedia's "On This Day" feed into a deduplicated candidate event list.

This is the first stage of the histle dataset pipeline: it turns 732 calendar-day API
responses (366 days x two feed types) into `data/raw/candidates.json`, one record per
distinct historical event, keyed by Wikidata QID. Everything downstream — the Wikidata
enrichment join, the curation pass, the shipped `data/events.json` — reads that QID, so
this stage's only real job is to produce a clean, honest, regenerable key set.

Boundary. Input is the network (`api.wikimedia.org`, anonymous, no key); output is two
things on disk: the untouched per-day JSON under `data/raw/onthisday/`, and the merged
candidate list. Nothing here interprets an event's meaning — no categories, no regions,
no fame scoring. Those need Wikidata and a human, and live in later stages. The raw
files are the cache: `data/raw/` is git-ignored precisely because this script can
rebuild it, and the resume check makes a re-run cost nothing for days already on disk.

Gotchas worth knowing before editing:

- The response's top-level key equals the requested feed type ("events" / "selected"),
  not a generic name; a payload missing that key is a shape change and raises rather
  than quietly yielding zero events.
- `pages[0]` is treated as the canonical entity, per the feed's own ordering (the first
  link is the one the blurb is about). It is not always the *event*: for "selected"
  entries the lead link is often the person or country involved (a 2024 UK election
  entry canonicalizes to Keir Starmer). That is a curation problem, not a harvest one —
  this stage records what the feed says and lets the human pass fix it.
- `page["title"]` is the underscored URL form ("Keir_Starmer"). Kept as-is because it is
  the stable page key; `normalizedtitle` holds the display form if a later stage wants
  it, and the Wikidata join will supply proper labels anyway.
- Years are ints and may be negative (BCE). Era bucketing therefore cannot assume
  positivity, and any "year missing" test must distinguish `None` from a falsy `0`.
- Windows default encoding is not UTF-8 and these payloads are full of non-ASCII names,
  so every `open()` here passes `encoding="utf-8"` explicitly.
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import requests

API_TEMPLATE = "https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/{feed}/{mm}/{dd}"
USER_AGENT = (
    "histle-dataset-builder/0.1 "
    "(https://github.com/arda-basarici/histle; ardabasarici@gmail.com)"
)
FEED_TYPES = ("events", "selected")

REQUEST_TIMEOUT_SECONDS = 30
REQUEST_SPACING_SECONDS = 0.15
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (1.0, 3.0)
PROGRESS_EVERY_DAYS = 30

DAYS_IN_MONTH = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)  # Feb 29 always included

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw" / "onthisday"
CANDIDATES_PATH = REPO_ROOT / "data" / "raw" / "candidates.json"

ERA_BUCKETS = (
    ("pre-0", None, 0),
    ("0-1000", 0, 1000),
    ("1000-1500", 1000, 1500),
    ("1500-1800", 1500, 1800),
    ("1800-1900", 1800, 1900),
    ("1900-2000", 1900, 2000),
    ("2000+", 2000, None),
)


@dataclass
class FetchStats:
    """Tally of how the fetch pass spent its time, for the acceptance report."""

    requested: int = 0
    resumed: int = 0
    failed_days: list[str] = field(default_factory=list)


@dataclass
class MergeStats:
    """Tally of what the merge pass saw, kept, and threw away — and why."""

    raw_entries: int = 0
    dropped: Counter[str] = field(default_factory=Counter)
    duplicate_qids: int = 0

    @property
    def total_dropped(self) -> int:
        return sum(self.dropped.values())


def iter_calendar_days() -> Iterator[tuple[str, str]]:
    """Every (mm, dd) of a leap year as zero-padded strings, January 1 through
    December 31. Feb 29 is included deliberately: the feed serves it, and a game that
    skipped it would have a hole every four years."""
    for month_index, day_count in enumerate(DAYS_IN_MONTH, start=1):
        for day in range(1, day_count + 1):
            yield f"{month_index:02d}", f"{day:02d}"


def raw_path(feed: str, mm: str, dd: str) -> Path:
    """Where one day's raw response lives. Flat, one file per feed-day, so a partial
    run is inspectable and resumable by nothing more than an `ls`."""
    return RAW_DIR / f"{feed}-{mm}-{dd}.json"


def read_cached_payload(path: Path) -> dict[str, Any] | None:
    """The saved payload if the file is present, non-empty and parses; otherwise None,
    which the caller reads as "fetch it". A truncated file from an interrupted run is
    treated as absent rather than as an error — re-fetching is cheap, and silently
    trusting half a JSON document is not."""
    if not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError:
        return None


def fetch_payload(session: requests.Session, feed: str, mm: str, dd: str) -> dict[str, Any]:
    """One day's feed from the API, retrying transient failures with a short backoff.
    Raises RuntimeError naming the day and the last error once the attempts are spent —
    the caller decides that one bad day should not end the run."""
    url = API_TEMPLATE.format(feed=feed, mm=mm, dd=dd)
    last_error: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            time.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, json.JSONDecodeError) as error:
            last_error = error

    raise RuntimeError(
        f"fetching {feed} {mm}-{dd} failed after {MAX_ATTEMPTS} attempts; "
        f"expected HTTP 200 with a JSON body from {url}; last error: {last_error}"
    )


def harvest_raw_days() -> FetchStats:
    """Fetch-and-save pass over the whole calendar, both feed types, skipping whatever
    is already on disk. Effects live here: this is the only function that touches the
    network. Failures are recorded and stepped over, never raised, so a flaky day costs
    one day rather than the run."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stats = FetchStats()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    for day_number, (mm, dd) in enumerate(iter_calendar_days(), start=1):
        for feed in FEED_TYPES:
            path = raw_path(feed, mm, dd)
            if read_cached_payload(path) is not None:
                stats.resumed += 1
                continue
            try:
                payload = fetch_payload(session, feed, mm, dd)
            except RuntimeError as error:
                print(f"  FAILED {feed} {mm}-{dd}: {error}", flush=True)
                stats.failed_days.append(f"{feed} {mm}-{dd}")
                continue
            with path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            stats.requested += 1
            time.sleep(REQUEST_SPACING_SECONDS)

        if day_number % PROGRESS_EVERY_DAYS == 0:
            print(
                f"  {mm}-{dd}: {day_number}/366 days | "
                f"{stats.requested} fetched, {stats.resumed} resumed, "
                f"{len(stats.failed_days)} failed",
                flush=True,
            )

    return stats


def entries_of(payload: dict[str, Any], feed: str, mm: str, dd: str) -> list[dict[str, Any]]:
    """The event entries inside one day's payload. The list is stored under a key equal
    to the feed type; its absence means the API's shape moved and is worth stopping
    for, since every count downstream would otherwise be quietly wrong."""
    entries = payload.get(feed)
    if entries is None:
        raise RuntimeError(
            f"payload for {feed} {mm}-{dd} has no '{feed}' key; "
            f"expected the entry list under it, found keys: {sorted(payload)}"
        )
    if not isinstance(entries, list):
        raise RuntimeError(
            f"payload for {feed} {mm}-{dd} stores '{feed}' as {type(entries).__name__}; "
            "expected a list of entries"
        )
    return entries


def extract_record(entry: dict[str, Any], feed: str, mm: str, dd: str) -> dict[str, Any] | str:
    """One entry reduced to the fields histle needs, or a drop-reason string if it
    cannot be reduced. The canonical entity is `pages[0]` — the feed orders the blurb's
    own subject first — and its QID is the record's identity."""
    year = entry.get("year")
    if year is None:
        return "no_year"

    pages = entry.get("pages") or []
    if not pages:
        return "no_pages"

    page = pages[0]
    qid = page.get("wikibase_item")
    if not qid:
        return "no_wikibase_item"

    return {
        "qid": qid,
        "title": page.get("title") or "",
        "year": year,
        "text": entry.get("text") or "",
        "selected": feed == "selected",
        "month": mm,
        "day": dd,
    }


def validation_error(record: dict[str, Any]) -> str | None:
    """Why this record must not enter the dataset, or None if it is sound. Checked at
    the boundary rather than trusted from the API: a bad year type or a malformed QID
    would survive all the way into the shipped game as a broken puzzle."""
    qid = record["qid"]
    if not (isinstance(qid, str) and qid.startswith("Q") and qid[1:].isdigit() and len(qid) > 1):
        return "bad_qid_format"
    if not isinstance(record["year"], int) or isinstance(record["year"], bool):
        return "year_not_int"
    if not record["title"].strip():
        return "empty_title"
    return None


def merge_candidates() -> tuple[list[dict[str, Any]], MergeStats]:
    """Read every saved day and fold it into one record per QID. First sighting wins for
    text, title and provenance date; the selected flag is sticky, so a QID seen in any
    "selected" feed stays flagged however many plain "events" sightings follow. Pure
    apart from reading the cache directory — no network, no writes."""
    stats = MergeStats()
    by_qid: dict[str, dict[str, Any]] = {}

    for mm, dd in iter_calendar_days():
        for feed in FEED_TYPES:
            payload = read_cached_payload(raw_path(feed, mm, dd))
            if payload is None:
                continue

            for entry in entries_of(payload, feed, mm, dd):
                stats.raw_entries += 1
                extracted = extract_record(entry, feed, mm, dd)
                if isinstance(extracted, str):
                    stats.dropped[extracted] += 1
                    continue

                reason = validation_error(extracted)
                if reason:
                    stats.dropped[reason] += 1
                    continue

                existing = by_qid.get(extracted["qid"])
                if existing is None:
                    by_qid[extracted["qid"]] = extracted
                    continue

                stats.duplicate_qids += 1
                existing["selected"] = existing["selected"] or extracted["selected"]

    return list(by_qid.values()), stats


def era_of(year: int) -> str:
    """The era bucket a year falls in, using half-open [lower, upper) ranges so no year
    lands in two buckets."""
    for name, lower, upper in ERA_BUCKETS:
        if (lower is None or year >= lower) and (upper is None or year < upper):
            return name
    raise RuntimeError(f"year {year} matched no era bucket; expected the buckets to be exhaustive")


def write_candidates(records: list[dict[str, Any]], fetch: FetchStats, merge: MergeStats) -> None:
    """Persist the candidate list with the counts that produced it, so the file carries
    its own provenance and a reader need not re-run the harvest to know what it dropped."""
    CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "generated_from": "onthisday harvest",
        "counts": {
            "requests_made": fetch.requested,
            "resumed_from_cache": fetch.resumed,
            "failed_days": len(fetch.failed_days),
            "raw_entries": merge.raw_entries,
            "dropped_total": merge.total_dropped,
            "dropped_by_reason": dict(merge.dropped),
            "duplicate_qid_sightings": merge.duplicate_qids,
            "unique_qids": len(records),
            "selected_flagged": sum(1 for record in records if record["selected"]),
        },
        "events": records,
    }
    with CANDIDATES_PATH.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1)


def sample_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Five records for eyeballing: one flagged `selected`, the rest drawn from distinct
    era buckets oldest-first. Deterministic by construction — no RNG — so two runs over
    the same cache print the same samples and a diff means the data moved."""
    if not records:
        return []

    chosen = [next((r for r in records if r["selected"]), records[0])]
    seen_eras = {era_of(chosen[0]["year"])}

    for name, _, _ in ERA_BUCKETS:
        if len(chosen) == 5:
            break
        if name in seen_eras:
            continue
        match = next((r for r in records if era_of(r["year"]) == name), None)
        if match is None:
            continue
        chosen.append(match)
        seen_eras.add(name)

    for record in records:  # top up if the era spread ran out of distinct buckets
        if len(chosen) == 5:
            break
        if record not in chosen:
            chosen.append(record)

    return chosen


def print_report(records: list[dict[str, Any]], fetch: FetchStats, merge: MergeStats) -> None:
    """The acceptance evidence: what was fetched, what survived, how it spreads over
    time, and five records to read with your own eyes."""
    years = [record["year"] for record in records]
    era_counts = Counter(era_of(year) for year in years)

    print("\n" + "=" * 72)
    print("HARVEST REPORT — Wikipedia On This Day -> candidates")
    print("=" * 72)

    print("\nFETCH")
    print(f"  requests made      : {fetch.requested}")
    print(f"  skipped (resumed)  : {fetch.resumed}")
    print(f"  failed days        : {len(fetch.failed_days)}")
    for label in fetch.failed_days:
        print(f"      - {label}")

    print("\nMERGE")
    print(f"  raw event entries  : {merge.raw_entries}")
    print(f"  dropped            : {merge.total_dropped}")
    for reason, count in sorted(merge.dropped.items(), key=lambda item: -item[1]):
        print(f"      {reason:<20} {count}")
    print(f"  duplicate sightings: {merge.duplicate_qids}")
    print(f"  unique QIDs        : {len(records)}")
    print(f"  selected-flagged   : {sum(1 for record in records if record['selected'])}")

    print("\nYEAR DISTRIBUTION")
    if years:
        print(f"  min / max          : {min(years)} / {max(years)}")
        for name, _, _ in ERA_BUCKETS:
            count = era_counts.get(name, 0)
            share = 100 * count / len(years)
            print(f"      {name:<12} {count:>6}  {share:5.1f}%")
    else:
        print("  (no records)")

    print("\nSAMPLES")
    for record in sample_records(records):
        flag = "selected" if record["selected"] else "events  "
        text = record["text"]
        if len(text) > 110:
            text = text[:107] + "..."
        print(f"  [{flag}] {record['qid']:<12} {record['year']:>6}  {record['title']}")
        print(f"             {record['month']}-{record['day']}  {text}")

    print(f"\nWROTE {CANDIDATES_PATH}")
    print("=" * 72, flush=True)


def main() -> int:
    """Fetch, merge, persist, report. Returns non-zero only if the merge produced
    nothing, which means the run is not evidence of anything."""
    # Sample texts carry accents and non-Latin names; a redirected stdout on Windows
    # defaults to the ANSI codepage and would raise UnicodeEncodeError on them.
    sys.stdout.reconfigure(encoding="utf-8")

    print("Fetching On This Day feeds (366 days x 2 types)...", flush=True)
    fetch = harvest_raw_days()
    print(
        f"Fetch done: {fetch.requested} requested, {fetch.resumed} resumed, "
        f"{len(fetch.failed_days)} failed.\nMerging...",
        flush=True,
    )

    records, merge = merge_candidates()
    write_candidates(records, fetch, merge)
    print_report(records, fetch, merge)

    if not records:
        print("ERROR: merge produced zero records; expected thousands.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
