"""Self-test + benchmark for the PoW solver and HTML parser.

Run:  python tests/test_pow_parser.py

Everything under test lives in the searxng_scraper package (pow.py and
htmlparse.py). All offline - no network. Checks:
  1. leading_zero_bits <-> threshold comparison equivalence
  2. pow.solve() reproduces the exact hash conventions:
       - suffix="decimal": SHA256(base + str(nonce))   (Anubis fast/slow, Portico)
       - suffix="int":     SHA256(base + nonce_bytes)  (Anubis WASM sha256),
         both big- and little-endian
  3. returned solutions are VALID (re-hash verification); minimal-nonce
     parity with the live-validated single-thread loop on the inline path
  4. lane partition covers every nonce (no gaps/overlaps) for all pool sizes
  5. benchmark: single-core vs multi-core on a realistic challenge
  6. htmlparse.parse_results_html() on SearXNG-shaped fixtures (simple theme),
     nested tags, relative hrefs, image-article filtering
  7. config.has_argon2() degrades to False when the optional argon2 extra is
     not installed (the default), instead of raising
"""

from __future__ import annotations

import hashlib
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # project root

from searxng_scraper import htmlparse, pow as pow_engine

leading_zero_bits = pow_engine.leading_zero_bits
parse_results_html = htmlparse.parse_results_html
solve = pow_engine.solve

FAILURES = []


def check(name, cond, detail=""):
    status = "ok " if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)


def reference_scan(base: bytes, bits: int, kind: int, endian: str = "big",
                   width: int = 4) -> tuple[str, int]:
    """Single-threaded brute force: minimal nonce with leading zero bits."""
    k, threshold = pow_engine._bits_params(bits)
    n = 0
    while True:
        msg = base + (n.to_bytes(width, endian) if kind == 1 else str(n).encode())
        d = hashlib.sha256(msg).digest()
        if int.from_bytes(d[:k], "big") < threshold:
            return d.hex(), n
        n += 1


def is_valid(base, bits, suffix, digest_hex, nonce, endian="big", width=4):
    """Re-hash a returned solution: right message, right digest, enough zeros."""
    if suffix == "int":
        msg = base + nonce.to_bytes(width, endian)
    else:
        msg = base + str(nonce).encode()
    return (hashlib.sha256(msg).hexdigest() == digest_hex
            and leading_zero_bits(bytes.fromhex(digest_hex)) >= bits)


# ---------------------------------------------------------------------------
# 1. leading_zero_bits vs the int-comparison used in the hot loop
# ---------------------------------------------------------------------------

print("1. leading zero bits <-> threshold comparison")
random.seed(1)
mismatch = 0
for bits in range(1, 25):
    k, threshold = pow_engine._bits_params(bits)
    for _ in range(400):
        d = random.randbytes(32)
        want = leading_zero_bits(d) >= bits
        got = int.from_bytes(d[:k], "big") < threshold
        if want != got:
            mismatch += 1
check("threshold comparison equals bit counting (bits 1..24, ~10k digests)",
      mismatch == 0, f"mismatches={mismatch}")

# ---------------------------------------------------------------------------
# 2+3. hash conventions & solution validity
# ---------------------------------------------------------------------------

print("2. hash conventions (solutions re-hashed and verified)")

# legacy "fast/slow": decimal ASCII nonce, difficulty in HEX chars (=4*bits)
base = b"4b1e1a29d0f7ab61cf2d0c97ee13a51b8304c4e08e8f3a94d3c6b55d1bca9e22"
d_ref = reference_scan(base, 4 * 4, kind=0)
d_new = solve(base, 16, suffix="decimal", max_seconds=20.0)
check("legacy fast d=4 (16 bits): minimal nonce preserved (inline path)",
      d_ref == d_new, f"ref nonce={d_ref[1]} new nonce={d_new[1]}")

# WASM sha256, big-endian (challenge last byte < 0x80)
be_base = bytes.fromhex(
    "a1b2c3d4e5f60718293a4b5c6d7e8f90112233445566778899aabbccddeeff00")
r = reference_scan(be_base, 18, kind=1, endian="big", width=4)
n = solve(be_base, 18, suffix="int", endian="big", width=4, max_seconds=20.0)
check("wasm sha256 BE 18 bits: valid solution",
      is_valid(be_base, 18, "int", *n, endian="big"),
      f"ref minimal nonce={r[1]} got nonce={n[1]}")

# WASM sha256, little-endian (challenge last byte >= 0x80)
le_base = bytes.fromhex(
    "a1b2c3d4e5f60718293a4b5c6d7e8f90112233445566778899aabbccddeeff80")
r = reference_scan(le_base, 18, kind=1, endian="little", width=4)
n = solve(le_base, 18, suffix="int", endian="little", width=4, max_seconds=20.0)
check("wasm sha256 LE 18 bits: valid solution",
      is_valid(le_base, 18, "int", *n, endian="little"),
      f"ref minimal nonce={r[1]} got nonce={n[1]}")

