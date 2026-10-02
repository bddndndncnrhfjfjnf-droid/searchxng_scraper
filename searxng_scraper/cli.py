"""Command line entry point: argparse, JSON to stdout, exit code 1 on error."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import QUIET
from .api import QUICK_START, results_path
from .race import execute, print_results

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
