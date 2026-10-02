"""Where the list of public SearXNG instances comes from, and which to skip.

The list is the same JSON that searx.space renders, cached for six hours.
Instances that failed recently are remembered with a per-failure-class TTL so
one flaky host does not poison every run.
"""

from __future__ import annotations

import json
import time

from curl_cffi import requests as cffi

from .config import IMPERSONATE, data_dir

# instances.py - where the instance list comes from, and which ones to skip
# ===========================================================================
"""SearXNG meta-search without a browser - fast and light.

1. Pulls the live list of public instances from https://searx.space/data/instances.json
   (the same data the site's UI renders) - no cookies, no headless browser.
   The list is cached on disk for 6h; instances that failed recently are skipped.
2. Sorts instances: working search -> fast -> good TLS grade, skips Tor-only ones.
3. Races the top candidates in parallel (default 10 at a time); the first instance
   that answers wins (per-instance escalation ladder, see the file docstring).

Modes: --pdf (filetype:pdf) and --profiles (LinkedIn/README people search).

Dependencies: curl_cffi only (+ argon2-cffi for the rare argon2id challenge).
"""

INSTANCES_URL = "https://searx.space/data/instances.json"
MAX_INSTANCES_TO_TRY = 40
TIMEOUT = 12
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

CACHE_FILE = data_dir(".cache") / "instances.json"
INSTANCES_TTL = 6 * 3600          # searx.space refreshes its data every few hours
BAD_TTL_DEFAULT = 3600            # instance failed for an unknown/transient reason
BAD_TTLS = {                      # per failure class
    "timeout": 600,               # connection problems are often transient
    "429": 600,                   # limiter cools down
    "limiter": 600,               # Anubis solved, but botdetection still 429s
    "403": 6 * 3600,              # JSON disabled by admin - unlikely to change
    "0 results": 24 * 3600,       # search itself disabled
    "anubis": 6 * 3600,           # challenge itself unsolvable (6h, real block)
}


# ---------------------------------------------------------------------------
# on-disk state: instance list + recently-failed instances
# ---------------------------------------------------------------------------

def _get(url: str, *, params: dict | None = None, headers: dict | None = None,
         timeout: float = TIMEOUT):
    """curl_cffi GET with the Chrome TLS fingerprint used everywhere here."""
    return cffi.get(url, params=params, headers=headers,
                    impersonate=IMPERSONATE, timeout=timeout)

def _load_state() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(exist_ok=True)
        CACHE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def fetch_instance_list(fresh: bool = False) -> list[dict]:
    """Fetch searx.space data (cached 6h) and return candidates sorted best-first."""
    state = {} if fresh else _load_state()
    inst = state.get("instances") or {}
    if inst.get("fetched_at", 0) > time.time() - INSTANCES_TTL and inst.get("candidates"):
        return inst["candidates"]

    resp = _get(
        INSTANCES_URL,
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    data = resp.json()
    candidates = []
    for url, info in data.get("instances", {}).items():
        # Skip Tor-only instances: they need a Tor proxy we don't have
        if info.get("network_type") == "tor" or url.endswith(".onion"):
            continue
        timing = info.get("timing") or {}
        search = timing.get("search") or {}
        success = search.get("success_percentage") or 0
        if success <= 0:
            continue  # search doesn't even work for searx.space's crawler
        median = (search.get("all") or {}).get("median") or 99.0
        grade = (info.get("http") or {}).get("grade") or "Z"
        candidates.append(
            {"url": url, "success": success, "median": median, "grade": grade}
        )

    # best search success rate first, then fastest median search, then TLS grade
    candidates.sort(key=lambda c: (-c["success"], c["median"], c["grade"]))
    state["instances"] = {"fetched_at": time.time(), "candidates": candidates}
    _save_state(state)
    return candidates


def _bad_until(reason: str) -> float:
    ttl = BAD_TTL_DEFAULT
    for key, seconds in BAD_TTLS.items():
        if key in reason:
            ttl = seconds
            break
    return time.time() + ttl


def remember_failures(failures: list[tuple[str, str]]) -> None:
    """Mark instances as bad for a while so future runs skip them instantly."""
    if not failures:
        return
    state = _load_state()
    bad = state.get("bad") or {}
    now = time.time()
    for base, reason in failures:
        bad[base] = {"until": _bad_until(reason), "reason": reason}
    # drop expired entries while we're here
    state["bad"] = {u: v for u, v in bad.items() if v.get("until", 0) > now}
    _save_state(state)


def filter_bad(candidates: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Split candidates into (usable, skipped) using the bad-instance cache.

    `skipped` maps url -> failure reason (e.g. to pick cooldown-retry
    candidates in the endgame stage)."""
    state = _load_state()
    bad = state.get("bad") or {}
    now = time.time()
    ok, skipped = [], {}
    for c in candidates:
        entry = bad.get(c["url"])
        if entry and entry.get("until", 0) > now:
            skipped[c["url"]] = entry.get("reason", "")
            continue
        ok.append(c)
    return ok, skipped
