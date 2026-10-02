"""Live demo: run one query ONLY against Anubis-protected instances.

For every candidate whose front page answers with an Anubis challenge:
  * cached auth cookies are dropped first, so the PoW solve is always live;
  * the full escalation ladder runs (JSON -> HTML -> Anubis solve -> search);
  * the result path (json+anubis / html+anubis) and timing are reported.

This calls search_on_instance() directly, so failures are NOT recorded in
the bad-instance cache - the demo never pollutes the rotation state.
Transient limiter cooldowns get one 21s wait + retry (cookie reused from
cache), one-off network timeouts get an immediate retry.

Run:  python tests/live_anubis.py ["query"]
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # project root

from searxng_scraper.anubis import _PROBE_CACHE, _drop_cached_cookie, has_anubis
from searxng_scraper.instances import UA, fetch_instance_list, filter_bad
from searxng_scraper.race import search_on_instance

QUERY = sys.argv[1] if len(sys.argv) > 1 else "ai agent free"

# Instances known (from the audit sweep) to run Anubis; the live probe below
# re-checks each one - the list just makes sure they are considered even if
# searx.space ranks them below the top-30 cutoff.
KNOWN = [
    "https://search.hbubli.cc/",
    "https://ononoki.org/",
    "https://search.mizuki.st/",
    "https://searxng.deggo.fyi/",
    "https://baresearch.org/",  # "flappy": challenges only when suspicious
]


def cookie_cache() -> dict:
    try:
        return json.loads(Path(".anubis_cookies.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def cookie_summary() -> str:
    items = []
    for host, e in cookie_cache().items():
        left = e.get("expires", 0) - time.time()
        items.append(f"{host}: {'%.0f min left' % (left / 60) if left > 0 else 'expired'}")
    return "; ".join(items) or "(empty)"


def main() -> int:
    print(f"query: {QUERY!r}  -  only Anubis-fronted instances\n")
    print(f"cookie cache before: {cookie_summary()}")

    # 1. probe: which candidates are Anubis-fronted right now?
    candidates = fetch_instance_list()
    usable, _ = filter_bad(candidates)
    pool = sorted({c["url"] for c in usable[:30]} | set(KNOWN))

    def probe(base: str):
        return base, has_anubis(base, UA, timeout=8)

    anubis_instances: list[str] = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for base, verdict in ex.map(probe, pool):
            if verdict:
                anubis_instances.append(base)
    print(f"probed {len(pool)} front pages -> "
          f"{len(anubis_instances)} behind Anubis: "
          f"{[urlparse(b).netloc for b in anubis_instances]}\n")

    # 2. live solve + search on each protected instance.
    #    First attempt always force-solves the PoW (cookies dropped).
    #    Retries reuse the freshly cached cookies: a limiter cooldown (20s
    #    ip_limit window) or a one-off network timeout is not a solver
    #    failure, so we wait/retry exactly like the rotation endgame does.
    ok = 0
    for base in anubis_instances:
        host = urlparse(base).netloc
        print(f"=== {base} ===")
        _drop_cached_cookie(host)   # force a fresh PoW solve
        _PROBE_CACHE.pop(host, None)
        for attempt in (1, 2, 3):
            t0 = time.perf_counter()
            try:
                results, path = search_on_instance(base, QUERY)
                print(f"[OK] {len(results)} results via [{path}] "
                      f"in {time.perf_counter() - t0:.1f}s")
                for r in results[:3]:
                    print(f"     - {r.get('title', '')[:70]}")
                ok += 1
                break
            except Exception as e:
                reason = str(e)
                elapsed = time.perf_counter() - t0
                if attempt == 3:
                    print(f"[fail] {type(e).__name__}: {reason} ({elapsed:.1f}s)")
                    break
                if "connection failed" in reason or "Timeout" in type(e).__name__:
                    print(f"[~] {type(e).__name__} ({elapsed:.1f}s) - retrying now...")
                    continue
                if "limiter still blocks" in reason or "429" in reason.lower():
                    print(f"[~] {reason}; waiting 21s for the ip_limit window, "
                          "retrying with the cached cookie...")
                    time.sleep(21)
                    continue
                print(f"[fail] {type(e).__name__}: {reason} ({elapsed:.1f}s)")
                break

    print(f"\ncookie cache after: {cookie_summary()}")
    print(f"\n{ok}/{len(anubis_instances)} Anubis instances answered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
