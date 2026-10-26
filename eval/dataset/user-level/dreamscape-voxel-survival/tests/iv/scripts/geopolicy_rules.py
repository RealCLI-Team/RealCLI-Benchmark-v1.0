"""Rule tables for geopolitical / territorial compliance scanning of generated frontend artifacts.

Design notes
------------
* Rules are DATA, not code, so reviewers can audit and amend them without touching the scanner.
* Every rule carries an ACTION:
    BLOCK  - high-precision violation; hold the artifact out of the dataset.
    REVIEW - real signal but context-dependent; a human decides.
    WARN   - provenance only (a risky source was used); not a finding by itself.
* Precision matters more than recall here. A blunt keyword blocklist over terms like
  "Hong Kong", "Tibet" or "Xinjiang" would flag travel apps, weather dashboards and
  timezone pickers by the thousand. Rules therefore key on *sovereignty assertions*
  (own ISO3 / own country record / independence framing / separatist symbol), not on
  place names.
"""

import re

BLOCK, REVIEW, WARN = "BLOCK", "REVIEW", "WARN"

# ---------------------------------------------------------------------------
# A. Sovereignty depiction: PRC-claimed territory rendered as its own sovereign state.
#    The tell is a *country peer record* (own ISO3 / ccn3 / flag asset), not a place name.
# ---------------------------------------------------------------------------
SOVEREIGNTY = [
    ("tw_iso3_record", BLOCK,
     re.compile(r'"(?:iso3|cca3|alpha3|ISO_A3)"\s*:\s*"TWN"', re.I)),
    ("tw_country_record", BLOCK,
     re.compile(r'"name"\s*:\s*"Taiwan".{0,120}?"(?:iso2|iso3|cca2|cca3)"\s*:\s*"TWN?"', re.I | re.S)),
    ("tw_flag_emoji", BLOCK, re.compile("\U0001F1F9\U0001F1FC")),
    # The value must END at 158 -- unanchored, this matched UUIDs like "1580ce91-a43e-...".
    ("tw_ccn3_158", REVIEW,
     re.compile(r'"(?:ccn3|id)"\s*:\s*(?:"158"|158)(?=\s*[,}\]\n])')),
    ("tw_republic_of_china", REVIEW, re.compile(r"Republic of China(?!\w)|Chinese Taipei", re.I)),

    # Tibet / Xinjiang as sovereign entities (place name alone is NOT a hit).
    ("tibet_as_country", BLOCK,
     re.compile(r'"name"\s*:\s*"Tibet"\s*,\s*"(?:iso|cca|alpha)|Republic of Tibet|State of Tibet', re.I)),
    ("east_turkestan_state", BLOCK,
     re.compile(r"East\s*Turkest[ai]n|Uygh?urstan|Republic of Uygh?ur", re.I)),

    # HK/MO carry their own ISO codes in virtually every dataset as *territories* —
    # that is standard and not a finding. Only sovereign framing is.
    ("hk_mo_as_sovereign", REVIEW,
     re.compile(r'"(?:Hong Kong|Macau|Macao)"\s*,\s*"(?:type|status)"\s*:\s*"(?:sovereign|country|independent)', re.I)),
]

# ---------------------------------------------------------------------------
# B. Separatist symbols / slogans appearing in copy, asset names or alt text.
# ---------------------------------------------------------------------------
SEPARATIST = [
    ("taiwan_independence", BLOCK,
     re.compile(r"Taiwan\s+independence|Republic of Taiwan|Taiwan\s+is\s+(?:an?\s+)?(?:independent|sovereign)"
                r"|台\s*独|台灣獨立|台湾独立", re.I)),
    ("tibet_independence", BLOCK,
     re.compile(r"Free\s+Tibet|Tibet(?:an)?\s+independence|藏\s*独|西藏獨立", re.I)),
    ("xinjiang_independence", BLOCK,
     re.compile(r"Free\s+(?:Xinjiang|Uygh?urs?)|疆\s*独", re.I)),
    ("hk_independence", BLOCK,
     re.compile(r"Hong\s*Kong\s+independence|港\s*独|光復香港|光复香港|Liberate\s+Hong\s*Kong|時代革命|时代革命", re.I)),
]

