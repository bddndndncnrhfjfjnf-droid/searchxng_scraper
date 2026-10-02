# SearXNG meta-search — no browser, one file

Search public SearXNG instances **without a browser, without Selenium, without
headless**. It keeps working where the admin disabled the JSON API, where an
Anubis or Portico captcha sits in front, or where SearXNG's rate limiter
bites — all of it is solved on CPU.

The whole tool is one file, [main.py](main.py). The only dependency is
`curl_cffi`, plus `argon2-cffi` if you happen to hit the rare argon2id
challenge.

```bash
pip install searchxng_scraper
sxng --input "nasa cosmos"
```

Python 3.13 or newer. No browser, no display, no headless anything.

**Language:** **English** · [Русский](README.ru.md)

---

## Contents

- [1. Run it](#1-run-it)
    - [Every command](#every-command)
    - [Two rules that used to break things](#two-rules-that-used-to-break-things)
- [2. What each file is for](#2-what-each-file-is-for)
- [3. How it works](#3-how-it-works)
    - [How main.py is laid out](#how-mainpy-is-laid-out)
- [4. Captchas: why this works without a browser](#4-captchas-why-this-works-without-a-browser)
    - [Anubis (Techaro) — section 4](#anubis-techaro--section-4)
    - [Portico — section 3](#portico--section-3)
- [5. Where results go](#5-where-results-go)
- [6. Use it as a library](#6-use-it-as-a-library)
    - [Handing the library to someone else](#handing-the-library-to-someone-else)
    - [Where an installed library writes files](#where-an-installed-library-writes-files)
    - [Quick start](#quick-start)
    - [What the API offers](#what-the-api-offers)
    - [Slightly fancier examples](#slightly-fancier-examples)
- [7. Every flag and the JSON](#7-every-flag-and-the-json)
- [8. When something goes wrong](#8-when-something-goes-wrong)
- [Reference](#reference)
    - [SearXNG's rate limiter: what it checks](#searxngs-rate-limiter-what-it-checks)
        - [Why spoofing X-Forwarded-For stopped working](#why-spoofing-x-forwarded-for-stopped-working)
        - [What actually works, and is implemented here](#what-actually-works-and-is-implemented-here)
    - [What lives in the caches](#what-lives-in-the-caches)
    - [Installing from scratch](#installing-from-scratch)
    - [Licence](#licence)

---

# 1. Run it

If you installed it from PyPI, the command is already on your PATH:

```bash
pip install searchxng_scraper
sxng --input "nasa cosmos"
```

That takes one to four seconds and writes `results/nasa_cosmos.json`.

If you cloned this repository instead, set the environment up once — the `-e`
flag installs the checkout in editable mode and drops `sxng` on PATH, so no
paths ever need typing again:

```bash
python -m venv .venv
.venv/Scripts/pip install -e .        # Windows
.venv/bin/pip install -e .            # macOS / Linux
```

Then activate it per terminal:

```powershell
.venv\Scripts\Activate.ps1     # (.venv) appears at the prompt
sxng --input "nasa cosmos"
deactivate
```

`python main.py --input "..."` also works from the checkout, and is what to use
while editing the engine — the `-e` install points back at your files.

## Every command

With the environment active, the command is the same everywhere:

| Command | What it does |
|---|---|
| `sxng --input "what to search"` | normal search, 10 results |
| `sxng --input "what to search" -n 20` | 20 results instead of 10 |
| `sxng --input "what to search" -v` | **verbose log**: every instance tried, where a captcha got solved, who won the race |
| `sxng --input "nato plan 2022" --pdf` | PDF documents only |
| `sxng --input "nato staff" --profiles` | people profiles (LinkedIn `/in/`) |
| `sxng` | the cheat sheet |
| `python tests/test_pow_parser.py` | offline engine checks (~1 minute, no network) |
| `python tests/test_rotation.py` | offline rotation checks |
| `python tests/live_audit.py` | live audit of every instance → `results/audit_report.json` |
| `python tests/live_anubis.py "query"` | live demo of instances behind an Anubis captcha |

## Two rules that used to break things

1. **Quote multi-word queries.** Otherwise PowerShell hands the second word over
   as a separate argument: `--input nasa cosmos` → `unrecognized arguments: cosmos`.
2. **`&` is PowerShell's background operator, not part of a path.** A command
   like `...python.exe c:/Users/PC/Desk& c:/...` gets cut in half, which is where
   `unrecognized arguments: scrape` came from.

`pip install` gets you a real `.exe` shim, so neither problem exists once the
package is installed. Inside an activated virtualenv they don't either.

---

# 2. What each file is for

| File / folder | Needed to run it? | What it does | When to open it |
|---|---|---|---|
| **[main.py](main.py)** | **yes, this is the program** | all of the search: instance list, race, captchas, limiter, CLI | when you need to change or understand something |
| **[pyproject.toml](pyproject.toml)** | no | dependencies and package metadata | when adding a dependency |
| **[tests/](tests)** | no | checks: `test_*` run offline, `live_*` need the network | when you want proof you broke nothing |
| **[README.md](README.md)** | — | this file | when you forget a command |
| `results/` | — | one file per query | when reading results |
| `.cache/` | — | instance list and Anubis cookies | never; safe to delete |
| `.venv/` | for source checkouts | the Python interpreter with dependencies | never |
| `.vscode/` | — | editor settings, local only | never |
| `anubis_source/` | no | captured Anubis challenge JavaScript, git-ignored | when working on the captcha code |

The full layout of a source checkout:

```
searchxng_scraper/
├── main.py          ← the tool itself, the only entry point
├── searxng_search.py ← the name you import once it is pip-installed
├── README.md        ← this file
├── README.ru.md     ← the Russian version
├── pyproject.toml   ← dependencies and metadata
├── .vscode/         ← VS Code settings (git-ignored)
├── tests/           ← checks: test_* = offline, live_* = network
│   ├── test_pow_parser.py    offline: PoW engine, lane split, HTML parser
│   ├── test_rotation.py      offline: instance rotation, 429 retries
│   ├── live_anubis.py        live: only instances behind an Anubis captcha
│   ├── live_profiles.py      live: --profiles mode
│   └── live_audit.py         live: run every instance
├── anubis_source/   ← Anubis reference material (git-ignored, not used at runtime)
├── results/         ← results: nasa_cosmos.json is the query "nasa cosmos"
├── .cache/          ← caches, safe to delete
└── .venv/           ← the working environment
```

---

# 3. How it works

```
searx.space/data/instances.json (refreshed every 6 hours)
        │
        ▼
  filter: search success rate → speed → TLS grade, minus the "bad" list from cache
        │
        ▼
  race: up to 40 instances at once (the -j window), first one with results wins
        │   each walks an escalation ladder until it returns results:
        │
   a) GET /search?format=json                  JSON available → win
   b) 200 but it's HTML (JSON disabled)       → parse the HTML on the fly
   c) Anubis stub page                        → solve the PoW on CPU → get the
                                                  cookie → search again
   d) 429 / 302 from the limiter              → browser-like session + link_token
                                                  → retry
   e) Portico captcha                         → solve the second PoW → POST → HTML
        │
        ▼
  winner → results/<query>.json
  everything hit 429? → wait 21 seconds and try the instances that cooled down
```

The central idea: SearXNG's limits live **on a single instance**. Forty
instances are forty independent budgets, so exhausting all of them at once is
not possible.

## How main.py is laid out

One file, seven sections, findable by the `SECTION n/7` headers:

| Section | Lines | What's inside | When to go there |
|---|---|---|---|
| 1 | 127–432 | SHA-256 proof-of-work engine, multi-core | changing captcha speed or difficulty |
| 2 | 433–540 | stdlib parser for SearXNG HTML result pages | an instance changed its markup |
| 3 | 541–647 | limiter: browser headers, link_token, Portico | debugging 429/302 |
| 4 | 648–975 | Anubis solver + cookie cache | debugging the Anubis captcha |
| 5 | 976–1578 | instance list, the race, `--pdf` / `--profiles` | changing search logic |
| 6 | 1579–1795 | **public API**: `search()`, `SearchResult`, `search_many()`, `search_async()` | writing your own code on top |
| 7 | 1798–end | CLI, flags, result filename | changing flags or the output folder |

The knobs people actually turn:

| What to change | Where |
|---|---|
| User-Agent | [main.py:995](main.py#L995) (`UA`) |
| Browser TLS fingerprint | [main.py:72](main.py#L72) (`IMPERSONATE`) |
| How many instances to try | [main.py:993](main.py#L993) (`MAX_INSTANCES_TO_TRY`) |
| Request timeout | [main.py:994](main.py#L994) (`TIMEOUT`) |
| How long to remember a "bad" instance | [main.py:1003](main.py#L1003) (`BAD_TTLS`) |
| Results folder | [main.py:1582](main.py#L1582) (`RESULTS_DIR`) |

---

# 4. Captchas: why this works without a browser

A browser solves captchas with JavaScript. This does the same thing, on CPU:

| What the browser does | What main.py does |
|---|---|
| request with Chrome's TLS fingerprint | `curl_cffi impersonate=chrome131` |
| sends Accept / Sec-Fetch / Accept-Language | the same headers, set by hand (section 3) |
| pulls in the random CSS `/client<token>.css` | `ping_link_token()` — finds the link and fetches it |
| stores cookies | `curl_cffi.Session` with its own cookie jar |
| JS worker computes SHA-256 for Anubis | the same SHA-256 across all cores (sections 1 and 4) |
| JS computes the Portico PoW | same engine, different nonce packing |
| navigates between pages | one HTTP request plus an HTML parse |

## Anubis (Techaro) — section 4

The stub page carries the challenge right in the HTML:

```html
<script id="anubis_challenge" type="application/json">{"rules":…,"challenge":…}</script>
```

The job is to find a nonce for which `SHA256(randomData + nonce)` starts with N
zero hex characters, then hand it to
`/.within.website/x/cmd/anubis/api/pass-challenge` — the server checks the hash
and issues a `techaro.lol-anubis-auth-*` cookie.

| Algorithm | What we do |
|---|---|
| `fast` / `slow` (older) | `SHA256(data + str(nonce))`, difficulty in hex characters, multi-core |
| `sha256` (WASM era) | the challenge is decoded from hex into bytes, the nonce is 4 bytes, byte order picked by the challenge's last byte (≥ 0x80 → little-endian), difficulty in **bits**, multi-core |
| `argon2id` (WASM era) | `Argon2id(challenge, salt=nonce_bytes, t=3, m=19 MB)`, single-threaded: it is memory-hard, so processes would only slow it down. Needs `argon2-cffi` |
| `hashx` | not supported — it's a separate VM-program generator; no SearXNG instance uses it, and the code fails with a clear error |

Things that cost real debugging time:

* the session **must** carry cookies: Anubis first sets
  `*-anubis-cookie-verification-*` and answers 500 "cookies disabled" if you
  don't send it back;
* the auth cookie name differs between versions: `techaro.lol-anubis-auth-<suffix>`
  (≤ 1.27) versus plain `techaro.lol-anubis` (devel) — accept both, or some
  instances silently redirect good responses away;
* cookies are cached until the JWT expires (usually ~1 hour); if the server
  rejects one, it is dropped and the captcha is solved again within the same run.

Anubis' own sources were captured locally to verify the hashing convention above.
They are git-ignored and not shipped: nothing in `anubis_source/` runs.

## Portico — section 3

SearXNG's native captcha: `SHA256(seed + "\0" + counter)` must produce `pow_bits`
leading zero **bits**, where the seed is `payload`, `signature`, `nonce` and the
User-Agent glued together with `\0`. The finished answer is POSTed to `/portico`
as `captcha_js_proof="<counter>:<hexdigest>"` — same engine as section 1.

---

# 5. Where results go

Every query gets **its own readable file in `results/`**:

| Query | File |
|---|---|
| `nasa` | `results/nasa.json` |
| `nasa cosmos` | `results/nasa_cosmos.json` |
| `nasa cosmos` (again) | `results/nasa_cosmos_2.json` |
| `nato staff` with `--pdf` | `results/nato_staff_pdf.json` |

* the name is the query: spaces and special characters become `_`, case is
  lowercased, Cyrillic is kept, long queries are cut to 60 characters;
* an existing file is **never overwritten** — a repeat gets `_2`, `_3`;
* `--pdf` / `--profiles` add the mode to the name;
* `-o path` sets your own path, `-o -` prints to stdout and writes nothing.

`results/` is disposable: delete the whole folder whenever you like.

---

# 6. Use it as a library

[main.py](main.py) is a real library, not just a script.

## Handing the library to someone else

From PyPI, into any Python environment:

```bash
pip install searchxng_scraper
```

From the Git repository:

```bash
pip install git+https://github.com/bddndndncnrhfjfjnf-droid/searchxng_scraper.git
```

From a local checkout:

```bash
pip install .
```

Either way the other person gets a properly named module and a command on PATH:

```python
from searxng_search import search, search_many, search_async, SearchResult
```

```bash
sxng --input "nasa cosmos"
```

Dependencies come along automatically (`curl_cffi` + `argon2-cffi`); all that's
needed is Python 3.13+.

If installing is impossible — someone else's machine, no pip, a read-only disk —
copying still works, because there is only one file:

```python
# 1. copy main.py to your project as sxng.py and import it:
import sxng

# 2. or point at the folder holding the engine (the whole API works here too):
import sys; sys.path.append(r"C:\Users\PC\Desktop\searchxng_scraper")
import main as sxng
```

## Where an installed library writes files

`results/` and `.cache/` are created next to the engine when that location is
writable. Installed into a **system** `site-packages` it may not be (admin rights
required), so everything moves to `%LOCALAPPDATA%\.sxng_search\` instead. You
can always set your own path explicitly: `r.save("out/report.json")`.

## Quick start

```python
import searxng_search as sxng

r = sxng.search("nasa cosmos", limit=5)      # -> SearchResult

print(len(r), "results via", r.instance)     # 5 results via https://...
print(r.urls[:3])                            # the links
print(r.titles[0])                           # the first title
print(r.elapsed_s, "s,", r.path, "anubis:", r.anubis)

for hit in r:                                 # iterate results like a list
    print(hit["title"], "->", hit["url"])

r.save()                        # -> results/nasa_cosmos.json
r.save("out/x.json")            # -> your own path (folders are created)
print(r.to_json())              # JSON string, ready for an API response
```

## What the API offers

| Call | What it does |
|---|---|
| `search(query, limit=10, mode="web", parallel=10, fresh=False)` | the main call, returns a `SearchResult` |
| `search_json(query, **kwargs)` | the same, as a JSON string |
| `search_many(queries, limit=10, mode="web", parallel=10, gap=2.0, on_result=None)` | runs a list of queries one after another; `on_result` is a progress callback and a failing query is skipped rather than killing the list |
| `await search_async(query, **kwargs)` | asyncio wrapper: several searches at once via `asyncio.gather` |
| `available_instances(fresh=False)` | the usable SearXNG instances |
| `clear_bad_instances()` | drop every "bad instance" mark, returns how many were cleared |
| `set_verbose(True/False)` | toggle the diagnostic log (silence by default) |

`SearchResult` carries the fields `query`, `mode`, `effective_query`, `instance`,
`path`, `elapsed_s`, `fetched_at`, `results`, plus the properties `titles`,
`urls`, `snippets`, `anubis`, `portico`, `used_html`, and the methods `len()`,
iteration, indexing, `to_dict()`, `to_json()`, `save()`.

`mode` is `"web"`, `"pdf"` (documents only) or `"profiles"` (people).

## Slightly fancier examples

Several queries at once:

```python
import asyncio, searxng_search as sxng

queries = ["rust tokio", "go generics", "zig build"]
results = asyncio.run(asyncio.gather(*[sxng.search_async(q, limit=3)
                                       for q in queries]))
for r in results:
    print(r.query, len(r))
```

A batch with progress and saving:

```python
def show(r):
    print(f"{r.query}: {len(r)} hits via {r.instance}")

for r in sxng.search_many(["climate report", "nato members"], on_result=show):
    r.save()          # each one gets its own file in results/
```

If a generic `main.py` in your project would collide, rename the file when you
copy it (`sxng.py`) — no module name is hardcoded anywhere inside.

---

# 7. Every flag and the JSON

| Flag | What it does |
|---|---|
| `--input "query"` | the search query (required, quote it) |
| `-n N` | how many results to return (default 10) |
| `--pdf` | documents only: injects the `filetype:pdf` operator |
| `--profiles` | people profiles: `site:linkedin.com/in`, with enrichment |
| `-o PATH` | your own path instead of `results/<query>.json`; `-o -` prints to stdout |
| `--fresh` | ignore the instance cache and the bad-instance list |
| `-j N` | how many instances race at once (default 10; `0`/`1` means serial) |
| `-v` | verbose log of the race, captchas and failures |

By default the program is **silent**: stdout carries only JSON, so it pipes
straight into `jq` or another script. Errors go to stderr with exit code 1.

```json
{
  "query": "nasa",
  "mode": "web",
  "effective_query": "nasa",
  "instance": "https://searx.dresden.network/",
  "path": "html",
  "elapsed_s": 1.74,
  "fetched_at": "2026-10-02T16:41:05",
  "results": [
    {"title": "NASA", "url": "https://www.nasa.gov/",
     "snippet": "NASA.gov brings you the latest news ...", "engine": "google, bing"}
  ]
}
```

| Field | Meaning |
|---|---|
| `instance` | the instance that won the race |
| `path` | how the results were obtained: `json` / `html` (JSON disabled) / `html+anubis` / `html+portico` / `json+limiter` |
| `effective_query` | the query with the mode operator substituted |
| `engine` | which upstream engines answered |

Straight from the engine:

```python
import main as sx
out = sx.execute("nasa", limit=10, mode="web", parallel=10, fresh=False)
# mode: "web" | "pdf" | "profiles"; raises RuntimeError if no instance worked
```

---

# 8. When something goes wrong

| Symptom | What to do |
|---|---|
| `[!] search failed: all N tried instances failed` | every instance is cooling down, or there's no network. Wait a minute or two, then retry with `--fresh` |
| `unrecognized arguments: ...` | you forgot the quotes around a multi-word query |
| "Anubis solved but limiter still blocks search" | that instance flagged your IP; wait ~10 minutes and it drops out of the bad-instance cache |
| Anubis cookies expired | they re-solve themselves: the server returned the stub, old cookies are dropped, the captcha is computed again |
| one instance suddenly returns nothing | the HTML path depends on SearXNG's theme skeleton (`article.result`). Check offline with `python tests/test_pow_parser.py` |
| it broke after you edited the code | run the two offline test scripts below — both must pass |

The check scripts:

```bash
python tests/test_pow_parser.py      # PoW engine, lane split, HTML parser, no network
python tests/test_rotation.py        # instance rotation and 429 retries, no network
python tests/live_audit.py           # live run over every instance
python tests/live_anubis.py "query"  # live demo of captcha-gated instances
```

---

# Reference

## SearXNG's rate limiter: what it checks

Beyond captchas, SearXNG has a `searx.botdetection` plugin
(docs: `docs.searxng.org/src/searx.botdetection.html`).

| Method | What it checks |
|---|---|
| `http_accept` / `_encoding` / `_language` | browser-like Accept / Accept-Encoding / Accept-Language |
| `http_user_agent` | UA is not on a blocklist (curl, python-requests, wget, HeadlessChrome…) |
| `http_sec_fetch` | Sec-Fetch-Dest / -Mode / -Site like a real browser |
| `link_token` | the client should have fetched the randomized `/client<token>.css` from the page beforehand (a browser does this on its own; a bot without rendering does not) |
| `ip_limit` | sliding counter windows per IP network (Valkey) |

The limits are **constants in the source**, not something an instance config can
raise:

| Window | Normal client | "Suspicious" |
|---|---|---|
| burst 20 s | 15 requests | **2** |
| long 10 min | 150 | 10 |
| API (`format=json`) 1 hour | **4** | — |
| suspicious-IP 30 days | 3 strikes → redirect to the front page | |

A request becomes "suspicious" from **either** a failed header check **or** a
missing `link_token` ping.

### Why spoofing X-Forwarded-For stopped working

Historically (searxng issue #1237) the limiter took the client IP from XFF, so
"every request with a new IP" walked straight past the limits. Today XFF is only
honoured when the connecting address is listed in the instance's
`botdetection.trusted_proxies` — public instances do not list other people's
subnets, so a fake XFF is silently ignored. There is no "bypass the limiter"
button.

### What actually works, and is implemented here

1. **Looking like a browser** — the full Accept/Sec-Fetch header set plus
   `curl_cffi impersonate=chrome131`: this moves us from 2 per 20 s "suspicious"
   to the normal 15 per 20 s.
2. **Pinging `link_token`** — GET the index, parse `href="/clientXXX.css"`,
   fetch that CSS. The server-side note lives for 1 hour.
3. **Saving requests** — the HTML path instead of JSON wherever JSON is
   disabled: a non-HTML format request lands in the API window, which only
   allows 4 per hour.
4. **Rotating instances** — the main structural "workaround".
5. **Patient retries** — a 21 s pause (just over the 20 s burst window) and a
   retry.

The pass-list (`[botdetection.ip_lists] pass_ip`) is the only legitimate way to
raise limits, and it lives in the instance admin's config, not the client's.

A practical note: don't hammer one instance in a row (the burst budget is shared
across every path). Prefer `-j 3` over 10, and after a series of tests wait a
minute — the windows clear themselves, and the `.cache/instances.json` cache
skips the "bad" ones meanwhile.

## What lives in the caches

| File | What it stores |
|---|---|
| `.cache/instances.json` | the instance list (6 h) plus "bad" instances with a TTL per failure class (timeout/429 → 10 min, JSON disabled → 6 h, empty search → 24 h) |
| `.cache/anubis_cookies.json` | Anubis cookies per host, until the JWT expires |

## Installing from scratch

```bash
python -m venv .venv
.venv/bin/pip install -e .             # installs the checkout + curl_cffi
.venv/bin/pip install argon2-cffi      # optional: argon2id challenges
```

Python 3.13 or newer. Nothing else needs installing: [main.py](main.py) is
self-contained — copy it to any machine and run it.

## Licence

MIT. See [LICENSE](LICENSE).