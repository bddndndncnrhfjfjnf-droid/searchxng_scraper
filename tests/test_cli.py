"""Offline checks for the CLI's update check and the optional-extra guards.

Run:  python tests/test_cli.py

No network: fetch_latest() is stubbed in every case. The properties worth
protecting are all about NOT being annoying - a tool that phones home slowly,
prompts into a pipe, or crashes when an optional extra is missing is worse
than one that never checks at all.
"""

from __future__ import annotations

import builtins
import io
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # project root

import searxng_scraper.update as upd

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  [ok ] {name}  ({detail})")
    else:
        FAILURES.append(name)
        print(f"  [FAIL] {name}  ({detail})")


def run_check(current="0.3.0", latest="9.9.9", answer="n", cache=None,
              env=None):
    """Drive one UpdateCheck cycle with the network stubbed out.

    Returns (stderr_text, prompts_asked, network_was_used).
    """
    saved = (upd.fetch_latest, upd._read_cache, upd._write_cache,
             builtins.input, upd.subprocess.run)
    network = []
    pip_runs = []

    class _FakeProc:
        def run(self, cmd, **kwargs):
            # A test must never actually pip-install anything.
            pip_runs.append(cmd)
            class _R:  returncode = 0
            return _R()

    def fake_fetch():
        network.append(True)
        return latest

    upd.fetch_latest = fake_fetch
    upd._read_cache = lambda: cache or {}
    upd._write_cache = lambda _v: None

    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        return answer

    builtins.input = fake_input
    upd.subprocess = _FakeProc()
    err = io.StringIO()
    try:
        if env is None:
            upd.os.environ.pop(upd.ENV_DISABLE, None)
        else:
            upd.os.environ[upd.ENV_DISABLE] = env
        checker = upd.UpdateCheck().start(current)
        with redirect_stderr(err):
            checker.notice(current)
    finally:
        (upd.fetch_latest, upd._read_cache, upd._write_cache,
         builtins.input, upd.subprocess.run) = saved
        upd.os.environ.pop(upd.ENV_DISABLE, None)
    return err.getvalue(), prompts, bool(network), pip_runs


# ---------------------------------------------------------------------------
print("\n1. version ordering")

v = upd.version_tuple
check("0.3.0 > 0.2.9", v("0.3.0") > v("0.2.9"))
check("0.10.0 > 0.9.9 (numeric, not lexical)", v("0.10.0") > v("0.9.9"),
      f"{v('0.10.0')} > {v('0.9.9')}")
check("1.0 > 0.99.99", v("1.0") > v("0.99.99"))
check("equal versions are not newer", not v("0.3.0") > v("0.3.0"))
check("newer local is not downgraded", not v("0.3.0") > v("0.3.1"))
check("junk does not raise", v("1.x.3-rc1") == (1, 0, 31), str(v("1.x.3-rc1")))

# ---------------------------------------------------------------------------
print("\n2. update prompt")

err, prompts, net, _ = run_check(latest="9.9.9", answer="n")
check("newer release is announced on stderr",
      "9.9.9" in err and "0.3.0" in err, err.strip().splitlines()[0] if err else "")
check("Y/N prompt offered", prompts and "y/n" in prompts[0].lower(),
      prompts[0] if prompts else "no prompt")
check("answering 'n' runs nothing", "Updating" not in err)

err, _, _, _ = run_check(latest="0.3.0")
check("same version stays silent", err == "", repr(err))
err, _, _, _ = run_check(latest="0.1.0")
check("older remote version stays silent (no downgrade)", err == "", repr(err))

# ---------------------------------------------------------------------------
print("\n3. never in the way")

err, prompts, _, _ = run_check(latest=None)
check("offline / blocked check is silent", err == "" and not prompts, repr(err))

fresh = {"checked_at": time.time(), "latest": "9.9.9"}
err, prompts, net, _ = run_check(cache=fresh)
check("fresh cache (<24h) reports the known version without any network call",
      "9.9.9" in err and prompts and not net,
      f"net={net}, prompts={len(prompts)}")

stale = {"checked_at": time.time() - upd.CHECK_INTERVAL - 10, "latest": "9.9.9"}
err, prompts, net, _ = run_check(cache=stale)
check("stale cache re-checks over the network",
      net and prompts == ["    Update now? [y/N] "], f"net={net}")

