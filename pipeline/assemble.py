"""Assemble the harvested, enriched and augmented raw data into the game's shipping files.

Fourth and final stage of the histle dataset pipeline, and the first one that makes
judgements. It reads `data/raw/candidates.json` (blurbs and harvest years),
`data/raw/enriched.json` (Wikidata facts), `data/raw/augmented.json` (redirect aliases and
coordinates) and the untouched per-day feeds under `data/raw/onthisday/` (the only place a
thumbnail URL exists), and writes three files: `data/events.core.json`,
`data/labels.en.json` and `data/curation.csv`.

What it decides. The event pool arrives as 10567 items that Wikidata gave an event date, and
an event date is a weak claim: beer has one (-3500), so does the dog, so does every calendar
year the feed name-checked. Two rules do the sorting. A P31 blocklist drops the classes that
are categorically not events — people, taxa, years, countries, cities, companies — unless the
item also carries an event-shaped class, because "historical country" plus "war" is a war
described in territorial terms, not a country. A year-gap rule drops items whose Wikidata date
is more than a century from the year the "On This Day" blurb placed them under, which is the
signature of an entity that merely got mentioned: beer's -3500 against a blurb about a brewery
in 1516. Between those extremes, gaps of 11–100 years are kept and flagged for a human, since
that band holds both genuine disagreement about ancient datings and real mistakes.

Boundary. Input is four things on disk; output is three files and a printed report. No
network. Everything here is derivable from the raw cache, so the outputs are regenerable and
this script can be re-run at will — with one exception that matters: `data/curation.csv` is
the sheet a human is about to write in, and re-running overwrites it. The categories and the
`famous` column it carries are *prefills*, explicitly marked unreviewed (`category_reviewed`
is false in the core file, `category_final` is an empty column in the sheet); the curated
answer pool is a later stage's product, not this one's.

The split between the three outputs is the multilanguage seam. `events.core.json` holds only
what does not depend on a language — id, year, place, category, popularity, thumbnail — and
`labels.en.json` holds the names, aliases and blurbs. A second language is then a second
labels file, not a second dataset, and the game's daily-answer derivation keeps operating on
one shared list of ids.

Gotchas worth knowing before editing:

- Wikidata's year numbering is ISO 8601, where year 0 exists and equals 1 BC; the game speaks
  historian's years, where it does not. So every stored year here is a *display* year:
  `date_year - 1` for `date_year <= 0`, unchanged above. The Battle of Marathon is -489 in
  `enriched.json` and -490 in `events.core.json`, matching the feed and the textbooks. A
  consequence worth relying on: display year 0 is unreachable, which is why the validation can
  treat a zero as corruption.
- Continents come from Wikidata P30 and are not a clean six-value vocabulary. The pool carries
  "Eurasia", "Afro-Eurasia", "Insular Oceania", "Central America", "Australian continent" and
  "Antarctica" alongside the six the game's region axis uses. The unambiguous ones are folded
  in (Central America is North America, Insular Oceania and the Australian continent are
  Oceania); the supercontinents are dropped rather than guessed at, because "Eurasia" for the
  War in the Vendée would have to resolve to Europe and for the Soviet–Lithuanian Peace Treaty
  is a genuine coin flip. An item left with nothing is flagged `region_missing` for curation,
  not silently placed.
- Thumbnails exist in exactly one place: the raw per-day feeds. Neither Wikidata nor the
  candidate file carries an image, so the feeds are scanned once here and reduced to a
  QID -> URL map. An entity sighted on several days can have several thumbnails; the sighting
  with the lowest `page_index` wins, the same centrality tiebreak the harvest used to pick a
  blurb, so the picture comes from the day the feed treated the entity as the subject.
- The category prefill resolves in three passes — an exact P31 class map, then keyword
  patterns over the P31 class names, then the same patterns over the label — and an item
  voting for several categories is settled by a fixed precedence, not by whichever class
  sorted first. Precedence puts war and disaster ahead of politics because the fallback *is*
  politics: an item that reached the end with no evidence is filed there and flagged
  `category_guess`, so "politics & diplomacy" in the sheet means either a real political event
  or a shrug, and only the flag separates them.
- Aliases merge two vocabularies with different habits. Wikidata's `skos:altLabel` values are
  curator-written; Wikipedia's redirect titles are page keys, so they carry namespaces
  ("Talk:...") that no player would ever type and are dropped on the colon. Deduplication is
  case-insensitive but keeps the first spelling seen, so "Pearl Harbor attack" survives and its
  capitalised twin does not — the game's matcher owns case folding, this file should not
  pre-decide it.
- `curation.csv` is written UTF-8 *with* a BOM. Excel reads a BOM-less UTF-8 CSV as the local
  codepage and turns every accented event name into mojibake, and this file exists to be
  opened in a spreadsheet.
- Windows default encoding is not UTF-8 and these labels are full of non-ASCII names, so every
  `open()` here passes an explicit encoding and `main` reconfigures stdout.
"""

from __future__ import annotations

import csv
import gzip
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_PATH = REPO_ROOT / "data" / "raw" / "candidates.json"
ENRICHED_PATH = REPO_ROOT / "data" / "raw" / "enriched.json"
AUGMENTED_PATH = REPO_ROOT / "data" / "raw" / "augmented.json"
ONTHISDAY_DIR = REPO_ROOT / "data" / "raw" / "onthisday"

CORE_PATH = REPO_ROOT / "data" / "events.core.json"
LABELS_PATH = REPO_ROOT / "data" / "labels.en.json"
CURATION_PATH = REPO_ROOT / "data" / "curation.csv"

FEED_TYPES = ("events", "selected")

