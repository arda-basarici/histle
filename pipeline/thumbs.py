"""Fetch a thumbnail for every hand-written manual event, the one slice of the pool that has none.

A side stage of the histle dataset pipeline, run before `assemble.py` and independent of the
harvest chain. It reads `data/manual_events.draft.json` for its `wiki_title` values and writes
`data/raw/manual_thumbs.json`, a flat `{wiki_title: thumbnail URL or null}` cache that the
assembly stage reads if it is there.

Why a separate stage. Thumbnails for harvested events come out of the raw "On This Day" feeds,
which is a file the assembly stage already has open — no network, no ordering constraint. The
manual entries were never in any feed: they exist precisely because the feed represents those
events only through their artifact, so there is no sighting to carry a picture. Their pictures
have to be asked for by name, and asking is a network call, which does not belong in a stage
whose whole boundary claim is "six files in, three files out, no network". Hence one small
script that owns the call, writes a cache, and leaves the assembly stage reading a file.

It costs one request. Fifty titles fit in a single anonymous `titles=` batch, and the response
carries every thumbnail at once, so the polite-fetching machinery the redirect stage needs
(continuation, per-batch caching, spacing) is absent here on purpose — there is nothing to
resume between. Resumability is instead the whole-run kind: a cache that already covers every
title in the draft means the run has nothing to do, and `--refresh` is how you say otherwise.

Boundary. Input is the draft and the network; output is one cache file and a printed report of
what was found. No judgement: a title with no picture is written as null rather than omitted,
so the cache always answers for every entry and "we asked and there is none" stays
distinguishable from "we never asked".

Gotchas worth knowing before editing:

- The draft's `wiki_title` values are URL path segments, so a few are percent-encoded
  (`Philosophi%C3%A6_Naturalis_Principia_Mathematica`). The API wants the decoded title, and
  the cache is keyed by the *encoded* form the draft wrote — that is the string the assembly
  stage will look up, and rewriting the draft's own keys here would make the two files agree
  only by luck.
- Joining the response back to what was asked takes two maps, not one. MediaWiki normalises the
  title it was sent (`Wright_Flyer` -> `Wright Flyer`) and then, with `redirects=1`, resolves it
  again to the article it points at (`Invention_of_the_telephone` -> `Invention of the
  telephone` -> `History of the telephone`, say). The `pages` array is keyed by that final
  title and is not in request order, so the chain sent -> `normalized` -> `redirects` -> page is
  the only sound join; position and string equality both quietly mismatch.
- `redirects=1` is what makes the picture the *article's*, not the redirect stub's — a redirect
  page carries no `pageimage` at all, so without it a third of these would come back empty and
  look like Wikipedia has no picture of the printing press.
- A `missing` page means the draft's `wiki_title` names an article that does not exist, which is
  a curation error rather than a coverage gap. It is reported separately from "no thumbnail"
  for that reason: the second is Wikipedia's fact about the page, the first is our typo.
- Windows default encoding is not UTF-8 and these titles carry accents, so every `open()` here
  passes `encoding="utf-8"` and `main` reconfigures stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = (
    "histle-dataset-builder/0.1 "
    "(https://github.com/arda-basarici/histle; ardabasarici@gmail.com)"
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MANUAL_PATH = REPO_ROOT / "data" / "manual_events.draft.json"
THUMBS_PATH = REPO_ROOT / "data" / "raw" / "manual_thumbs.json"

TITLE_BATCH_SIZE = 50  # the API's anonymous ceiling on `titles`
THUMB_WIDTH_PX = 330  # the board renders a 52px slot; 330 covers a 3x display and reuse
TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 3.0)
BATCH_SPACING_SECONDS = 0.2
MISSING_SHOWN = 50  # the list is the report's point at this size, so effectively "all of them"


def load_wiki_titles() -> list[str]:
    """The draft's `wiki_title` values, in file order and deduplicated.

    An entry without one is a draft that cannot be illustrated and stops the run: the field is
    what the draft's own comment says it exists for, and silently skipping the entry would show
    up later as an unexplained hole in the coverage report."""
    if not MANUAL_PATH.is_file():
        raise RuntimeError(
            f"no manual additions file at {MANUAL_PATH}; expected the curated draft this "
            "stage exists to illustrate"
        )
    with MANUAL_PATH.open(encoding="utf-8") as handle:
        entries = json.load(handle).get("events") or []

    titles: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        title = entry.get("wiki_title")
        if not title:
            raise RuntimeError(
                f"manual entry {index} ({entry.get('name')!r}) has no wiki_title; "
                "expected the Wikipedia page key every other entry carries"
            )
        if title in seen:
            continue
        seen.add(title)
        titles.append(title)
    return titles


def api_title(wiki_title: str) -> str:
    """The draft's page key as the API wants it: percent-decoded, underscores left alone.

    Underscores need no handling — MediaWiki normalises them to spaces and reports the swap in
    the response's `normalized` list, which is the join key anyway."""
    return urllib.parse.unquote(wiki_title)


