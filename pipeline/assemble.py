"""Assemble the harvested, enriched and augmented raw data into the game's shipping files.

Fourth and final stage of the histle dataset pipeline, and the first one that makes
judgements. It reads `data/raw/candidates.json` (blurbs and harvest years),
`data/raw/enriched.json` (Wikidata facts), `data/raw/augmented.json` (redirect aliases and
coordinates), the untouched per-day feeds under `data/raw/onthisday/` (the only place a
thumbnail URL exists) and two curation inputs — `data/manual_events.draft.json` (events the
feed structurally lacks) and the previous `data/curation.csv` (a human's rulings on the last
sheet) — and writes three files: `data/events.core.json`, `data/labels.en.json` and
`data/curation.csv`.

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

Boundary. Input is six things on disk; output is three files, one backup and a printed
report. No network. Everything derived from the raw cache is regenerable, so this script can
be re-run at will — but `data/curation.csv` is also an *input* now, and that is the one thing
here that is not reproducible from the caches. A run reads the existing sheet, keeps every
cell a human deviated from its prefill, regenerates the sheet under the current taxonomy, and
writes the kept cells back onto it; the previous sheet is copied to
`data/curation.backup-<date>.csv` before it is overwritten, because a bug in the read-back
would otherwise destroy the only copy of work that cannot be recomputed.

What the sheet decides. The `famous` column is the answer pool: `y` makes an event a
`famous_candidate` in the core file, `n` removes it, `category_final` overrides the prefilled
kind and sets `category_reviewed`, and a `notes` cell containing "drop" deletes the event from
both output files. The top band of the sheet ships prefilled `y`, so the answer pool is a few
hundred recognisable events from the first run rather than every one of the 2596 items that
merely cleared the sitelink bar — a prefill a human can veto, not a guess the game hides.

Manual additions. The "On This Day" feed represents some events only through their artifact:
there is a page for the printing press, none for its invention. `manual_events.draft.json`
carries those as hand-written records, which join the pool with an `M`-prefixed id and
`"manual": true`, after a normalised containment check against every assembled name and alias
drops the ones the harvest already found. They have no sitelinks to rank by, so they take a
fixed popularity that seats them among the icons rather than at either extreme.

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
  is a genuine coin flip.
- Region resolves down a four-step chain, because P30 alone left a quarter of the pool null.
  An event whose countries sit on two or more continents — or that Wikidata types a world war —
  is "multi", a seventh legal region meaning "this genuinely spans continents", which the game
  reads as no signal rather than as a wrong answer. Otherwise a single canonical P30 continent
  answers itself. Otherwise the first country goes through `COUNTRY_TO_CONTINENT`, a static
  table covering every country label the pool carries, historical states included. Otherwise
  coordinates fall through crude bounding boxes. Only then is the field null, and still
  flagged `region_missing`.
  P30 carrying *several* continents is deliberately not a multi trigger, and that is the
  chain's load-bearing subtlety: Wikidata gives the Battle of Stalingrad "Asia, Europe" because
  Russia is transcontinental, not because the battle was — 377 of the 530 such items are
  Russian, Soviet or Turkish. Those fall through to the country table, which resolves them to
  the continent of the state's core, and Stalingrad comes out Europe.
- Thumbnails exist in exactly one place: the raw per-day feeds. Neither Wikidata nor the
  candidate file carries an image, so the feeds are scanned once here and reduced to a
  QID -> URL map. An entity sighted on several days can have several thumbnails; the sighting
  with the lowest `page_index` wins, the same centrality tiebreak the harvest used to pick a
  blurb, so the picture comes from the day the feed treated the entity as the subject.
- The category prefill resolves in three passes — an exact P31 class map, then keyword
  patterns over the P31 class names, then the same patterns over the label — and an item
  voting for several kinds is settled by a fixed precedence, not by whichever class sorted
  first. Precedence puts the three violence kinds ahead of politics because the fallback *is*
  politics: an item that reached the end with no evidence is filed there and flagged
  `category_guess`, so "politics & diplomacy" in the sheet means either a real political event
  or a shrug, and only the flag separates them.
- The taxonomy conflates two things a single label cannot carry at once: what *form* an event
  took and what *domain* it belonged to. The Kennedy assassination is an attack by form and
  politics by domain, and precedence has to pick one. So the losing votes are no longer thrown
  away — every kind the evidence suggested is kept as `tags`, with the ruling kind first and
  the rest in precedence order, and `tags[0]` is the category by construction. Tags are
  *evidence*, gathered across all three passes; the category is a *ruling*, still decided by
  the first pass that says anything. Nothing in the game reads tags yet: the primary kind
  remains the single scored axis, and what tags are for is a later design.
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
import shutil
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_PATH = REPO_ROOT / "data" / "raw" / "candidates.json"
ENRICHED_PATH = REPO_ROOT / "data" / "raw" / "enriched.json"
AUGMENTED_PATH = REPO_ROOT / "data" / "raw" / "augmented.json"
ONTHISDAY_DIR = REPO_ROOT / "data" / "raw" / "onthisday"
MANUAL_PATH = REPO_ROOT / "data" / "manual_events.draft.json"

CORE_PATH = REPO_ROOT / "data" / "events.core.json"
LABELS_PATH = REPO_ROOT / "data" / "labels.en.json"
CURATION_PATH = REPO_ROOT / "data" / "curation.csv"

FEED_TYPES = ("events", "selected")

# An item this far from the year its blurb placed it under is not that blurb's event.
YEAR_GAP_DROP_ABOVE = 100
YEAR_GAP_FLAG_ABOVE = 10

REVIEW_CANDIDATE_MIN_SITELINKS = 25
CURATION_PREFILLED_FAMOUS = 400
TOP_POPULARITY_SHOWN = 15
DROP_EXAMPLES_SHOWN = 3

# Manual additions have no sitelinks, so their popularity is a placement decision rather than a
# measurement. 60 seats them above the long tail (the pool's median candidate) and below the
# genuine icons, which is where the autocomplete has to surface them for a player typing
# "printing press" without letting them outrank the Second World War.
MANUAL_POPULARITY = 60

# A containment test on very short strings is noise, not evidence: "Rome" is inside a hundred
# unrelated names. Below this many characters a surface only matches exactly.
OVERLAP_MIN_CONTAINMENT_CHARS = 8

# Leading words the overlap check ignores, because the draft writes prose names and Wikidata
# writes titles: "the Hijra" and "Hijra" are the same event.
ARTICLES = frozenset({"the", "a", "an"})

# The token a curator writes in `notes` to delete an event outright.
DROP_TOKEN = "drop"

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

BATTLE = "battle & war"
REVOLUTION = "revolution & uprising"
ATTACK = "attack & violence"
DISASTER = "disaster & accident"
POLITICS = "politics & diplomacy"
SCIENCE = "science & exploration"
CULTURE = "culture & religion"
SOCIETY = "society & economy"

CATEGORIES = frozenset(
    {BATTLE, REVOLUTION, ATTACK, DISASTER, POLITICS, SCIENCE, CULTURE, SOCIETY}
)

# Older spellings of the same kinds, tolerated on the way *in* (the manual draft was written
# against a taxonomy that has since been renamed twice) and never written out. Only unambiguous
# renames belong here: the retired "war & conflict" split three ways and cannot be mapped.
CATEGORY_ALIASES = {
    "revolt & revolution": REVOLUTION,
    "attack & terror": ATTACK,
    "science & technology": SCIENCE,
    "exploration & discovery": SCIENCE,
}

# How a multi-voting item is settled, and the order tags are listed in. Three readings of
# organised violence lead, most specific first: an attack is aimed at people who cannot fight
# back, a battle is two armies, an uprising is a population against its own state — the Amritsar
# massacre is an attack even though the army did it, and the Easter Rising is an uprising even
# though it was fought.
#
# The line inside that third kind is who is doing the challenging. Revolution and uprising mean
# a bottom-up or mass challenge to the political order; a coup d'état is an elite or
# institutional seizure of the government by people already inside it, which is a political
# manoeuvre and files under politics & diplomacy. Self-coups and the coup-adjacent classes
# follow the same rule.
#
# Politics sits last because it doubles as the fallback: an item that reached the end with no
# evidence is filed there and flagged, so the flag, not the label, is what separates a political
# event from a shrug. A coup therefore lands in the same bucket as a shrug — the flag is what
# tells them apart, exactly as it does for every other politics row.
CATEGORY_PRECEDENCE = (ATTACK, BATTLE, REVOLUTION, DISASTER, SCIENCE, CULTURE, SOCIETY, POLITICS)
FALLBACK_CATEGORY = POLITICS

# Exact P31 class -> category. Written out class by class rather than by pattern because these
# are the classes the pool actually carries in quantity, and an exact name is the one mapping
# that cannot misfire ("airliner bombing" is an attack, "aviation accident" is not).
CLASS_CATEGORY: dict[str, str] = {
    name: category
    for category, names in (
        (
            BATTLE,
            (
                "battle", "naval battle", "war", "world war", "civil war", "series of wars",
                "war of independence", "war of national liberation", "war of succession",
                "religious war", "siege", "sack", "storming", "military operation",
                "military campaign", "military expedition", "military raid", "military occupation",
                "military intervention", "military alliance", "armed conflict",
                "military conflict", "international conflict", "ethnic conflict",
                "environmental conflict", "conflict", "theater of war", "war front",
                "naval warfare", "invasion", "conquest", "annexation by force", "occupation",
                "offensive", "landing operation", "bombardment", "aerial bombing of a city",
                "airstrike", "air raid", "razzia", "ambush", "skirmish", "last stand",
                "dogfight", "war crime", "airspace intrusion", "border incident",
                "hostage-rescue mission", "covert operation", "liberation", "capitulation",
                "sinking of a warship",
            ),
        ),
        (
            REVOLUTION,
            (
                "mutiny", "rebellion", "princely rebellion", "slave rebellion", "peasant revolt",
                "revolt", "revolution", "insurgency", "resistance movement",
            ),
        ),
        (
            ATTACK,
            (
                "massacre", "school massacre", "genocide", "ethnic cleansing", "pogrom",
                "terrorist attack", "coordinated terrorist attack", "domestic terrorist attack",
                "terrorist organization", "failed terrorism plot", "bomb attack", "bombing",
                "suicide bombing", "suicide attack", "suicide car bombing", "truck bombing",
                "airliner bombing", "train attack", "vehicle-ramming attack", "attack",
                "shooting attack", "stabbing attack", "mass stabbing", "aircraft hijacking",
                "aircraft shootdown", "shootout", "fusillade", "hostage taking", "purge",
                "persecution", "ethnic violence", "mass killing", "mass murder", "mass shooting",
                "school shooting", "university shooting", "spree shooting", "shooting",
                "lynching", "hate crime", "assassination", "assassination attempt",
                "political murder", "magnicide",
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
                "coup d'état", "attempted coup d'état", "military coup", "self-coup",
                "political movement", "political event", "political union", "government",
                "presidential term", "legislative term", "enlargement of the European Union",
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
                "underground nuclear weapons test", "nuclear test series", "total solar eclipse",
                "solar eclipse", "lunar eclipse", "transit of Venus", "astronomical observation",
                "experiment", "invention", "scientific journal", "scientific theory",
                "UFO sighting", "clinical trial", "expedition", "research expedition",
                "polar expedition", "mountaineering expedition", "exploration", "voyage",
                "circumnavigation", "marine navigation", "discovery", "first ascent", "flight",
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
                "sports riot", "ethnic riot", "looting", "murder", "attempted murder",
                "murder–suicide", "homicide", "killing", "matricide",
                "pedicide", "suicide", "pilot suicide", "death", "capital punishment",
                "public execution", "execution", "kidnapping",
                "robbery", "bank robbery", "theft", "rape", "assault",
                "police brutality in the United States", "police operation", "police raid",
                "prison escape", "criminal case", "legal case", "trial", "war crimes trial",
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
            r"\b(war|wars|warfare|battle|siege|invasion|invades?|military|bombard\w*|airstrike"
            r"|conquest|raid|offensive|shelling|guerrilla|hostilities|army|troops|naval)\b",
            BATTLE,
        ),
        (
            r"\b(revolution|revolutionary|rebellion|revolt|uprising|insurrection|insurgen\w*"
            r"|mutiny|resistance movement)\b",
            REVOLUTION,
        ),
        # Deliberately narrower than its class map: a bare "attack" would pull the Attack on
        # Pearl Harbor out of battle & war, and attack outranks battle in the precedence. Only
        # phrases that name violence against people who are not an opposing army appear here.
        (
            r"\b(terroris\w*|massacre|genocide|pogrom|ethnic cleansing|assassinat\w*|hijack\w*"
            r"|suicide bombing|car bomb\w*|truck bomb\w*|bomb attack|bombing attack"
            r"|mass shooting|school shooting|shooting spree|mass murder|mass killing"
            r"|atrocit\w*|lynching)\b",
            ATTACK,
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
            # "assassinat*" appears here as well as under attack & violence, on purpose and as
            # the only such overlap: an assassination is an attack by form and a political act
            # by domain, and that double reading is the whole reason tags exist. It cannot
            # change a category — attack outranks politics, and the class map decides before
            # this pass runs — so its only effect is to put the second, true kind in `tags`.
            r"|inaugurat\w*|annexation|annexes|partition|impeach\w*|edict|scandal|crisis"
            r"|coup|putsch|junta|assassinat\w*)\b",
            POLITICS,
        ),
        (
            r"\b(spaceflight|spacecraft|space|satellite|rocket|orbit\w*|eclipse|comet|telescope"
            r"|patent|invention|invents?|experiment|vaccine|computer|internet|nuclear test"
            r"|transit of|expedition|exploration|explorers?|voyage|circumnavigat\w*|ascent"
            r"|discovery|discovers?|landfall|first flight)\b",
            SCIENCE,
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

# The seventh legal region: an event whose countries sit on two or more continents. Not a
# missing value — a positive claim the game reads as "no region signal here".
REGION_MULTI = "multi"

# The P31 class that makes an event multi-continent by definition, whatever its country list
# happens to name.
WORLD_WAR_CLASS = "world war"

# Country label -> continent, covering every value the pool's `countries` carries. Modern states
# are geography; historical states are judgement, and three rules settle them.
#
# 1. A transcontinental state goes to the continent of its capital or political core. That is
#    what makes the chain work at all: Wikidata gives half of Russia's events "Asia, Europe",
#    and resolving Russia to Europe puts the Battle of Stalingrad in Europe instead of nowhere.
#    So: Russia, the Russian Empire, the Soviet Union and the RSFSR -> Europe (Moscow, St
#    Petersburg); the Ottoman and Byzantine Empires and the Latin Empire -> Europe
#    (Constantinople sits on the European shore); the Roman Empire -> Europe (Rome); the Mongol
#    Empire -> Asia (Karakorum); the maritime empires -> their metropole, so the British,
#    Spanish and Portuguese Empires are Europe, not the places they ruled.
# 2. Modern Turkey breaks with its predecessor deliberately: Ankara and 97% of the landmass are
#    in Asia, so Turkey -> Asia while the Ottoman Empire -> Europe. The visible consequence is
#    that a Byzantine-Ottoman clash at the straits lists countries on two continents and comes
#    out "multi", which for a battle fought across the Bosphorus is the honest answer.
# 3. Where the sovereign and the ground disagree, the ground wins for territories and the
#    capital wins for states. Greenland -> North America (Danish, but not in Europe); the
#    Caribbean, including Trinidad and Tobago, -> North America; the Ayyubid and Fatimid
#    Caliphates -> Africa (Cairo) though both held the Levant; the United Arab Republic ->
#    Africa (Cairo) though it was Egypt and Syria; Cyprus and Venetian Cyprus -> Europe, which
#    is politics over geography and the one place this table knowingly picks the softer claim.
COUNTRY_TO_CONTINENT: dict[str, str] = {
    name: continent
    for continent, names in (
        (
            "Europe",
            (
                "United Kingdom", "France", "Italy", "Germany", "Spain", "Greece",
                "Kingdom of England", "Poland", "Belgium", "Netherlands", "Portugal", "Finland",
                "United Kingdom of Great Britain and Ireland", "Ireland", "Sweden", "Switzerland",
                "Norway", "Kingdom of Scotland", "Kingdom of Spain", "Romania", "Bulgaria",
                "Serbia", "Kingdom of France", "Nazi Germany", "Vatican City", "Denmark",
                "Austria", "Estonia", "Kingdom of Great Britain", "Hungary", "Holy Roman Empire",
                "Croatia", "Czech Republic", "Latvia", "Lithuania", "Weimar Republic", "Malta",
                "Kingdom of Ireland", "Kingdom of Italy", "Albania", "Belarus", "Iceland",
                "Kingdom of Portugal", "German Reich", "Crown of Castile",
                "Bosnia and Herzegovina", "Ancient Rome", "French First Republic", "Moldova",
                "Slovakia", "German Empire", "North Macedonia", "Papal States", "Yugoslavia",
                "Polish People's Republic", "West Germany", "Spanish Republic at War", "Slovenia",
                "Second Spanish Republic", "Kingdom of Greece", "Austria–Hungary", "Luxembourg",
                "Kingdom of Hungary", "Federal Republic of Yugoslavia", "Northern Ireland",
                "Kingdom of Romania", "French Third Republic", "Czechoslovakia",
                "Commonwealth of England", "Socialist Federal Republic of Yugoslavia",
                "Faroe Islands", "Austrian Empire", "Tsardom of Russia", "Spanish Netherlands",
                "Andorra", "Monaco", "First French Empire", "Dutch Republic",
                "Polish–Lithuanian Commonwealth", "Habsburg Netherlands", "Kingdom of Toledo",
                "Liechtenstein", "Kingdom of Wessex", "Emirate of Granada",
                "Independent State of Croatia", "Ukrainian People's Republic", "Kingdom of Saxony",
                "Prussia", "Kingdom of Prussia", "San Marino", "Crown of Aragon",
                "Republic of Florence", "Second Polish Republic", "Wales", "Kingdom of Sicily",
                "Kingdom of Naples", "Kingdom of Serbia", "Republic of Venice",
                "Spain under the Restoration", "Grand Principality of Moscow", "Russian Republic",
                "United Kingdom of the Netherlands", "Hospitaller Malta", "Francoist Spain",
                "Duchy of Milan", "Swedish Empire", "Hungarian People's Republic",
                "Seventeen Provinces", "Kingdom of Germany", "Bourbon Restoration in France",
                "Socialist Republic of Romania", "Second French Empire", "Duchy of Brabant",
                "Francia", "Protectorate of Bohemia and Moravia", "Old Swiss Confederacy",
                "Principality of Catalonia", "Gibraltar", "July Monarchy", "Latin Empire",
                "West Francia", "Kingdom of Yugoslavia", "Kingdom of Bohemia", "Kingdom of Poland",
                "Kingdom of Sardinia", "League of Lezhë", "General Government",
                "Federal State of Austria", "Lotharingia", "Habsburg monarchy",
                "Second Bulgarian Empire", "Principality of Bulgaria",
                "Government of National Salvation", "Czechoslovak Socialist Republic",
                "People's Republic of Bulgaria", "Thespiae", "Visigothic Kingdom",
                "Hispania Ulterior", "Dauphiné", "Duchy of Brittany", "Civitas Schinesghe",
                "State of the Teutonic Order", "Duchy of Holstein", "First Republic of Austria",
                "Axis occupation of Serbia", "Free City of Kraków", "Archbishopric of Magdeburg",
                "Serbian Despotate", "Kingdom of the Netherlands", "Denmark–Norway",
                "Kingdom of Sicily under Spanish rule", "Central Europe", "Kingdom of Dublin",
                "Austrian Netherlands", "Austrian Lombardy", "Gaelic Ireland",
                "Republic of Bosnia and Herzegovina", "Principality of Moldavia",
                "Serbia and Montenegro", "Third Czechoslovak Republic", "Irish Republic",
                "Kingdom of Pamplona", "Classical Athens", "County of Flanders",
                "Principality of Transylvania", "Socialist Republic of Serbia",
                "Principality of Wallachia", "Kingdom of Mercia",
                "Union between Sweden and Norway", "Angevin Empire",
                "West Ukrainian People's Republic", "Kingdom of Leon", "Catholic Monarchy",
                "Jersey", "Republic of Lithuania", "Grand Duchy of Lithuania",
                "Kingdom of the Two Sicilies", "State of Presidi", "Duchy of Mirandola",
                "Cretan State", "Montenegro", "Kingdom of Montenegro",
                "Kingdom of Serbs, Croats and Slovenes", "Memelland", "Vichy France",
                "Lithuanian Soviet Socialist Republic", "Saar Protectorate", "Batavian Republic",
                "Territory of the Saar Basin", "Republic of Genoa", "Crown Colony of Malta",
                "Valencian Community", "Emirate of Córdoba", "County of Toulouse",
                "Duchy of Normandy", "Sweden-Finland", "Novgorodian Land", "Ukraine",
                "Republic of Crimea", "Kherson Oblast", "Zaporozhye Oblast",
                "Donetsk People's Republic", "Luhansk People's Republic", "European Union",
                "Taifa of Granada", "Taifa of Valencia (third period)",
                "taifa of Algeciras and Ronda", "Hispanic Monarchy", "Cyprus", "Venetian Cyprus",
                "Morea eyalet", "Ottoman Greece", "Crimean Khanate", "Chechnya",
                "Chechen Republic of Ichkeria", "allied-occupied Germany", "German-occupied Poland",
                "Russian Socialist Federative Soviet Republic", "Russia", "Soviet Union",
                "Ottoman Empire", "Roman Empire", "Western Roman Empire", "Byzantine Empire",
                "Russian Empire", "British Empire", "Spanish Empire", "Portuguese Empire",
                "German Democratic Republic", "Kosovo", "French Fourth Republic",
                "Kingdom of Northumbria",
            ),
        ),
        (
            "Asia",
            (
                "Turkey", "India", "People's Republic of China", "Japan", "Iran", "Indonesia",
                "Pakistan", "South Korea", "Syria", "Israel", "Philippines", "Iraq", "Vietnam",
                "Afghanistan", "Lebanon", "Malaysia", "Taiwan", "Republic of China", "Sri Lanka",
                "Thailand", "Bangladesh", "British India", "Myanmar", "North Korea",
                "Empire of Japan", "Azerbaijan", "Nepal", "Singapore", "Saudi Arabia", "Armenia",
                "Yemen", "Qing dynasty", "Georgia", "South Vietnam", "Palestine", "Kazakhstan",
                "Cambodia", "Jordan", "United Arab Emirates", "Mandatory Palestine", "British Raj",
                "First Republic of South Korea", "Joseon", "Abbasid Caliphate", "Pahlavi Iran",
                "Ming dynasty", "Kingdom of Jerusalem", "Qatar", "Uzbekistan", "Timor-Leste",
                "Mongolia", "Turkmenistan", "Maldives", "China", "Kyrgyzstan", "Dutch East Indies",
                "Sultanate of Rum", "British Malaya", "Tajikistan", "Tokugawa shogunate",
                "Fourth Republic of South Korea", "Bhutan", "Ba'athist Iraq", "British Hong Kong",
                "Azerbaijan Soviet Socialist Republic", "Sasanian Empire", "Kuwait", "Laos",
                "Oman", "Yuan dynasty", "Chinese Empire", "Tang dynasty",
                "Government of Meiji Japan", "Kingdom of Yemen",
                "Israel and The Occupied Territories", "Kingdom of Iraq", "Safavid Iran",
                "French Indochina", "Occupation of Japan", "First Republic of Iraq",
                "East Pakistan", "Ottoman Syria", "Nizari Ismaili state", "Bahrain",
                "Israeli-occupied territories", "Han dynasty", "Islamic Emirate of Waziristan",
                "Achaemenid Empire", "Korea under Japanese rule",
                "United States Army Military Government in Korea",
                "Second Republic of South Korea", "Korean Empire", "Yemen Arab Republic",
                "Emirate of Afghanistan", "Democratic Kampuchea", "People's Republic of Kampuchea",
                "Palestinian National Authority", "Republic of Ezo", "Republic of Formosa",
                "British Ceylon", "Dominion of Ceylon", "Kazakh Soviet Socialist Republic",
                "Hatti", "Song dynasty", "Qajar dynasty", "Ryukyu Kingdom",
                "Government of the Grand National Assembly", "Republic of Afghanistan", "Tibet",
                "Xin dynasty", "Southern Song dynasty", "Second Syrian Republic", "State of Syria",
                "Syrian Arab Republic", "Syrian Republic under French mandate", "Outer Mongolia",
                "Siam", "Kingdom of Georgia", "Nguyen dynasty", "French Tonkin", "Manchukuo",
                "Dominion of India", "Kingdom of Mysore", "Kamakura shogunate", "Safavid Shirvan",
                "Federation of Malaya", "Cao Wei", "County of Tripoli",
                "Occupied Enemy Territory Administration", "North Vietnam",
                "Interim Government of Iran", "Pahlavi dynasty", "Southern Ming dynasty",
                "Empire of Trebizond", "Gaza Strip", "Buyid dynasty", "Samanid Empire", "Macau",
                "Brunei", "Mataram Sultanate", "Neo-Babylonian Empire", "Hamdanid dynasty",
                "Islamic Republic of Afghanistan", "Uzbek Soviet Socialist Republic",
                "British North Borneo", "Republic of Georgia (1990–1992)", "Umayyad Caliphate",
                "Mongol Empire", "Empire of Nicaea", "Principality of Antioch",
                "Japanese occupation of the Philippines", "Japanese occupation of Singapore",
                "Insular Government of the Philippine Islands", "Commonwealth of the Philippines",
                "Captaincy General of the Philippines", "Occupied Palestinian territories",
                "All-Palestine Protectorate", "United States occupation of the Ryukyu Islands",
                "Democratic Republic of Georgia", "Republic of Artsakh",
                "Azerbaijan Democratic Republic", "People's Committee of North Korea",
            ),
        ),
        (
            "Africa",
            (
                "Egypt", "South Africa", "Nigeria", "Algeria", "Morocco", "Somalia", "Libya",
                "Tunisia", "Ethiopia", "Kenya", "Democratic Republic of the Congo", "Sudan",
                "Angola", "Zimbabwe", "Cameroon", "Madagascar", "Mali", "Uganda", "Tanzania",
                "Guinea", "Ivory Coast", "South Sudan", "Burundi", "Rwanda", "Ethiopian Empire",
                "Guinea-Bissau", "Mozambique", "Liberia", "Niger", "Republic of Upper Volta",
                "Ghana", "Mauritania", "Malawi", "Senegal", "Burkina Faso", "Comoros", "Eritrea",
                "Rhodesia", "Kingdom of Egypt", "Central African Republic", "Sierra Leone", "Chad",
                "Somali Democratic Republic", "Seychelles", "Zulu Kingdom",
                "French protectorate in Morocco", "Republic of the Congo", "Gabon", "Benin",
                "Togo", "Equatorial Guinea", "Lesotho", "French protectorate of Tunisia",
                "Zanzibar Islands", "Mauritius", "Republic of Sudan", "Gold Coast Colony",
                "Zimbabwe Rhodesia", "Congo Free State", "São Tomé and Príncipe",
                "Spanish protectorate in Morocco", "Saadi dynasty", "Mahdist Sudan",
                "Republic of Egypt", "Khedivate of Egypt", "South African Republic",
                "Northern Rhodesia", "Ruanda-Urundi", "Zambia", "Botswana", "Southern Rhodesia",
                "Namibia", "Nyasaland", "Derg", "Central African Empire", "United Arab Republic",
                "Union of South Africa", "Djibouti", "Eswatini", "Transkei", "Kingdom of Rwanda",
                "Ciskei", "Kenya Colony", "Boer republic", "Allied administration of Libya",
                "Kingdom of Libya", "Ancient Egypt", "Ancient Carthage", "Ptolemaic Kingdom",
                "Fatimid Caliphate", "Almohad Caliphate", "Marinid dynasty", "Regency of Algiers",
                "Tripoli eyalet", "Ifriqiya", "Songhai Empire", "Sultanate of Egypt",
                "Ayyubid Sultanate", "Democratic Republic of Sudan",
            ),
        ),
        (
            "North America",
            (
                "United States", "Canada", "Mexico", "Cuba", "Haiti", "Guatemala", "Jamaica",
                "Honduras", "Panama", "Dominican Republic", "El Salvador",
                "Confederate States of America", "Thirteen Colonies", "Nicaragua", "Costa Rica",
                "The Bahamas", "Saint-Domingue", "New Spain", "Belize", "Republic of Texas",
                "First Mexican Republic", "Puerto Rico", "New France", "Aztec Empire",
                "Trinidad and Tobago", "Antigua and Barbuda", "Aruba", "Grenada", "Bermuda",
                "Greenland", "British America", "Captaincy General of Cuba",
                "Third Dominican Republic", "Colony of Jamaica", "Mosquitia", "British West Indies",
                "West Florida", "Centralist Republic of Mexico", "Windward Islands",
                "British Windward Islands", "Anguilla", "British Virgin Islands", "Saint Martin",
                "Turks and Caicos Islands", "United States Virgin Islands", "Dominica", "Barbados",
                "Curaçao", "Saint Lucia", "Saint Vincent and the Grenadines", "Province of Canada",
            ),
        ),
        (
            "South America",
            (
                "Brazil", "Argentina", "Chile", "Peru", "Venezuela", "Colombia", "Bolivia",
                "Ecuador", "Paraguay", "Uruguay", "Guyana", "Suriname", "Empire of Brazil",
                "Viceroyalty of the Río de la Plata", "Colonial Brazil",
                "United States of Venezuela", "Inca Empire", "Viceroyalty of Peru",
                "United Provinces of the Río de la Plata", "State of Brazil", "Patria Vieja",
                "New Kingdom of Granada", "Viceroyalty of New Granada",
            ),
        ),
        (
            "Oceania",
            (
                "Australia", "New Zealand", "Papua New Guinea", "Fiji", "Palau", "Samoan Islands",
                "Niue", "Vanuatu", "Tonga", "Federated States of Micronesia", "Marshall Islands",
                "United Tribes of New Zealand", "Colony of Victoria",
                "Colony of Western Australia", "Kingdom of Hawaiʻi",
            ),
        ),
    )
    for name in names
}

# Values in the `countries` field that no continent honestly fits. Listed rather than left to
# fall through so a genuinely new label shows up in the report as unmapped instead of hiding
# among these. "recurring sporting event edition" is an upstream defect — a P31 class that
# leaked into a country slot — and is kept here to make it visible, not to excuse it.
UNMAPPED_COUNTRIES = frozenset(
    {
        "Commonwealth of Nations",
        "internationality",
        "Antarctic Treaty area",
        "French Southern and Antarctic Lands",
        "Chagos Archipelago",
        "recurring sporting event edition",
    }
)

# Last resort, and crude on purpose: an event with coordinates but no usable country or
# continent gets a continent from the box its point falls in, first match winning. The boxes
# overlap — Europe's eastern edge and Africa's northern one both cover Anatolia — so the order
# encodes the tiebreak, and the whole table is wrong for islands and for anything near a
# boundary. That is acceptable for a step that only runs after three better ones have failed.
CONTINENT_BOXES = (
    ("Oceania", -50.0, 0.0, 110.0, 180.0),
    ("Oceania", -50.0, 0.0, -180.0, -130.0),
    ("South America", -56.0, 13.0, -82.0, -34.0),
    ("North America", 7.0, 84.0, -170.0, -52.0),
    ("Europe", 36.0, 72.0, -25.0, 40.0),
    ("Africa", -35.0, 37.0, -18.0, 52.0),
    ("Asia", 0.0, 78.0, 25.0, 180.0),
)

CURATION_COLUMNS = (
    "qid",
    "name",
    "year",
    "category_auto",
    "tags_auto",
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
    region_source: str
    country: str | None
    lat: float | None
    lon: float | None
    category: str
    tags: list[str]
    category_reviewed: bool
    review_candidate: bool
    famous_candidate: bool
    popularity: int
    thumb: str | None
    name: str
    aliases: list[str]
    blurb: str
    manual: bool
    flags: list[str]

    @property
    def sort_key(self) -> tuple[str, int]:
        """Ids come in two families now — harvested `Q…` and manual `M…` — so the numeric sort
        that keeps Q99 before Q100 needs the prefix in front of it to stay a total order. The
        game's `daily.js` sorts its pool by the same key, and the two must agree or the daily
        draw indexes into a different list than this file describes."""
        return self.qid[0], int(self.qid[1:])


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
    terrorist attack and an aviation accident is an attack; deciding that by precedence rather
    than by whichever P31 class happened to sort first is what keeps the prefill
    reproducible."""
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


