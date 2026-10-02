"""SearXNG meta-search - ONE self-contained file, fully browserless.

Everything is embedded here (no local imports):
    SECTION 1: pow_solver      - shared SHA-256 proof-of-work engine (multi-core)
    SECTION 2: sxng_html       - stdlib HTML results parser (JSON-API-shaped dicts)
    SECTION 3: searxng_limiter - SearXNG botdetection / link_token / Portico helpers
    SECTION 4: anubis_solver   - Anubis (Techaro) PoW challenge solver + cookie cache
    SECTION 5: searxng_search  - instance list, rotation race, escalation ladder
    SECTION 6: CLI

Why single-file: copy this one .py to any machine, ensure
    pip install curl_cffi      (argon2-cffi only for the rare argon2id challenge)
and run:
    python main.py --input nasa
    python main.py --input "nato staff" --profiles -n 10
    python main.py --input "nato strategy 2022" --pdf
    python main.py --input nasa -n 20 -o my.json -j 10 --fresh

Results are written to results/<query-slug>.json - one readable file per query,
never overwritten (running "nasa cosmos" twice gives nasa_cosmos.json and
nasa_cosmos_2.json, so "nasa" can never clobber it). -o overrides the path,
-o - prints JSON to stdout and writes no file. Pure caches live in .cache/
next to this script (.cache/instances.json / .cache/anubis_cookies.json) and
are safe to delete anytime.

Works even when an instance has JSON disabled: the per-instance escalation
ladder falls back to HTML (and solves Anubis / Portico PoW challenges on CPU
when needed):

    a. plain JSON API (?format=json)
    b. 200-but-HTML -> parse the HTML right away (JSON silently disabled);
       200-but-Anubis-interstitial -> fall through to the Anubis ladder
    c. Anubis front -> solve PoW on all CPU cores -> retry with auth cookies
    d. limiter blocked (429/302) -> browser-like session + link_token ping
       -> JSON retry, then HTML page -> parse results on the fly
    e. Portico PoW captcha -> solve in CPU -> parse resulting HTML

From code, the same API as the searxng_search module is exposed:
    execute(query, limit=10, mode="web", parallel=10, fresh=False) -> dict
    print_results(envelope)
"""

from __future__ import annotations

import argparse
import atexit
import base64
import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from curl_cffi import requests as cffi

try:  # argon2id WASM challenge support (optional dependency)
    from argon2.low_level import Type as _ArgonType
    from argon2.low_level import hash_secret_raw as _argon2_hash_raw

    _HAS_ARGON2 = True
except Exception:  # pragma: no cover
    _HAS_ARGON2 = False

IMPERSONATE = "chrome131"  # curl_cffi browser TLS fingerprint used everywhere

# global output mute: set by the CLI (default quiet mode, -v clears it)
QUIET = threading.Event()


def data_dir(name: str) -> Path:
    """Writable directory for caches and results.

    Prefers a folder next to this file. A library installed into a read-only
    site-packages (system-wide, no admin rights) falls back to the user profile
    (LOCALAPPDATA/sxng_search/<name>) and finally to the cwd.
    """
    local = Path(__file__).with_name(name)
    try:
        local.mkdir(exist_ok=True)
        probe = local / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return local
    except OSError:
        fallback = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / ".sxng_search"
        try:
            (fallback / name).mkdir(parents=True, exist_ok=True)
        except OSError:
            return Path.cwd() / name
        return fallback / name


def qprint(*args, **kwargs) -> None:
    """print() that respects the global quiet flag (CLI -v/--verbose)."""
    if not QUIET.is_set():
        print(*args, **kwargs)


# Imported as a library (not run as a script): stay silent by default, so the
# host program's stdout contains only what IT prints. Call set_verbose(True)
# to get the live race/captcha log back.
if __name__ != "__main__":
    QUIET.set()


def set_verbose(enabled: bool = True) -> None:
    """Turn the instance-race / captcha progress log on or off.

    Silent by default when imported as a library, and when the CLI runs
    without -v.
    """
    if enabled:
        QUIET.clear()
    else:
        QUIET.set()


# ===========================================================================
# SECTION 1/7: pow_solver - shared SHA-256 proof-of-work engine
# ===========================================================================
"""Fast shared SHA-256 proof-of-work solver (Anubis + Portico challenges).

Why it is fast:
  * the leading-zero check is folded into a single int comparison per hash
    with a one-byte prefilter (``d[0] == 0``), so the hot loop is essentially
    just digest + one byte test - no hexdigest(), no startswith(),
    no per-byte bit counting;
  * easy challenges (< ~0.5s expected) never leave the calling thread -
    on Windows spawning worker processes costs more than solving them;
  * harder challenges fan nonce lanes out to a persistent
    ProcessPoolExecutor (one worker per CPU core, capped at 16). All the
    blocking work - spawning workers, submitting chunks, collecting
    results - happens in a background "feeder" thread, so the calling
    thread hashes its own lane from the very first millisecond instead of
    stalling on process spawns (which take seconds on Windows);
  * the pool is created lazily and kept warm for the process lifetime, so
    every challenge after the first one gets the full multi-core speedup
    with zero spawn cost;
  * unfinished chunks are cancelled on a hit and are small (2048 nonces).

One convention for all solvers: find a nonce such that
SHA256(base + suffix(nonce)) has ``difficulty_bits`` leading zero bits.
``suffix`` is either the decimal ASCII of the nonce (Anubis legacy
"fast"/"slow", SearXNG Portico) or the nonce as fixed-width big/little
-endian bytes (Anubis WASM-era "sha256" algorithm).

The returned nonce is A valid solution, not necessarily the minimal one:
the server only verifies the hash, so any lane's hit is accepted.
"""

INLINE_CHUNK = 2048        # nonces per scan chunk (one unit of work per task)
SPAWN_THRESHOLD_S = 0.5    # don't spawn a pool for cheaper challenges
_MAX_WORKERS = 16

_POOL: ProcessPoolExecutor | None = None
_POOL_BROKEN = False
_POOL_LOCK = threading.Lock()


def leading_zero_bits(digest: bytes) -> int:
    """Count leading zero bits of a digest."""
    bits = 0
    for byte in digest:
        if byte:
            bits += 8 - byte.bit_length()
            break
        bits += 8
    return bits


def _bits_params(bits: int) -> tuple[int, int]:
    """(k, threshold) so that `leading zero bits >= bits` is exactly
    ``int.from_bytes(digest[:k], 'big') < threshold``."""
    k = (bits + 7) // 8
    return k, 1 << (8 * k - bits)


