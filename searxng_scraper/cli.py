"""Command line entry point: argparse, JSON to stdout, exit code 1 on error."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import QUIET

QUICK_START = """\
SearXNG meta-search, no browser.

  sxng --input "nasa cosmos"            10 results, shown here and saved
  sxng --input "nasa cosmos" -n 20      20 results instead of 10
  sxng --input "nasa cosmos" -v         add a live log of the instance race
  sxng --input "nato staff" --pdf       PDF documents only
  sxng --input "nato staff" --profiles  people profiles (LinkedIn /in/)
  sxng --input "nasa cosmos" --json     print JSON instead of the report
  sxng --input "nasa cosmos" -o -       JSON to stdout, no file written
  sxng --results-dir ./out              save the report somewhere else
  sxng                                  this cheat sheet

  Reports land in ./SearchXNG_report/ (created on first run).
  Piping or redirecting gives you raw JSON automatically, so
  `sxng --input "x" | jq` keeps working without a flag.

  Library:  from searxng_search import search, search_many, SearchResult
  Checks:   python tests/test_pow_parser.py   (offline, no network)
            python tests/test_rotation.py     (offline, no network)
            python tests/test_cli.py          (offline, no network)

  README.md has the details: how it works, the captchas, the file layout.
"""

# cli.py - flags, stdout, exit codes
# ===========================================================================

def main() -> int:
    # Windows consoles default to cp1251 and crash on non-latin output
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) == 1:      # no args: print the cheat sheet, not an argparse error
        print(QUICK_START)
        return 0

    ap = argparse.ArgumentParser(
        description="Browserless SearXNG meta-search (JSON optional)"
    )
    ap.add_argument("--input", required=True,
                    help="search query (quote multi-word queries: "
                         "--input \"nasa cosmos\")")
    ap.add_argument("-n", "--limit", type=int, default=10,
                    help="max results (default 10)")
    ap.add_argument("-o", "--output",
                    help="output JSON file ('-' = stdout only; "
                         "default SearchXNG_report/<query>.json)")
    ap.add_argument("--json", action="store_true",
                    help="print the JSON to stdout instead of the human "
                         "readable report (what a pipe or redirect gets anyway)")
    ap.add_argument("--results-dir", metavar="DIR",
                    help="where reports are written "
                         "(default ./SearchXNG_report)")
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
    ap.add_argument("--update", action="store_true",
                    help="check PyPI for a newer release right now and offer it")
    ap.add_argument("--no-update-check", action="store_true",
                    help="never check for a newer release (also: "
                         "SXNG_NO_UPDATE_CHECK=1)")
    args = ap.parse_args()

    if args.pdf and args.profiles:
        ap.error("--pdf and --profiles are mutually exclusive")
    if not args.verbose:
        QUIET.set()            # default: mute progress chatter unless -v
    mode = "profiles" if args.profiles else ("pdf" if args.pdf else "web")

    from . import __version__
    from .update import UpdateCheck

    # Only prompt when there is a human to answer. A pipe, a redirect or an
    # explicit `-o -` means this is a script, and a script must never block.
    interactive = (
        not args.no_update_check
        and args.output != "-"
        and sys.stdout.isatty()
        and sys.stderr.isatty()
    )
    checker = (
        UpdateCheck().start(__version__, force=args.update)
        if (interactive or args.update) else None
    )

    # Only now is the engine needed. Importing it costs ~300 ms (curl_cffi,
    # asyncio), and everything above this line works without it.
    from .api import results_path
    from .race import execute, print_results

    try:
        out = execute(args.input, limit=args.limit, mode=mode,
                      parallel=args.parallel, fresh=args.fresh)
    except RuntimeError as e:
        print(f"[!] search failed: {e}", file=sys.stderr)
        return 1

    payload = json.dumps(out, indent=2, ensure_ascii=False)

    # Where the report goes. `--results-dir` wins, then an explicit path,
    # then the default ./SearchXNG_report.
    written = None
    if args.output != "-":
        if args.output:
            written = Path(args.output)
        elif args.results_dir:
            written = Path(args.results_dir) / results_path(args.input, mode).name
        else:
            written = results_path(args.input, mode)
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_text(payload, encoding="utf-8")

    # Humans get the readable report; machines keep getting pure JSON.
    # Deciding on isatty() means `sxng ... | jq` and `sxng ... > file.json`
    # behave exactly as they always did, with no flag to remember.
    to_stdout_as_json = args.output == "-" or args.json or not sys.stdout.isatty()

    if to_stdout_as_json:
        print(payload)
    else:
        print_results(out, saved_to=written)

    if checker is not None:
        checker.notice(__version__)   # after the results, on stderr only
    return 0


if __name__ == "__main__":
    sys.exit(main())
