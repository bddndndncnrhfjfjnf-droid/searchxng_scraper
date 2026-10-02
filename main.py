"""Runnable shim: `python main.py --input "nasa cosmos"`.

The engine lives in the searxng_scraper package. This file stays because
`python main.py` is the shortest thing that works from a checkout, and because
older code does `import main as sxng`.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python main.py` from a checkout that was never pip-installed.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from searxng_scraper import (  # noqa: E402
    SearchResult,
    available_instances,
    clear_bad_instances,
    execute,
    print_results,
    results_path,
    search,
    search_async,
    search_json,
    search_many,
    set_verbose,
    slugify,
)
from searxng_scraper.cli import main  # noqa: E402

__version__ = "0.3.0"

__all__ = [
    "SearchResult",
    "available_instances",
    "clear_bad_instances",
    "execute",
    "main",
    "print_results",
    "results_path",
    "search",
    "search_async",
    "search_json",
    "search_many",
    "set_verbose",
    "slugify",
]

if __name__ == "__main__":
    sys.exit(main())