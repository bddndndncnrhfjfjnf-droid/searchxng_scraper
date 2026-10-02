# Reference

The long version. [README.md](README.md) has the short version; this file has
the module-by-module detail, the captcha algorithms, what SearXNG's rate limiter
actually checks, and the cache layout.

## How the modules fit together

Read them bottom-up. Each one only imports from the rows above it:

```
cli.py          argparse, exit codes
api.py          search(), search_many(), SearchResult   <- the public API
race.py         the escalation ladder, the parallel race
  ├── anubis.py    Anubis PoW + cookie cache
  ├── limiter.py   browser headers, link_token, Portico
  ├── instances.py where the instance list comes from, which to skip
  └── htmlparse.py HTML page -> the same dicts the JSON API returns
pow.py          the shared SHA-256 solver both captchas sit on
config.py       TLS fingerprint, timeouts, where files go, quiet flag
```

| Module | Lines | What lives there | When to go there |
|---|---|---|---|
| [config.py](searxng_scraper/config.py) | 111 | TLS fingerprint, timeouts, paths, quiet flag, lazy `curl_cffi` import | changing anything global |
| [pow.py](searxng_scraper/pow.py) | 322 | SHA-256 proof-of-work engine, multi-core | captcha speed or difficulty |
| [htmlparse.py](searxng_scraper/htmlparse.py) | 119 | HTML results page → JSON-shaped dicts | an instance changed its markup |
| [limiter.py](searxng_scraper/limiter.py) | 122 | browser headers, link_token ping, Portico | debugging 429/302 |
| [anubis.py](searxng_scraper/anubis.py) | 350 | Anubis PoW solver + cookie cache | debugging the Anubis captcha |
| [instances.py](searxng_scraper/instances.py) | 150 | instance list, cache, "bad" bookkeeping | "why did it skip everything" |
| [race.py](searxng_scraper/race.py) | 501 | the escalation ladder and the parallel race | changing search logic |
| [api.py](searxng_scraper/api.py) | 222 | the public API: `search()`, `SearchResult`, … | writing your own code on top |
| [cli.py](searxng_scraper/cli.py) | 122 | flags, cheat sheet, stdout, exit codes | adding a flag |
| [update.py](searxng_scraper/update.py) | 147 | background "is there a newer release?" check | changing update behaviour |

### The knobs people actually turn

