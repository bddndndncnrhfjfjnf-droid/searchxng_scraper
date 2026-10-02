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

**Termux / Android:**

```bash
pkg upgrade python-curl-cffi     # Termux ships a build that must match your Python
pip install searchxng_scraper
sxng --input "nasa cosmos"
```

If `sxng` dies with `dlopen failed: library "libpython3.X.so" not found`, your
Termux `curl_cffi` was compiled against a different Python than the one
installed. `pkg upgrade python-curl-cffi` fixes it; `pip install
--force-reinstall curl_cffi` is the fallback.

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

```console
$ sxng --input "searxng python api" -n 2
{
  "query": "searxng python api",
  "mode": "web",
  "effective_query": "searxng python api",
  "instance": "https://searxng.deggo.fyi/",
  "path": "html+anubis (10 from HTML)",
  "elapsed_s": 1.92,
  "fetched_at": "2026-10-02T22:12:36",
  "results": [
    {
      "title": "GitHub - searxng/searxng: SearXNG is a free internet metasearch …",
      "url": "https://github.com/searxng/searxng",
      "snippet": "SearXNG is a free internet metasearch engine …",
      "engine": "google, bing"
    }
  ]
}
```

Written to `results/searxng_python_api.json`. With `-v` you also get a live log
of which instances were tried and where a captcha got solved.

| Field | Meaning |
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

r.save()                               # -> results/nasa_cosmos.json
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
   results/<query>.json
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
| `-v` | live progress log; without it stdout is *only* JSON |

By default the tool is silent, so it pipes straight into `jq`. Errors go to
stderr, exit code 1.

Quote multi-word queries (`--input "nasa cosmos"`), or PowerShell hands the
second word over as a separate argument.

## Where results go

One file per query in `results/`, named after the query, never overwritten — a
repeat gets `_2`, `_3`. Safe to delete the folder at any time.

## Tests

```bash
python tests/test_pow_parser.py      # PoW engine + HTML parser, offline
python tests/test_rotation.py        # instance rotation, offline
python tests/live_audit.py           # live sweep over every instance
```

## More

- [REFERENCE.md](REFERENCE.md) — how each module works, the captcha algorithms,
  what SearXNG's rate limiter checks and why spoofing `X-Forwarded-For` stopped
  working, cache layout, troubleshooting.
- **Русский**: [README.ru.md](README.ru.md)
- MIT licensed.