# An item this far from the year its blurb placed it under is not that blurb's event.
YEAR_GAP_DROP_ABOVE = 100
YEAR_GAP_FLAG_ABOVE = 10

FAMOUS_CANDIDATE_MIN_SITELINKS = 25
CURATION_PREFILLED_FAMOUS = 400
TOP_POPULARITY_SHOWN = 15
DROP_EXAMPLES_SHOWN = 3

# Classes that are categorically not events. Present on an item, they mean the feed linked a
# person, a place, an organisation or a unit of time while describing something that happened.
P31_BLOCKLIST = frozenset(
    {
        "human",
        "taxon",
        "fossil taxon",
        "calendar year",
        "year",
        "decade",
        "century",
        "historical country",
        "country",
        "sovereign state",
        "big city",
        "city",
        "town",
        "village",
        "city in the United States",
        "county seat",
        "business",
        "enterprise",
        "public company",
        "organization",
        "airline",
        "airport",
        "commercial traffic aerodrome",
        "international airport",
        "political party",
        "position",
        "Wikimedia list article",
        "aircraft family",
        "aircraft model",
        "ship class",
        "product",
        "food",
        "drink",
        "language",
        "ethnic group",
        "planet",
        "inner planet of the Solar System",
    }
)

# Classes that override a blocklist hit. Wikidata routinely types a war as both an "armed
# conflict" and a "historical country"; the event-shaped claim is the one that decides.
P31_EVENT_RESCUE = frozenset(
    {
        "war",
        "world war",
        "battle",
        "naval battle",
        "siege",
        "invasion",
        "military campaign",
        "military operation",
        "armed conflict",
        "military conflict",
        "historical event",
        "occurrence",
        "massacre",
        "rebellion",
        "revolution",
        "coup d'état",
        "treaty",
        "peace treaty",
    }
)

WAR = "war & conflict"
POLITICS = "politics & diplomacy"
DISASTER = "disaster & accident"
SCIENCE = "science & technology"
CULTURE = "culture & religion"
EXPLORATION = "exploration & discovery"
SOCIETY = "society & economy"

CATEGORIES = frozenset({WAR, POLITICS, DISASTER, SCIENCE, CULTURE, EXPLORATION, SOCIETY})

# How a multi-voting item is settled. Specific beats generic, and politics sits last because it
# doubles as the fallback — see the module docstring.
CATEGORY_PRECEDENCE = (WAR, DISASTER, EXPLORATION, SCIENCE, CULTURE, SOCIETY, POLITICS)
FALLBACK_CATEGORY = POLITICS