def order_tags(primary: str, votes: set[str]) -> list[str]:
    """Every kind the evidence suggested, primary first and the rest in precedence order.
    Leading with the primary rather than sorting the whole list by precedence is what lets a
    reader rely on `tags[0]` being the category even when a later, weaker pass turned up a
    higher-precedence kind."""
    return [primary, *(kind for kind in CATEGORY_PRECEDENCE if kind in votes and kind != primary)]


def assign_category(instance_of: list[str], label: str) -> tuple[str, list[str], bool]:
    """The prefilled category, the full tag set behind it, and whether the category is a guess.

    Three passes, most trustworthy first: the exact P31 map, keyword patterns over the P31 class
    names, then the same patterns over the label. The *category* is the precedence winner of the
    first pass that says anything, so a hand-written class mapping still outranks a keyword hit
    on a word in the title. The *tags* are the union of all three passes, because the passes
    disagree about kind rather than about quality — the Kennedy assassination is an attack by
    P31 and politics by its label, and both are true of it.

    Reaching the end with nothing means the item never said what kind of event it was, so it
    lands in the fallback bucket carrying the flag that says so — the sheet must not be able to
    hide a shrug behind a plausible-looking category."""
    passes = (
        votes_from_classes(instance_of),
        votes_from_keywords(instance_of),
        votes_from_keywords([label]),
    )
    every_vote = set().union(*passes)
    for votes in passes:
        winner = resolve_votes(votes)
        if winner is not None:
            return winner, order_tags(winner, every_vote), False
    return FALLBACK_CATEGORY, [FALLBACK_CATEGORY], True