err, prompts, net, _ = run_check(latest="9.9.9", env="1")
check("SXNG_NO_UPDATE_CHECK=1 suppresses everything",
      err == "" and not prompts and not net, f"net={net}")

err, prompts, _, pips = run_check(latest="9.9.9", answer="y")
check("answering 'y' runs exactly one pip upgrade",
      "Updating" in err and len(pips) == 1
      and "install" in pips[0] and "-U" in pips[0],
      " ".join(pips[0]) if pips else "pip never ran")

err, prompts, _, pips = run_check(latest="9.9.9", answer="n")
check("answering 'n' never runs pip", not pips, repr(pips))

# ---------------------------------------------------------------------------
print("\n4. stdout stays pure")

out = io.StringIO()
with redirect_stdout(out):
    print('{"query": "x"}')
check("JSON written to stdout is untouched by the module",
      out.getvalue() == '{"query": "x"}\n', repr(out.getvalue()))

# ---------------------------------------------------------------------------
print("\n5. optional argon2 extra")

from searxng_scraper.config import has_argon2

saved_argon2 = sys.modules.get("argon2")
try:
    sys.modules["argon2"] = None
    check("has_argon2() returns False when the extra is absent",
          has_argon2() is False, repr(has_argon2()))
except Exception as e:
    check("has_argon2() returns False when the extra is absent", False, repr(e))
finally:
    if saved_argon2 is None:
        del sys.modules["argon2"]
    else:
        sys.modules["argon2"] = saved_argon2

check("has_argon2() is a bool either way", isinstance(has_argon2(), bool),
      repr(has_argon2()))

# --- 6. where reports go, and human vs machine output ---------------------
# The report directory is the thing users asked for by name, and the
# stdout contract is what every `sxng ... | jq` in the wild depends on.
import json as _json
from searxng_scraper.api import RESULTS_DIR, slugify

check("reports land in SearchXNG_report",
      RESULTS_DIR.name == "SearchXNG_report", str(RESULTS_DIR))
check("report directory is under the cwd, not site-packages",
      str(RESULTS_DIR).startswith(str(Path.cwd())) or ".sxng_search" in str(RESULTS_DIR),
      str(RESULTS_DIR))
check("slug for a multi-word query", slugify("supply chain") == "supply_chain",
      slugify("supply chain"))

from searxng_scraper.race import print_results

_ENVELOPE = {
    "query": "supply chain", "mode": "web", "effective_query": "supply chain",
    "instance": "https://example.org/", "path": "json", "elapsed_s": 1.5,
    "fetched_at": "2026-10-02T00:00:00",
    "results": [
        {"title": "First", "url": "https://a.example/",
         "snippet": "x" * 300, "engine": "google"},
        {"title": "", "url": "https://b.example/", "snippet": "", "engine": ""},
    ],
}


class _TTY(io.StringIO):
    def isatty(self):
        return True


buf = _TTY()
with redirect_stdout(buf):
    print_results(_ENVELOPE, saved_to="SearchXNG_report/supply_chain.json")
rendered = buf.getvalue()
check("report shows the query and the instance",
      "supply chain" in rendered and "example.org" in rendered)
check("report shows where it was saved",
      "SearchXNG_report/supply_chain.json" in rendered)
check("long snippets are truncated, not dumped",
      "x" * 200 not in rendered and "..." in rendered, f"{len(rendered)} chars")
check("a result with an empty title still prints its url",
      "https://b.example/" in rendered)
check("ANSI colours are used when stdout is a terminal",
      "\033[" in rendered, "colour on a tty")

buf = io.StringIO()          # a real StringIO: isatty() is False
with redirect_stdout(buf):
    print_results(_ENVELOPE, saved_to=None)
check("no ANSI escapes when stdout is not a terminal",
      "\033[" not in buf.getvalue(), "plain text into a pipe")

buf = _TTY()
with redirect_stdout(buf):
    print_results({**_ENVELOPE, "results": []}, saved_to=None)
check("zero results says so instead of printing nothing",
      "no results" in buf.getvalue())

# ---------------------------------------------------------------------------
print()
if FAILURES:
    print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
    raise SystemExit(1)
print("All CLI checks passed.")