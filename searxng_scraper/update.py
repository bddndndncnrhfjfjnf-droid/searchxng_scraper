"""Background check for a newer release on PyPI.

The check never delays the search. A daemon thread asks PyPI while the query
is already running - by the time results are on screen the answer has usually
arrived, and if it has not we simply say nothing.

Deliberate constraints, all of them load-bearing:

  * **stdout stays pure JSON.** Everything here goes to stderr, so
    `sxng --input x | jq` keeps working.
  * **No prompt without a TTY.** If stdout is a pipe or `-o -` was used, the
    check is skipped entirely - a tool that can block on input is a tool that
    hangs every script.
  * **Nothing for library users.** Only cli.py calls this.
  * **At most one request a day**, recorded in .cache/update_check.json.
  * **Every failure is silent.** Offline, blocked, TLS trouble, DNS - all of it
    ends in "no news", never in a traceback.
  * **stdlib urllib, not curl_cffi.** Importing curl_cffi would cost ~120 ms
    and would drag in the exact import that breaks on Android.

An update is offered, never imposed: the default answer is no.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

from .config import data_dir

PYPI_JSON_URL = "https://pypi.org/pypi/searchxng_scraper/json"
CHECK_INTERVAL = 24 * 3600     # ask PyPI at most once a day
NET_TIMEOUT = 3.0              # seconds; never hold the search hostage
CACHE_FILE = data_dir(".cache") / "update_check.json"
ENV_DISABLE = "SXNG_NO_UPDATE_CHECK"
PACKAGE_NAME = "searchxng_scraper"


def version_tuple(version: str) -> tuple:
    """"0.3.0" -> (0, 3, 0). Unparseable chunks count as 0, never raise."""
    parts = []
    for chunk in str(version).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _read_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_cache(latest: str) -> None:
    try:
        CACHE_FILE.write_text(
            json.dumps({"checked_at": time.time(), "latest": latest}),
            encoding="utf-8",
        )
    except OSError:
        pass


def fetch_latest() -> str | None:
    """Latest version on PyPI, or None when that cannot be determined."""
    try:
        req = urllib.request.Request(
            PYPI_JSON_URL, headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8")).get("info", {}).get("version")
    except Exception:
        # Offline, captive portal, TLS failure, 429, malformed JSON - all of it
        # means "no news", and none of it is worth interrupting a search for.
        return None


class UpdateCheck:
    """Fire-and-forget version check, consulted after the results are shown."""

    def __init__(self) -> None:
        self.latest: str | None = None
        self._thread: threading.Thread | None = None

    def start(self, current: str, force: bool = False) -> "UpdateCheck":
        """Begin the check if one is due. Returns self, never raises."""
        if not force and os.environ.get(ENV_DISABLE):
            return self

        cached = _read_cache()
        if not force and cached:
            age = time.time() - float(cached.get("checked_at") or 0)
            if age < CHECK_INTERVAL:
                self._latest_if_newer(current, cached.get("latest"))
                return self
        try:
            self._thread = threading.Thread(
                target=self._work, args=(current,), daemon=True
            )
            self._thread.start()
        except Exception:
            pass
        return self

    def _work(self, current: str) -> None:
        latest = fetch_latest()
        if latest:
            _write_cache(latest)
            self._latest_if_newer(current, latest)

    def _latest_if_newer(self, current: str, latest) -> None:
        if latest and version_tuple(latest) > version_tuple(current):
            self.latest = latest

    def notice(self, current: str) -> None:
        """After the search: prompt on stderr if a newer release exists."""
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        if not self.latest:
            return

        print(
            f"\n[i] {PACKAGE_NAME} {self.latest} is available "
            f"(you have {current}).",
            file=sys.stderr,
        )
        try:
            answer = input("    Update now? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("", file=sys.stderr)
            return
        if answer not in ("y", "yes"):
            return

        print("    Updating...", file=sys.stderr)
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-U", PACKAGE_NAME],
                check=False,
            )
        except Exception as exc:
            print(f"[!] update failed: {exc}", file=sys.stderr)