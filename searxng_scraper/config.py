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
    """Return a writable directory for reports or caches, creating it.

    Order matters and used to be wrong. The current working directory comes
    first: that is where a CLI user expects output, and it is what `git` and
    friends do. The package directory comes next so that a checkout keeps its
    reports beside the source. The user profile is the last resort, for when
    neither is writable - a system site-packages installed without admin
    rights, for instance.

    site-packages used to be FIRST, which meant an installed package wrote its
    reports into `.../site-packages/results/`: writable, invisible, and not
    where anyone would ever look for them.
    """
    for candidate in (
        Path.cwd() / name,
        PACKAGE_DIR.parent / name,
        Path(os.environ.get("LOCALAPPDATA") or Path.home()) / ".sxng_search" / name,
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


_cffi = None


def cffi():
    """Import curl_cffi on first use and cache it.

    Importing it costs ~120 ms (it dlopens libcurl and resolves the TLS
    backend), which is pure waste for `sxng` with no arguments or `--help` -
    neither of them ever touches the network.

    Failing here rather than at module import also means the message can
    explain itself. On Android/Termux curl_cffi is often packaged against a
    different Python than the one installed, and dies inside dlopen with a
    bare "libpython3.X.so not found" that means nothing to whoever typed the
    command.
    """
    global _cffi
    if _cffi is None:
        try:
            from curl_cffi import requests as _cffi_module
        except ImportError as e:
            raise ImportError(
                "curl_cffi could not be imported, and this tool cannot work "
                "without it.\n\n"
                "  Desktop / WSL / macOS:  pip install --force-reinstall curl_cffi\n"
                "  Termux:               pkg upgrade python-curl-cffi\n"
                "                         (or: pip install --force-reinstall curl_cffi)\n\n"
                f"Underlying error: {e}"
            ) from e
        _cffi = _cffi_module
    return _cffi


def has_argon2() -> bool:
    """True when the optional argon2-cffi extra is installed.

    Only the rare argon2id flavour of the Anubis challenge needs it; every
    other challenge path works without it.

    argon2-cffi is an extra, not a hard dependency, so the common case is
    "not installed". find_spec() raises rather than returning None when the
    PARENT package is missing, hence the guard.
    """
    try:
        return importlib.util.find_spec("argon2.low_level") is not None
    except (ImportError, ValueError):
        return False