# Exact P31 class -> category. Written out class by class rather than by pattern because these
# are the classes the pool actually carries in quantity, and an exact name is the one mapping
# that cannot misfire ("airliner bombing" is an attack, "aviation accident" is not).
CLASS_CATEGORY: dict[str, str] = {
    name: category
    for category, names in (
        (
            WAR,
            (
                "battle", "naval battle", "war", "world war", "civil war", "series of wars",
                "war of independence", "war of national liberation", "war of succession",
                "religious war", "siege", "sack", "storming", "massacre", "school massacre",
                "genocide", "ethnic cleansing", "pogrom", "military operation",
                "military campaign", "military expedition", "military raid", "military occupation",
                "military intervention", "military alliance", "military coup", "armed conflict",
                "military conflict", "international conflict", "ethnic conflict",
                "environmental conflict", "conflict", "theater of war", "war front",
                "naval warfare", "invasion", "conquest", "annexation by force", "occupation",
                "offensive", "landing operation", "bombardment", "aerial bombing of a city",
                "airstrike", "air raid", "razzia", "ambush", "skirmish", "last stand",
                "dogfight", "mutiny", "rebellion", "princely rebellion", "slave rebellion",
                "peasant revolt", "revolt", "revolution", "insurgency", "resistance movement",
                "coup d'état", "attempted coup d'état", "war crime", "war crimes trial",
                "terrorist attack", "coordinated terrorist attack", "domestic terrorist attack",
                "terrorist organization", "failed terrorism plot", "bomb attack", "bombing",
                "suicide bombing", "suicide attack", "suicide car bombing", "truck bombing",
                "airliner bombing", "train attack", "vehicle-ramming attack", "attack",
                "shooting attack", "stabbing attack", "mass stabbing", "aircraft hijacking",
                "aircraft shootdown", "airspace intrusion", "border incident", "shootout",
                "fusillade", "hostage taking", "hostage-rescue mission", "covert operation",
                "purge", "persecution", "nuclear test series", "liberation", "capitulation",
                "ethnic violence", "ethnic riot", "mass killing", "sinking of a warship",
            ),
        ),
        (
            DISASTER,
            (
                "aviation accident", "aviation incident", "aircraft crash", "airplane crash",
                "helicopter crash", "mid-air collision", "ground collision", "ditching",
                "emergency landing", "flight disappearance", "earthquake", "tsunami", "flood",
                "non-water flood", "storm surge", "hurricane", "typhoon", "tropical cyclone",
                "North Atlantic tropical cyclone", "extratropical cyclone", "European windstorm",
                "Atlantic hurricane season", "storm", "geomagnetic storm", "blizzard", "avalanche",
                "tornado", "tornado outbreak", "wildfire", "bushfires in Australia",
                "conflagration", "structure fire", "city fire", "industrial fire", "tunnel fire",
                "train fire", "fire", "arson", "explosion", "gas explosion", "disaster",
                "natural disaster", "environmental disaster", "industrial disaster",
                "maritime disaster", "stadium disaster", "nuclear accident", "Broken Arrow",
                "chemical accident", "mining accident", "work accident", "accident",
                "railway accident", "derailment", "runaway train", "train collision",
                "head-on collision", "transport collision", "traffic collision", "collision",
                "bus accident", "ship collision", "shipwrecking", "sinking", "oil spill",
                "landslide", "volcanic eruption", "Plinian eruption", "phreatic eruption",
                "famine", "epidemic", "pandemic", "disease outbreak", "crowd crush", "stampede",
                "structural failure", "bridge failure", "dam failure", "tailings dam failure",
                "power outage", "drought", "heat wave",
            ),
        ),
        (
            POLITICS,
            (
                "treaty", "peace treaty", "bilateral treaty", "multilateral treaty",
                "constitutive treaty", "secret treaty", "unequal treaties", "United Nations treaty",
                "treaty of the European Union", "agreement", "armistice", "ceasefire", "truce",
                "cession", "partition", "annexation", "independence",
                "declaration of independence", "referendum", "independence referendum",
                "constitutional referendum", "self-determination referendum", "plebiscite",
                "election", "public election", "presidential election", "leadership election",
                "group of elections", "United States presidential election",
                "United Kingdom general election", "Canadian federal general election",
                "Spanish general election", "general election", "coronation",
                "coronation of the British monarch", "United States presidential inauguration",
                "constitution", "legislation", "statute", "decree", "proclamation",
                "State of the Union address", "oration", "Act of Congress in the United States",
                "Public General Act of the Parliament of the United Kingdom", "impeachment",
                "United States presidential impeachment", "political crisis",
                "constitutional crisis", "international crisis", "crisis", "international incident",
                "summit", "international conference", "peace conference", "convention", "congress",
                "meeting", "party conference", "Democratic National Convention",
                "political scandal", "corruption scandal", "scandal", "controversy",
                "regime change", "dictatorship", "martial law", "state of emergency",
                "political movement", "political event", "political union", "government",
                "presidential term", "legislative term", "enlargement of the European Union",
                "assassination", "assassination attempt", "political murder", "magnicide",
                "state funeral", "withdrawal", "dissolution of an administrative territorial entity",
                "historical document", "conspiracy", "political slogan", "public policy",
                "occupied territory",
            ),
        ),
        (
            SCIENCE,
            (
                "spaceflight", "human spaceflight", "human spaceflight program",
                "expedition to the International Space Station", "NASA program", "rocket model",
                "space probe", "artificial satellite", "nuclear weapons testing",
                "underground nuclear weapons test", "total solar eclipse", "solar eclipse",
                "lunar eclipse", "transit of Venus", "astronomical observation", "experiment",
                "invention", "scientific journal", "scientific theory", "UFO sighting",
                "clinical trial",
            ),
        ),
        (
            EXPLORATION,
            (
                "expedition", "research expedition", "polar expedition",
                "mountaineering expedition", "exploration", "voyage", "circumnavigation",
                "marine navigation", "discovery", "first ascent", "flight",
            ),
        ),
        (
            CULTURE,
            (
                "papal election", "conclave", "ecumenical council", "synod",
                "schism in Christianity", "religious controversy", "canonization", "beatification",
                "royal wedding", "wedding", "funeral", "public holiday", "religious festival",
                "pilgrimage", "Secular Games", "concert", "art exhibition", "art movement",
                "cultural movement", "award ceremony", "Academy Awards ceremony",
                "Eurovision Song Contest edition", "world's fair", "television series",
                "animated television series", "television program", "news program",
                "media franchise", "written work", "newspaper", "daily newspaper", "magazine",
                "weekly magazine", "periodical", "website", "sports season",
                "recurring sporting event", "recurring sporting event edition",
                "recurring event edition", "Summer Olympic Games edition",
                "Winter Olympic Games edition", "Olympic sporting event",
                "international sporting event", "international association football match",
                "association football club match", "final of the FIFA World Cup",
                "American football game", "Super Bowl", "boxing match",
            ),
        ),
        (
            SOCIETY,
            (
                "economic crisis", "financial crisis", "recession", "stock market crash",
                "bankruptcy", "currency", "obsolete currency", "dollar", "strike", "general strike",
                "protest", "student protest", "protest march", "demonstration", "rally",
                "social movement", "riot", "civil disorder", "food riot", "prison riot",
                "sports riot", "looting", "murder", "attempted murder", "murder–suicide",
                "homicide", "mass murder", "mass shooting", "school shooting",
                "university shooting", "spree shooting", "shooting", "killing", "matricide",
                "pedicide", "suicide", "pilot suicide", "death", "capital punishment",
                "public execution", "execution", "lynching", "hate crime", "kidnapping",
                "robbery", "bank robbery", "theft", "rape", "assault",
                "police brutality in the United States", "police operation", "police raid",
                "prison escape", "criminal case", "legal case", "trial",
                "decision of the Supreme Court of the United States", "forced displacement",
                "deportation", "disappearance", "same-sex marriage in a geographic region",
                "human migration", "slavery",
            ),
        ),
    )
    for name in names
}

