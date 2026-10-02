"""The public API: what you get when you import this as a library.

    from searxng_search import search
    r = search("nasa cosmos", limit=5)

SearchResult wraps the envelope dict from race.execute() with the accessors
people actually use (titles, urls, snippets) plus save()/to_json().
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import QUIET, data_dir
from .instances import _load_state, _save_state, fetch_instance_list
from .race import execute

# SECTION 6/7: public API - import this from your own projects
# ===========================================================================

RESULTS_DIR = data_dir("results")
SLUG_MAX = 60

QUICK_START = """\
SearXNG meta-search, no browser. A short cheat sheet:

  Installed from PyPI (any shell):
    sxng --input "nasa cosmos"             -> results/nasa_cosmos.json
    sxng --input "nasa cosmos" -v          same + verbose instance-race log
    sxng --input "nato staff" --pdf        PDF-only mode
    sxng                                 this cheat sheet

  From a source checkout (after `pip install -e .` and activating the venv):
    sxng --input "nato staff" --pdf      PDF-only mode
    python main.py --input "nasa cosmos" -n 10

  Checks (test_* need no network):
    python tests/test_pow_parser.py       offline engine checks
    python tests/test_rotation.py         offline rotation checks
    python tests/live_audit.py            live audit of every instance

  Details on the captchas, the limiter and the project layout: README.md
"""


def slugify(text: str) -> str:
    """Readable, filesystem-safe name: "nasa cosmos!" -> "nasa_cosmos".

    Unicode word characters are kept, so Cyrillic queries stay readable; any
    other run of characters becomes a single underscore.
    """
    slug = re.sub(r"\W+", "_", text.strip(), flags=re.UNICODE).strip("_").lower()
    return slug[:SLUG_MAX].strip("_") or "query"


def results_path(query: str, mode: str = "web") -> Path:
    """Where a query's results belong: results/<slug>[_<mode>].json.

    Never returns a path that already exists - a repeated query gets _2, _3
    instead of silently overwriting an earlier run.
    """
    slug = slugify(query)
    if mode != "web" and not slug.endswith(f"_{mode}"):
        slug = f"{slug}_{mode}"   # no double suffix: "... pdf" with --pdf
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{slug}.json"
    n = 2
    while path.exists():
        path = RESULTS_DIR / f"{slug}_{n}.json"
        n += 1
    return path


@dataclass(frozen=True, eq=False)   # eq=False keeps object identity hashing:
#                                    otherwise frozen + a list field breaks hash()
class SearchResult:
    """Result of one search, iterable and sliceable like a list of dicts.

        for hit in result:      # each hit: {title, url, snippet, engine}
        len(result), result[0]
        result.urls, result.titles, result.snippets
        result.anubis          # True if the winner was behind Anubis
        result.to_json()
        result.save()          # results/<slug>.json next to the engine
    """

    query: str
    mode: str
    effective_query: str
    instance: str
    path: str
    elapsed_s: float
    fetched_at: str
    results: list[dict] = field(default_factory=list)

    # --- convenience accessors ---
    def __len__(self) -> int:
        return len(self.results)

    def __iter__(self):
        return iter(self.results)

    def __getitem__(self, i):
        return self.results[i]

    def __bool__(self) -> bool:
        return bool(self.results)

    @property
    def titles(self) -> list[str]:
        return [r.get("title", "") for r in self.results]

    @property
    def urls(self) -> list[str]:
        return [r.get("url", "") for r in self.results]

    @property
    def snippets(self) -> list[str]:
        return [r.get("snippet", "") for r in self.results]

    @property
    def anubis(self) -> bool:
        """True if the winning instance was behind Anubis (captcha solved)."""
        return "anubis" in self.path

    @property
    def portico(self) -> bool:
        """True if a Portico captcha was solved."""
        return "portico" in self.path

    @property
    def used_html(self) -> bool:
        """True if the instance had no JSON API: results came from HTML."""
        return "html" in self.path

    def to_dict(self) -> dict:
        return {
            "query": self.query, "mode": self.mode,
            "effective_query": self.effective_query, "instance": self.instance,
            "path": self.path, "elapsed_s": self.elapsed_s,
            "fetched_at": self.fetched_at, "results": self.results,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save(self, path=None) -> Path:
        """Write to results/<slug>.json, or to `path` when given."""
        target = Path(path) if path else results_path(self.query, self.mode)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(), encoding="utf-8")
        return target


def search(query: str, limit: int = 10, mode: str = "web",
           parallel: int = 10, fresh: bool = False) -> SearchResult:
    """Search the web and return a SearchResult.

    mode: "web" | "pdf" | "profiles".
    Raises RuntimeError when no instance could answer.

        import sxng
        r = sxng.search("nasa cosmos", limit=5)
        print(r.urls[:3], r.anubis, r.elapsed_s)
    """
    return SearchResult(**execute(query, limit=limit, mode=mode,
                                  parallel=parallel, fresh=fresh))


def search_json(query: str, **kwargs) -> str:
    """Same as search(), but returns the result as a JSON string."""
    return search(query, **kwargs).to_json()


def search_many(queries, limit: int = 10, mode: str = "web",
                parallel: int = 10, gap: float = 2.0,
                on_result=None) -> list[SearchResult]:
    """Run a list of queries one by one.

    gap - seconds between queries, so instance rate limits are not burned.
    on_result - callback per finished query (progress bar, log, ...).
    A failing query is skipped instead of aborting the whole list.
    """
    out: list[SearchResult] = []
    for i, q in enumerate(queries):
        if i and gap:
            time.sleep(gap)
        try:
            res = search(q, limit=limit, mode=mode, parallel=parallel)
        except RuntimeError as e:
            if not QUIET.is_set():
                print(f"[-] {q!r}: {e}", file=sys.stderr)
            continue
        out.append(res)
        if on_result:
            on_result(res)
    return out


async def search_async(query: str, **kwargs) -> SearchResult:
    """asyncio wrapper - run several searches at once:

        tasks = [sxng.search_async(q) for q in queries]
        for r in await asyncio.gather(*tasks): ...
    """

    return await asyncio.to_thread(search, query, **kwargs)


def available_instances(fresh: bool = False) -> list[dict]:
    """Usable SearXNG instances, best first: url / success / median / grade."""
    return fetch_instance_list(fresh=fresh)


def clear_bad_instances() -> int:
    """Forget every "bad instance" mark (e.g. after a cooldown expired).

    Returns how many marks were cleared.
    """
    state = _load_state()
    n = len(state.get("bad") or {})
    state["bad"] = {}
    _save_state(state)
    return n


__all__ = [
    "SearchResult", "search", "search_json", "search_many", "search_async",
    "available_instances", "clear_bad_instances", "execute", "results_path",
    "slugify",
]


# ===========================================================================