def canonical_continents(continents: list[str]) -> list[str]:
    """The item's continents reduced to the six the region axis uses, sorted and deduplicated.
    Narrower and alias forms fold into their parent; supercontinents and Antarctica drop out
    rather than being guessed at, which can legitimately empty the list."""
    folded = {CONTINENT_ALIASES.get(name, name) for name in continents}
    return sorted(folded & set(REGION_CANONICAL))


def continents_of_countries(countries: list[str]) -> set[str]:
    """The continents this event's countries sit on, by the static table. Labels the table does
    not know contribute nothing rather than a guess, so an unmapped country can never manufacture
    a spurious second continent and turn a single-continent event into "multi"."""
    return {
        COUNTRY_TO_CONTINENT[country] for country in countries if country in COUNTRY_TO_CONTINENT
    }


def continent_from_coordinates(lat: float | None, lon: float | None) -> str | None:
    """The continent whose bounding box contains this point, or None. First match wins — see
    `CONTINENT_BOXES` for why the order is the tiebreak and why this is a last resort."""
    if lat is None or lon is None:
        return None
    for continent, lat_min, lat_max, lon_min, lon_max in CONTINENT_BOXES:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return continent
    return None


def resolve_region(
    continents: list[str], countries: list[str], instance_of: list[str], lat: float | None,
    lon: float | None,
) -> tuple[str | None, str, list[str]]:
    """The event's region, the chain step that decided it, and any flags the choice earned.

    Four steps, strongest evidence first. A world war, or a country list crossing continents, is
    "multi" — a real answer meaning the event does not sit on one continent, which beats picking
    the alphabetically first of the continents it touched (the old rule filed the 2004 Indian
    Ocean earthquake under Africa). A single canonical P30 continent answers itself. Otherwise
    the first country goes through the static table, which is what rescues the transcontinental
    states P30 cannot place. Otherwise coordinates fall through crude boxes. Only then is it
    null, still flagged for a human.

    P30 listing several continents is deliberately *not* a multi trigger: Wikidata says
    "Asia, Europe" about the Battle of Stalingrad because Russia is transcontinental, and reading
    that as the battle spanning continents would be wrong about 377 mostly Russian, Soviet and
    Turkish items. Those fall to the country step, which answers Europe."""
    canonical = canonical_continents(continents)
    spanned = continents_of_countries(countries)

    if WORLD_WAR_CLASS in instance_of:
        return REGION_MULTI, "world war", []
    if len(spanned) >= 2:
        return REGION_MULTI, "countries span continents", []
    if len(canonical) == 1:
        return canonical[0], "single P30 continent", []
    if countries and countries[0] in COUNTRY_TO_CONTINENT:
        return COUNTRY_TO_CONTINENT[countries[0]], "country table", []

    boxed = continent_from_coordinates(lat, lon)
    if boxed is not None:
        return boxed, "coordinate box", ["region_boxed"]
    return None, "unresolved", ["region_missing"]


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

    lat, lon = extra.get("lat"), extra.get("lon")
    region, region_source, region_flags = resolve_region(
        record["continents"], record["countries"], record["instance_of"], lat, lon
    )
    category, tags, guessed = assign_category(record["instance_of"], record["label"])
    popularity = record["sitelinks"] or 0

    flags = [*(["gap_review"] if gap_flagged else []), *(["category_guess"] if guessed else [])]
    flags.extend(region_flags)

    return AssembledEvent(
        qid=qid,
        year=display_year(record["date_year"]),
        region=region,
        region_source=region_source,
        country=record["countries"][0] if record["countries"] else None,
        lat=lat,
        lon=lon,
        category=category,
        tags=tags,
        category_reviewed=False,
        review_candidate=popularity >= REVIEW_CANDIDATE_MIN_SITELINKS,
        famous_candidate=False,
        popularity=popularity,
        thumb=inputs.thumbnails.get(qid),
        name=record["label"],
        aliases=merge_aliases(
            record["label"], record["aliases"], extra.get("redirect_aliases") or []
        ),
        blurb=blurb.get("text") or "",
        manual=False,
        flags=flags,
    )