# Second and third passes: the same patterns run over the P31 class names, then over the label.
# Word-boundary anchored because the short words are the dangerous ones — an unanchored "war"
# matches "software" and an unanchored "fire" matches "ceasefire".
KEYWORD_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), category)
    for pattern, category in (
        (
            r"\b(war|wars|battle|siege|massacre|invasion|invades?|military|bombing|bombard\w*"
            r"|airstrike|terroris\w*|genocide|insurgen\w*|mutiny|rebellion|revolt|uprising|coup"
            r"|conquest|raid|offensive|shelling|guerrilla|hostilities|army|troops|attack\w*)\b",
            WAR,
        ),
        (
            r"\b(earthquake|tsunami|flood\w*|hurricane|typhoon|cyclone|tornado|storm|blizzard"
            r"|wildfire|fire|eruption|volcan\w*|avalanche|landslide|famine|epidemic|pandemic"
            r"|plague|disaster|accident|crash\w*|derail\w*|shipwreck\w*|sinks?|sinking|sank"
            r"|capsiz\w*|collision|explosion|explodes?|collapse\w*|outbreak|drought|spill)\b",
            DISASTER,
        ),
        (
            r"\b(treaty|treaties|peace|accord|accords|pact|armistice|ceasefire|election\w*"
            r"|referendum|plebiscite|coronation|crowned|constitution\w*|independence|charter"
            r"|declaration|proclamation|proclaims?|summit|conference|congress|parliament"
            r"|inaugurat\w*|annexation|annexes|partition|impeach\w*|assassinat\w*|edict)\b",
            POLITICS,
        ),
        (
            r"\b(spaceflight|spacecraft|space|satellite|rocket|orbit\w*|eclipse|comet|telescope"
            r"|patent|invention|invents?|experiment|vaccine|computer|internet|nuclear test"
            r"|transit of)\b",
            SCIENCE,
        ),
        (
            r"\b(expedition|exploration|explorers?|voyage|circumnavigat\w*|ascent|discovery"
            r"|discovers?|landfall|first flight)\b",
            EXPLORATION,
        ),
        (
            r"\b(festival|olympics?|olympic|world cup|championship|concert|opera|premiere"
            r"|exhibition|cathedral|church|mosque|temple|pope|papal|council|synod|canoni\w*"
            r"|religio\w*|wedding|funeral|award|ceremony|film|album|novel)\b",
            CULTURE,
        ),
        (
            r"\b(strike|protest\w*|riot|riots|demonstration|boycott|recession|depression"
            r"|bankrupt\w*|currency|abolition|abolishes|slavery|suffrage|migration|census)\b",
            SOCIETY,
        ),
    )
)

# Wikidata P30 is not a six-value vocabulary. The unambiguous narrower and alias forms fold in;
# supercontinents (Eurasia, Afro-Eurasia) and Antarctica are left out — see the module docstring.
REGION_CANONICAL = ("Africa", "Asia", "Europe", "North America", "Oceania", "South America")
CONTINENT_ALIASES = {
    "Central America": "North America",
    "Insular Oceania": "Oceania",
    "Australian continent": "Oceania",
}

CURATION_COLUMNS = (
    "qid",
    "name",
    "year",
    "category_auto",
    "region",
    "country",
    "popularity",
    "flags",
    "famous",
    "category_final",
    "notes",
)


@dataclass
class RawInputs:
    """The four raw sources, parsed once. Held together because every derivation below needs
    two or three of them at the same time and these files are 30 MB in total."""

    blurbs: dict[str, dict[str, Any]]
    enriched: dict[str, dict[str, Any]]
    augmented: dict[str, dict[str, Any]]
    thumbnails: dict[str, str]


@dataclass
class DropRule:
    """One rule of the pool waterfall: what it is called, how much it took, and a few of the
    items it took. The examples are the point — a drop count alone cannot tell you whether a
    rule caught the beer or the Battle of Hastings."""

    name: str
    dropped: int = 0
    examples: list[str] = field(default_factory=list)

    def record(self, example: str) -> None:
        self.dropped += 1
        if len(self.examples) < DROP_EXAMPLES_SHOWN:
            self.examples.append(example)


@dataclass
class Selection:
    """The pool waterfall's result: which QIDs survived, what each rule removed on the way, and
    which survivors a human should look at because their date is suspicious but not absurd."""

    started: int
    kept: list[str]
    rules: list[DropRule]
    gap_flagged: set[str]


@dataclass
class AssembledEvent:
    """One finished event, before it is split across the three output files. Language-dependent
    fields (`name`, `aliases`, `blurb`) sit next to language-independent ones here and are
    separated only at write time, because every derivation needs the whole record and only the
    file format cares about the seam."""

    qid: str
    year: int
    region: str | None
    country: str | None
    lat: float | None
    lon: float | None
    category: str
    famous_candidate: bool
    popularity: int
    thumb: str | None
    name: str
    aliases: list[str]
    blurb: str
    flags: list[str]


def load_document(path: Path, key: str, produced_by: str) -> dict[str, Any]:
    """The keyed payload of one raw file. A missing file or a missing key means an earlier
    pipeline stage has not run or its output shape moved, and either way every count downstream
    would be quietly wrong, so both stop the run instead of yielding an empty dict."""
    if not path.is_file():
        raise RuntimeError(
            f"no input file at {path}; expected the {produced_by} stage to have written it"
        )
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)

    payload = document.get(key)
    if not payload:
        raise RuntimeError(
            f"{path} has no non-empty '{key}'; expected the {produced_by} stage's records, "
            f"found keys: {sorted(document)}"
        )
    return payload


def load_blurbs() -> dict[str, dict[str, Any]]:
    """The harvest's records keyed by QID. The harvest already emits one record per QID, so the
    first sighting wins by construction; `setdefault` only guards against that changing."""
    events = load_document(CANDIDATES_PATH, "events", "harvest")
    blurbs: dict[str, dict[str, Any]] = {}
    for event in events:
        blurbs.setdefault(event["qid"], event)
    return blurbs


