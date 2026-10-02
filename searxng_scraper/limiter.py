"""SearXNG botdetection helpers: browser-like session, link_token, Portico.

SearXNG's searx.botdetection plugin marks a request "suspicious" when the
headers look wrong or when the randomized /client<token>.css was never
fetched. Both are cheap to imitate, so we do.
"""

from __future__ import annotations

import re
import time

from curl_cffi import requests as cffi

from .config import IMPERSONATE
from .pow import solve

# limiter.py - botdetection / link_token / Portico helpers
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