def assemble_events(inputs: RawInputs, selection: Selection) -> list[AssembledEvent]:
    """Every kept QID as a finished event, in id order — the order the core file ships in, so
    the file is byte-stable across runs and a diff means the data moved."""
    events = [
        assemble_event(qid, inputs, qid in selection.gap_flagged) for qid in selection.kept
    ]
    return sorted(events, key=lambda event: event.sort_key)


# ------------------------------------------------------------------- manual additions


def normalize_surface(text: str) -> str:
    """A name or alias reduced to the form the overlap check compares: case-folded, stripped of
    diacritics and of a leading article, whitespace collapsed. The folding is what `matcher.js`
    does for autocomplete, and for the same reason — "Niepce" and "Niépce" are one string as far
    as a duplicate is concerned.

    The article goes because the draft was written in prose and the harvest in Wikidata's
    title case: "the Hijra" and "Hijra" are one event, and without this the pair survives the
    check on a three-letter difference. Short names cannot be compared by containment (see
    `surfaces_overlap`), so equality is the only test that can catch them."""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    words = stripped.casefold().split()
    if len(words) > 1 and words[0] in ARTICLES:
        words = words[1:]
    return " ".join(words)


def surfaces_overlap(left: str, right: str) -> bool:
    """Whether two normalised surfaces name the same thing, by containment either way. Equality
    always counts; containment only once both strings are long enough to mean something, because
    at four characters "Rome" is inside a hundred unrelated names and the test stops being
    evidence."""
    if not left or not right:
        return False
    if left == right:
        return True
    if min(len(left), len(right)) < OVERLAP_MIN_CONTAINMENT_CHARS:
        return False
    return left in right or right in left