def iter_feed_pages() -> Iterator[dict[str, Any]]:
    """Every linked page of every blurb in every saved day, with its position in the blurb's
    link list attached as `page_index`. Files are walked in sorted order so that a tie between
    two sightings resolves the same way on every run."""
    for path in sorted(ONTHISDAY_DIR.glob("*.json")):
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        for feed in FEED_TYPES:
            for entry in payload.get(feed) or []:
                for page_index, page in enumerate(entry.get("pages") or []):
                    yield {**page, "page_index": page_index}


def scan_thumbnails() -> dict[str, str]:
    """QID -> thumbnail URL, from the only source that has one. The feeds are read once, here,
    because the alternative is 732 file opens per lookup. Where an entity was pictured on
    several days the lowest `page_index` wins: the harvest used that same measure to decide
    which blurb an entity was most central to, and the image should come from the same place
    the text did."""
    if not ONTHISDAY_DIR.is_dir():
        raise RuntimeError(
            f"no raw feed directory at {ONTHISDAY_DIR}; "
            "expected the harvest stage's per-day files — thumbnails exist nowhere else"
        )

    best: dict[str, tuple[int, str]] = {}
    for page in iter_feed_pages():
        qid = page.get("wikibase_item")
        source = (page.get("thumbnail") or {}).get("source")
        if not qid or not source:
            continue
        rank = page["page_index"]
        seen = best.get(qid)
        if seen is None or rank < seen[0]:
            best[qid] = (rank, source)
    return {qid: source for qid, (_, source) in best.items()}


def load_inputs() -> RawInputs:
    """All four raw sources, parsed once each. Effects are concentrated here so everything
    below operates on plain dicts."""
    return RawInputs(
        blurbs=load_blurbs(),
        enriched=load_document(ENRICHED_PATH, "items", "enrich"),
        augmented=load_document(AUGMENTED_PATH, "items", "augment"),
        thumbnails=scan_thumbnails(),
    )


def display_year(date_year: int) -> int:
    """A Wikidata ISO year as the historian's year the game speaks. ISO 8601 has a year 0 and
    calls it 1 BC, so everything at or below zero shifts down by one and year 0 becomes -1;
    positive years are already the same in both conventions. Marathon: -489 in, -490 out."""
    return date_year - 1 if date_year <= 0 else date_year


def is_blocked_type(instance_of: list[str]) -> bool:
    """Whether P31 says this item is not an event at all. An item with no P31 values is not
    blocked: absence of typing is absence of evidence, and the year-gap rule still applies to
    it. A blocklist hit alongside an event-shaped class is not blocked either — Wikidata types
    plenty of wars as historical countries, and the event claim is the specific one."""
    classes = set(instance_of)
    if not classes & P31_BLOCKLIST:
        return False
    return not (classes & P31_EVENT_RESCUE)


def select_pool(inputs: RawInputs) -> Selection:
    """The event pool, narrowed from every dated item to the ones that can plausibly be a quiz
    question, with the waterfall that produced it. Rules run in a fixed order and each records
    what it took, because "10567 became 9000" is not reviewable and "the blocklist took 800,
    here are three of them" is. Items are walked in numeric QID order so the examples are
    stable between runs."""
    dated = [
        qid
        for qid in sorted(inputs.enriched, key=lambda key: int(key[1:]))
        if inputs.enriched[qid]["has_event_date"]
    ]

    blocked = DropRule("P31 blocklist, unrescued")
    year_gap = DropRule(f"year gap > {YEAR_GAP_DROP_ABOVE}y vs harvest")
    unnamed = DropRule("no English label")

    kept: list[str] = []
    gap_flagged: set[str] = set()

    for qid in dated:
        record = inputs.enriched[qid]
        label = record["label"] or "(no label)"
        year = display_year(record["date_year"]) if record["date_year"] is not None else None

        if is_blocked_type(record["instance_of"]):
            hits = ", ".join(sorted(set(record["instance_of"]) & P31_BLOCKLIST))
            blocked.record(f"{qid} {label} ({year}) — instance of {hits}")
            continue

        harvest_year = inputs.blurbs[qid]["year"] if qid in inputs.blurbs else None
        if year is not None and harvest_year is not None:
            gap = abs(year - harvest_year)
            if gap > YEAR_GAP_DROP_ABOVE:
                year_gap.record(f"{qid} {label} — wikidata {year} vs harvest {harvest_year}")
                continue
            if gap > YEAR_GAP_FLAG_ABOVE:
                gap_flagged.add(qid)

        if not record["label"]:
            unnamed.record(f"{qid} ({year}) — instance of {', '.join(record['instance_of']) or '—'}")
            continue

        kept.append(qid)

    return Selection(
        started=len(dated), kept=kept, rules=[blocked, year_gap, unnamed], gap_flagged=gap_flagged
    )


def resolve_votes(votes: set[str]) -> str | None:
    """The winning category among several, by fixed precedence. An event that is both a
    terrorist attack and an aviation accident is a war-and-conflict event; deciding that by
    precedence rather than by whichever P31 class happened to sort first is what keeps the
    prefill reproducible."""
    for category in CATEGORY_PRECEDENCE:
        if category in votes:
            return category
    return None


def votes_from_classes(instance_of: list[str]) -> set[str]:
    """Categories the exact P31 map asserts for this item."""
    return {CLASS_CATEGORY[name] for name in instance_of if name in CLASS_CATEGORY}


def votes_from_keywords(texts: list[str]) -> set[str]:
    """Categories the keyword patterns find in any of these strings — used first over the P31
    class names (which cover a 1966-value long tail no hand-written map can) and then over the
    label, which is the last evidence available before the fallback."""
    return {
        category for pattern, category in KEYWORD_RULES if any(pattern.search(t) for t in texts)
    }


