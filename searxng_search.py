"""searxng_search - the import name documented in the README.

    from searxng_search import search
    r = search("nasa cosmos", limit=5)

Everything lives in the searxng_scraper package; this file only re-exports the
public API so the name in the README keeps working.
"""

from __future__ import annotations

from searxng_scraper import *  # noqa: F401,F403
from searxng_scraper import __all__, __version__  # noqa: F401