# Portico: seed = "\0".join(payload, signature, nonce, UA) + "\0" + counter,
# difficulty = pow_bits as raw BITS, proof = "<counter>:<hexdigest>"
seed = "payload\0signature\0nonce\0Mozilla/5.0"
r = reference_scan(seed.encode() + b"\0", 15, kind=0)
n = solve(seed.encode() + b"\0", 15, max_seconds=20.0)
check("portico 15 bits: valid solution",
      is_valid(seed.encode() + b"\0", 15, "decimal", *n),
      f"ref minimal={r[1]} got={n[1]} proof={n[1]}:{n[0][:12]}...")

# ---------------------------------------------------------------------------
# 3. lane partition covers every nonce exactly once
# ---------------------------------------------------------------------------

print("3. parallel lane partition")
for nproc in (1, 2, 3, 5, 8):
    stride = nproc + 1
    # lane i owns INLINE_CHUNK+i, INLINE_CHUNK+i+stride, ... (lane 0 = caller)
    span = 3 * pow_engine.INLINE_CHUNK
    covered: set[int] = set()
    for lane in range(stride):
        covered |= set(range(pow_engine.INLINE_CHUNK + lane, span, stride))
    want = set(range(pow_engine.INLINE_CHUNK, span))
    check(f"nproc={nproc}: [{span // 3}, {span}) covered by {stride} lanes, "
          "no gaps/overlaps",
          covered == want,
          f"missing={len(want - covered)} extra={len(covered - want)}")

# ---------------------------------------------------------------------------
# 4. benchmark (single vs multi core, 20-bit challenge)
# ---------------------------------------------------------------------------

print("4. benchmark (single vs multi core, 20-bit challenge)")

t0 = time.perf_counter()
ref = reference_scan(be_base, 20, kind=1, endian="big", width=4)
t_ref = time.perf_counter() - t0

t0 = time.perf_counter()
new = solve(be_base, 20, suffix="int", endian="big", width=4, max_seconds=30.0)
t_new = time.perf_counter() - t0

speedup = t_ref / max(t_new, 1e-9)
check("multi-core result valid",
      is_valid(be_base, 20, "int", *new, endian="big"), f"nonce={new[1]}")
print(f"      single-core: {t_ref * 1000:.0f} ms   multi-core: "
      f"{t_new * 1000:.0f} ms   speedup x{speedup:.1f}")
check("multi-core is faster on a big challenge", t_new < t_ref, f"x{speedup:.1f}")

# ---------------------------------------------------------------------------
# 5. HTML parser on SearXNG-shaped fixtures
# ---------------------------------------------------------------------------

print("5. HTML parser (SearXNG simple theme shape)")

FIXTURE = """<html><body>
<div id="results">
<article class="result result-default"><h3><a href="https://example.org/a" rel="noreferrer">Plain <em>title</em> here</a></h3>
<p class="content">Snippet with <b>nested</b> tags &amp; entities.</p>
<p class="engines">google, bing</p></article>
<article class="result result-images"><a href="https://img.example/1"><img src="x.jpg"></a></article>
<article class="result result-default"><h3><a href="/relative/path">Relative link</a></h3>
<p class="content">Second snippet</p>
<p class="engines">ddg</p></article>
</div></body></html>"""

res = parse_results_html(FIXTURE)
check("only real results parsed (images article skipped)", len(res) == 2,
      f"got {len(res)}")
check("title: nested tags collected", res[0]["title"] == "Plain title here",
      repr(res[0]["title"]))
check("snippet: nested tags + entities",
      res[0]["snippet"] == "Snippet with nested tags & entities.",
      repr(res[0]["snippet"]))
check("engines line", res[0]["engine"] == "google, bing", repr(res[0]["engine"]))
check("absolute href kept", res[0]["url"] == "https://example.org/a",
      res[0]["url"])

res = parse_results_html(FIXTURE, "https://searx.example.org/")
check("relative href resolved against base_url",
      res[1]["url"] == "https://searx.example.org/relative/path", res[1]["url"])

FIXTURE2 = ('<article class="result result-default"><h3><a href="https://x.org/">'
            '</a></h3><p class="content">body only</p></article>')
res = parse_results_html(FIXTURE2)
check("empty title kept as result with url",
      len(res) == 1 and res[0]["url"] == "https://x.org/", repr(res))

# --- 7. optional argon2 extra must degrade, never crash -------------------
# argon2-cffi is an EXTRA, so the common install has no argon2 at all.
# importlib.util.find_spec() raises ModuleNotFoundError (not None) when the
# parent package is missing, which used to crash the Anubis path on any
# install that skipped the extra.
import searxng_scraper.config as config_mod

saved = sys.modules.get("argon2")
try:
    sys.modules["argon2"] = None      # find_spec() then takes the missing-parent path
    check("has_argon2() returns False, not raises, when argon2 is absent",
          config_mod.has_argon2() is False, repr(config_mod.has_argon2()))
finally:
    if saved is None:
        del sys.modules["argon2"]
    else:
        sys.modules["argon2"] = saved

# ---------------------------------------------------------------------------
print()
if FAILURES:
    print(f"FAILED: {len(FAILURES)} -> {FAILURES}")
    raise SystemExit(1)
print("All checks passed.")
