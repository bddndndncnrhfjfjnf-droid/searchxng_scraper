"""Shared settings: where files live, how chatty the tool is, TLS fingerprint.

Every other module imports from here, so this file depends on nothing but the
standard library. Change something below and the whole tool changes with it.
"""

from __future__ import annotations

import importlib.util
import os
import threading
from pathlib import Path

# Chrome TLS fingerprint. curl_cffi replays the real handshake for it, which is
# what makes SearXNG treat us as an ordinary browser rather than a scraper.
IMPERSONATE = "chrome131"

# The package directory. In a checkout the parent of it is the project root,
# which is where results/ and .cache/ belong.
PACKAGE_DIR = Path(__file__).resolve().parent

# Set while printing the tool's own progress log. Set = do not print.
# The CLI clears it for -v, the library keeps it set until set_verbose(True).
QUIET = threading.Event()


def quiet() -> None:
    """Default to silence: only JSON on stdout, errors on stderr."""
    QUIET.set()


def data_dir(name: str) -> Path:
    """Return a writable directory for caches or results, creating it.

    Tries the project root first, so a checkout keeps its results/ next to the
    source. An installed package normally lands in a read-only site-packages
    without admin rights, in which case everything moves to the user profile.
    """
    for candidate in (
        PACKAGE_DIR.parent / name,
        Path(os.environ.get("LOCALAPPDATA") or Path.home()) / ".sxng_search" / name,
        Path.cwd() / name,
    ):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return candidate
        except OSError:
            continue
    return Path.cwd() / name  # nothing was writable; the caller will complain


def qprint(*args, **kwargs) -> None:
    """print() that respects the quiet flag (CLI -v / set_verbose)."""
    if not QUIET.is_set():
        print(*args, **kwargs)


def set_verbose(enabled: bool = True) -> None:
    """Turn the instance-race and captcha progress log on or off.

    Silent by default, both for the CLI without -v and for library users, so
    stdout carries nothing but what the host program prints itself.
    """
    if enabled:
        QUIET.clear()
    else:
        QUIET.set()


def has_argon2() -> bool:
    """True when the optional argon2-cffi extra is installed.

    Only the rare argon2id flavour of the Anubis challenge needs it; every
    other challenge path works without it.
    """
    return importlib.util.find_spec("argon2.low_level") is not None