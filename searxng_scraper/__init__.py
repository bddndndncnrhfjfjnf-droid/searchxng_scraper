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

Imports here are lazy on purpose. Pulling in the engine costs ~130 ms (curl_cffi,
concurrent.futures, the PoW pool), and the two commands that must feel instant -
a bare `sxng` and `sxng --help` - never touch it. Attribute access below
triggers the real import once, then caches it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# config.py is cheap (stdlib only) and its state has to be set at import time,
# so this one stays eager.
from .config import quiet, set_verbose

__version__ = "0.3.1"

# Imported as a library, not run as a script: keep stdout clean so the host
# program owns it. set_verbose(True) brings the race log back.
quiet()

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

# Public name -> the submodule that actually defines it.
_EXPORTS = {
    "SearchResult": "api",
    "available_instances": "api",
    "clear_bad_instances": "api",
    "results_path": "api",
    "search": "api",
    "search_async": "api",
    "search_json": "api",
    "search_many": "api",
    "slugify": "api",
    "QUICK_START": "cli",
    "RESULTS_DIR": "api",
    "execute": "race",
    "print_results": "race",
}

if TYPE_CHECKING:  # what a type checker and an IDE should see
    from .api import (
        RESULTS_DIR,  # noqa: F401
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
    from .cli import QUICK_START  # noqa: F401
    from .race import execute, print_results


def __getattr__(name: str):
    """Import a public name from the submodule that owns it (PEP 562)."""
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value          # cache: the next lookup is a dict hit
    return value


def __dir__():
    return sorted(__all__)