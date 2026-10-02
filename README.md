# SearXNG meta-search — no browser needed

Searches public SearXNG instances **without a browser, without Selenium, without
headless**. Works even where the admin disabled the JSON API, where an Anubis or
Portico captcha sits in front, or where the rate limiter bites — all solved on CPU.

One dependency: `curl_cffi`. Python 3.13+.

## Install

```bash
pip install searchxng_scraper
```

That's it. Then:

```bash
sxng --input "nasa cosmos"
```

**Termux / Android:** see [Installing on Termux](#installing-on-termux) below.

Optional, only if you hit the rare argon2id captcha:

```bash
pip install "searchxng_scraper[argon2]"
```

**From a source checkout:**

```bash
python -m venv .venv && .venv/bin/pip install -e .
python main.py --input "nasa cosmos"
```

## What it looks like

Run it with no pipes and it prints the hits, then tells you where the report
went:

```console
$ sxng --input "supply chain" -n 3

supply chain  3 results via https://searxng.eshnetwork.space/ [html] in 5.93s
saved to ./SearchXNG_report/supply_chain.json

 1. Supply chain - Wikipedia
    https://en.wikipedia.org/wiki/Supply_chain
    Supply and demand stacked in a conceptual chain A supply chain is a
    complex logistics system that consists of facilities that convert …

 2. Supply Chain Basics: The Ultimate Guide for Beginners
    https://www.supplychaintoday.com/supply-chain-basics-…/
    Supply chain basics are the foundation of efficient operations …

 3. What is supply chain and how does it function? | McKinsey
    https://www.mckinsey.com/featured-insights/mckinsey-explainers/…
```

The same run also writes the full JSON envelope to
`SearchXNG_report/<query>.json`.

**Pipe it and you get JSON instead**, with no flag to remember — the tool checks
whether stdout is a terminal:

```console
$ sxng --input "supply chain" | jq '.results[].url'
https://en.wikipedia.org/wiki/Supply_chain
https://www.supplychaintoday.com/supply-chain-basics-…/
```

Force either side when you want to: `--json` for the report on a terminal, `-o -`
for JSON with nothing written to disk.

| Field in the JSON | Meaning |
|---|---|
| `instance` | the instance that won the race |
| `path` | how results arrived: `json` / `html` / `html+anubis` / `html+portico` / `json+limiter` |
| `elapsed_s` | wall-clock seconds |

## As a library

```python
import searxng_search as sxng          # or: from searxng_scraper import search

r = sxng.search("nasa cosmos", limit=5)
print(len(r), "results via", r.instance)
print(r.titles[:3])

r.save()                               # -> ./SearchXNG_report/nasa_cosmos.json
```

Also available: `search_json()`, `search_many()`, `search_async()`,
`available_instances()`, `clear_bad_instances()`, `set_verbose()`.
`SearchResult` carries `.titles`, `.urls`, `.snippets`, `.path`, `.elapsed_s`,
`.anubis`, `.to_dict()`, `.to_json()`, `.save()`.

## How it works

```
searx.space/data/instances.json        refreshed every 6 hours
        │
        ▼  rank by success rate → speed → TLS grade, minus recently-failed hosts
   race: up to 40 instances at once, first one with results wins
        │   each climbs the same ladder:
        │     1. GET /search?format=json          done
        │     2. 200 but HTML (JSON disabled)     parse the HTML
        │     3. Anubis stub page                 solve the PoW on CPU, retry
        │     4. 429 / 302 from the limiter       browser session + link_token
        │     5. Portico captcha                  solve it, POST, parse HTML
        │
        ▼
   ./SearchXNG_report/<query>.json   + the hits, printed
```

The point of racing: SearXNG's limits live **on a single instance**. Forty
instances are forty independent budgets, so exhausting all of them at once isn't
possible.

Everything is one module per job — `pow.py` (the SHA-256 engine both captchas
sit on), `limiter.py`, `anubis.py`, `instances.py`, `race.py`, `api.py`, `cli.py`
— and each imports only from the layer below it.

## Flags

| Flag | Effect |
|---|---|
| `--input "query"` | the search query (required, quote it) |
| `-n N` | how many results (default 10) |
| `--pdf` | PDF documents only |
| `--profiles` | people profiles (LinkedIn `/in/`) |
| `-o PATH` | your own output path; `-o -` prints to stdout |
| `--fresh` | ignore the instance cache and the bad-instance list |
| `-j N` | instances raced in parallel (default 10; `0`/`1` = serial) |
| `-v` | live progress log of the instance race |
| `--json` | print the JSON envelope to stdout instead of the report |
| `--results-dir DIR` | write reports somewhere other than `./SearchXNG_report` |
| `--update` | check PyPI for a newer release now and offer it |
| `--no-update-check` | never check for a newer release |

On a terminal you get the readable report; in a pipe or redirect you get JSON.
Errors go to stderr, exit code 1.

### Update check

A background thread asks PyPI whether a newer release exists **while your
search is already running**. If one does, you get a `y/N` prompt on stderr
*after* the results are on screen.

It stays out of the way by construction: at most one request a day (cached in
`.cache/update_check.json`), skipped entirely when stdout isn't a terminal (so
`sxng … | jq` can never block on a prompt), never run at all by library code,
3-second timeout, and every failure — offline, blocked, DNS — is silent.
`--no-update-check` or `SXNG_NO_UPDATE_CHECK=1` turns it off for good.

