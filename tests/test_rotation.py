"""Offline simulation of the rotation strategy (no network):

  1. serial mode: first instance 429s -> wait out the limiter window ->
     retry the SAME instance -> win there (no rotation needed);
  2. parallel race: everyone 429s -> stage B waits 21s once -> retries the
     coolest victims -> first retry wins;
  3. parallel race: all failures permanent -> stage B falls back to a
     validator-unworthy set -> error only if literally nothing arrived.

Run:  .venv-lite/Scripts/python tests/test_rotation.py
"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # корень проекта

import main as sx  # single-file build under test

FAKE_RESULTS = ([{"title": "Fake", "url": "https://x.test/", "content": "s",
                  "engine": "fake"}], "json+simulated")

sleeps: list[float] = []
sx.time.sleep = lambda s: sleeps.append(s)  # type: ignore[assignment]


def scenario_1_serial_retry():
    """429 once -> SAME instance retried after the pause -> win."""
    calls = {"a": 0}

    def fake_try(base, query):
        calls[base[0]] += 1
        if base[0] == "a" and calls["a"] == 1:
            return base, RuntimeError("HTTP 429 and HTML fallback failed")
        return base, FAKE_RESULTS

    sx._try_one = fake_try
    pool = [{"url": "a"}, {"url": "b"}]
    results, inst, path = sx.run_search("q", 10, parallel=1, fresh=True)
    # monkeypatching the real fetcher is impossible offline, so feed the pool
    # through the same code path run_search uses:
    return inst, path, calls, sleeps


def scenario_1_actual():
    calls = {"a": 0, "b": 0}

    def fake_try(base, query):
        calls[base] = calls.get(base, 0) + 1
        if base == "a" and calls["a"] == 1:
            return base, RuntimeError("HTTP 429 and HTML fallback failed")
        return base, FAKE_RESULTS

    sx._try_one = fake_try
    # run_search fetches its own candidate list; patch it to our pool
    sx.fetch_instance_list = lambda fresh=False: [
        {"url": "a", "success": 100, "median": 1, "grade": "A"},
        {"url": "b", "success": 100, "median": 2, "grade": "A"},
    ]
    sx.filter_bad = lambda cands: (cands, {})
    sx.remember_failures = lambda failures: None
    results, inst, path = sx.run_search("q", 10, parallel=1, fresh=True)
    return inst, path, calls


def scenario_2_endgame_win():
    calls = {}

    def fake_try(base, query):
        calls[base] = calls.get(base, 0) + 1
        # a and b 429 on the first pass, recover on the retry
        if calls[base] == 1:
            return base, RuntimeError("HTTP 429 and HTML fallback failed")
        return base, FAKE_RESULTS

    sx._try_one = fake_try
    sx.fetch_instance_list = lambda fresh=False: [
        {"url": u, "success": 100, "median": 1, "grade": "A"}
        for u in ("a", "b")
    ]
    sx.filter_bad = lambda cands: (cands, {})
    sx.remember_failures = lambda failures: None
    results, inst, path = sx.run_search("q", 10, parallel=2, fresh=True)
    return inst, path, calls


def scenario_3_total_failure_with_fallback():
    def fake_try(base, query):
        if base == "a":
            return base, RuntimeError("HTTP 403 and HTML fallback failed")
        return base, ([{"title": "Generic", "url": "https://y.test/",
                        "content": "", "engine": "e"}], "html")

    sx._try_one = fake_try
    sx.fetch_instance_list = lambda fresh=False: [
        {"url": "a", "success": 100, "median": 1, "grade": "A"},
        {"url": "b", "success": 100, "median": 2, "grade": "A"},
    ]
    sx.filter_bad = lambda cands: (cands, {})
    sx.remember_failures = lambda failures: None
    # validator nothing can satisfy -> fallback still returned
    results, inst, path = sx.run_search(
        "q", 10, parallel=2, fresh=True, validator=lambda rs: False
    )
    return inst, path


ok = True
sleeps.clear()
inst, path, calls = scenario_1_actual()
same = calls.get("a", 0) == 2 and inst == "a"
print(f"1. serial 429-retry: winner={inst} [{path}], calls={calls}, "
      f"paused={sleeps}")
ok &= same and len(sleeps) == 1 and sleeps[0] == 21

sleeps.clear()
inst, path, calls = scenario_2_endgame_win()
good = inst in ("a", "b") and len(sleeps) == 1 and sleeps[0] == 21 \
    and max(calls.values()) == 2
print(f"2. race all-429 -> endgame retry: winner={inst} [{path}], "
      f"calls={calls}, paused={sleeps}")
ok &= good

sleeps.clear()
inst, path = scenario_3_total_failure_with_fallback()
print(f"3. permanent failures -> validator-unworthy fallback: winner={inst} [{path}]")
ok &= inst == "b" and not sleeps

print()
print("ALL STRATEGY CHECKS PASSED" if ok else "STRATEGY CHECKS FAILED")
sys.exit(0 if ok else 1)
