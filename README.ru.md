# Мета-поиск SearXNG — без браузера

Поиск по публичным инстансам SearXNG **без браузера, без Selenium, без
headless**. Работает даже там, где админ отключил JSON API, где стоит капча
Anubis или Portico, или где срабатывает лимитер SearXNG — всё решается на CPU.

Движок — это пакет [searxng_scraper/](searxng_scraper): по одному модулю на
задачу, от солвера SHA-256-капчи до CLI. Единственная зависимость —
`curl_cffi`, плюс `argon2-cffi`, если попадётся редкая argon2id-капча.

```bash
pip install searchxng_scraper
sxng --input "nasa cosmos"
```

Python 3.13 или новее. Ни браузера, ни дисплея, ни headless — ничего этого.

**Язык:** [English](README.md) · **Русский**

---

## Содержание

- [1. Как запустить](#1-как-запустить)
    - [Как модули связаны между собой](#как-модули-связаны-между-собой)
    - [Все команды](#все-команды)
    - [Два правила, из-за которых раньше сыпались ошибки](#два-правила-из-за-которых-раньше-сыпались-ошибки)
- [2. Что где лежит и что тебе реально нужно](#2-что-где-лежит-и-что-тебе-реально-нужно)
- [3. Как это работает](#3-как-это-работает)
    - [Как устроен код](#как-устроен-код)
- [4. Капчи: почему всё это работает без браузера](#4-капчи-почему-всё-это-работает-без-браузера)
    - [Anubis (Techaro) — `anubis.py`](#anubis-techaro--anubispy)
    - [Portico — `limiter.py`](#portico--limiterpy)
- [5. Куда сохраняются результаты](#5-куда-сохраняются-результаты)
- [6. Использование как библиотеки](#6-использование-как-библиотеки)
    - [Как дать библиотеку другому человеку](#как-дать-библиотеку-другому-человеку)
    - [Куда установленная библиотека пишет файлы](#куда-установленная-библиотека-пишет-файлы)
    - [Быстрый старт](#быстрый-старт)
    - [Что есть в API](#что-есть-в-api)
    - [Примеры посложнее](#примеры-посложнее)
- [7. Все флаги и что в JSON](#7-все-флаги-и-что-в-json)
- [8. Если что-то пошло не так](#8-если-что-то-пошло-не-так)
- [Справочник](#справочник)
    - [Лимитер SearXNG: что он проверяет](#лимитер-searxng-что-он-проверяет)
        - [Почему спуфинг `X-Forwarded-For` больше не работает](#почему-спуфинг-x-forwarded-for-больше-не-работает)
        - [Что реально работает, и что реализовано здесь](#что-реально-работает-и-что-реализовано-здесь)
    - [Что в кэшах](#что-в-кэшах)
    - [Установка с нуля](#установка-с-нуля)
    - [Лицензия](#лицензия)

---

# 1. Как запустить

Если ставил с PyPI, команда уже в PATH:

```bash
pip install searchxng_scraper
sxng --input "nasa cosmos"
```

Это от 1 до 4 секунд, результат ложится в `results/nasa_cosmos.json`.

Если же ты клонировал этот репозиторий, настрой окружение один раз — флаг `-e`
ставит чекаут в режиме редактирования и кладёт `sxng` в PATH, так что пути
набирать больше не придётся:

```bash
python -m venv .venv
.venv/Scripts/pip install -e .        # Windows
.venv/bin/pip install -e .            # macOS / Linux
```

Дальше активируй его в каждом терминале:

```powershell
.venv\Scripts\Activate.ps1     # в начале строки появится (.venv)
sxng --input "nasa cosmos"
deactivate
```

`python main.py --input "..."` из чекаута тоже работает — и это то, что нужно
при правке движка: установка с `-e` смотрит прямо в твои файлы.

## Как модули связаны между собой

Читай снизу вверх. Каждый модуль импортирует только из строк под ним:

```
cli.py          argparse, коды возврата
api.py          search(), search_many(), SearchResult   <- публичный API
race.py         лесенка эскалаций, параллельная гонка
  ├── anubis.py    PoW Anubis + кэш кук
  ├── limiter.py   браузерные заголовки, link_token, Portico
  ├── instances.py откуда список инстансов, какие пропускать
  └── htmlparse.py HTML-страница -> те же словари, что отдаёт JSON API
pow.py          общий движок SHA-256, на котором стоят обе капчи
config.py       TLS-отпечаток, таймауты, куда писать файлы, флаг тишины
```

## Все команды

С активным окружением команда везде одна и та же:

| Команда | Что делает |
|---|---|
| `sxng --input "что искать"` | обычный поиск, 10 результатов |
| `sxng --input "что искать" -n 20` | 20 результатов вместо 10 |
| `sxng --input "что искать" -v` | **подробный лог**: видно, какие инстансы пробуются, где решилась капча, кто победил |
| `sxng --input "nato plan 2022" --pdf` | только PDF-документы |
| `sxng --input "nato staff" --profiles` | профили людей (LinkedIn `/in/`) |
| `sxng` | памятка |
| `python tests/test_pow_parser.py` | офлайн-проверки движка (~1 минута, сеть не нужна) |
| `python tests/test_rotation.py` | офлайн-проверки ротации |
| `python tests/live_audit.py` | живой аудит всех инстансов → `results/audit_report.json` |
| `python tests/live_anubis.py "запрос"` | живое демо инстансов за капчей Anubis |

## Два правила, из-за которых раньше сыпались ошибки

1. **Запрос из нескольких слов — в кавычках.** Иначе PowerShell отдаст второе
   слово отдельным аргументом: `--input nasa cosmos` → `unrecognized arguments: cosmos`.
2. **`&` в PowerShell — это фоновый запуск, а не часть пути.** Строку вида
   `...python.exe c:/Users/PC/Desk& c:/...` он разрежет пополам — так и
   появляется `unrecognized arguments: scrape`.

После `pip install` обе проблемы исчезают: там ставится настоящий `.exe`-шим.
В активированном виртуальном окружении — тоже.

---

# 2. Что где лежит и что тебе реально нужно

| Файл / папка | Нужен для работы? | За что отвечает | Когда открывать |
|---|---|---|---|
| **[searxng_scraper/](searxng_scraper)** | **да, это и есть программа** | весь поиск: список инстансов, гонка, капчи, лимитер, CLI | когда нужно что-то поправить или понять |
| **[main.py](main.py)** | нет | тонкий шим, чтобы работал `python main.py` | никогда |
| **[searxng_search.py](searxng_search.py)** | нет | тонкий шим, имя для импорта из документации | никогда |
| **[pyproject.toml](pyproject.toml)** | нет | зависимости и метаданные пакета | когда добавляешь зависимость |
| **[tests/](tests)** | нет | проверки: `test_*` идут офлайн, `live_*` нуждаются в сети | когда хочешь убедиться, что ничего не сломал |
| **[README.md](README.md)** | — | английская версия этого файла | когда забыл команду |
| **results/** | — | по файлу на запрос | когда смотришь результаты |
| **.cache/** | — | список инстансов и куки Anubis | никогда, можно удалить целиком |
| **.venv/** | для чекаута с исходниками | интерпретатор с зависимостями | никогда |
| **.vscode/** | — | настройки редактора, только локально | никогда |
| **anubis_source/** | нет | снятые исходники челленджера Anubis, в git не попадают | когда разбираешься с капчей |

Полная картина чекаута с исходниками:

```
searchxng_scraper/
├── searxng_scraper/       ← движок, по модулю на задачу
│   ├── __init__.py        публичный API, реэкспорт
│   ├── api.py             search(), search_many(), SearchResult
│   ├── cli.py             argparse, коды возврата
│   ├── race.py            лесенка эскалаций и параллельная гонка
│   ├── instances.py       список инстансов, кэш, учёт «плохих»
│   ├── limiter.py         браузерные заголовки, link_token, Portico
│   ├── anubis.py          солвер PoW Anubis и кэш кук
│   ├── htmlparse.py       HTML-страница -> словари как из JSON
│   ├── pow.py             общий движок SHA-256 proof-of-work
│   └── config.py          TLS-отпечаток, таймауты, пути, флаг тишины
├── main.py                ← шим: python main.py --input "..."
├── searxng_search.py      ← шим: имя для импорта из документации
├── README.md              ← английская версия этого файла
├── README.ru.md           ← русская версия
├── pyproject.toml         ← зависимости и метаданные
├── .vscode/               ← настройки VS Code (в git не попадают)
├── tests/                 ← проверки: test_* = офлайн, live_* = сеть
│   ├── test_pow_parser.py    офлайн: движок PoW, lane-разбиение, HTML-парсер
│   ├── test_rotation.py      офлайн: ротация инстансов, ретраи после 429
│   ├── live_anubis.py        живой: только инстансы за капчей Anubis
│   ├── live_profiles.py      живой: режим --profiles
│   └── live_audit.py         живой: прогон всех инстансов
├── anubis_source/   ← справочник по Anubis (в git не попадает, в работе не участвует)
├── results/         ← результаты: nasa_cosmos.json = запрос «nasa cosmos»
├── .cache/          ← кэши, можно удалить целиком
└── .venv/           ← рабочее окружение
```

---

# 3. Как это работает

```
searx.space/data/instances.json (обновляется раз в 6 часов)
        │
        ▼
  отбор: успешность поиска → скорость → TLS-грейд, минус «плохие» из кэша
        │
        ▼
  гонка: до 40 инстансов одновременно (окно -j), ПЕРВЫЙ с результатами побеждает
        │   каждый идёт по лесенке, пока не вернёт результаты:
        │
   a) GET /search?format=json                  JSON есть → победа
   b) ответ 200, но это HTML (JSON выключен)  → разбираем HTML на лету
   c) заглушка Anubis                           → считаем PoW на CPU → получаем
                                                  куку → снова ищем
   d) 429 / 302 от лимитера                    → браузерная сессия + link_token
                                                  → повтор запроса
   e) капча Portico                            → считаем второй PoW → POST → HTML
        │
        ▼
  победитель → results/<запрос>.json
  все упёрлись в 429? → ждём 21 секунду и пробуем «остывшие» инстансы
```

Главная идея: лимиты SearXNG живут **на конкретном инстансе**. Сорок инстансов
— это сорок независимых бюджетов, поэтому упереться во все сразу невозможно.

## Как устроен код

Каждый модуль делает одну задачу и импортирует только слой под собой. Размеры
примерно пропорциональны тому, сколько работы в задаче:

| Модуль | Строк | Что внутри | Когда сюда лезть |
|---|---|---|---|
| [config.py](searxng_scraper/config.py) | 78 | TLS-отпечаток, таймауты, пути файлов, флаг тишины | меняешь что-то глобальное |
| [pow.py](searxng_scraper/pow.py) | 322 | движок SHA-256 proof-of-work, многоядерный | скорость или сложность капчи |
| [htmlparse.py](searxng_scraper/htmlparse.py) | 119 | HTML-страница → словари как из JSON | инстанс поменял вёрстку |
| [limiter.py](searxng_scraper/limiter.py) | 124 | браузерные заголовки, ping link_token, Portico | отладка 429/302 |
| [anubis.py](searxng_scraper/anubis.py) | 352 | солвер PoW Anubis + кэш кук | отладка капчи Anubis |
| [instances.py](searxng_scraper/instances.py) | 152 | откуда список инстансов, какие пропускать | «почему оно всё пропустило» |
| [race.py](searxng_scraper/race.py) | 503 | лесенка эскалаций и параллельная гонка | правки в логике поиска |
| [api.py](searxng_scraper/api.py) | 240 | **публичный API**: `search()`, `SearchResult`, … | когда пишешь свой код поверх |
| [cli.py](searxng_scraper/cli.py) | 77 | флаги, stdout, коды возврата | добавляешь флаг |

Точки настройки, которые чаще всего правят:

| Что поменять | Где |
|---|---|
| TLS-отпечаток браузера | [config.py:16](searxng_scraper/config.py#L16) (`IMPERSONATE`) |
| User-Agent | [instances.py:36](searxng_scraper/instances.py#L36) (`UA`) |
| сколько инстансов пробовать | [instances.py:34](searxng_scraper/instances.py#L34) (`MAX_INSTANCES_TO_TRY`) |
| таймаут запроса | [instances.py:35](searxng_scraper/instances.py#L35) (`TIMEOUT`) |
| насколько долго помнить «плохой» инстанс | [instances.py:44](searxng_scraper/instances.py#L44) (`BAD_TTLS`) |
| откуда берётся список инстансов | [instances.py:33](searxng_scraper/instances.py#L33) (`INSTANCES_URL`) |
| папка результатов | [api.py:27](searxng_scraper/api.py#L27) (`RESULTS_DIR`) |

---

# 4. Капчи: почему всё это работает без браузера

Браузер решает капчи джаваскриптом. Мы делаем ровно то же, но считаем на CPU:

| Что делает браузер | Что делает пакет |
|---|---|
| запрос с TLS-отпечатком Chrome | `curl_cffi impersonate=chrome131` |
| заголовки Accept / Sec-Fetch / Accept-Language | те же заголовки вручную в `limiter.py` |
| подгрузка случайного CSS `/client<token>.css` | `ping_link_token()` — сам достаёт ссылку и качает её |
| хранение кук | `curl_cffi.Session` со своим cookie jar |
| JS-воркер считает SHA-256 для Anubis | тот же SHA-256 на всех ядрах (`pow.py`) |
| JS считает PoW Portico | тот же движок, другая упаковка nonce |
| переходы между страницами | один HTTP-запрос + парсер HTML |

## Anubis (Techaro) — `anubis.py`

Заглушка содержит challenge прямо в HTML:

```html
<script id="anubis_challenge" type="application/json">{"rules":…,"challenge":…}</script>
```

Нужно найти nonce, для которого `SHA256(randomData + nonce)` начинается с N
нулевых hex-символов, и отдать его на
`/.within.website/x/cmd/anubis/api/pass-challenge` — сервер проверит хеш и
выдаст куку `techaro.lol-anubis-auth-*`.

| Алгоритм | Что делаем |
|---|---|
| `fast` / `slow` (старый) | `SHA256(data + str(nonce))`, сложность в hex-символах — многоядерно |
| `sha256` (WASM-era) | challenge декодируется из hex в байты, nonce — 4 байта, порядок байт выбирается последним байтом challenge (≥ 0x80 → little-endian), сложность в **битах** — многоядерно |
| `argon2id` (WASM-era) | `Argon2id(challenge, salt=nonce_bytes, t=3, m=19 МБ)`, однопоточно: он memory-hard, процессы только тормозили бы. Нужен `argon2-cffi` |
| `hashx` | не поддержан — это отдельная VM-генератор программ; ни один инстанс SearXNG её не использует, код падает с внятной ошибкой |

Подводные камни, на которые ушла отладка:

* сессия **обязательно** с куками: Anubis сначала ставит
  `*-anubis-cookie-verification-*` и отвечает 500 «cookies disabled», если её
  не вернуть;
* имя auth-куки разное у версий: `techaro.lol-anubis-auth-<суффикс>` (≤ 1.27)
  и просто `techaro.lol-anubis` (devel) — принимаем оба, иначе часть
  инстансов молча редиректит хорошие ответы;
* куки кэшируются до истечения JWT (обычно ~1 час); если сервер отверг куку,
  она выбрасывается и капча решается заново в том же запуске.

Исходники самого Anubis были сняты локально, чтобы сверить конвенцию
хеширования выше. Они в `.gitignore` и в поставку не идут: в `anubis_source/`
ничего не исполняется.

## Portico — `limiter.py`

Родная капча SearXNG: `SHA256(seed + "\0" + counter)` должен дать `pow_bits`
ведущих нулевых **бит**, где seed — `payload`, `signature`, `nonce` и
User-Agent, склеенные `\0`. Готовый ответ отдаётся POST-ом на `/portico` как
`captcha_js_proof="<counter>:<hexdigest>"` — тот же движок из `pow.py`.

---

# 5. Куда сохраняются результаты

Каждый запрос — **свой понятный файл в `results/`**:

| Запрос | Файл |
|---|---|
| `nasa` | `results/nasa.json` |
| `nasa cosmos` | `results/nasa_cosmos.json` |
| `nasa cosmos` (второй раз) | `results/nasa_cosmos_2.json` |
| `nato staff` + `--pdf` | `results/nato_staff_pdf.json` |

* имя — это сам запрос: пробелы и спецсимволы → `_`, регистр нижний,
  кириллица сохраняется, длинные запросы обрезаются до 60 символов;
* существующий файл **никогда не перезаписывается** — повтор получает `_2`, `_3`;
* `--pdf` / `--profiles` добавляют в имя режим;
* `-o path` — свой путь, `-o -` — только stdout, файл не пишется.

Папку `results/` можно удалять целиком — это перезаписываемые артефакты.

---

# 6. Использование как библиотеки

`searxng_scraper` — полноценная библиотека, а не только скрипт.

## Как дать библиотеку другому человеку

С PyPI, в любое окружение Python:

```bash
pip install searchxng_scraper
```

Из git-репозитория:

```bash
pip install git+https://github.com/bddndndncnrhfjfjnf-droid/searchxng_scraper.git
```

Из локальной папки:

```bash
pip install .
```

У человека появляется модуль с нормальным именем и команда в PATH:

```python
from searxng_search import search, search_many, search_async, SearchResult
```

```bash
sxng --input "nasa cosmos"
```

Зависимости подтягиваются автоматически (`curl_cffi` + `argon2-cffi`),
нужен только Python 3.13+.

Если установка невозможна — чужой компьютер, нет pip, запрет на запись —
остаётся копирование. Скопируй папку [searxng_scraper/](searxng_scraper) рядом
со своим кодом: внутри нет ничего скомпилированного, а за пределами
стандартной библиотеки импортируется только `curl_cffi`.

```python
# 1. скопировать searxng_scraper/ к себе и просто импортировать:
import searxng_scraper as sxng
r = sxng.search("nasa cosmos", limit=5)

# 2. или указать путь к папке с чекаутом (весь API доступен и тут):
import sys; sys.path.append(r"C:\Users\PC\Desktop\searchxng_scraper")
import searxng_scraper as sxng
```

## Куда установленная библиотека пишет файлы

Папки `results/` и `.cache/` создаются рядом с движком, если туда можно
писать. При установке в **системный** `site-packages` записи может не быть
(нужны права админа) — тогда всё само уезжает в
`%LOCALAPPDATA%\.sxng_search\`. Свой путь всегда можно задать явно:
`r.save("out/report.json")`.

## Быстрый старт

```python
import searxng_search as sxng

r = sxng.search("nasa cosmos", limit=5)      # -> SearchResult

print(len(r), "результатов через", r.instance)   # 5 результатов через https://...
print(r.urls[:3])                                  # список ссылок
print(r.titles[0])                                 # первый заголовок
print(r.elapsed_s, "сек,", r.path, "anubis:", r.anubis)

for hit in r:                                       # листаем результаты как список
    print(hit["title"], "->", hit["url"])

r.save()                        # -> results/nasa_cosmos.json
r.save("out/x.json")            # -> свой путь (папка создастся сама)
print(r.to_json())              # строка JSON, готовая для ответа API
```

## Что есть в API

| Что | Что делает |
|---|---|
| `search(query, limit=10, mode="web", parallel=10, fresh=False)` | главный вызов, возвращает `SearchResult` |
| `search_json(query, **kwargs)` | сразу строкой JSON |
| `search_many(queries, limit=10, mode="web", parallel=10, gap=2.0, on_result=None)` | список запросов подряд; `on_result` — колбэк для прогресса, одна ошибка не роняет список |
| `await search_async(query, **kwargs)` | асинхронно: несколько поисков параллельно через `asyncio.gather` |
| `available_instances(fresh=False)` | список пригодных инстансов SearXNG |
| `clear_bad_instances()` | снять все пометки «плохой инстанс», вернёт их число |
| `set_verbose(True/False)` | вкл/выкл служебный лог (по умолчанию тишина) |

У `SearchResult` есть поля `query`, `mode`, `effective_query`, `instance`,
`path`, `elapsed_s`, `fetched_at`, `results` и свойства `titles`, `urls`,
`snippets`, `anubis`, `portico`, `used_html`, плюс `len()`, итерация,
индексация, `to_dict()`, `to_json()`, `save()`.

`mode` — `"web"`, `"pdf"` (только документы) или `"profiles"` (профили людей).

## Примеры посложнее

Несколько запросов одновременно:

```python
import asyncio, searxng_search as sxng

queries = ["rust tokio", "go generics", "zig build"]
results = asyncio.run(asyncio.gather(*[sxng.search_async(q, limit=3)
                                       for q in queries]))
for r in results:
    print(r.query, len(r))
```

Обработка пачки с прогрессом и сохранением:

```python
def show(r):
    print(f"{r.query}: {len(r)} шт. через {r.instance}")

for r in sxng.search_many(["climate report", "nato members"], on_result=show):
    r.save()          # у каждого свой файл в results/
```

Работа прямо из чекаута, без установки? Для этого и есть шим:

```python
import sys; sys.path.append(r"C:\Users\PC\Desktop\searchxng_scraper")
from main import search, execute, SearchResult
```

`main.py` только реэкспортирует пакет, так что оба способа импорта ведут себя
одинаково. Если общий `main.py` в твоём проекте мешает — импортируй
[searxng_scraper](searxng_scraper) напрямую: имя в пространстве имён, а внутри
движка ни одно имя не зашито.

---

# 7. Все флаги и что в JSON

| Флаг | Что делает |
|---|---|
| `--input "запрос"` | поисковый запрос (обязательный, в кавычках) |
| `-n N` | сколько результатов вернуть (по умолчанию 10) |
| `--pdf` | только документы: подставляет оператор `filetype:pdf` |
| `--profiles` | профили людей: `site:linkedin.com/in`, с докапливанием |
| `-o PATH` | свой путь вместо `results/<запрос>.json`; `-o -` — только stdout |
| `--fresh` | игнорировать кэш инстансов и список отказов |
| `-j N` | сколько инстансов гонять одновременно (по умолчанию 10; `0`/`1` — по одному) |
| `-v` | подробный лог гонки, капч и отказов |

По умолчанию программа **молчит** — на stdout только JSON, удобно перенаправлять
в `jq` или другой скрипт. Ошибка уходит в stderr с кодом возврата 1.

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

| Поле | Значение |
|---|---|
| `instance` | инстанс, который выиграл гонку |
| `path` | каким путём получены результаты: `json` / `html` (JSON отключён) / `html+anubis` / `html+portico` / `json+limiter` |
| `effective_query` | запрос с подставленным оператором режима |
| `engine` | какие поисковики инстанса ответили |

Из кода:

```python
import main as sx
out = sx.execute("nasa", limit=10, mode="web", parallel=10, fresh=False)
# mode: "web" | "pdf" | "profiles"; RuntimeError, если не сработал ни один инстанс
```

---

# 8. Если что-то пошло не так

| Симптом | Что делать |
|---|---|
| `[!] search failed: all N tried instances failed` | все инстансы в кулдауне или нет сети. Подождать минуту-две, затем `--fresh` |
| `unrecognized arguments: ...` | забыты кавычки вокруг запроса из нескольких слов |
| «Anubis solved but limiter still blocks search» | инстанс пометил наш IP; подожди ~10 минут, инстанс выпадет из кэша отказов |
| куки Anubis истекли | перерешаются сами: сервер вернул заглушку → старые куки выброшены → капча посчитана заново |
| на одном инстансе пропали результаты | HTML-путь держится на каркасе темы SearXNG (`article.result`). Проверить без сети: `python tests/test_pow_parser.py` |
| сломал после правок кода | прогнать оба офлайн-скрипта ниже — оба должны пройти |

Проверочные скрипты:

```bash
python tests/test_pow_parser.py      # движок PoW, lane-разбиение, HTML-парсер, без сети
python tests/test_rotation.py        # ротация инстансов и ретраи после 429, без сети
python tests/live_audit.py           # живой прогон всех инстансов
python tests/live_anubis.py "запрос" # живое демо инстансов за капчей
```

---

# Справочник

## Лимитер SearXNG: что он проверяет

Кроме капч у SearXNG есть плагин `searx.botdetection`
(документация: `docs.searxng.org/src/searx.botdetection.html`).

| Метод | Что проверяет |
|---|---|
| `http_accept` / `_encoding` / `_language` | браузерные Accept / Accept-Encoding / Accept-Language |
| `http_user_agent` | UA не из чёрного списка (curl, python-requests, wget, HeadlessChrome…) |
| `http_sec_fetch` | Sec-Fetch-Dest / -Mode / -Site как у настоящего браузера |
| `link_token` | клиент должен был заранее скачать случайный CSS `/client<token>.css` со страницы (браузер делает это сам; бот без рендеринга — нет) |
| `ip_limit` | скользящие окна счётчиков на IP-сеть (Valkey) |

Лимиты — **константы в исходниках**, конфигом инстанса не поднимаются:

| Окно | Обычный клиент | «Подозрительный» |
|---|---|---|
| burst 20 с | 15 запросов | **2** |
| long 10 мин | 150 | 10 |
| API (`format=json`) 1 час | **4** | — |
| suspicious-IP 30 дней | 3 пометки → редирект на главную | |

«Подозрительным» запрос делает любая проваленная проверка заголовков **или**
отсутствующий ping `link_token`.

### Почему спуфинг `X-Forwarded-For` больше не работает

Исторически (issue searxng#1237) лимитер брал IP из XFF, и «каждый запрос с
новым IP» обходил лимиты. Сейчас XFF учитывается **только** если адрес
подключившегося входит в `botdetection.trusted_proxies` конфига инстанса —
публичные инстансы чужие подсети туда не вписывают, поэтому фейковый XFF
молча игнорируется. Кнопки «обойти лимитер» не существует.

### Что реально работает, и что реализовано здесь

1. **Маскировка под браузер** — полный набор Accept/Sec-Fetch заголовков +
   TLS-отпечаток `curl_cffi impersonate=chrome131`: переводит нас из
   «подозрительных» 2/20 с в обычные 15/20 с.
2. **ping `link_token`** — GET главной, парсим `href="/clientXXX.css"`,
   качаем этот CSS. Заметка живёт 1 час на стороне сервера.
3. **Экономия запросов** — HTML-путь вместо JSON везде, где JSON отключён:
   запрос формата ≠ html попадает в окно API, где всего 4 в час.
4. **Ротация инстансов** — главный структурный «обход».
5. **Терпеливые ретраи** — пауза 21 с (чуть больше окна 20 с) и повтор.

Pass-list (`[botdetection.ip_lists] pass_ip`) — единственный законный способ
поднять лимиты, но это конфиг админа инстанса, не клиента.

Совет: не долби один инстанс подряд (бюджет burst общий на все пути), ставь
`-j 3` вместо 10, а после серии тестов подожди минуту — окна очистятся сами,
а кэш `.cache/instances.json` пропустит «плохие» инстансы.

## Что в кэшах

| Файл | Что хранит |
|---|---|
| `.cache/instances.json` | список инстансов (6 ч) + «плохие» инстансы с TTL по типу отказа (таймаут/429 — 10 мин, JSON-выключен — 6 ч, пустой поиск — 24 ч) |
| `.cache/anubis_cookies.json` | Anubis-куки по хостам, до истечения JWT |

## Установка с нуля

```bash
python -m venv .venv
.venv/bin/pip install -e .             # ставит чекаут + curl_cffi
.venv/bin/pip install argon2-cffi      # необязательно: argon2id-капча
```

Нужен Python 3.13 или новее. Больше ничего ставить не надо:
[searxng_scraper/](searxng_scraper) — чистый Python, скопируй папку на любую
машину и запускай.

## Лицензия

MIT, файл [LICENSE](LICENSE).