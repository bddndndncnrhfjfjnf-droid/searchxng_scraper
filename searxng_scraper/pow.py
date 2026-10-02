"""Shared SHA-256 proof-of-work solver for the Anubis and Portico challenges.

Both captchas are "find a nonce so the digest starts with N zero bits". The
work is embarrassingly parallel, so a chunk of nonces goes to a process pool
above a size threshold and is scanned inline below it.
"""

from __future__ import annotations

import atexit
import hashlib
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor

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