def batched(titles: list[str], size: int) -> list[list[str]]:
    """The title list cut into request-sized slices."""
    return [titles[start : start + size] for start in range(0, len(titles), size)]


def build_session() -> requests.Session:
    """A session carrying the identifying User-Agent the API asks anonymous clients for."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def get_json(session: requests.Session, params: dict[str, str], label: str) -> dict[str, Any]:
    """One MediaWiki Action API response, retrying transient failures with a short backoff.
    Raises naming the batch and the last error once the attempts are spent — with a single
    request in the whole run there is no partial result worth salvaging, so failure ends it."""
    last_error: Exception | str | None = None

    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            time.sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])
        try:
            response = session.get(WIKIPEDIA_API, params=params, timeout=TIMEOUT_SECONDS)
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                continue
            return response.json()
        except (requests.RequestException, json.JSONDecodeError) as error:
            last_error = error

    raise RuntimeError(
        f"{label} failed after {MAX_ATTEMPTS} attempts; expected HTTP 200 with a JSON body "
        f"from {WIKIPEDIA_API}; last error: {last_error}"
    )


def resolved_titles(payload: dict[str, Any]) -> dict[str, str]:
    """"The title I sent" -> "the title the pages array uses", following both hops.

    `normalized` records the API's own tidying of what was asked for; `redirects` records the
    article a redirect stub points at, and its `from` side speaks the normalised form. Composing
    them in that order is what lets a sent title find its page; a title the API neither
    normalised nor redirected is absent from both lists and joins to itself, which the caller
    handles."""
    query = payload.get("query") or {}
    normalized = {entry["from"]: entry["to"] for entry in query.get("normalized") or []}
    redirected = {entry["from"]: entry["to"] for entry in query.get("redirects") or []}

    resolved: dict[str, str] = {}
    for sent, tidied in normalized.items():
        resolved[sent] = redirected.get(tidied, tidied)
    for sent, target in redirected.items():
        resolved.setdefault(sent, target)
    return resolved


def fetch_thumbnail_batch(session: requests.Session, titles: list[str]) -> dict[str, str | None]:
    """One batch of page keys mapped to a thumbnail URL, `None`, or absent when the page itself
    does not exist. The three outcomes are kept apart here rather than flattened, because the
    caller reports "Wikipedia has no picture" and "this article does not exist" differently."""
    sent = [api_title(title) for title in titles]
    label = f"thumbnail batch of {len(sent)} titles (first: {sent[0]})"
    payload = get_json(
        session,
        {
            "action": "query",
            "prop": "pageimages",
            "piprop": "thumbnail",
            "pithumbsize": str(THUMB_WIDTH_PX),
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
            "titles": "|".join(sent),
        },
        label,
    )

    resolved = resolved_titles(payload)
    thumb_by_page: dict[str, str | None] = {}
    for page in (payload.get("query") or {}).get("pages") or []:
        if page.get("missing") or page.get("invalid"):
            continue
        thumb_by_page[page["title"]] = (page.get("thumbnail") or {}).get("source")

    found: dict[str, str | None] = {}
    for wiki_title, asked in zip(titles, sent):
        page_title = resolved.get(asked, asked)
        if page_title in thumb_by_page:
            found[wiki_title] = thumb_by_page[page_title]
    return found


def read_cache() -> dict[str, str | None]:
    """The cache as it stands, or an empty mapping when there is none. A corrupt cache raises
    rather than being silently rebuilt: it is one request to refetch, but a JSON file that
    stopped parsing usually means something else wrote it."""
    if not THUMBS_PATH.is_file():
        return {}
    with THUMBS_PATH.open(encoding="utf-8") as handle:
        cached = json.load(handle)
    if not isinstance(cached, dict):
        raise RuntimeError(
            f"{THUMBS_PATH} holds {type(cached).__name__}; expected a "
            "{wiki_title: url or null} object — delete it and re-run to rebuild"
        )
    return cached


def write_cache(thumbs: dict[str, str | None]) -> None:
    """Persist the cache, keys sorted so two runs over the same draft produce the same bytes and
    a diff means the answers moved."""
    THUMBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with THUMBS_PATH.open("w", encoding="utf-8") as handle:
        json.dump(dict(sorted(thumbs.items())), handle, ensure_ascii=False, indent=1)


def print_report(
    titles: list[str], thumbs: dict[str, str | None], absent_pages: list[str], fetched: bool
) -> None:
    """What the draft can be illustrated with, and what it cannot. The misses are listed rather
    than counted: each one is a title a curator can look up in a browser in ten seconds, and a
    bare "7 missing" is not actionable.

    `absent_pages` is only knowable on a fetching run — the cache stores a null for "no
    picture" and for "no such page" alike — so the split is printed only when this run asked."""
    with_thumb = [title for title in titles if thumbs.get(title)]
    without = [title for title in titles if not thumbs.get(title)]

    print("\n" + "=" * 78)
    print("MANUAL THUMBNAILS — one request against the pageimages API")
    print("=" * 78)
    print(f"  source                     {'network' if fetched else 'cache (already complete)'}")
    print(f"  titles in the draft        {len(titles):>6}")
    print(f"  with a thumbnail           {len(with_thumb):>6}")
    print(f"  without                    {len(without):>6}")
    if fetched:
        print(f"     of those, no such page  {len(absent_pages):>6}")

    absent = set(absent_pages)
    for title in without[:MISSING_SHOWN]:
        label = "NO SUCH PAGE" if title in absent else "no picture"
        print(f"      [{label}] {title}")

    print(f"\nWROTE {THUMBS_PATH}")
    print("=" * 78, flush=True)