def find_overlap(entry: dict[str, Any], events: list[AssembledEvent]) -> AssembledEvent | None:
    """The assembled event a manual draft entry duplicates, or None.

    The draft was written from general knowledge, without checking what the harvest already
    found, so some of its entries certainly exist in the pool under a different phrasing. The
    check runs the manual name against every event's name and aliases and every event's name
    against the manual aliases; alias-against-alias is left out because two events can
    legitimately share a weak alias and the pairing produces noise rather than duplicates."""
    manual_name = normalize_surface(entry["name"])
    manual_aliases = [normalize_surface(alias) for alias in entry.get("aliases") or []]

    for event in events:
        event_name = normalize_surface(event.name)
        if surfaces_overlap(manual_name, event_name):
            return event
        if any(surfaces_overlap(manual_name, normalize_surface(a)) for a in event.aliases):
            return event
        if any(surfaces_overlap(alias, event_name) for alias in manual_aliases):
            return event
    return None


def manual_event(entry: dict[str, Any], index: int) -> AssembledEvent:
    """One draft entry as a finished event. Ids are `M` plus the entry's 1-based position in the
    file, so an id is stable as long as the file is only appended to — reordering the draft
    renumbers, which is why the draft is a curation artifact and not a scratch file. Everything
    the harvest would have supplied is honestly absent: no thumbnail, no coordinates, no country,
    a fixed popularity, and `manual: true` on the core record so nothing downstream mistakes a
    hand-written year for a Wikidata one."""
    category = entry["category"]
    category = CATEGORY_ALIASES.get(category, category)
    if category not in CATEGORIES:
        raise RuntimeError(
            f"manual entry {entry['name']!r} has category {entry['category']!r}; "
            f"expected one of {sorted(CATEGORIES)}"
        )

    region = entry.get("region")
    if region is not None and region not in REGION_CANONICAL and region != REGION_MULTI:
        raise RuntimeError(
            f"manual entry {entry['name']!r} has region {region!r}; "
            f"expected null, {REGION_MULTI!r} or one of {list(REGION_CANONICAL)}"
        )

    return AssembledEvent(
        qid=f"M{index:03d}",
        year=entry["year"],
        region=region,
        region_source="manual draft",
        country=None,
        lat=None,
        lon=None,
        category=category,
        tags=[category],
        category_reviewed=False,
        review_candidate=True,
        famous_candidate=False,
        popularity=MANUAL_POPULARITY,
        thumb=None,
        name=entry["name"],
        aliases=list(entry.get("aliases") or []),
        blurb="",
        manual=True,
        flags=["manual"],
    )