| What to change | Where |
|---|---|
| Browser TLS fingerprint | [config.py:16](searxng_scraper/config.py#L16) (`IMPERSONATE`) |
| User-Agent | [instances.py:34](searxng_scraper/instances.py#L34) (`UA`) |
| How many instances to try | [instances.py:32](searxng_scraper/instances.py#L32) (`MAX_INSTANCES_TO_TRY`) |
| Request timeout | [instances.py:33](searxng_scraper/instances.py#L33) (`TIMEOUT`) |
| How long to remember a "bad" instance | [instances.py:42](searxng_scraper/instances.py#L42) (`BAD_TTLS`) |
| Where the instance list comes from | [instances.py:31](searxng_scraper/instances.py#L31) (`INSTANCES_URL`) |
| Results folder | [api.py:26](searxng_scraper/api.py#L26) (`RESULTS_DIR`) |

### Why the imports are lazy

`sxng` with no arguments and `sxng --help` never touch the network, so importing
the engine first would be pure waste: `curl_cffi` (~120 ms, it dlopens libcurl),
`concurrent.futures.process` (~40 ms, for the PoW worker pool) and `asyncio`
(~150 ms, only `search_async` needs it). `__init__.py` resolves public names on
first access via PEP 562, and `cli.py` imports the engine only after argument
parsing. Package import cost is ~35 ms over a bare interpreter.

### The update check

`update.py` runs a daemon thread that asks
`https://pypi.org/pypi/searchxng_scraper/json` while the search is already in
flight. By the time results are printed the answer has usually arrived;
`notice()` joins the thread with a 0.5 s cap and says nothing if it has not.

Every guard exists because the failure mode is not "wrong answer", it is
"nagging":

| Guard | Why |
|---|---|
| everything on stderr | stdout must stay pure JSON for `| jq` |
| skipped unless stdout **and** stderr are TTYs | a tool that can block on input hangs every script |
| `[-o -]` disables it | the user explicitly asked for machine-readable output |
| 24 h cache in `.cache/update_check.json` | one request a day, not one per search |
| 3 s timeout, every exception swallowed | a failed check must never delay or break a search |
| stdlib `urllib`, not `curl_cffi` | avoids the ~120 ms import and the Android import failure |
| never imported by `api.py` | library users get no network call they did not ask for |
| default answer is no | updating is offered, never performed |

Turn it off with `--no-update-check` or `SXNG_NO_UPDATE_CHECK=1`.

### Why argon2-cffi is an extra

`argon2-cffi` is only needed for the `argon2id` flavour of the Anubis challenge.
Every other path works without it, and the import is already lazy. It stays out
of the required dependencies because its cffi bindings have **no prebuilt wheel
for Android** — as a hard dependency, pip compiled them from source on Termux
(1.8 MB, needs a working toolchain) for a feature most installs never touch.

## Captchas: why this works without a browser

A browser solves captchas with JavaScript. This does the same thing, on CPU:

| What the browser does | What this package does |
|---|---|
| request with Chrome's TLS fingerprint | `curl_cffi impersonate=chrome131` |
| sends Accept / Sec-Fetch / Accept-Language | the same headers, set by hand in `limiter.py` |
| pulls in the random CSS `/client<token>.css` | `ping_link_token()` — finds the link and fetches it |
| stores cookies | `curl_cffi.Session` with its own cookie jar |
| JS worker computes SHA-256 for Anubis | the same SHA-256 across all cores (`pow.py`) |
| JS computes the Portico PoW | same engine, different nonce packing |
| navigates between pages | one HTTP request plus an HTML parse |

### Anubis (Techaro)

The stub page carries the challenge right in the HTML:

```html
<script id="anubis_challenge" type="application/json">{"rules":…,"challenge":…}</script>
```

Find a nonce for which `SHA256(randomData + nonce)` starts with N zero hex
characters, hand it to
`/.within.website/x/cmd/anubis/api/pass-challenge`, and the server verifies the
hash and issues a `techaro.lol-anubis-auth-*` cookie.

| Algorithm | What we do |
|---|---|
| `fast` / `slow` (older) | `SHA256(data + str(nonce))`, difficulty in hex characters, multi-core |
| `sha256` (WASM era) | challenge decoded from hex into bytes, nonce 4 bytes, byte order picked by the challenge's last byte (≥ 0x80 → little-endian), difficulty in **bits**, multi-core |
| `argon2id` (WASM era) | `Argon2id(challenge, salt=nonce_bytes, t=3, m=19 MB)`, single-threaded: it is memory-hard, so processes would only slow it down. Needs `pip install "searchxng_scraper[argon2]"` |
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

### Portico

SearXNG's native captcha: `SHA256(seed + "\0" + counter)` must produce `pow_bits`
leading zero **bits**, where the seed is `payload`, `signature`, `nonce` and the
User-Agent glued together with `\0`. The answer is POSTed to `/portico` as
`captcha_js_proof="<counter>:<hexdigest>"` — same engine as `pow.py`.

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
2. **Pinging `link_token`** — GET the index, parse `href="/clientXXX.css"`, fetch
   that CSS. The server-side note lives for 1 hour.
3. **Saving requests** — the HTML path instead of JSON wherever JSON is disabled:
   a non-HTML format request lands in the API window, which only allows 4/hour.
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

## Where files go

Reports go to `./SearchXNG_report/` in the current working directory, which is
what a CLI user expects and what `git` and friends do. If that is not writable,
the folder falls back to the package directory (so a source checkout keeps its
reports beside the code), then to `%LOCALAPPDATA%\.sxng_search\` on Windows or
`~/.sxng_search/` elsewhere. The chosen path is printed whenever a report is
written. Override it per run with `--results-dir`, or per result with
`r.save("out/report.json")`.

site-packages used to be tried *first*, which put reports in
`.../site-packages/results/` — writable, invisible, and nowhere anyone looks.
That order was the bug the `SearchXNG_report` rename came with.

## When something goes wrong

| Symptom | What to do |
|---|---|
| `[!] search failed: all N tried instances failed` | every instance is cooling down, or there's no network. Wait a minute or two, then retry with `--fresh` |
| `unrecognized arguments: ...` | you forgot the quotes around a multi-word query |
| "Anubis solved but limiter still blocks search" | that instance flagged your IP; wait ~10 minutes and it drops out of the bad-instance cache |
| `dlopen failed: library "libpython3.X.so" not found` | your `curl_cffi` was built against a different Python. Termux: `pkg upgrade python-curl-cffi`; elsewhere: `pip install --force-reinstall curl_cffi` |
| one instance suddenly returns nothing | the HTML path depends on SearXNG's theme skeleton (`article.result`). Check offline with `python tests/test_pow_parser.py` |
| it broke after you edited the code | run both offline test scripts — both must pass |

## Installing from source

```bash
python -m venv .venv
.venv/bin/pip install -e .             # installs the checkout + curl_cffi
.venv/bin/pip install argon2-cffi      # optional: argon2id challenges
```

Python 3.13 or newer. Nothing else needs installing: [searxng_scraper/](searxng_scraper)
is pure Python — copy the folder to any machine and run it. If installing is
impossible, copying still works:

```python
import searxng_scraper as sxng
r = sxng.search("nasa cosmos", limit=5)
```