def main() -> int:
    """Read the draft, fetch what the cache does not already answer for, write, report.

    The cache is authoritative when it covers every title: this stage's whole input is a
    hand-edited file that changes a few times a year, and re-asking Wikipedia on every pipeline
    run would be rude for no information. `--refresh` is the escape hatch for the case the cache
    cannot detect — a title whose article gained a picture since."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-ask the API even when the cache already covers every title",
    )
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    titles = load_wiki_titles()
    cached = read_cache()
    covered = all(title in cached for title in titles)

    if covered and not args.refresh:
        print(f"Cache at {THUMBS_PATH} already covers all {len(titles)} titles; not refetching.")
        print_report(titles, cached, absent_pages=[], fetched=False)
        return 0

    session = build_session()
    answered: dict[str, str | None] = {}
    batches = batched(titles, TITLE_BATCH_SIZE)
    for index, batch in enumerate(batches):
        if index:
            time.sleep(BATCH_SPACING_SECONDS)
        print(f"Fetching batch {index + 1}/{len(batches)} ({len(batch)} titles)...", flush=True)
        answered.update(fetch_thumbnail_batch(session, batch))

    # A title whose article does not exist is cached as null like any other picture-less entry:
    # the cache's contract is that it answers for every title in the draft, so the next run has
    # nothing left to ask. The distinction survives only into this run's report.
    thumbs = {title: answered.get(title) for title in titles}
    absent_pages = [title for title in titles if title not in answered]

    write_cache(thumbs)
    print_report(titles, thumbs, absent_pages, fetched=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