@dataclass
class ManualMerge:
    """What the manual draft contributed and what it did not: the events that joined the pool,
    and the entries dropped because the harvest already had them, each paired with the event it
    matched so the skip is reviewable rather than a count."""

    merged: list[AssembledEvent]
    skipped: list[tuple[str, str]]


def merge_manual_events(events: list[AssembledEvent]) -> ManualMerge:
    """The draft's entries folded into the assembled pool, minus the duplicates.

    Overlap is checked against the pool as it grows, so two draft entries naming the same event
    also collide — the draft is hand-written and that is a real failure mode."""
    if not MANUAL_PATH.is_file():
        raise RuntimeError(
            f"no manual additions file at {MANUAL_PATH}; expected the curated draft "
            "(an empty 'events' list is how you say there are none)"
        )
    with MANUAL_PATH.open(encoding="utf-8") as handle:
        entries = json.load(handle).get("events") or []

    pool = list(events)
    merged: list[AssembledEvent] = []
    skipped: list[tuple[str, str]] = []
    for index, entry in enumerate(entries, start=1):
        match = find_overlap(entry, pool)
        if match is not None:
            skipped.append((entry["name"], f"{match.qid} {match.name} ({match.year})"))
            continue
        event = manual_event(entry, index)
        pool.append(event)
        merged.append(event)
    return ManualMerge(merged=merged, skipped=skipped)


