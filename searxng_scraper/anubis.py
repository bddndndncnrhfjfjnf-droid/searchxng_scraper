"""Anubis (Techaro) challenge solver and cookie cache.

Anubis answers every request with a stub page carrying a JSON challenge. The
page expects the browser to brute-force a SHA-256 nonce on all cores and post
it back; then a JWT cookie lets us through for about an hour.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from urllib.parse import urlparse

from .config import IMPERSONATE, cffi, data_dir, has_argon2, qprint
from .pow import leading_zero_bits, solve

# anubis.py - Anubis (Techaro) PoW challenge solver
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

Performance: the PoW itself is delegated to solve() in pow.py, which hashes
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
    solve() in pow.py."""
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
        if not has_argon2():
            raise RuntimeError("argon2id challenge requires: pip install argon2-cffi")
        from argon2.low_level import Type as _ArgonType
        from argon2.low_level import hash_secret_raw as _argon2_hash_raw

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
        resp = cffi().Session(impersonate=IMPERSONATE).get(
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
    s = cffi().Session(impersonate=IMPERSONATE)

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