def assign_category(instance_of: list[str], label: str) -> tuple[str, bool]:
    """The prefilled category and whether it is a guess. Three passes, most trustworthy first:
    the exact P31 map, keyword patterns over the P31 class names, then the same patterns over
    the label. Reaching the end means nothing in the item said what kind of event it was, so it
    lands in the fallback bucket carrying the flag that says so — the sheet must not be able to
    hide a shrug behind a plausible-looking category."""
    for votes in (
        votes_from_classes(instance_of),
        votes_from_keywords(instance_of),
        votes_from_keywords([label]),
    ):
        winner = resolve_votes(votes)
        if winner is not None:
            return winner, False
    return FALLBACK_CATEGORY, True


def canonical_continents(continents: list[str]) -> list[str]:
    """The item's continents reduced to the six the region axis uses, sorted and deduplicated.
    Narrower and alias forms fold into their parent; supercontinents and Antarctica drop out
    rather than being guessed at, which can legitimately empty the list."""
    folded = {CONTINENT_ALIASES.get(name, name) for name in continents}
    return sorted(folded & set(REGION_CANONICAL))


def resolve_region(continents: list[str]) -> tuple[str | None, list[str]]:
    """The event's region and any flags the choice earned. One continent answers itself.
    Several means the event spanned them and the first alphabetically is a placeholder, not an
    answer — the 2004 Indian Ocean earthquake resolves to Africa this way — so it is flagged
    for a human. None leaves the field null and flagged; coordinates are stored either way and
    a later stage can reverse-geocode, but this stage does not invent a continent."""
    canonical = canonical_continents(continents)
    if len(canonical) == 1:
        return canonical[0], []
    if canonical:
        return canonical[0], ["region_multi"]
    return None, ["region_missing"]


def merge_aliases(name: str, wikidata_aliases: list[str], redirect_aliases: list[str]) -> list[str]:
    """The names a player might type for this event, from both vocabularies, in one list.
    Wikidata's curator-written aliases lead and Wikipedia's redirects follow, so the more
    deliberate spelling of a duplicate is the one kept. Deduplication is case-insensitive but
    preserves the first form seen rather than lowercasing, because case folding is the game
    matcher's job and doing it here would throw away the display spelling. Namespaced redirect
    titles ("Talk:...") and anything merely re-capitalising the name are dropped: neither is a
    name anyone would type."""
    merged: list[str] = []
    seen = {name.casefold()}
    for alias in [*wikidata_aliases, *(a.replace("_", " ") for a in redirect_aliases)]:
        folded = alias.casefold()
        if ":" in alias or folded in seen or not alias.strip():
            continue
        seen.add(folded)
        merged.append(alias)
    return merged


def assemble_event(qid: str, inputs: RawInputs, gap_flagged: bool) -> AssembledEvent:
    """One kept QID rendered as a finished event. Every field is derived here and nothing is
    fetched; the flags accumulate as the derivations make compromises, so the curation sheet
    inherits an honest account of which fields were decided and which were guessed."""
    record = inputs.enriched[qid]
    extra = inputs.augmented.get(qid, {})
    blurb = inputs.blurbs.get(qid, {})

    region, region_flags = resolve_region(record["continents"])
    category, guessed = assign_category(record["instance_of"], record["label"])
    popularity = record["sitelinks"] or 0

    flags = [*(["gap_review"] if gap_flagged else []), *(["category_guess"] if guessed else [])]
    flags.extend(region_flags)

    return AssembledEvent(
        qid=qid,
        year=display_year(record["date_year"]),
        region=region,
        country=record["countries"][0] if record["countries"] else None,
        lat=extra.get("lat"),
        lon=extra.get("lon"),
        category=category,
        famous_candidate=popularity >= FAMOUS_CANDIDATE_MIN_SITELINKS,
        popularity=popularity,
        thumb=inputs.thumbnails.get(qid),
        name=record["label"],
        aliases=merge_aliases(
            record["label"], record["aliases"], extra.get("redirect_aliases") or []
        ),
        blurb=blurb.get("text") or "",
        flags=flags,
    )


def assemble_events(inputs: RawInputs, selection: Selection) -> list[AssembledEvent]:
    """Every kept QID as a finished event, in id order — the order the core file ships in, so
    the file is byte-stable across runs and a diff means the data moved."""
    return [
        assemble_event(qid, inputs, qid in selection.gap_flagged)
        for qid in sorted(selection.kept, key=lambda key: int(key[1:]))
    ]


def core_record(event: AssembledEvent) -> dict[str, Any]:
    """One event as the game's language-independent row. `category_reviewed` ships false on
    every row and stays false until the curation pass writes it — a prefill that cannot be told
    apart from a human judgement is worse than no prefill."""
    return {
        "id": event.qid,
        "year": event.year,
        "region": event.region,
        "country": event.country,
        "lat": event.lat,
        "lon": event.lon,
        "category": event.category,
        "category_reviewed": False,
        "famous_candidate": event.famous_candidate,
        "popularity": event.popularity,
        "thumb": event.thumb,
    }


def write_core(events: list[AssembledEvent]) -> None:
    """Persist the language-independent dataset with the counts that produced it, so the file
    carries its own provenance."""
    CORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "generated": "assemble stage",
        "counts": {
            "events": len(events),
            "famous_candidates": sum(1 for event in events if event.famous_candidate),
            "with_region": sum(1 for event in events if event.region),
            "with_coordinates": sum(1 for event in events if event.lat is not None),
            "with_thumb": sum(1 for event in events if event.thumb),
            "by_category": dict(sorted(Counter(event.category for event in events).items())),
            "flagged": dict(sorted(Counter(f for e in events for f in e.flags).items())),
        },
        "events": [core_record(event) for event in events],
    }
    with CORE_PATH.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1)