def core_record(event: AssembledEvent) -> dict[str, Any]:
    """One event as the game's language-independent row. `category_reviewed` is false unless a
    curator wrote a `category_final` for this id — a prefill that cannot be told apart from a
    human judgement is worse than no prefill. `tags` carries every kind the evidence suggested,
    primary first; nothing in the game reads it yet."""
    return {
        "id": event.qid,
        "year": event.year,
        "region": event.region,
        "country": event.country,
        "lat": event.lat,
        "lon": event.lon,
        "category": event.category,
        "tags": event.tags,
        "category_reviewed": event.category_reviewed,
        "famous_candidate": event.famous_candidate,
        "popularity": event.popularity,
        "thumb": event.thumb,
        "manual": event.manual,
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
            "manual": sum(1 for event in events if event.manual),
            "with_region": sum(1 for event in events if event.region),
            "with_coordinates": sum(1 for event in events if event.lat is not None),
            "with_thumb": sum(1 for event in events if event.thumb),
            "by_category": dict(sorted(Counter(event.category for event in events).items())),
            "by_tag": dict(sorted(Counter(t for e in events for t in e.tags).items())),
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


@dataclass
class CurationEdit:
    """One row a human changed, keyed by qid. Only the three writable columns are carried: the
    rest of the sheet is derived and a run regenerates it."""

    famous: str
    category_final: str
    notes: str


@dataclass
class CurationReadBack:
    """What the previous sheet said, and how much of it was human. `rows_read` against
    `edits` is the honest measure — "3 edits" means nothing without "out of 2596 rows"."""

    rows_read: int
    edits: dict[str, CurationEdit]
    backup_path: Path | None


def back_up_curation() -> Path | None:
    """Copy the existing sheet aside before this run overwrites it, returning where it went.

    The sheet is the one input here that cannot be recomputed from the raw caches, and the
    read-back below is new code standing between a curator's work and its only copy. The backup
    is dated rather than numbered so a second run on the same day overwrites its own copy
    instead of burying yesterday's under a pile of near-identical files."""
    if not CURATION_PATH.is_file():
        return None
    backup = CURATION_PATH.with_name(f"curation.backup-{date.today().isoformat()}.csv")
    shutil.copy2(CURATION_PATH, backup)
    return backup


def prefilled_famous_default(row_index: int, qid: str) -> str:
    """What the `famous` cell of this row held when the sheet was written.

    The sheet ships ranked, most popular first, with the top band prefilled and every manual
    addition prefilled wherever it landed — so a row's default is a function of its position and
    its id, and nothing else. Reconstructing it is what lets the read-back tell a curator's "y"
    from a prefill's "y", which the cell itself cannot say.

    This must stay in step with `prefilled_famous_ids`, and the coupling is load-bearing in a
    way that bites quietly: if this function is stricter than what the sheet writes, every run
    reads its own prefills back as human edits and freezes them, and the sheet stops tracking
    the data. That is not hypothetical — it happened, to the manual rows, before this signature
    took the qid."""
    manual = qid.startswith("M")
    return "y" if row_index < CURATION_PREFILLED_FAMOUS or manual else ""


def read_curation() -> CurationReadBack:
    """The previous sheet's human edits, keyed by qid.

    A cell counts as an edit when it differs from what this script would have written there:
    any non-empty `category_final` or `notes`, or a `famous` value that is not the prefill its
    row position implies. Read with `utf-8-sig` because the sheet is written with a BOM for
    Excel's sake and Excel writes one back; a plain `utf-8` read would leave the BOM glued to
    the first column name and silently find no qids at all."""
    backup = back_up_curation()
    if backup is None:
        return CurationReadBack(rows_read=0, edits={}, backup_path=None)

    edits: dict[str, CurationEdit] = {}
    with CURATION_PATH.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    for index, row in enumerate(rows):
        famous = (row.get("famous") or "").strip()
        category_final = (row.get("category_final") or "").strip()
        notes = (row.get("notes") or "").strip()
        if (
            famous == prefilled_famous_default(index, row["qid"])
            and not category_final
            and not notes
        ):
            continue
        edits[row["qid"]] = CurationEdit(
            famous=famous, category_final=category_final, notes=notes
        )

    return CurationReadBack(rows_read=len(rows), edits=edits, backup_path=backup)


@dataclass
class CurationOutcome:
    """What applying the sheet did to the dataset — the numbers the report has to show, because
    "the pool is 449" is only trustworthy next to what produced it."""

    dropped: list[str]
    famous_from_prefill: int
    famous_from_human: int
    unfamous_from_human: int
    categories_overridden: int
    edits_matched: int
    edits_unmatched: list[str]


def prefilled_famous_ids(events: list[AssembledEvent]) -> set[str]:
    """The ids whose `famous` cell ships "y".

    Two groups. The top band by popularity, which is the sheet's whole ergonomic argument — a
    reviewer should spend their attention on the boundary, not on confirming that the French
    Revolution is famous. And every manual addition, unconditionally: those entries exist
    *because* they are civilizational icons the harvest missed, and their popularity is a
    placeholder that would rank them out of the band on a technicality."""
    ranked = sorted(
        (event for event in events if event.review_candidate),
        key=lambda event: (-event.popularity, event.sort_key),
    )
    top_band = {event.qid for event in ranked[:CURATION_PREFILLED_FAMOUS]}
    return top_band | {event.qid for event in events if event.manual}


def apply_curation(
    events: list[AssembledEvent], read_back: CurationReadBack, prefilled: set[str]
) -> tuple[list[AssembledEvent], CurationOutcome]:
    """The sheet's rulings written into the dataset, and an account of what they did.

    Four things the sheet can say. A `notes` cell containing "drop" deletes the event from both
    output files — the reviewer's verdict that it is not an event at all, and the only
    destructive column. `famous` decides answer-pool membership, prefill included: a "y" that no
    human touched still counts, which is what makes the pool a few hundred recognisable events
    on the first run instead of every item that cleared the sitelink bar. `category_final`
    overrides the prefilled kind and sets `category_reviewed`. Anything else the sheet holds is
    derived and gets regenerated.

    Edits whose qid is no longer in the pool are reported rather than dropped in silence: it
    means the sheet and the dataset have diverged, which a curator needs to know before they
    trust the next sheet."""
    outcome = CurationOutcome(
        dropped=[],
        famous_from_prefill=0,
        famous_from_human=0,
        unfamous_from_human=0,
        categories_overridden=0,
        edits_matched=0,
        edits_unmatched=[],
    )

    by_qid = {event.qid: event for event in events}
    for qid in read_back.edits:
        if qid in by_qid:
            outcome.edits_matched += 1
        else:
            outcome.edits_unmatched.append(qid)

    kept: list[AssembledEvent] = []
    for event in events:
        edit = read_back.edits.get(event.qid)

        if edit is not None and DROP_TOKEN in edit.notes.casefold():
            outcome.dropped.append(f"{event.qid} {event.name} ({event.year}) — {edit.notes}")
            continue

        if edit is not None and edit.famous:
            event.famous_candidate = edit.famous.casefold() == "y"
            if event.famous_candidate:
                outcome.famous_from_human += 1
            else:
                outcome.unfamous_from_human += 1
        elif event.qid in prefilled:
            event.famous_candidate = True
            outcome.famous_from_prefill += 1

        if edit is not None and edit.category_final:
            if edit.category_final not in CATEGORIES:
                raise RuntimeError(
                    f"curation row {event.qid} ({event.name}) has category_final "
                    f"{edit.category_final!r}; expected one of {sorted(CATEGORIES)}"
                )
            event.category = edit.category_final
            event.tags = order_tags(edit.category_final, set(event.tags))
            event.category_reviewed = True
            outcome.categories_overridden += 1

        kept.append(event)

    return kept, outcome


def curation_rows(
    events: list[AssembledEvent], read_back: CurationReadBack, prefilled: set[str]
) -> list[dict[str, Any]]:
    """The review sheet's rows: the review-candidate slice, most popular first, carrying the
    human's own cells wherever they wrote one and the prefill everywhere else. Ties in
    popularity break on the id so two runs order the sheet identically and a reviewer's row
    numbers stay meaningful.

    `tags_auto` is informational and one-way: the sheet shows the curator every kind the
    evidence suggested, and the run never reads that column back. A curator who disagrees with
    the primary says so in `category_final`."""
    ranked = sorted(
        (event for event in events if event.review_candidate),
        key=lambda event: (-event.popularity, event.sort_key),
    )
    rows = []
    for event in ranked:
        edit = read_back.edits.get(event.qid)
        rows.append(
            {
                "qid": event.qid,
                "name": event.name,
                "year": event.year,
                "category_auto": event.category,
                "tags_auto": ";".join(event.tags),
                "region": event.region or "",
                "country": event.country or "",
                "popularity": event.popularity,
                "flags": ";".join(event.flags),
                "famous": (
                    edit.famous
                    if edit and edit.famous
                    else ("y" if event.qid in prefilled else "")
                ),
                "category_final": edit.category_final if edit else "",
                "notes": edit.notes if edit else "",
            }
        )
    return rows


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
    legal_regions = [*REGION_CANONICAL, REGION_MULTI]
    seen: set[str] = set()
    for event in events:
        if not isinstance(event.year, int) or isinstance(event.year, bool) or event.year == 0:
            raise RuntimeError(
                f"{event.qid} has year {event.year!r}; expected a non-zero int "
                "(historian's years have no year 0)"
            )
        if event.region is not None and event.region not in legal_regions:
            raise RuntimeError(
                f"{event.qid} has region {event.region!r}; "
                f"expected null or one of {legal_regions}"
            )
        if event.category not in CATEGORIES:
            raise RuntimeError(
                f"{event.qid} has category {event.category!r}; expected one of {sorted(CATEGORIES)}"
            )
        if not event.tags or event.tags[0] != event.category:
            raise RuntimeError(
                f"{event.qid} has tags {event.tags!r} under category {event.category!r}; "
                "expected the category first — every reader relies on tags[0] being it"
            )
        if len(set(event.tags)) != len(event.tags) or not set(event.tags) <= CATEGORIES:
            raise RuntimeError(
                f"{event.qid} has tags {event.tags!r}; "
                f"expected a duplicate-free subset of {sorted(CATEGORIES)}"
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
    """A handful of events worth reading with your own eyes, each with the reason it was picked:
    the most famous BCE event (the one that proves the year conversion did not silently produce
    an off-by-one or a positive year), the three most famous events overall, the median-
    popularity event, which is what the bulk of the pool actually looks like, and the most
    popular manual addition, which is the only way to see a hand-written record end to end.
    Chosen by rank rather than at random, so a diff between runs means the data moved."""
    if not events:
        return []

    ranked = sorted(events, key=lambda event: (-event.popularity, event.sort_key))
    chosen: list[tuple[str, AssembledEvent]] = []
    taken: set[str] = set()

    for reason, pool in (
        ("most famous BCE", [event for event in ranked if event.year < 0]),
        ("most famous", ranked[:1]),
        ("2nd most famous", ranked[1:2]),
        ("3rd most famous", ranked[2:3]),
        ("median popularity", [ranked[len(ranked) // 2]]),
        ("manual addition", [event for event in ranked if event.manual]),
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


def print_manual_merge(merge: ManualMerge) -> None:
    """What the hand-written draft added, and every entry the harvest already had. The skip list
    is printed in full rather than counted: each line is a claim that two differently-worded
    names are the same event, and that claim is only checkable by reading it."""
    print("\n2. MANUAL ADDITIONS")
    print(f"  merged into the pool            {len(merge.merged):>6}")
    print(f"  skipped as already harvested    {len(merge.skipped):>6}")
    for name, match in merge.skipped:
        print(f"        {name}  ->  {match}")


def print_kinds(events: list[AssembledEvent], famous: list[AssembledEvent]) -> None:
    """The kind distribution over the whole pool and over the answer pool side by side. The
    second column is the one that matters for play: the game asks about famous events, and a
    taxonomy that balances across ten thousand items but leaves the answer pool 80% battles is
    still a broken axis."""
    total, famous_total = len(events), len(famous)
    famous_counts = Counter(event.category for event in famous)
    print("\n3. KIND DISTRIBUTION (auto-prefill, unreviewed)")
    print(f"  {'kind':<24} {'all':>6} {'share':>7}   {'famous':>6} {'share':>7}")
    for category, count in Counter(event.category for event in events).most_common():
        print(
            f"  {category:<24} {count:>6} {percent(count, total)}   "
            f"{famous_counts[category]:>6} {percent(famous_counts[category], famous_total)}"
        )


def print_tags(events: list[AssembledEvent]) -> None:
    """How much the tag set adds over the single kind. An event with one tag learned nothing
    from the extra machinery; the pairs are where the taxonomy's form/domain conflation shows up
    concretely, which is the input the follow-up design needs."""
    total = len(events)
    multi = [event for event in events if len(event.tags) > 1]
    pairs = Counter(
        (event.tags[0], other) for event in multi for other in sorted(event.tags[1:])
    )
    print("\n4. TAGS (informational — nothing in the game reads these yet)")
    print(f"  events with 2+ tags      {len(multi):>6}  {percent(len(multi), total)}")
    print(f"  tags per event (mean)    {sum(len(e.tags) for e in events) / total:>6.2f}")
    print("  top co-occurring pairs (primary + secondary):")
    for (primary, secondary), count in pairs.most_common(10):
        print(f"      {count:>5}  {primary}  +  {secondary}")


def print_regions(events: list[AssembledEvent]) -> None:
    """Where the events are, and which link of the chain put them there. The per-step counts are
    the auditable part: "region is 96% filled" is only trustworthy alongside how much of that
    came from a bounding box."""
    total = len(events)
    regions = Counter(event.region for event in events)
    print("\n5. REGION DISTRIBUTION")
    for region in (*REGION_CANONICAL, REGION_MULTI):
        print(f"  {region:<24} {regions[region]:>6}  {percent(regions[region], total)}")
    print(f"  {'(null)':<24} {regions[None]:>6}  {percent(regions[None], total)}")

    print("  resolved by chain step:")
    sources = Counter(event.region_source for event in events)
    for source in (
        "world war",
        "countries span continents",
        "single P30 continent",
        "country table",
        "coordinate box",
        "manual draft",
        "unresolved",
    ):
        print(f"      {source:<28} {sources[source]:>6}  {percent(sources[source], total)}")


def print_curation(outcome: CurationOutcome, read_back: CurationReadBack, famous: int) -> None:
    """What the previous sheet's human rulings did to this dataset. Printed even when it did
    nothing — "0 edits found in 2596 rows" is the evidence that the read-back ran and the sheet
    is genuinely untouched, which is not the same as the read-back silently failing."""
    print("\n6. CURATION READ-BACK")
    print(f"  previous sheet             {read_back.backup_path or '(none — first run)'}")
    print(f"  rows read                  {read_back.rows_read:>6}")
    print(f"  human edits found          {len(read_back.edits):>6}")
    print(f"  edits re-applied by qid    {outcome.edits_matched:>6}")
    if outcome.edits_unmatched:
        print(f"  edits with no matching id  {len(outcome.edits_unmatched):>6}")
        for qid in outcome.edits_unmatched[:DROP_EXAMPLES_SHOWN]:
            print(f"        {qid}")
    print(f"  dropped by a 'drop' note   {len(outcome.dropped):>6}")
    for line in outcome.dropped[:DROP_EXAMPLES_SHOWN]:
        print(f"        {line}")
    print(f"  categories overridden      {outcome.categories_overridden:>6}")
    print(f"  famous from prefill        {outcome.famous_from_prefill:>6}")
    print(f"  famous set by a human      {outcome.famous_from_human:>6}")
    print(f"  un-famoused by a human     {outcome.unfamous_from_human:>6}")
    print(f"  ANSWER POOL (famous)       {famous:>6}")


def print_report(
    events: list[AssembledEvent],
    selection: Selection,
    merge: ManualMerge,
    read_back: CurationReadBack,
    outcome: CurationOutcome,
    curation_row_count: int,
) -> None:
    """The acceptance evidence: what the filter did, what the draft added, how the survivors
    distribute over the game's axes, how the region chain filled the map, what the curation sheet
    ruled, and a handful of records to read. The top-15 list is the honest eyeball test — these
    are the events a player would be asked about, and if beer is among them the filter is wrong
    however good the counts look."""
    total = len(events)
    flags = Counter(flag for event in events for flag in event.flags)
    famous = [event for event in events if event.famous_candidate]

    print("\n" + "=" * 78)
    print("ASSEMBLE REPORT — raw pipeline output -> shipping dataset")
    print("=" * 78)

    print_waterfall(selection)
    print_manual_merge(merge)
    print_kinds(events, famous)
    print_tags(events)
    print_regions(events)
    print_curation(outcome, read_back, len(famous))

    print("\n7. COVERAGE")
    for name, count in (
        ("thumbnail", sum(1 for event in events if event.thumb)),
        ("coordinates", sum(1 for event in events if event.lat is not None)),
        ("country", sum(1 for event in events if event.country)),
        ("blurb", sum(1 for event in events if event.blurb)),
        ("aliases", sum(1 for event in events if event.aliases)),
    ):
        print(f"  {name:<24} {count:>6}  {percent(count, total)}")

    print(f"\n8. ANSWER POOL — top {TOP_POPULARITY_SHOWN} by popularity")
    ranked = sorted(famous, key=lambda event: (-event.popularity, event.sort_key))
    for event in ranked[:TOP_POPULARITY_SHOWN]:
        print(f"      {event.popularity:>4}  {event.year:>6}  {event.name}  [{event.category}]")

    print("\n9. FLAGS")
    for flag in ("gap_review", "category_guess", "region_missing", "region_boxed", "manual"):
        print(f"  {flag:<24} {flags[flag]:>6}  {percent(flags[flag], total)}")
    print(
        f"  {'review_candidate':<24} "
        f"{sum(1 for e in events if e.review_candidate):>6}  "
        f"{percent(sum(1 for e in events if e.review_candidate), total)}"
    )

    print("\n10. OUTPUT FILES")
    for path in (CORE_PATH, LABELS_PATH, CURATION_PATH):
        raw, packed = file_sizes(path)
        print(f"  {path.name:<20} {raw / 1024:>8.1f} KB raw  {packed / 1024:>8.1f} KB gzipped")
    print(f"  curation.csv rows    {curation_row_count:>8} (excluding header)")

    print("\n11. SAMPLES (core + label)")
    for reason, event in sample_events(events):
        print(f"  [{reason}] {event.qid} — {event.name}")
        print(
            f"      year {event.year} | region {event.region} ({event.region_source}) | "
            f"country {event.country} | {event.category} | popularity {event.popularity} | "
            f"famous_candidate={event.famous_candidate} | manual={event.manual}"
        )
        print(f"      tags        : {', '.join(event.tags)}")
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
    """Load, select, derive, merge, curate, validate, persist, report. Returns non-zero only if
    the pool came out empty, which means the run is not evidence of anything.

    Order is load-bearing in two places. The curation sheet is read *before* anything is written,
    because writing the new sheet destroys the old one. And the prefilled-famous band is computed
    once, before the sheet's rulings are applied, so the `famous` column the sheet ships and the
    `famous_candidate` flag the core ships are decided by the same set — recomputing it after a
    drop would let the two disagree by one row and nothing would notice."""
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
    harvested = assemble_events(inputs, selection)

    merge = merge_manual_events(harvested)
    events = sorted([*harvested, *merge.merged], key=lambda event: event.sort_key)

    read_back = read_curation()
    prefilled = prefilled_famous_ids(events)
    events, outcome = apply_curation(events, read_back, prefilled)
    validate(events)

    rows = curation_rows(events, read_back, prefilled)
    write_core(events)
    write_labels(events)
    write_curation(rows)

    print_report(events, selection, merge, read_back, outcome, len(rows))

    if not events:
        print("ERROR: the pool came out empty; expected thousands of events.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