def _scan(task):
    """Hash one chunk of nonces; return (hexdigest, nonce) or None.

    task = (kind, base, k, threshold, endian, width, start, step, count)
    kind 0: message = base + decimal-ASCII nonce
    kind 1: message = base + nonce as `width`-byte big/little-endian int

    Hot loop: for k >= 2 a hit requires the first digest byte to be zero
    (threshold <= 128 there), which skips the int comparison on 255/256 of
    the nonces.
    """
    kind, base, k, threshold, endian, width, start, step, count = task
    sha = hashlib.sha256
    from_bytes = int.from_bytes
    if kind == 1:
        tb = int.to_bytes
        if k == 1:
            for i in range(count):
                n = start + i * step
                d = sha(base + tb(n, width, endian)).digest()
                if d[0] < threshold:
                    return d.hex(), n
        else:
            for i in range(count):
                n = start + i * step
                d = sha(base + tb(n, width, endian)).digest()
                if d[0] == 0 and from_bytes(d[:k], "big") < threshold:
                    return d.hex(), n
        return None
    if k == 1:
        for i in range(count):
            n = start + i * step
            d = sha(b"%s%d" % (base, n)).digest()
            if d[0] < threshold:
                return d.hex(), n
        return None
    for i in range(count):
        n = start + i * step
        d = sha(b"%s%d" % (base, n)).digest()
        if d[0] == 0 and from_bytes(d[:k], "big") < threshold:
            return d.hex(), n
    return None


def _scan_inline(kind, base, k, threshold, endian, width, start, deadline, limit):
    """Contiguous single-thread scan from `start`; raises TimeoutError."""
    while start <= limit:
        if time.monotonic() > deadline:
            raise TimeoutError("PoW deadline exceeded")
        count = min(INLINE_CHUNK, limit - start + 1)
        hit = _scan((kind, base, k, threshold, endian, width, start, 1, count))
        if hit:
            return hit
        start += count
    raise TimeoutError("PoW nonce space exhausted")


def _get_pool() -> ProcessPoolExecutor | None:
    """Lazily create (and keep warm) the worker pool; None if unavailable."""
    global _POOL
    if _POOL_BROKEN:
        return None
    with _POOL_LOCK:
        if _POOL is None:
            try:
                workers = min(os.cpu_count() or 1, _MAX_WORKERS)
                _POOL = ProcessPoolExecutor(max_workers=workers)
                atexit.register(_shutdown_pool)
            except Exception:
                return None
        return _POOL


def _shutdown_pool() -> None:
    """Terminate workers cleanly at interpreter exit.

    Without this, dying mid-spawn leaves child processes reading a closed
    pipe -> noisy 'EOFError: Ran out of input' on stderr (Windows).
    """
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            try:
                _POOL.shutdown(wait=True, cancel_futures=True)
            except Exception:
                pass
            _POOL = None