# ---------------------------------------------------------------------------
# C. Disputed territory / naming. Mostly REVIEW: the "correct" label is jurisdictional,
#    so a reviewer picks the house style rather than the scanner guessing.
# ---------------------------------------------------------------------------
DISPUTED = [
    ("senkaku_naming", REVIEW, re.compile(r"Senkaku", re.I)),
    ("scs_island_naming", REVIEW, re.compile(r"Spratly|Paracel|Scarborough Shoal", re.I)),
    ("india_china_border", REVIEW, re.compile(r"Arunachal Pradesh|Aksai Chin|South Tibet", re.I)),
    ("kashmir", REVIEW, re.compile(r"Azad Kashmir|Pakistan[- ]occupied Kashmir|Indian[- ]occupied Kashmir", re.I)),
    ("sea_naming", REVIEW, re.compile(r"\bEast Sea\b|Sea of Japan|Persian Gulf|Arabian Gulf", re.I)),
    ("nine_dash_absent", WARN,  # geometry-level; text scan can only note the topic
     re.compile(r"nine[- ]dash|ten[- ]dash|U-shaped line", re.I)),
]

# ---------------------------------------------------------------------------
# D. Political / ideological content inside a *code* artifact.
#
#    Deliberately VALENCE-NEUTRAL and country-neutral: it asks "is this coding artifact
#    carrying political advocacy at all", not "is this criticism of a particular party".
#    For a code-generation dataset that is both the more defensible rule and the more
#    useful one - political payloads of any direction are off-topic noise. Always REVIEW,
#    never auto-BLOCK, because the false-positive surface (a news dashboard, an election
#    results demo, a history timeline) is genuinely large.
# ---------------------------------------------------------------------------
POLITICAL_CONTENT = [
    ("regime_change_advocacy", REVIEW,
     re.compile(r"overthrow the (?:government|regime|party)|end (?:one[- ]party|communist) rule|"
                r"boycott \w+ (?:goods|products)", re.I)),
    ("atrocity_claim_copy", REVIEW,
     re.compile(r"\bgenocide\b|\bethnic cleansing\b|\bconcentration camps?\b|\bre-?education camps?\b", re.I)),
    ("contested_event_copy", REVIEW,
     re.compile(r"Tiananmen|六四|天安門|天安门|Cultural Revolution death|Great Leap.{0,20}famine", re.I)),
    ("party_evaluative_copy", REVIEW,
     re.compile(r"(?:Chinese\s+)?Communist Party.{0,40}(?:dictator|authoritarian|oppress|tyrann|regime)|"
                r"(?:dictator|authoritarian|oppress|tyrann).{0,40}(?:Chinese\s+)?Communist Party", re.I)),
]

# ---------------------------------------------------------------------------
# B2. Filename rules. Applied to every file path in the bundle (binaries included),
#     because a bundled flag asset is itself the assertion.
# ---------------------------------------------------------------------------
IMG = r"\.(?:png|svg|jpe?g|webp|gif|ico)$"

FLAG_FILE_RULES = [
    # A dedicated national flag file for a governed territory = treated as a country.
    ("tw_flag_asset", "sovereignty", BLOCK,
     re.compile(r"(?:^|/)(?:flags?|flag)/tw" + IMG, re.I)),
    ("separatist_flag_asset", "separatist", BLOCK,
     re.compile(r"(?:^|[/_-])(?:free[_-]?tibet|tibet[_-]?flag|snow[_-]?lion|"
                r"east[_-]?turkest[ai]n|etim|roc[_-]?flag)" + IMG, re.I)),
]

ALL_RULES = (
    [("sovereignty", *r) for r in SOVEREIGNTY]
    + [("separatist", *r) for r in SEPARATIST]
    + [("disputed", *r) for r in DISPUTED]
    + [("political", *r) for r in POLITICAL_CONTENT]
)

# ---------------------------------------------------------------------------
# Performance: cheap lowercase literal prefilters. A regex only runs if one of its
# literals is present in the file. Scanning a batch means gigabytes of baked JSON, and
# a bare `in` test is orders of magnitude cheaper than 20+ regex passes per file.
# Each entry MUST be implied by its rule (a rule can never match without one of these),
# otherwise the prefilter would silently drop findings.
# ---------------------------------------------------------------------------
PREFILTER = {
    "tw_iso3_record": ["twn"],
    "tw_country_record": ["taiwan"],
    "tw_flag_emoji": ["\U0001F1F9\U0001F1FC"],
    "tw_ccn3_158": ["158"],
    "tw_republic_of_china": ["republic of china", "chinese taipei"],
    "tibet_as_country": ["tibet"],
    "east_turkestan_state": ["turkest", "uygh", "uigh"],
    "hk_mo_as_sovereign": ["hong kong", "macau", "macao"],
    "taiwan_independence": ["taiwan", "\u53f0"],
    "tibet_independence": ["tibet", "\u85cf\u72ec", "\u897f\u85cf"],
    "xinjiang_independence": ["xinjiang", "uygh", "uigh", "\u7586"],
    "hk_independence": ["hong kong", "\u6e2f\u72ec", "\u5149\u5fa9", "\u5149\u590d",
                        "liberate hong", "\u6642\u4ee3\u9769\u547d", "\u65f6\u4ee3\u9769\u547d"],
    "senkaku_naming": ["senkaku"],
    "scs_island_naming": ["spratly", "paracel", "scarborough"],
    "india_china_border": ["arunachal", "aksai", "south tibet"],
    "kashmir": ["kashmir"],
    "sea_naming": ["east sea", "sea of japan", "persian gulf", "arabian gulf"],
    "nine_dash_absent": ["nine-dash", "nine dash", "ten-dash", "ten dash", "u-shaped line"],
    "regime_change_advocacy": ["overthrow the", "one-party rule", "one party rule",
                               "communist rule", "boycott"],
    "atrocity_claim_copy": ["genocide", "ethnic cleansing", "concentration camp",
                            "reeducation camp", "re-education camp"],
    "contested_event_copy": ["tiananmen", "\u516d\u56db", "\u5929\u5b89\u9580",
                             "\u5929\u5b89\u95e8", "cultural revolution", "great leap"],
    "party_evaluative_copy": ["communist party"],
}


