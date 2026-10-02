"""Audit sweep: try the full escalation ladder on every usable instance,
record per-instance outcomes and timings, and summarise weak spots.

Run:  .venv-lite/Scripts/python tests/live_audit.py [query]
Writes: results/audit_report.json + prints a human-readable summary.

NOTES
  - runs serially with pauses: the goal is truthful per-instance verdicts,
    not speed; parallel hammering would trip ip_limit everywhere and paint
    half the network red for an hour afterwards.
  - per-instance failure classification mirrors main.BAD_TTLS so
    the report shows which failures are transient vs structural.
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # project root

import main as sx  # single-file build under test
from main import (  # noqa: E402
    TIMEOUT, UA, fetch_instance_list, filter_bad, _get,
    is_challenge_page, has_anubis, get_anubis_cookies,
    is_portico_captcha, new_session, ping_link_token, solve_portico,
    parse_results_html,
)

QUERY = sys.argv[1] if len(sys.argv) > 1 else "audit probe test"
PAUSE = 2.0


def classify(base: str) -> dict:
    """Walk the ladder manually, recording every verdict."""
    rec = {"url": base, "steps": []}

    # step 1: plain JSON
    try:
        r = _get(base + "search", params={"q": QUERY, "format": "json"},
                 headers={"User-Agent": UA})
    except Exception as e:
        rec["steps"].append(f"json:conn-fail {type(e).__name__}")
        rec["verdict"] = "network"
        return rec
    if r.status_code == 200:
        try:
            res = r.json().get("results", [])
            rec["steps"].append(f"json:200 ({len(res)} results)")
            if res:
                rec["verdict"] = "json"
                return rec
            rec["steps"].append("json:empty")
        except Exception:
            if is_challenge_page(r.text):
                rec["steps"].append("json:200+anubis-page")
            else:
                res = parse_results_html(r.text, base)
                rec["steps"].append(f"json:200-nonjson (html-res {len(res)})")
                if res:
                    rec["verdict"] = "html"
                    return rec
    else:
        rec["steps"].append(f"json:{r.status_code}")

    # step 2: Anubis?
    anubis = is_challenge_page(r.text) if r.status_code == 200 else has_anubis(base, UA)
    if anubis:
        rec["steps"].append("anubis:detected")
        try:
            cookies = get_anubis_cookies(base, UA)
        except Exception as e:
            cookies = None
            rec["steps"].append(f"anubis:solve-error {type(e).__name__}")
        if cookies:
            rec["steps"].append("anubis:solved")
            s = sx.cffi.Session(impersonate=sx.IMPERSONATE)
            for k, v in cookies.items():
                s.cookies.set(k, v)
            sx.ping_link_token(s, base, timeout=TIMEOUT)
            rh = s.get(base + "search", params={"q": QUERY}, timeout=TIMEOUT)
            if rh.status_code == 200 and "<article" in rh.text:
                res = parse_results_html(rh.text, base)
                rec["steps"].append(f"anubis+html:200 ({len(res)} results)")
                rec["verdict"] = "html+anubis" if res else "anubis-limited"
                return rec
            rec["steps"].append(f"anubis+html:{rh.status_code} (limiter?)")
            rec["verdict"] = "anubis-limited"
            return rec
        rec["verdict"] = "anubis-unsolved"
        return rec

    # step 3: limiter session
    s = new_session(UA)
    ping_link_token(s, base, timeout=TIMEOUT)
    rj = s.get(base + "search", params={"q": QUERY, "format": "json"}, timeout=TIMEOUT)
    if rj.status_code == 200:
        try:
            res = rj.json().get("results", [])
            if res:
                rec["steps"].append(f"limiter-json:200 ({len(res)})")
                rec["verdict"] = "json+limiter"
                return rec
        except Exception:
            pass
    rec["steps"].append(f"limiter-json:{rj.status_code}")

    rh = s.get(base + "search", params={"q": QUERY}, timeout=TIMEOUT)
    html = rh.text
    if rh.status_code == 200 and is_portico_captcha(html):
        rec["steps"].append("portico:captcha")
        solved = solve_portico(s, base, html, UA, timeout=TIMEOUT)
        if solved and "captcha-screen" not in solved:
            res = parse_results_html(solved, base)
            rec["steps"].append(f"portico:solved ({len(res)} results)")
            rec["verdict"] = "html+portico" if res else "portico-no-results"
            return rec
        rec["steps"].append("portico:failed")
        rec["verdict"] = "portico-failed"
        return rec
    if rh.status_code == 200:
        res = parse_results_html(html, base)
        if res:
            rec["steps"].append(f"html:200 ({len(res)} results)")
            rec["verdict"] = "html"
            return rec
        rec["steps"].append("html:200-no-results")
        rec["verdict"] = "empty"
        return rec
    rec["steps"].append(f"html:{rh.status_code}")
    rec["verdict"] = f"http-{rh.status_code}"
    return rec


def main():
    t0 = time.perf_counter()
    candidates = fetch_instance_list()
    usable, skipped = filter_bad(candidates)
    print(f"[i] {len(usable)} usable instances "
          f"({skipped} skipped from bad-cache), serial sweep with {PAUSE}s pauses\n")

    results = []
    for i, cand in enumerate(usable[:40], 1):
        base = cand["url"]
        rec = classify(base)
        rec["median_ms"] = cand.get("median")
        results.append(rec)
        print(f"{i:>2}. {base:42} {rec['verdict']:18} | {'; '.join(rec['steps'])}",
              flush=True)
        time.sleep(PAUSE)

    verdicts = Counter(r["verdict"] for r in results)
    print("\n=== SUMMARY ===")
    for v, n in verdicts.most_common():
        print(f"  {n:>2} x {v}")
    print(f"\ntotal: {time.perf_counter() - t0:.0f}s")

    out_path = Path(__file__).resolve().parent.parent / "results" / "audit_report.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"query": QUERY, "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "results": results}, f, indent=1, ensure_ascii=False)
    print("saved results/audit_report.json")


if __name__ == "__main__":
    main()