def _solve_parallel(kind, base, k, threshold, endian, width,
                    deadline, limit, nproc):
    """Fan nonce lanes out to the pool while the caller keeps hashing lane 0.

    Lane partition of [INLINE_CHUNK, limit]: lane i (i = 0..nproc) owns the
    nonces INLINE_CHUNK+i, INLINE_CHUNK+i+stride, ... with stride = nproc+1.
    The nproc+1 consecutive start points cover every residue mod stride
    exactly once - no gap, no overlap. Lane 0 is scanned by the calling
    thread; lanes 1..nproc by pool workers.

    The feeder thread absorbs all blocking work (process spawns on Windows
    take ~0.1-0.3s EACH and happen inside submit(), chunk submission and
    result collection) so the calling thread never stops hashing.
    """
    stride = nproc + 1
    hit: list = []                       # [hexdigest, nonce] on a worker hit
    hit_evt = threading.Event()
    stop_evt = threading.Event()
    state = {"pool_ok": None, "broken": False}

    def feeder():
        try:
            ex = _get_pool()
            if ex is None:
                state["pool_ok"] = False
                return
            state["pool_ok"] = True
            next_start = {i: INLINE_CHUNK + i for i in range(1, stride)}
            pending: dict = {}

            def refill() -> bool:
                """Top the queue up; True if anything was submitted."""
                for lane in list(next_start):
                    if len(pending) >= 2 * stride:
                        break
                    start = next_start[lane]
                    if start > limit:
                        continue
                    count = min(INLINE_CHUNK, (limit - start) // stride + 1)
                    if count <= 0:
                        continue
                    try:
                        fut = ex.submit(
                            _scan, (kind, base, k, threshold, endian,
                                    width, start, stride, count)
                        )
                    except Exception:
                        state["broken"] = True
                        return False
                    pending[fut] = lane
                    next_start[lane] = start + count * stride
                return bool(pending)

            refill()
            while pending and not stop_evt.is_set():
                done = [f for f in pending if f.done()]
                if not done:
                    stop_evt.wait(0.01)
                    continue
                for fut in done:
                    pending.pop(fut)
                    try:
                        r = fut.result()
                    except Exception:
                        state["broken"] = True
                        r = None
                    if r:
                        hit.extend(r)
                        hit_evt.set()
                        stop_evt.set()
                        for f2 in pending:
                            f2.cancel()
                        return
                if not refill():
                    break   # every lane exhausted past `limit`
        except Exception:
            state["broken"] = True
        finally:
            stop_evt.set()

    ft = threading.Thread(target=feeder, daemon=True)
    ft.start()

    start = INLINE_CHUNK
    try:
        while start <= limit:
            if hit_evt.is_set():
                return hit[0], hit[1]
            if time.monotonic() > deadline:
                raise TimeoutError("PoW deadline exceeded")
            count = min(INLINE_CHUNK, (limit - start) // stride + 1)
            r = _scan((kind, base, k, threshold, endian, width,
                       start, stride, count))
            if r:
                stop_evt.set()
                return r
            start += count * stride
            if state["pool_ok"] is False or (state["broken"] and not ft.is_alive()):
                # pool unavailable or died: sweep the rest contiguously
                return _scan_inline(kind, base, k, threshold, endian, width,
                                    start, deadline, limit)
        # lane 0 exhausted: wait for the workers to cover their lanes
        while ft.is_alive() and not hit_evt.wait(0.05):
            if time.monotonic() > deadline:
                raise TimeoutError("PoW deadline exceeded")
        if hit_evt.is_set():
            return hit[0], hit[1]
        if state["broken"]:
            return _scan_inline(kind, base, k, threshold, endian, width,
                                INLINE_CHUNK, deadline, limit)
        raise TimeoutError("PoW nonce space exhausted")
    finally:
        stop_evt.set()


def solve(
    base: bytes,
    difficulty_bits: int,
    *,
    suffix: str = "decimal",   # "decimal" (ascii nonce) | "int" (fixed-width bytes)
    endian: str = "big",
    width: int = 4,
    max_seconds: float = 30.0,
    limit: int = 0xFFFF_FFFF,
) -> tuple[str, int]:
    """Find (hexdigest, nonce) with SHA256(base + suffix(nonce)) having
    `difficulty_bits` leading zero bits.

    Raises TimeoutError when max_seconds elapsed or the nonce space is
    exhausted.
    """
    kind = 1 if suffix == "int" else 0
    k, threshold = _bits_params(difficulty_bits)
    deadline = time.monotonic() + max_seconds

    # Stage 1: one inline chunk - also measures this machine's hash rate.
    n1 = min(INLINE_CHUNK, limit + 1)
    t0 = time.perf_counter()
    hit = _scan((kind, base, k, threshold, endian, width, 0, 1, n1))
    if hit:
        return hit
    inline_rate = n1 / max(time.perf_counter() - t0, 1e-9)
    expected_time = (2.0 ** difficulty_bits) / inline_rate

    nproc = min(os.cpu_count() or 1, _MAX_WORKERS)
    # cold pool: spawning is only worth it above the threshold;
    # warm pool: parallelism is (almost) free, always use it
    use_pool = nproc >= 2 and (
        expected_time > SPAWN_THRESHOLD_S or _POOL is not None
    )
    if not use_pool:
        return _scan_inline(kind, base, k, threshold, endian, width,
                            INLINE_CHUNK, deadline, limit)
    return _solve_parallel(kind, base, k, threshold, endian, width,
                           deadline, limit, nproc)


# ===========================================================================
# SECTION 2/7: sxng_html - stdlib parser for SearXNG HTML result pages
# ===========================================================================
"""Stdlib-only parser for SearXNG HTML result pages (simple/oscar themes).

SearXNG renders results as
    <article class="result result-default">
      <h3><a href="https://...">Title</a></h3>
      <p class="content">snippet text</p>
      <p class="engines">google, bing</p>
    </article>
A result is only counted when it has a title link inside an <h3> - this
mirrors the old CSS selector (``article.result h3 a``) and filters out
image/video articles that merely carry a "result" class. Unlike naive
``.text`` extraction this collects ALL descendant text of the title link
and snippet paragraph (titles like ``<a>Py<em>thon</em> docs</a>`` and
snippets containing nested tags come out complete).
"""

# capture tag/class -> result dict key
_KEY = {"title": "title", "content": "snippet", "engines": "engine"}


class _ResultParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._art: dict | None = None
        self._h3 = False            # inside the article's <h3>
        self._link_done = False     # title link already captured
        self._cap: str | None = None  # "title" | "content" | "engines"
        self._buf: list[str] = []

    def _flush(self):
        if self._art is not None and self._cap:
            self._art[_KEY[self._cap]] = "".join(self._buf).strip()
        self._cap = None
        self._buf = []

    def _close_article(self):
        if self._cap:
            self._flush()
        if self._art is not None and self._art["url"]:
            self.results.append(self._art)
        self._art = None
        self._h3 = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self._art is None:
            if tag == "article" and "result" in (a.get("class") or "").split():
                self._art = {"title": "", "url": "", "snippet": "", "engine": ""}
                self._h3 = False
                self._link_done = False
                self._cap = None
                self._buf = []
            return
        if tag == "h3":
            self._h3 = True
        elif tag == "a":
            if self._h3 and not self._link_done:
                self._link_done = True
                self._art["url"] = a.get("href") or ""
                self._cap, self._buf = "title", []
        elif tag == "p" and self._cap is None:
            cls = (a.get("class") or "").split()
            if "content" in cls:
                self._cap, self._buf = "content", []
            elif "engines" in cls:
                self._cap, self._buf = "engines", []

    def handle_endtag(self, tag):
        if self._art is None:
            return
        if tag == "article":
            self._close_article()
        elif tag == "h3":
            if self._cap == "title":
                self._flush()
            self._h3 = False
        elif tag == "a":
            if self._cap == "title":
                self._flush()
        elif tag == "p":
            if self._cap in ("content", "engines"):
                self._flush()

    def handle_data(self, data):
        if self._art is not None and self._cap:
            self._buf.append(data)


def parse_results_html(html: str, base_url: str | None = None) -> list[dict]:
    """Parse a results page into JSON-API-like dicts (title/url/snippet/engine)."""
    p = _ResultParser()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    out = []
    for r in p.results:
        if base_url and r["url"].startswith("/"):
            r["url"] = urljoin(base_url, r["url"])
        out.append(r)
    return out


# ===========================================================================
# SECTION 3/7: searxng_limiter - botdetection / link_token / Portico helpers
# ===========================================================================
"""Browserless helpers for SearXNG instances fronted by the stock `limiter` plugin.

The SearXNG limiter (searx.botdetection) evaluates requests with several checks:
  * http_* probes: User-Agent / Accept / Accept-Language / Sec-Fetch headers
  * ip_limit: sliding-window request counters per IP network
  * link_token: a request is "suspicious" unless the client previously fetched
    the randomized stylesheet /client<token>.css referenced from every page
    (a browser does this automatically when loading the CSS)

Suspicious requests are rate-limited much harder (BURST_MAX_SUSPICIOUS=2 per
20s instead of 15) and often answered with 429 or a 302 redirect to "/".
Newer SearXNG also ships "Portico": a JS proof-of-work captcha (SHA-256 with
`pow_bits` leading zero BITS over seed "\\0"-joined(payload, signature, nonce,
User-Agent), counter-suffix) that must be POSTed back to /portico as
captcha_js_proof="<counter>:<hexdigest>".

Both mechanisms are solved here in pure CPU - no browser required.
"""

BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}

_TOKEN_CSS_RE = re.compile(r'href="(/client[^"]+\.css)"')


def new_session(user_agent: str) -> cffi.Session:
    """curl_cffi session with Chrome TLS fingerprint and browser-like headers."""
    s = cffi.Session(impersonate=IMPERSONATE)
    s.headers.update({"User-Agent": user_agent, **BROWSER_HEADERS})
    return s


def ping_link_token(s: cffi.Session, base_url: str, timeout: int = 15) -> bool:
    """Fetch the randomized /client<token>.css like a browser loading the page.

    Marks this client network as "not suspicious" for the limiter's
    link_token check. Returns True if a token stylesheet was found and fetched.
    """
    base = base_url if base_url.endswith("/") else base_url + "/"
    try:
        r0 = s.get(base, timeout=timeout)
    except Exception:
        return False
    m = _TOKEN_CSS_RE.search(r0.text)
    if not m:
        return False
    try:
        s.get(base + m.group(1).lstrip("/"), timeout=timeout)
        return True
    except Exception:
        return False


def is_portico_captcha(html: str) -> bool:
    """True if the page is the SearXNG 'Portico' PoW captcha screen."""
    return "captcha-screen" in html and 'data-captcha-payload="' in html


def solve_portico(s: cffi.Session, base_url: str, html: str, user_agent: str,
                  timeout: int = 25) -> str | None:
    """Solve the Portico proof-of-work and POST it back.

    Returns the response HTML of the solved page (results or redirect target)
    or None on failure. SHA-256(seed + "\\0" + counter) must have `pow_bits`
    leading zero *bits*; proof format is "<counter>:<hexdigest>".
    """
    base = base_url if base_url.endswith("/") else base_url + "/"
    attrs = dict(re.findall(r'data-captcha-([\w-]+)="([^"]*)"', html))
    fields = dict(re.findall(r'<input[^>]*name="([^"]*)"[^>]*value="([^"]*)"', html))
    if "payload" not in attrs:
        return None
    bits = int(attrs.get("pow-bits", "15"))
    seed = "\0".join([attrs["payload"], attrs["signature"], attrs["nonce"], user_agent])

    started = time.monotonic()
    # SHA256(seed + "\0" + counter) needs `bits` leading zero BITS
    try:
        digest_hex, counter = solve(
            seed.encode() + b"\0", bits,
            max_seconds=20.0, limit=5_000_000,
        )
    except TimeoutError:
        return None

    data = dict(fields)
    data["captcha_js_proof"] = f"{counter}:{digest_hex}"
    # the JS waits captcha-delay (default 1050ms) before submitting
    time.sleep(min(1.1, max(0.0, 1.1 - (time.monotonic() - started))))
    try:
        resp = s.post(base + "portico", data=data, timeout=timeout)
        return resp.text
    except Exception:
        return None


# ===========================================================================
# SECTION 4/7: anubis_solver - Anubis (Techaro) PoW challenge solver
# ===========================================================================
"""Browserless Anubis (Techaro) proof-of-work challenge solver.

Anubis interstitials embed the whole challenge in the HTML:
  <script id="anubis_challenge" type="application/json">{"rules":...,"challenge":...}</script>

To pass it you must find a nonce such that hex(SHA256(randomData + nonce))
starts with `difficulty` leading zeros, then GET
  /.within.website/x/cmd/anubis/api/pass-challenge
      ?id=<id>&response=<hash>&nonce=<nonce>&redir=/&elapsedTime=<ms>

The server then sets a `techaro.lol-anubis-auth-*` JWT cookie (typically 1 hour).
The challenge is unbound random data and solve time is not validated, so solving
it in Python on CPU is functionally identical to what the browser's JS worker does.

Requirements that took live debugging to figure out:
  * A cookie-capable session is mandatory. Anubis first sets a
    `*-anubis-cookie-verification-*` cookie and refuses pass-challenge with
    500 "Your browser is configured to disable cookies" unless it is sent back.
    A plain `Fetcher.get` does not persist Set-Cookie, so we use a
    curl_cffi Session (also gives a proper browser TLS fingerprint).
  * The verification cookie may be marked Partitioned/Secure; the session jar
    handles it fine as long as it is reused across the two requests.

Performance: the PoW itself is delegated to solve() (SECTION 1), which hashes
on all CPU cores. Legacy "fast"/"slow" challenges (ASCII nonce suffix) and
WASM-era "sha256" challenges (fixed-width nonce bytes) both go through it;
argon2id stays a single-threaded loop because it is memory-hard (19 MiB per
hash - extra processes just thrash RAM).
"""

CHALLENGE_RE = re.compile(
    r'<script id="anubis_challenge" type="application/json">(.*?)</script>', re.S
)

# progress chatter can be muted once a parallel race has a winner
_QUIET = threading.Event()


def set_quiet() -> None:
    """Silence progress prints (used when another instance already won)."""
    _QUIET.set()

COOKIE_FILE = data_dir(".cache") / "anubis_cookies.json"


def _parse_jwt_expiry(token: str) -> float:
    """Return the `exp` claim of a JWT as a unix timestamp (0 if unreadable)."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload_b64)).get("exp", 0))
    except Exception:
        return 0.0


def _solve_pow(random_data: str, difficulty: int) -> tuple[str, int]:
    """Brute-force a nonce whose SHA256(random_data + nonce) hex has `difficulty`
    leading zero HEX chars (= 4*difficulty leading zero bits).

    Mirrors Anubis's worker/sha256-*.mjs loop; runs on all CPU cores via
    solve() (SECTION 1)."""
    digest, nonce = solve(
        random_data.encode(), difficulty * 4, max_seconds=60.0
    )
    return digest, nonce


# ---------------------------------------------------------------------------
# WASM-era challenges (algorithm = sha256 | argon2id | hashx).
#
# Newer Anubis ships the PoW as a WebAssembly module per algorithm. The module
# source (wasm/pow/<algo>/src/lib.rs) defines the exact hash convention, which
# we reproduce natively in Python - no WASM runtime needed:
#
#   * challenge "randomData" is decoded from HEX into raw bytes
#   * nonce endian-ness is chosen by the LAST BYTE of the raw challenge:
#     >= 0x80 -> little-endian u32, else big-endian u32 (anti-GPU measure)
#   * sha256:   hash = SHA256(challenge_bytes || nonce_bytes)   (u32 nonce)
#   * argon2id: hash = Argon2id(password=challenge_bytes, salt=nonce_bytes,
#                               default params t=3, m=19456 KiB, p=1)
#     NOTE: argon2id encodes the nonce as u64 (8 bytes) - the salt must be
#     8 bytes to satisfy Argon2's MIN_SALT_LEN; sha256 uses u32 (4 bytes).
#   * difficulty counts leading zero BITS (not hex chars!)
#   * response is the hex digest, nonce the decimal u32 -> same pass-challenge
#     endpoint as the legacy challenge
# ---------------------------------------------------------------------------


def _wasm_le_nonce(raw: bytes, nonce: int, width: int = 4) -> bytes:
    """Encode the nonce as LE/BE depending on the challenge's last byte.

    width=4 (u32) for the sha256 WASM algorithm, width=8 (u64) for argon2id
    (8 bytes are required to satisfy Argon2's MIN_SALT_LEN)."""
    endian = "little" if (raw and raw[-1] >= 128) else "big"
    return nonce.to_bytes(width, endian)


def _leading_zero_bits(digest: bytes) -> int:
    """Count leading zero bits of the digest (WASM difficulty semantics)."""
    return leading_zero_bits(digest)


def _wasm_nonce_endian(raw: bytes) -> str:
    """Endianness of the nonce, per the WASM module convention."""
    return "little" if (raw and raw[-1] >= 128) else "big"


def _solve_wasm(random_data: str, difficulty: int, algorithm: str) -> tuple[str, int]:
    """Solve a WASM-era challenge natively in Python.

    Supported algorithms: sha256 (always, multi-core), argon2id (requires
    argon2-cffi; memory-hard so kept single-threaded). hashx is intentionally
    unsupported: the HashX program generator is a whole bytecode VM and no
    SearXNG instance uses it - raise instead.
    """
    raw = bytes.fromhex(random_data)
    if algorithm == "sha256":
        return solve(
            raw, difficulty,
            suffix="int", endian=_wasm_nonce_endian(raw), width=4,
            max_seconds=60.0,
        )
    if algorithm == "argon2id":
        if not _HAS_ARGON2:
            raise RuntimeError("argon2id challenge requires: pip install argon2-cffi")
        salt_end = _wasm_nonce_endian(raw)
        # memory-hard: ~19 MiB per hash, parallel processes would thrash RAM
        for nonce in range(0x1_0000_0000):
            salt = nonce.to_bytes(8, salt_end)  # u64: 8-byte salt
            digest = _argon2_hash_raw(
                secret=raw, salt=salt,
                time_cost=3, memory_cost=19456, parallelism=1,
                hash_len=32, type=_ArgonType.ID,
            )
            if leading_zero_bits(digest) >= difficulty:
                return digest.hex(), nonce
        raise RuntimeError("argon2id wasm PoW exhausted u32 nonce space")
    raise RuntimeError(f"unsupported wasm algorithm: {algorithm!r} (hashx not implemented)")


def _auth_cookie_name(cookies: dict) -> str | None:
    """Name of the Anubis AUTH cookie in a jar snapshot, if present.

    Stable Anubis (<= 1.27) sets `techaro.lol-anubis-auth-<suffix>`;
    the devel branch renamed it to plain `techaro.lol-anubis` while the
    verification cookie keeps its name - both spellings must be accepted
    (mizuki.st runs devel and silently 302-redirects otherwise-good passes).
    """
    for k in cookies:
        kl = k.lower()
        if "anubis" in kl and ("auth" in kl or kl == "techaro.lol-anubis"):
            return k
    return None


def _load_cached_cookie(host: str) -> dict | None:
    if not COOKIE_FILE.exists():
        return None
    try:
        cache = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        entry = cache.get(host)
        if entry and entry.get("expires", 0) > time.time() + 60:
            return entry["cookies"]
    except Exception:
        pass
    return None


def _save_cached_cookie(host: str, cookies: dict, expires: float) -> None:
    cache = {}
    if COOKIE_FILE.exists():
        try:
            cache = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    cache[host] = {"cookies": cookies, "expires": expires}
    COOKIE_FILE.parent.mkdir(exist_ok=True)
    COOKIE_FILE.write_text(json.dumps(cache, indent=1), encoding="utf-8")


def _drop_cached_cookie(host: str) -> None:
    """Remove one host's entry from the cookie cache (e.g. after a rejection)."""
    if not COOKIE_FILE.exists():
        return
    try:
        cache = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        if host in cache:
            del cache[host]
            COOKIE_FILE.parent.mkdir(exist_ok=True)
            COOKIE_FILE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    except Exception:
        pass


def is_challenge_page(body: str) -> bool:
    """True if the HTML looks like an Anubis interstitial."""
    return bool(CHALLENGE_RE.search(body))


# Probe results live for the process lifetime; cross-run cache is overkill
# since a probe is a single fast GET.
_PROBE_CACHE: dict[str, bool | None] = {}
_PROBE_POSITIVE_TTL = 3000.0  # seconds to trust a positive Anubis verdict
_PROBE_POSITIVE_AT: dict[str, float] = {}


def has_anubis(base_url: str, user_agent: str, timeout: int = 15) -> bool | None:
    """Check whether an instance is fronted by an Anubis challenge.

    Probes the front page once per run (positives are remembered for
    _PROBE_POSITIVE_TTL seconds, negatives for the process lifetime).
    Returns True/False; None if the probe itself failed (network error).
    """
    host = urlparse(base_url).netloc
    if host in _PROBE_CACHE:
        if _PROBE_CACHE[host] is True and time.time() - _PROBE_POSITIVE_AT.get(host, 0) > _PROBE_POSITIVE_TTL:
            del _PROBE_CACHE[host]
        else:
            return _PROBE_CACHE[host]

    base = base_url if base_url.endswith("/") else base_url + "/"
    try:
        resp = cffi.Session(impersonate=IMPERSONATE).get(
            base, headers={"User-Agent": user_agent}, timeout=timeout
        )
        verdict = is_challenge_page(resp.text)
    except Exception:
        verdict = None
    if verdict is not None:
        _PROBE_CACHE[host] = verdict
        if verdict:
            _PROBE_POSITIVE_AT[host] = time.time()
    return verdict


def solve_and_get_cookies(base_url: str, user_agent: str) -> dict | None:
    """Solve the Anubis challenge for an instance and return the auth cookies.

    Uses a curl_cffi session (browser TLS impersonation, persistent cookie jar).
    Returns None if solving failed or the site is not Anubis-protected.
    """
    base = base_url if base_url.endswith("/") else base_url + "/"
    s = cffi.Session(impersonate=IMPERSONATE)

    # 1. Fetch the interstitial; session jar picks up the verification cookie
    resp = s.get(base, headers={"User-Agent": user_agent}, timeout=20)
    m = CHALLENGE_RE.search(resp.text)
    if not m:
        return None  # not an Anubis page
    challenge, _ = json.JSONDecoder().raw_decode(m.group(1))
    ch = challenge["challenge"]
    algorithm = (challenge["rules"].get("algorithm") or "fast").lower()
    difficulty = int(ch.get("difficulty") or challenge["rules"].get("difficulty", 4))
    started = time.monotonic()

    # 2. Solve the proof-of-work (CPU, all cores); dispatch on the algorithm
    if algorithm in ("fast", "slow"):
        digest, nonce = _solve_pow(ch["randomData"], difficulty)
    elif algorithm in ("sha256", "argon2id", "hashx"):
        digest, nonce = _solve_wasm(ch["randomData"], difficulty, algorithm)
    else:
        raise RuntimeError(f"unknown anubis algorithm: {algorithm!r}")
    if not _QUIET.is_set():
        qprint(
            f"    [anubis] solved {algorithm} d={difficulty} nonce={nonce} "
            f"in {time.monotonic() - started:.1f}s"
        )

    # 3. Submit: pass-challenge validates the solution, sets the auth cookie
    #    and redirects to redir. Verification cookie must be sent along.
    resp = s.get(
        base + ".within.website/x/cmd/anubis/api/pass-challenge",
        params={
            "id": ch["id"],
            "response": digest,
            "nonce": str(nonce),
            "redir": "/",
            "elapsedTime": str(int((time.monotonic() - started) * 1000)),
        },
        headers={"User-Agent": user_agent},
        timeout=20,
    )
    if resp.status_code != 200:
        return None

    cookies = {
        k: v for k, v in s.cookies.items() if "anubis" in k.lower()
    }
    if not _auth_cookie_name(cookies):
        return None
    return cookies


def get_anubis_cookies(base_url: str, user_agent: str) -> dict | None:
    """Return Anubis auth cookies for an instance, solving the PoW if needed.

    Results are cached in .cache/anubis_cookies.json until shortly before
    JWT expiry.
    Returns None if the site is not Anubis-protected or solving failed.
    """
    host = urlparse(base_url).netloc
    cached = _load_cached_cookie(host)
    if cached:
        return cached
    return refresh_anubis_cookies(base_url, user_agent)


def refresh_anubis_cookies(base_url: str, user_agent: str) -> dict | None:
    """Force-solve the challenge (cache bypass) and overwrite the cached cookies.

    Use when cached cookies are rejected by the server (restart / key rotation).
    A failed solve drops the stale cache entry so future calls re-solve too.
    """
    host = urlparse(base_url).netloc
    cookies = solve_and_get_cookies(base_url, user_agent)
    if not cookies:
        _drop_cached_cookie(host)
        return None
    auth_name = _auth_cookie_name(cookies)
    auth_token = cookies.get(auth_name, "") if auth_name else ""
    expires = _parse_jwt_expiry(auth_token) or (time.time() + 3600)
    _save_cached_cookie(host, cookies, expires)
    return cookies


# ===========================================================================
# SECTION 5/7: searxng_search - instance list, rotation race, escalation
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


# ---------------------------------------------------------------------------
# per-instance escalation ladder
# ---------------------------------------------------------------------------

def _json_results(resp) -> list[dict] | None:
    """Extract results from a JSON response; None if the body is not JSON."""
    try:
        return resp.json().get("results", [])
    except Exception:
        return None


def _anubis_session(base_url: str, refresh: bool = False):
    """Session carrying Anubis auth cookies (disk cache, or forced re-solve)."""
    get = refresh_anubis_cookies if refresh else get_anubis_cookies
    cookies = get(base_url, UA)
    if not cookies:
        return None
    s = cffi.Session(impersonate=IMPERSONATE)
    for k, v in cookies.items():
        s.cookies.set(k, v)
    return s


def _anubis_attempt(s, base_url: str, query: str) -> tuple[list[dict], str] | None:
    """Try JSON then HTML with an Anubis-authenticated session.

    Returns (results, path) on success, None if the cookies were rejected
    (the server served a fresh challenge page) or nothing usable came back.
    A fresh Anubis pass still looks "suspicious" to SearXNG's own limiter,
    which redirects /search to the index page - a browser recovers by
    loading the index and its link_token stylesheet, so we do the same and
    retry once."""
    def _json_try():
        resp = s.get(
            base_url + "search",
            params={"q": query, "format": "json"},
            headers={"User-Agent": UA},
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        results = _json_results(resp)
        if results:
            return results, "json+anubis"
        if is_challenge_page(resp.text):
            return None  # stale cookies: the server re-challenged us
        return None

    def _html_try():
        resp = s.get(
            base_url + "search",
            params={"q": query},
            headers={"User-Agent": UA, "Accept": "text/html"},
            timeout=TIMEOUT,
        )
        if resp.status_code == 200 and "<article" in resp.text:
            results = parse_results_html(resp.text, base_url)
            if results:
                return results, f"html+anubis ({len(results)} from HTML)"
        return None

    # A fresh Anubis pass still looks "suspicious" to SearXNG's own limiter.
    # Mimic the browser exactly: land on the index, fetch the link_token
    # stylesheet, then search HTML-only - JSON is 429-limited even for real
    # browsers here, and a 429 burns the "suspicious" burst budget that the
    # HTML request right after would need (BURST_MAX_SUSPICIOUS=2/20s).
    ping_link_token(s, base_url, timeout=TIMEOUT)
    got = _html_try()
    if got:
        return got
    return _json_try()


def search_on_instance(base_url: str, query: str) -> tuple[list[dict], str]:
    """Query one instance; returns (JSON-API-like results, path-used).

    Escalation ladder, all browserless (see the file docstring).
    Raises RuntimeError with a short reason if every path fails.
    """
    # --- a. plain JSON API ---
    try:
        resp = _get(
            base_url + "search",
            params={"q": query, "format": "json"},
            headers={"User-Agent": UA},
        )
    except Exception as e:
        raise RuntimeError(f"connection failed ({type(e).__name__})") from e

    if resp.status_code == 200:
        results = _json_results(resp)
        if results is not None:
            if results:
                return results, "json"
            raise RuntimeError("0 results (json empty)")
        # 200 but not JSON: some instances serve HTML even for format=json -
        # the page is already here, parse it for free
        results = parse_results_html(resp.text, base_url)
        if results:
            return results, "html"
        if not is_challenge_page(resp.text):
            raise RuntimeError("200 but no JSON and no HTML results")
        # the body itself is an Anubis interstitial -> fall through

    blocked_status = resp.status_code

    # --- c. Anubis front? (probe, or the challenge page we just got) ---
    if is_challenge_page(resp.text) or has_anubis(base_url, UA):
        qprint("    [i] Anubis challenge detected, solving without a browser...")
        got = None
        for refresh in (False, True):
            s = _anubis_session(base_url, refresh=refresh)
            if not s:
                break
            got = _anubis_attempt(s, base_url, query)
            if got:
                break
            # first attempt used cached cookies; a rejection means they went
            # stale (server restart / key rotation) -> force a fresh solve
        if got:
            return got
        # distinguish "PoW unsolvable" (rare, 6h ban) from "solved but the
        # SearXNG limiter is cooling our IP down" (transient, 10 min ban)
        raise RuntimeError("Anubis solved but limiter still blocks search "
                           "(ip_limit cooldown or JSON disabled)")

    # --- d. limiter (botdetection): browser-like session + link_token ping ---
    s = new_session(UA)
    ping_link_token(s, base_url, timeout=TIMEOUT)
    resp = s.get(
        base_url + "search",
        params={"q": query, "format": "json"},
        timeout=TIMEOUT,
    )
    if resp.status_code == 200:
        results = _json_results(resp)
        if results:
            return results, "json+limiter"

    # --- d/e. HTML page (possibly with Portico captcha) ---
    resp = s.get(
        base_url + "search",
        params={"q": query},
        timeout=TIMEOUT,
    )
    html = resp.text
    if resp.status_code == 200 and is_portico_captcha(html):
        qprint("    [i] Portico PoW captcha detected, solving...")
        solved = solve_portico(s, base_url, html, UA, timeout=TIMEOUT)
        if solved and "captcha-screen" not in solved:
            results = parse_results_html(solved, base_url)
            if results:
                return results, f"html+portico ({len(results)} from PoW)"
    elif resp.status_code == 200:
        results = parse_results_html(html, base_url)
        if results:
            return results, "html"

    raise RuntimeError(f"HTTP {blocked_status} and HTML fallback failed")


# ---------------------------------------------------------------------------
# rotation: parallel race (default) or serial ladder
# ---------------------------------------------------------------------------

def _looks_rate_limited(reason: str) -> bool:
    """True for transient limiter blocks worth a cooldown retry."""
    r = reason.lower()
    return "429" in r or "too many requests" in r


def _try_one(base: str, query: str):
    try:
        return base, search_on_instance(base, query)
    except Exception as e:
        return base, e


def run_search(
    query: str, limit: int, parallel: int = 6, fresh: bool = False,
    validator=None,
) -> tuple[list[dict], str, str]:
    """Rotate through candidate instances until one answers.

    `validator(results) -> bool` (optional): a result-set only WINS the race
    if it passes. Several engines silently drop operators like site:, so a
    profile/pdf search without a validator would be won by a generic result
    page. Sets that fail the validator are kept as a fallback but never
    recorded in the bad-instance cache (the instance itself works fine).

    Returns (results, instance_url, path-used)."""
    t0 = time.perf_counter()
    candidates = fetch_instance_list(fresh=fresh)
    usable, skipped = filter_bad(candidates)
    if skipped:
        qprint(f"[i] {len(usable)} usable instances "
              f"({len(skipped)} skipped from the bad-instance cache)")
    else:
        qprint(f"[i] {len(usable)} HTTP instances with working search")

    pool = usable[:MAX_INSTANCES_TO_TRY]
    failures: list[tuple[str, str]] = []

    if parallel <= 1:
        for cand in pool:
            base = cand["url"]
            for attempt in (1, 2):  # 429 is transient: wait out, retry once
                _base, outcome = _try_one(base, query)
                if not isinstance(outcome, Exception):
                    results, path = outcome
                    if results:
                        remember_failures(failures)
                        qprint(f"[+] {base} -> {len(results)} results "
                              f"in {time.perf_counter() - t0:.1f}s")
                        return results[:limit], base, path
                    outcome = RuntimeError("0 results")
                reason = str(outcome)
                if attempt == 1 and _looks_rate_limited(reason):
                    qprint(f"[~] {base} -> {reason}; waiting 21s for the "
                          "limiter window, retrying...")
                    time.sleep(21)
                    continue
                qprint(f"[-] {base} -> {reason}")
                failures.append((base, reason))
                break
        remember_failures(failures)
        raise RuntimeError(
            f"all {len(failures)} tried instances failed (banned, rate-limited or JSON API disabled)"
        )

    # --- parallel race: first instance with results wins, exit immediately ---
    # Daemon threads + a semaphore window instead of ThreadPoolExecutor:
    # an executor's `with` block joins every worker before returning, which
    # would stall the winner behind the slowest loser for many seconds.
    # Threads left running when the winner arrives simply die with the process.
    pass_q: queue.Queue = queue.Queue()   # passed the validator (or no validator)
    any_q: queue.Queue = queue.Queue()    # non-empty but validator-unworthy
    failures: list[tuple[str, str]] = []
    state_lock = threading.Lock()
    winner_evt = threading.Event()
    window = threading.Semaphore(parallel)
    finished = [0]  # workers that reported back (wins, fails, fallbacks)

    def _worker(base: str):
        window.acquire()
        try:
            if winner_evt.is_set():
                return
            b, outcome = _try_one(base, query)
            with state_lock:
                post_winner = winner_evt.is_set()
            if post_winner:
                return  # a late finisher: stay silent, the race is already won
            if isinstance(outcome, Exception):
                with state_lock:
                    failures.append((b, str(outcome)))
                qprint(f"[-] {b} -> {outcome}")
            elif not outcome[0]:
                with state_lock:
                    failures.append((b, "0 results"))
                qprint(f"[-] {b} -> 0 results")
            elif validator is not None and not validator(outcome[0]):
                qprint(f"[~] {b} -> {len(outcome[0])} results, "
                      "but not what the mode asked for")
                any_q.put((b, outcome[0], outcome[1]))
            else:
                pass_q.put((b, outcome[0], outcome[1]))
        except Exception as e:  # defensive: a worker must never wedge the race
            with state_lock:
                failures.append((base, f"worker error ({e})"))
        finally:
            with state_lock:
                finished[0] += 1
            window.release()

    threads = []
    for cand in pool:
        t = threading.Thread(target=_worker, args=(cand["url"],), daemon=True)
        t.start()
        threads.append(t)

    winner = None
    deadline = time.monotonic() + 90
    try:
        while winner is None and time.monotonic() < deadline:
            try:
                winner = pass_q.get(timeout=15)
            except queue.Empty:
                # nothing validator-worthy yet: a worthy fallback beats waiting
                try:
                    winner = any_q.get_nowait()
                except queue.Empty:
                    if finished[0] >= len(pool):
                        break  # everyone reported: move to stage B now
                    continue
        if winner is None:
            try:  # last resort: whatever arrived late
                winner = any_q.get_nowait()
            except queue.Empty:
                pass
    finally:
        if winner is not None:
            winner_evt.set()
            set_quiet()  # late finishers must not print progress chatter

    with state_lock:
        failure_snapshot = list(failures)

    # --- stage B (endgame): the race lost but we are not out of options ---
    # The bad-instance cache marks cooldown-blocked instances with a 10-min
    # TTL even though 429 typically clears in one 20s ip_limit window.
    # Before giving up, serially retry the freshest 429 victims after a
    # pause (they are the fastest instances by searx.space ranking, so
    # winning one back beats a fresh full race). Only untried instances
    # would be raced next run anyway, so this reuses - not doubles - work.
    if not winner:
        cooldown = [url for url, reason in failure_snapshot
                    if _looks_rate_limited(reason)]
        # coolest first: the earlier an instance 429'd, the more of its
        # window has already elapsed while other instances were racing
        if cooldown:
            qprint(f"[i] endgame: {len(cooldown)} instance(s) hit the limiter; "
                  "waiting out the 20s window and retrying...")
            time.sleep(21)
            for url in cooldown[:3]:
                _b, outcome = _try_one(url, query)
                if not isinstance(outcome, Exception) and outcome[0] \
                        and (validator is None or validator(outcome[0])):
                    winner = (url, outcome[0], outcome[1])
                    break
                qprint(f"[-] endgame {url} -> "
                      f"{outcome if isinstance(outcome, Exception) else 'no results'}")
        if winner is None:
            try:
                winner = any_q.get_nowait()
            except queue.Empty:
                pass

    remember_failures(failure_snapshot)
    if winner:
        base, results, path = winner
        qprint(f"[+] {base} -> {len(results)} results in {time.perf_counter() - t0:.1f}s")
        return results[:limit], base, path
    raise RuntimeError(
        f"all {len(failure_snapshot)} tried instances failed (banned, rate-limited or JSON API disabled)"
    )


def _profile_key(r: dict) -> int:
    """Rank: personal /in/ profiles first, then any linkedin page, then rest."""
    u = r.get("url", "")
    if "/in/" in u:
        return 0
    if "linkedin.com" in u:
        return 1
    return 2


def run_profiles(query: str, limit: int, parallel: int = 6,
                 fresh: bool = False) -> tuple[list[dict], str, str]:
    """People-profile search: prefer site:-query (real staff profiles where
    engines honor operators), top up with a quoted variant if the race
    winner's engine ignored the operator, and always float /in/ profiles
    to the top (some engines silently drop operators).
    Dedup by URL."""
    def _has_profiles(rs):
        return any("/in/" in r.get("url", "") for r in rs)

    results, instance, path = run_search(
        f"site:linkedin.com/in {query}", limit,
        parallel=parallel, fresh=fresh, validator=_has_profiles,
    )

    def _in_count(rs):
        return sum(1 for r in rs if "/in/" in r.get("url", ""))

    if _in_count(results) < 3:
        try:
            extra, inst2, path2 = run_search(
                f'"{query}" "linkedin.com/in"', limit,
                parallel=parallel, fresh=fresh, validator=_has_profiles,
            )
        except Exception:
            extra, inst2, path2 = [], None, None
        seen = {r.get("url") for r in results}
        results.extend(r for r in extra if r.get("url") not in seen)
        if inst2:
            instance = f"{instance} + {inst2}"
            path = f"{path} + {path2}"

    results.sort(key=_profile_key)
    return results[:limit], instance, path


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def execute(
    query: str,
    limit: int = 10,
    mode: str = "web",          # "web" | "pdf" | "profiles"
    parallel: int = 10,
    fresh: bool = False,
) -> dict:
    """Run a search and return a JSON-serializable envelope.

    Always succeeds in giving results as long as ANY instance path works:
    JSON API -> HTML (JSON disabled) -> Anubis PoW -> limiter unlock ->
    Portico PoW -> cooldown endgame. Raises RuntimeError only if every
    instance failed."""
    started = time.perf_counter()
    if mode == "profiles":
        effective = f"site:linkedin.com/in {query}"
        results, instance, path = run_profiles(
            query, limit, parallel=parallel, fresh=fresh
        )
    elif mode == "pdf":
        effective = f"filetype:pdf {query}"

        def _validator(rs):
            pdf = sum(1 for r in rs if ".pdf" in r.get("url", "").lower())
            return pdf >= max(1, len(rs) // 2)

        results, instance, path = run_search(
            effective, limit, parallel=parallel, fresh=fresh,
            validator=_validator,
        )
    else:
        effective = query
        results, instance, path = run_search(
            query, limit, parallel=parallel, fresh=fresh
        )

    cleaned = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": (r.get("content") or r.get("snippet") or "").strip(),
            "engine": r.get("engine", ""),
        }
        for r in results
    ]
    return {
        "query": query,
        "mode": mode,
        "effective_query": effective,
        "instance": instance,
        "path": path,
        "elapsed_s": round(time.perf_counter() - started, 2),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "results": cleaned,
    }


def print_results(out: dict) -> None:
    """Human-friendly console rendering of an execute() envelope."""
    print(f"\n[query] {out['query']!r} via {out['instance']} "
          f"[{out['path']}] in {out['elapsed_s']}s\n")
    for i, r in enumerate(out["results"], 1):
        print(f"{i:>2}. {r['title']}\n    {r['url']}\n    {r['snippet'][:120]}\n")


# ===========================================================================
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

    import asyncio

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
    "available_instances", "clear_bad_instances", "set_verbose",
    "execute", "print_results", "main",
]


# ===========================================================================
# SECTION 7/7: CLI
# ===========================================================================

def main() -> int:
    # Windows consoles default to cp1251 and crash on non-latin output
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) == 1:      # no args: print the cheat sheet, not an argparse error
        print(QUICK_START)
        return 0

    ap = argparse.ArgumentParser(
        description="Browserless SearXNG meta-search, single file (JSON optional)"
    )
    ap.add_argument("--input", required=True,
                    help="search query (quote multi-word queries: "
                         "--input \"nasa cosmos\")")
    ap.add_argument("-n", "--limit", type=int, default=10,
                    help="max results (default 10)")
    ap.add_argument("-o", "--output",
                    help="output JSON file ('-' = stdout only; "
                         "default results/<query>.json)")
    ap.add_argument("--pdf", action="store_true",
                    help="only PDF documents (filetype:pdf)")
    ap.add_argument("--profiles", action="store_true",
                    help="people profiles (LinkedIn /in/ pages)")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore on-disk caches (instance list, cooldowns)")
    ap.add_argument("-j", "--parallel", type=int, default=10,
                    help="instances raced in parallel (default 10, 0/1 = serial)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="show progress lines (instance race, Anubis solves, "
                         "failures); default output is ONLY the results JSON")
    args = ap.parse_args()

    if args.pdf and args.profiles:
        ap.error("--pdf and --profiles are mutually exclusive")
    if not args.verbose:
        QUIET.set()            # default: mute progress chatter unless -v
    mode = "profiles" if args.profiles else ("pdf" if args.pdf else "web")

    try:
        out = execute(args.input, limit=args.limit, mode=mode,
                      parallel=args.parallel, fresh=args.fresh)
    except RuntimeError as e:
        print(f"[!] search failed: {e}", file=sys.stderr)
        return 1

    payload = json.dumps(out, indent=2, ensure_ascii=False)
    if args.output == "-":
        print(payload)
    else:
        path = Path(args.output) if args.output else results_path(args.input, mode)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        if args.verbose:
            print_results(out)
            print(f"Saved to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
