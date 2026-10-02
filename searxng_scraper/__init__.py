"""searchxng_scraper - browserless meta-search over public SearXNG instances.

Install it and use it:

    pip install searchxng_scraper

    from searxng_scraper import search
    r = search("nasa cosmos", limit=5)
    print(r.titles[:3], r.instance)

or from the command line:

    sxng --input "nasa cosmos"

How a single query actually runs, in order:

    api.search()      -> race.execute()          picks instances, then races them
    race._try_one()   -> instances.filter_bad() drops recently-failed hosts
                      -> race.search_on_instance() walks the escalation ladder
                      -> limiter/  browser headers, link_token ping, Portico
                      -> anubis/   solves the Anubis PoW and keeps the JWT
                      -> htmlparse/ parses the HTML when JSON is switched off

pow.solve() is the shared proof-of-work engine underneath both captchas, and
config.py holds the knobs (TLS fingerprint, timeouts, where files go).
"""

from __future__ import annotations

from .config import quiet, set_verbose
from .api import (
    QUICK_START,       # noqa: F401  - the cheat sheet the CLI prints
    RESULTS_DIR,       # noqa: F401  - where results go by default
    SearchResult,
    available_instances,
    clear_bad_instances,
    results_path,
    search,
    search_async,
    search_json,
    search_many,
    slugify,
)
from .race import execute, print_results

# Imported as a library, not run as a script: keep stdout clean so the host
# program owns it. set_verbose(True) brings the race log back.
quiet()

__version__ = "0.3.0"

__all__ = [
    "SearchResult",
    "available_instances",
    "clear_bad_instances",
    "execute",
    "print_results",
    "results_path",
    "search",
    "search_async",
    "search_json",
    "search_many",
    "set_verbose",
    "slugify",
]