# ---------------------------------------------------------------------------
# E. CDN / remote source policy.
#
#    A remote fetch at build time launders a depiction into the artifact, so sources are
#    governed directly. DENY = the dataset is known to ship PRC-claimed territory as
#    separate sovereign features. ALLOW = inert infrastructure (fonts, engines, libs).
#    Anything else that is fetched during the build is REVIEW - unknown provenance.
# ---------------------------------------------------------------------------
CDN_DENY = [
    ("world-atlas", "world-atlas TopoJSON: Taiwan is a separate feature (ccn3 158)"),
    ("countries-110m", "Natural Earth 110m: Taiwan is a separate feature"),
    ("countries-50m", "Natural Earth 50m: Taiwan is a separate feature"),
    ("countries-10m", "Natural Earth 10m: Taiwan is a separate feature"),
    ("naturalearth", "Natural Earth (unpatched): separate Taiwan feature"),
    ("natural-earth", "Natural Earth (unpatched): separate Taiwan feature"),
    ("restcountries.com", "REST Countries: Taiwan as sovereign record, own ISO3/flag"),
    ("world-countries", "world-countries: Taiwan as sovereign record"),
    ("mledoze/countries", "mledoze/countries: Taiwan as sovereign record"),
    ("datamaps", "datamaps bundled world topojson: separate Taiwan"),
    ("geojson-maps", "geojson-maps: separate Taiwan"),
    ("world.geo.json", "world.geo.json: separate Taiwan"),
    ("simplemaps", "simplemaps world data: separate Taiwan"),
    ("amcharts", "amCharts geodata: separate Taiwan region"),
    ("/world.json", "echarts-style world.json: separate Taiwan"),
]

# Inert infrastructure: safe to fetch, carries no territorial claims.
CDN_ALLOW = [
    "fonts.googleapis.com", "fonts.gstatic.com",
    "cdn.jsdelivr.net/npm/three@", "cdn.jsdelivr.net/gh/mrdoob/three.js",
    "unpkg.com/three@", "cdn.babylonjs.com", "cdn.skypack.dev/three@",
    "cdn.jsdelivr.net/npm/d3", "cdn.jsdelivr.net/npm/chart.js",
    "cdn.jsdelivr.net/npm/earcut", "cdn.jsdelivr.net/npm/gsap",
    "cdn.tailwindcss.com", "cdnjs.cloudflare.com/ajax/libs/",
    "api.worldbank.org",  # statistics only, no geometry
]

# Harness / sandbox plumbing. These appear in EVERY trajectory (model gateway, package
# installers, the agent binary, object storage). They are not artifact provenance
# and would otherwise mark 100% of instances REVIEW, drowning the real signal.
HARNESS_HOSTS = [
    "aliyuncs.com", "astral.sh", "storage.googleapis.com/claude-code-dist",
    "registry.npmjs.org", "pypi.org", "files.pythonhosted.org", "deb.debian.org",
    "archive.ubuntu.com", "playwright.azureedge.net", "pptr.dev", "nodejs.org",
    "esm.sh", "unpkg.com", "cdn.jsdelivr.net/npm/@", "api.anthropic.com",
    "storage.googleapis.com/chrome-for-testing", "cdn.playwright.dev",
    "github.com/npm/cli", "127.0.0.1", "localhost",
    # documentation links in generated comments/copy are not data fetches
    "threejs.org/docs", "chartjs.org", "developer.mozilla.org", "caniuse.com",
    # browser / toolchain plumbing
    "googlechromelabs.github.io", "chromium.org", "developer.chrome.com",
    "edgedl.me.gvt1.com", "playwright.download.prss.microsoft.com",
    "pip.pypa.io", "cdn.npmmirror.com", "data.jsdelivr.com", "registry.npmmirror.com",
    # docs / specs / vendor sites quoted in generated comments
    "platform.claude.com", "docs.anthropic.com", "html.spec.whatwg.org", "schema.org",
    "rollupjs.org", "threejs.org", "docs.unrealengine.com", "webgpufundamentals.org",
    "www.gstatic.com", "fonts.google.com",
]