Quote multi-word queries (`--input "nasa cosmos"`), or PowerShell hands the
second word over as a separate argument.

## Where results go

One file per query in **`./SearchXNG_report/`**, created on first run, in the
directory you ran the command from. The name is the query, and an existing file
is never overwritten — a repeat gets `_2`, `_3`.

```bash
sxng --input "supply chain"                  # ./SearchXNG_report/supply_chain.json
sxng --input "supply chain" --results-dir ~/reports
```

Working out of a directory you don't own? The folder moves to the package
directory, then to `~/.sxng_search/`, until it finds somewhere writable. The
path is always printed when a report is written.

Everything there is disposable — delete the folder whenever you like.

## Installing on Termux

Android needs one extra step that desktop platforms do not, so give it its own
section.

**0. Get the right Termux.** Install it from **F-Droid**
(`com.termux`), not the Play Store — the Play Store build is unmaintained,
predates the NDK, and cannot run native extensions at all, so `curl_cffi`
cannot possibly load there.

**1. Python from Termux, not F-Droid's:**

```bash
pkg install python
python -V          # expect 3.13 or newer
```

**2. Fix `curl_cffi` before anything else.** This is the step that bites, and
the error it produces tells you nothing about the cause:

```
ImportError: dlopen failed: library "libpython3.13.so" not found:
needed by .../curl_cffi/_wrapper.abi3.so in namespace (default)
```

`curl_cffi` ships a compiled binary. Termux packages it for whichever Python
was current when it was built; once Termux itself upgrades Python, the package
is left behind pointing at a `libpython` that no longer exists. Fix it with:

```bash
pkg upgrade python-curl-cffi
```

If Termux has no rebuilt package yet, compile it against your interpreter —
one time, needs a toolchain:

```bash
pkg install clang libffi make pkg-config
pip install --force-reinstall curl_cffi
```

Verify before going further — this imports the binary, so it is the real test:

```bash
python -c "from curl_cffi import requests; print('curl_cffi OK')"
```

**3. Install and run:**

```bash
pip install searchxng_scraper
sxng --input "nasa cosmos"
```

If `pip` refuses with an "externally managed environment" error, add
`--break-system-packages` (or install into a venv with
`python -m venv .venv && .venv/bin/pip install searchxng_scraper`).

### Where files land on Android

Reports go to `./SearchXNG_report/` in whatever directory you ran the command
from, which on Termux is normally your home directory. If that is not writable
the folder falls back to `~/.sxng_search/`, and the path is printed either way:

```bash
sxng --input "nasa cosmos" --results-dir ~/reports
```

The background update check uses `urllib` from the standard library, not
`curl_cffi`, so it needs no extra setup and cannot fail the way the import above
does.

### Termux troubleshooting

| Symptom | Fix |
|---|---|
| `dlopen failed: library "libpython3.X.so" not found` | `pkg upgrade python-curl-cffi`, or the `--force-reinstall` route above |
| `curl_cffi` builds but segfaults on import | the package was compiled against another Python; reinstall after `pkg upgrade python` |
| `externally managed environment` | add `--break-system-packages`, or use a venv |
| Play Store Termux, nothing works | uninstall, install the F-Droid build |
| slow first search | the PoW solver uses every core; that is expected, later searches are cached |

## Tests

```bash
python tests/test_pow_parser.py      # PoW engine + HTML parser, offline
python tests/test_rotation.py        # instance rotation, offline
python tests/test_cli.py             # update check + optional extra, offline
python tests/live_audit.py           # live sweep over every instance
```

## More

- [REFERENCE.md](REFERENCE.md) — how each module works, the captcha algorithms,
  what SearXNG's rate limiter checks and why spoofing `X-Forwarded-For` stopped
  working, cache layout, troubleshooting.
- **Русский**: [README.ru.md](README.ru.md)
- MIT licensed.