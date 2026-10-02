"""NATO profile search: iterate query shapes until LinkedIn hits appear.

Run:  python tests/live_profiles.py
"""
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # project root

from searxng_scraper.race import run_search  # noqa: E402

SHAPES = [
    "NATO linkedin profile",
    '"NATO" "linkedin.com/in"',
    "NATO linkedin",
    "site:linkedin.com/in NATO",
    "NATO linkedin.com/in",
]

best = None
for q in SHAPES:
    try:
        t0 = time.perf_counter()
        results, instance, path = run_search(q, 15, parallel=8)
        dt = time.perf_counter() - t0
        li = [r for r in results if "linkedin.com" in r.get("url", "")]
        print(f"\n=== {q!r}: {len(results)} results, LINKEDIN={len(li)} "
              f"via {instance} [{path}] in {dt:.1f}s")
        for r in li[:5]:
            print(f"  LI: {(r.get('title') or '')[:60]} | {r.get('url', '')[:80]}")
        if li and (best is None or len(li) > best[0]):
            best = (len(li), q, results)
    except Exception as e:
        print(f"\n=== {q!r}: FAIL {e}")
    time.sleep(10)

if best:
    n, q, results = best
    out_path = Path(__file__).resolve().parent.parent / "results" / "nato_profiles.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"query": q, "linkedin_hits": n,
                   "results": results[:15]}, f, indent=1, ensure_ascii=False)
    print(f"\nBEST: {q!r} with {n} LinkedIn hits -> results/nato_profiles.json")
else:
    print("\nNo LinkedIn hits in any shape")