# Flag-image CDNs are a vector for separatist/sovereign flag assets: allowed in general,
# but a request for a governed territory's flag is a finding.
CDN_CONDITIONAL = [
    (re.compile(r"flagcdn\.com/[^\s\"']*/(tw)\.", re.I), BLOCK,
     "flag CDN request for TW as a national flag"),
]

# Non-fetches: XML namespaces, documentation placeholders, private/example hosts and
# regex fragments scraped out of source. These are not provenance and must not create noise.
INERT_URL = [
    re.compile(r"^https?://(?:www\.)?w3\.org/", re.I),
    re.compile(r"^https?://[^/]*\b(?:example|test|localhost|invalid)\b", re.I),
    re.compile(r"^https?://[^/]*\.(?:local|internal|test|localdomain)(?:[:/]|$)", re.I),
    re.compile(r"[{}\u2026<>\[\]^$\\]"),          # placeholder / regex fragment
    re.compile(r"^https?://[^/]*\$\{"),            # unexpanded template literal
    re.compile(r"^https?://\S{0,8}$"),               # truncated scrape
]


def is_inert(url):
    return any(rx.search(url) for rx in INERT_URL)


REMOTE_URL_RE = re.compile(r"https?://[^\s\"'`)\\<>]+")


# Physical-geography layers carry NO political boundaries: they are coastlines, oceans,
# rivers and graticules. Drawing Taiwan as an island is a geographic fact, not a
# sovereignty assertion, so these are allowed even though they live in packages
# (world-atlas, natural-earth-vector) whose *political* files are denied above.
#
# This is an explicit ALLOW-list of known-physical layer names, not a deny-list of
# political ones: an unrecognised Natural Earth layer stays denied by default.
# Political layers that must NOT match: ne_*_admin_0_countries, admin_0_boundary_lines*,
# admin_1_states_provinces*, ne_*_sovereignty, ne_*_map_units, ne_*_map_subunits.
_NE_PHYSICAL = (r"land|ocean|coastline|bathymetry\w*|graticules?\w*|geographic_lines|"
                r"rivers\w*|lakes\w*|glaciated_areas|antarctic_ice_shelves\w*|"
                r"playas|reefs|minor_islands\w*|geography_regions\w*|geography_marine\w*")
BORDERLESS_GEOMETRY = re.compile(
    r"/land-\d+m\.json|/land\.json"                    # world-atlas naming
    r"|(?:^|[/_])ne_\d+m_(?:" + _NE_PHYSICAL + r")\.(?:geo)?json"   # Natural Earth naming
    r"|/\d+m/physical/",                                 # natural-earth-geojson layout
    re.I)


# A link to a dataset's homepage or docs is an ATTRIBUTION CREDIT, not a data fetch --
# generated artifacts routinely footer-credit "coastline - Natural Earth 110m". Citing a
# source cannot launder a depiction into the artifact; only fetching its political layer
# can. So a deny-listed host with no data path is downgraded to WARN.
CITATION_PATH = re.compile(
    r"^https?://[^/]+/?$"                                   # bare host
    r"|^https?://[^/]+/(?:docs?|about|blog|features|pricing|legal|terms)(?:[/?#]|$)",
    re.I)


def classify_url(url):
    """Return (action, reason) for a remote URL, or (None, None) if allowed/inert."""
    if is_inert(url) or BORDERLESS_GEOMETRY.search(url):
        return None, None
    low0 = url.lower()
    if any(h in low0 for h in HARNESS_HOSTS):
        return None, None
    for rx, action, reason in CDN_CONDITIONAL:
        if rx.search(url):
            return action, reason
    low = url.lower()
    for marker, reason in CDN_DENY:
        if marker.lower() in low:
            if CITATION_PATH.match(url):
                return WARN, f"risky data source cited (attribution link, no data path): {reason}"
            return BLOCK, f"denied source: {reason}"
    for ok in CDN_ALLOW:
        if ok.lower() in low:
            return None, None
    return REVIEW, "unknown remote source fetched during build"
