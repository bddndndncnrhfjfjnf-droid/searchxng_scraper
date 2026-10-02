"""Offline simulation of the rotation strategy (no network):

  1. serial mode: first instance 429s -> wait out the limiter window ->
     retry the SAME instance -> win there (no rotation needed);
  2. parallel race: everyone 429s -> stage B waits 21s once -> retries the
     coolest victims -> first retry wins;
  3. parallel race: all failures permanent -> stage B falls back to a
     validator-unworthy set -> error only if literally nothing arrived.

The simulation works by replacing names in the race module's own globals, so
run_search resolves them exactly the way it does in production.

Run:  python tests/test_rotation.py
"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # project root

from searxng_scraper import race

FAKE_RESULTS = ([{"title": "Fake", "url": "https://x.test/", "content": "s",
                  "engine": "fake"}], "json+simulated")

sleeps: list[float] = []

# The 21s cooldown pause would make this test take half a minute.
time.sleep = lambda s: sleeps.append(s)  # type: ignore[assignment]


def scenario(try_one, parallel: int, validator=None):
    """Run one simulated race; returns (winner, path, calls)."""
    calls: dict[str, int] = {}
    seen: list[str] = []
    lock_free = []  # parallel=1 only; the fake needs no locking

    def fake_try(base, query):
        base = base["url"] if isinstance(base, dict) else base
        seen.append(base)
        calls[base] = calls.get(base, 0) + 1
        return base, try_one(base, calls[base])

    originals = {name: getattr(race, name) for name in
                 ("_try_one", "fetch_instance_list", "filter_bad",
                  "remember_failures")}
    try:
        race._try_one = fake_try
        race.fetch_instance_list = lambda fresh=False: [
            {"url": u, "success": 100, "median": 1 + i, "grade": "A"}
            for i, u in enumerate(seen_pools[0])
        ]
        race.filter_bad = lambda cands: (cands, {})
        race.remember_failures = lambda failures: None
        _results, inst, path = race.run_search(
            "q", 10, parallel=parallel, fresh=True, validator=validator
        )
    finally:
        for name, fn in originals.items():
            setattr(race, name, fn)
    return inst, path, calls, lock_free


def pool(*urls):
    seen_pools.append(list(urls))


seen_pools: list[list[str]] = []


def scenario_1_serial_retry():
    """429 once -> SAME instance retried after the pause -> win."""
    pool("a", "b")

    def behaviour(base, attempt):
        if base == "a" and attempt == 1:
            return RuntimeError("HTTP 429 and HTML fallback failed")
        return FAKE_RESULTS

    return scenario(behaviour, parallel=1)


def scenario_2_endgame_win():
    """Both instances 429 on the first pass, both recover on the retry."""
    pool("a", "b")

    def behaviour(base, attempt):
        if attempt == 1:
            return RuntimeError("HTTP 429 and HTML fallback failed")
        return FAKE_RESULTS

    return scenario(behaviour, parallel=2)


def scenario_3_total_failure_with_fallback():
    """a is dead for good, b answers; the validator rejects it, we take it anyway."""
    pool("a", "b")

    def behaviour(base, attempt):
        if base == "a":
            return RuntimeError("HTTP 403 and HTML fallback failed")
        return ([{"title": "Generic", "url": "https://y.test/",
                  "content": "", "engine": "e"}], "html")

    return scenario(behaviour, parallel=2, validator=lambda rs: False)


ok = True

sleeps.clear()
inst, path, calls, _ = scenario_1_serial_retry()
same = calls.get("a", 0) == 2 and inst == "a"
print(f"1. serial 429-retry: winner={inst} [{path}], calls={calls}, "
      f"paused={sleeps}")
ok &= same and len(sleeps) == 1 and sleeps[0] == 21

sleeps.clear()
inst, path, calls, _ = scenario_2_endgame_win()
good = inst in ("a", "b") and len(sleeps) == 1 and sleeps[0] == 21 \
    and max(calls.values()) == 2
print(f"2. race all-429 -> endgame retry: winner={inst} [{path}], "
      f"calls={calls}, paused={sleeps}")
ok &= good

sleeps.clear()
inst, path, calls, _ = scenario_3_total_failure_with_fallback()
print(f"3. permanent failures -> validator-unworthy fallback: winner={inst} "
      f"[{path}], calls={calls}")
ok &= inst == "b" and not sleeps

print()
print("ALL STRATEGY CHECKS PASSED" if ok else "STRATEGY CHECKS FAILED")
sys.exit(0 if ok else 1)