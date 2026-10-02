"""The escalation ladder: how one query is run against one instance.

Every candidate instance walks the same rungs until it returns results:

    a. GET /search?format=json
    b. 200 but HTML (JSON silently disabled) - parse it right away
    c. Anubis interstitial - solve the PoW on CPU, retry with cookies
    d. 429/302 from botdetection - browser-like session + link_token, retry
    e. Portico captcha - solve it, POST the proof, parse the HTML

Several instances race in parallel; the first one with results wins.
"""

from __future__ import annotations

import queue
import threading
import time

from curl_cffi import requests as cffi

from .anubis import (
    get_anubis_cookies,
    has_anubis,
    is_challenge_page,
    refresh_anubis_cookies,
    set_quiet,
)
from .config import IMPERSONATE, qprint
from .htmlparse import parse_results_html
from .instances import (
    MAX_INSTANCES_TO_TRY,
    TIMEOUT,
    UA,
    _get,
    fetch_instance_list,
    filter_bad,
    remember_failures,
)
from .limiter import is_portico_captcha, new_session, ping_link_token, solve_portico

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