def write_labels(events: list[AssembledEvent]) -> None:
    """Persist the English label file — the other half of the multilanguage seam. Keyed by QID
    rather than listed, because the game looks names up by the id it already holds."""
    document = {
        "lang": "en",
        "labels": {
            event.qid: {"name": event.name, "aliases": event.aliases, "blurb": event.blurb}
            for event in events
        },
    }
    with LABELS_PATH.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1)


def curation_rows(events: list[AssembledEvent]) -> list[dict[str, Any]]:
    """The review sheet's rows: the famous-candidate slice, most popular first, with the top
    band's `famous` column prefilled so the reviewer spends their attention on the boundary
    rather than on the obvious. Ties in popularity break on the QID so two runs order the sheet
    identically and a reviewer's row numbers stay meaningful."""
    ranked = sorted(
        (event for event in events if event.famous_candidate),
        key=lambda event: (-event.popularity, int(event.qid[1:])),
    )
    return [
        {
            "qid": event.qid,
            "name": event.name,
            "year": event.year,
            "category_auto": event.category,
            "region": event.region or "",
            "country": event.country or "",
            "popularity": event.popularity,
            "flags": ";".join(event.flags),
            "famous": "y" if rank < CURATION_PREFILLED_FAMOUS else "",
            "category_final": "",
            "notes": "",
        }
        for rank, event in enumerate(ranked)
    ]


def write_curation(rows: list[dict[str, Any]]) -> None:
    """Persist the review sheet. UTF-8 *with* a BOM and `newline=""`: this file's whole purpose
    is to be opened in Excel, which reads BOM-less UTF-8 as the local codepage and mangles
    every accented name, and which needs the csv module's own line endings rather than the
    doubled ones Python would otherwise write on Windows."""
    with CURATION_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CURATION_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def validate(events: list[AssembledEvent]) -> None:
    """Refuse to ship a dataset that cannot be true. Every check here is a shape the game would
    accept silently and then misbehave on: a year of zero (unreachable in historian's years, so
    it means the ISO conversion broke), a region outside the six the feedback axis compares, a
    category the UI has no bucket for, an id with no name, half a coordinate pair, a duplicate
    id that would give the daily draw two different answers. Raises rather than asserts, so the
    checks survive an optimised interpreter and say what was expected."""
    seen: set[str] = set()
    for event in events:
        if not isinstance(event.year, int) or isinstance(event.year, bool) or event.year == 0:
            raise RuntimeError(
                f"{event.qid} has year {event.year!r}; expected a non-zero int "
                "(historian's years have no year 0)"
            )
        if event.region is not None and event.region not in REGION_CANONICAL:
            raise RuntimeError(
                f"{event.qid} has region {event.region!r}; "
                f"expected null or one of {list(REGION_CANONICAL)}"
            )
        if event.category not in CATEGORIES:
            raise RuntimeError(
                f"{event.qid} has category {event.category!r}; expected one of {sorted(CATEGORIES)}"
            )
        if not event.name.strip():
            raise RuntimeError(f"{event.qid} has an empty name; expected every id to be nameable")
        if (event.lat is None) != (event.lon is None):
            raise RuntimeError(
                f"{event.qid} has lat={event.lat!r} lon={event.lon!r}; "
                "expected both coordinates or neither"
            )
        if event.lat is not None and not (-90 <= event.lat <= 90 and -180 <= event.lon <= 180):
            raise RuntimeError(
                f"{event.qid} has coordinates ({event.lat}, {event.lon}); "
                "expected latitude in [-90, 90] and longitude in [-180, 180]"
            )
        if event.qid in seen:
            raise RuntimeError(f"{event.qid} appears twice; expected one row per id")
        seen.add(event.qid)


def percent(count: int, total: int) -> str:
    """A share formatted for the report, safe when the denominator is zero."""
    if total == 0:
        return "  n/a"
    return f"{100 * count / total:5.1f}%"


def file_sizes(path: Path) -> tuple[int, int]:
    """A written file's size on disk and gzipped, in bytes. The compressed number is the one
    that matters: the game serves these over HTTP with compression on, so raw bytes overstate
    what a player waits for by roughly a factor of five."""
    raw = path.read_bytes()
    return len(raw), len(gzip.compress(raw))


def sample_events(events: list[AssembledEvent]) -> list[tuple[str, AssembledEvent]]:
    """Five events worth reading with your own eyes, each with the reason it was picked: the
    most famous BCE event (the one that proves the year conversion did not silently produce an
    off-by-one or a positive year), the three most famous events overall, and the median-
    popularity event, which is what the bulk of the pool actually looks like. Chosen by rank
    rather than at random, so a diff between runs means the data moved."""
    if not events:
        return []

    ranked = sorted(events, key=lambda event: (-event.popularity, int(event.qid[1:])))
    chosen: list[tuple[str, AssembledEvent]] = []
    taken: set[str] = set()

    for reason, pool in (
        ("most famous BCE", [event for event in ranked if event.year < 0]),
        ("most famous", ranked[:1]),
        ("2nd most famous", ranked[1:2]),
        ("3rd most famous", ranked[2:3]),
        ("median popularity", [ranked[len(ranked) // 2]]),
    ):
        pick = next((event for event in pool if event.qid not in taken), None)
        if pick is None:
            continue
        chosen.append((reason, pick))
        taken.add(pick.qid)
    return chosen


def print_waterfall(selection: Selection) -> None:
    """The pool narrowing, rule by rule, with what each rule took. This is the section that
    decides whether the filter is doing its job or eating the dataset."""
    print("\n1. POOL WATERFALL")
    remaining = selection.started
    print(f"  start (has_event_date)          {remaining:>6}")
    for rule in selection.rules:
        remaining -= rule.dropped
        print(f"  - {rule.name:<30} {rule.dropped:>6}  ->  {remaining} remain")
        for example in rule.examples:
            print(f"        {example}")
    print(f"  final pool                      {len(selection.kept):>6}")
    print(f"  flagged gap_review (kept)       {len(selection.gap_flagged):>6}")


def print_report(
    events: list[AssembledEvent], selection: Selection, curation_row_count: int
) -> None:
    """The acceptance evidence: what the filter did, how the survivors distribute over the
    game's axes, how well the optional fields are covered, what the answer-pool candidates look
    like at the top, and five records to read. The top-15 list is the honest eyeball test —
    these are the events a player would be asked about, and if beer is among them the filter is
    wrong however good the counts look."""
    total = len(events)
    flags = Counter(flag for event in events for flag in event.flags)

    print("\n" + "=" * 78)
    print("ASSEMBLE REPORT — raw pipeline output -> shipping dataset")
    print("=" * 78)

    print_waterfall(selection)

    print("\n2. CATEGORY DISTRIBUTION (auto-prefill, unreviewed)")
    for category, count in Counter(event.category for event in events).most_common():
        print(f"  {category:<24} {count:>6}  {percent(count, total)}")

    print("\n3. REGION DISTRIBUTION")
    regions = Counter(event.region for event in events)
    for region in REGION_CANONICAL:
        print(f"  {region:<24} {regions[region]:>6}  {percent(regions[region], total)}")
    print(f"  {'(null)':<24} {regions[None]:>6}  {percent(regions[None], total)}")

    print("\n4. COVERAGE")
    for name, count in (
        ("thumbnail", sum(1 for event in events if event.thumb)),
        ("coordinates", sum(1 for event in events if event.lat is not None)),
        ("country", sum(1 for event in events if event.country)),
        ("blurb", sum(1 for event in events if event.blurb)),
        ("aliases", sum(1 for event in events if event.aliases)),
    ):
        print(f"  {name:<24} {count:>6}  {percent(count, total)}")

    famous = [event for event in events if event.famous_candidate]
    print(f"\n5. FAMOUS CANDIDATES (sitelinks >= {FAMOUS_CANDIDATE_MIN_SITELINKS})")
    print(f"  count                    {len(famous):>6}  {percent(len(famous), total)}")
    print(f"  top {TOP_POPULARITY_SHOWN} by popularity:")
    ranked = sorted(famous, key=lambda event: (-event.popularity, int(event.qid[1:])))
    for event in ranked[:TOP_POPULARITY_SHOWN]:
        print(f"      {event.popularity:>4}  {event.year:>6}  {event.name}  [{event.category}]")

    print("\n6. FLAGS")
    for flag in ("gap_review", "category_guess", "region_missing", "region_multi"):
        print(f"  {flag:<24} {flags[flag]:>6}  {percent(flags[flag], total)}")
    print(f"  {'famous_candidate':<24} {len(famous):>6}  {percent(len(famous), total)}")

    print("\n7. OUTPUT FILES")
    for path in (CORE_PATH, LABELS_PATH, CURATION_PATH):
        raw, packed = file_sizes(path)
        print(f"  {path.name:<20} {raw / 1024:>8.1f} KB raw  {packed / 1024:>8.1f} KB gzipped")
    print(f"  curation.csv rows    {curation_row_count:>8} (excluding header)")

    print("\n8. SAMPLES (core + label)")
    for reason, event in sample_events(events):
        print(f"  [{reason}] {event.qid} — {event.name}")
        print(
            f"      year {event.year} | region {event.region} | country {event.country} | "
            f"{event.category} | popularity {event.popularity} | "
            f"famous_candidate={event.famous_candidate}"
        )
        print(f"      coords      : {event.lat}, {event.lon}")
        print(f"      thumb       : {event.thumb or '—'}")
        print(f"      flags       : {', '.join(event.flags) or '—'}")
        shown = ", ".join(event.aliases[:6])
        if len(event.aliases) > 6:
            shown += f" (+{len(event.aliases) - 6} more)"
        print(f"      aliases ({len(event.aliases):>3}): {shown or '—'}")
        blurb = event.blurb if len(event.blurb) <= 110 else event.blurb[:107] + "..."
        print(f"      blurb       : {blurb or '—'}")

    print(f"\nWROTE {CORE_PATH}\n      {LABELS_PATH}\n      {CURATION_PATH}")
    print("=" * 78, flush=True)


def main() -> int:
    """Load, select, derive, validate, persist, report. Returns non-zero only if the pool came
    out empty, which means the run is not evidence of anything."""
    # Event names carry accents and non-Latin scripts; a redirected stdout on Windows defaults
    # to the ANSI codepage and would raise UnicodeEncodeError on them.
    sys.stdout.reconfigure(encoding="utf-8")

    print("Loading raw inputs (candidates, enriched, augmented, 732 day feeds)...", flush=True)
    inputs = load_inputs()
    print(
        f"Loaded {len(inputs.blurbs)} blurbs, {len(inputs.enriched)} enriched records, "
        f"{len(inputs.augmented)} augmented records, {len(inputs.thumbnails)} thumbnails.",
        flush=True,
    )

    selection = select_pool(inputs)
    events = assemble_events(inputs, selection)
    validate(events)

    rows = curation_rows(events)
    write_core(events)
    write_labels(events)
    write_curation(rows)

    print_report(events, selection, len(rows))

    if not events:
        print("ERROR: the pool came out empty; expected thousands of events.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
