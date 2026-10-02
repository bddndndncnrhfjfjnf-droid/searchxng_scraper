# Мета-поиск SearXNG — без браузера

Ищет по публичным инстансам SearXNG **без браузера, без Selenium, без headless**.
Работает даже там, где админ отключил JSON API, где стоит капча Anubis или
Portico, или где срабатывает лимитер — всё решается на CPU.

Одна зависимость: `curl_cffi`. Нужен Python 3.13+.

## Установка

```bash
pip install searchxng_scraper
```

Всё. Дальше:

```bash
sxng --input "nasa cosmos"
```

**Termux / Android:** см. раздел [Установка на Termux](#установка-на-termux).

Опционально, только если попадётся редкая argon2id-капча:

```bash
pip install "searchxng_scraper[argon2]"
```

**Из исходников:**

```bash
python -m venv .venv && .venv/bin/pip install -e .
python main.py --input "nasa cosmos"
```

## Как это выглядит

Без пайпов выводит найденное и говорит, куда сохранился отчёт:

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

Тот же запуск ещё и пишет полный JSON в
`SearchXNG_report/<запрос>.json`.

**А через пайп приходит JSON** — без всяких флагов, программа сама проверяет,
терминал ли stdout:

```console
$ sxng --input "supply chain" | jq '.results[].url'
https://en.wikipedia.org/wiki/Supply_chain
https://www.supplychaintoday.com/supply-chain-basics-…/
```

Захочешь явно — `--json` для отчёта в терминале, `-o -` для JSON без записи
файла.

| Поле в JSON | Значение |
|---|---|
| `instance` | инстанс, выигравший гонку |
| `path` | как пришли результаты: `json` / `html` / `html+anubis` / `html+portico` / `json+limiter` |
| `elapsed_s` | прошло секунд |

## Как библиотека

```python
import searxng_search as sxng          # или: from searxng_scraper import search

r = sxng.search("nasa cosmos", limit=5)
print(len(r), "результатов через", r.instance)
print(r.titles[:3])

r.save()                               # -> ./SearchXNG_report/nasa_cosmos.json
```

Ещё доступно: `search_json()`, `search_many()`, `search_async()`,
`available_instances()`, `clear_bad_instances()`, `set_verbose()`.
У `SearchResult` есть `.titles`, `.urls`, `.snippets`, `.path`, `.elapsed_s`,
`.anubis`, `.to_dict()`, `.to_json()`, `.save()`.

## Как это работает

```
searx.space/data/instances.json        обновляется раз в 6 часов
        │
        ▼  отбор: успешность → скорость → TLS-грейд, минус недавно упавшие
   гонка: до 40 инстансов одновременно, первый с результатами побеждает
        │   каждый идёт по одной и той же лесенке:
        │     1. GET /search?format=json          готово
        │     2. 200, но это HTML (JSON выключен) разбираем HTML
        │     3. заглушка Anubis                  считаем PoW на CPU, повтор
        │     4. 429 / 302 от лимитера           браузерная сессия + link_token
        │     5. капча Portico                   считаем, POST, разбираем HTML
        │
        ▼
   ./SearchXNG_report/<запрос>.json   + сами ссылки в терминале
```

Смысл гонки: лимиты SearXNG живут **на конкретном инстансе**. Сорок инстансов —
это сорок независимых бюджетов, упереться во все сразу невозможно.

Каждая задача живёт в своём модуле — `pow.py` (движок SHA-256, на котором стоят
обе капчи), `limiter.py`, `anubis.py`, `instances.py`, `race.py`, `api.py`,
`cli.py` — и каждый импортирует только слои под собой.

## Флаги

| Флаг | Что делает |
|---|---|
| `--input "запрос"` | поисковый запрос (обязательный, в кавычках) |
| `-n N` | сколько результатов (по умолчанию 10) |
| `--pdf` | только PDF-документы |
| `--profiles` | профили людей (LinkedIn `/in/`) |
| `-o PATH` | свой путь вывода; `-o -` — в stdout |
| `--fresh` | игнорировать кэш инстансов и список отказов |
| `-j N` | сколько инстансов гонять одновременно (по умолчанию 10; `0`/`1` — по одному) |
| `-v` | живой лог гонки инстансов |
| `--json` | вывести JSON в stdout вместо читаемого отчёта |
| `--results-dir DIR` | писать отчёты не в `./SearchXNG_report`, а куда скажешь |
| `--update` | спросить PyPI про новую версию прямо сейчас и предложить её |
| `--no-update-check` | никогда не проверять обновления |

В терминале — читаемый отчёт, в пайпе или редиректе — JSON. Ошибки идут в
stderr, код возврата 1.

### Проверка обновлений

Фоновый поток спрашивает у PyPI, есть ли новая версия, **пока идёт твой поиск**.
Если есть — промпт `y/N` в stderr, и только после того, как результаты уже на
экране.

Она не мешает по построению: не больше одного запроса в сутки (кэш в
`.cache/update_check.json`), полностью пропускается, если stdout не терминал
(так что `sxng … | jq` никогда не зависнет на вопросе), в библиотечном режиме
не работает вовсе, таймаут 3 секунды, а любая ошибка — офлайн, блокировка, DNS —
молчит. Отключается навсегда через `--no-update-check` или
`SXNG_NO_UPDATE_CHECK=1`.

Запрос из нескольких слов бери в кавычки (`--input "nasa cosmos"`), иначе
PowerShell отдаст второе слово отдельным аргументом.

## Куда сохраняются результаты

По файлу на запрос в **`./SearchXNG_report/`** — папка создаётся при первом
запуске, в той директории, откуда ты запустил команду. Имя — сам запрос,
существующий файл никогда не перезаписывается (повтор получает `_2`, `_3`).

```bash
sxng --input "supply chain"                  # ./SearchXNG_report/supply_chain.json
sxng --input "supply chain" --results-dir ~/reports
```

Работаешь в непригодной директории? Папка переедет в каталог пакета, потом в
`~/.sxng_search/` — пока не найдётся что-то доступное для записи. Путь всегда
печатается, когда отчёт записан.

Всё содержимое одноразовое — папку можно удалять в любой момент.

## Установка на Termux

Android требует одного лишнего шага, которого нет на десктопе, поэтому у него
отдельный раздел.

**0. Правильный Termux.** Ставь из **F-Droid** (`com.termux`), а не из Play
Store: сборка из Play Store неподдерживаемая, старше NDK и вообще не умеет
запускать нативные расширения, поэтому `curl_cffi` там загрузиться не может
никак.

**1. Python из Termux, а не из F-Droid:**

```bash
pkg install python
python -V          # ожидаем 3.13 или новее
```

**2. Сначала почини `curl_cffi`.** Именно этот шаг и кусается, а ошибка
ничего не говорит о причине:

```
ImportError: dlopen failed: library "libpython3.13.so" not found:
needed by .../curl_cffi/_wrapper.abi3.so in namespace (default)
```

`curl_cffi` приезжает с готовым бинарником. Termux собирает его под тот Python,
который был актуален на момент сборки пакета; как только сам Termux обновляет
Python, пакет остаётся позади и указывает на несуществующий `libpython`.
Лечится так:

```bash
pkg upgrade python-curl-cffi
```

Если пересобранного пакета у Termux ещё нет, компилируем под свой интерпретатор
— один раз, нужен тулчейн:

```bash
pkg install clang libffi make pkg-config
pip install --force-reinstall curl_cffi
```

Проверь до того, как идти дальше: импортом бинарника проверяется именно он:

```bash
python -c "from curl_cffi import requests; print('curl_cffi OK')"
```

**3. Установка и запуск:**

```bash
pip install searchxng_scraper
sxng --input "nasa cosmos"
```

Если `pip` ругается «externally managed environment», добавь
`--break-system-packages` (либо поставь в venv:
`python -m venv .venv && .venv/bin/pip install searchxng_scraper`).

### Куда ложатся файлы на Android

Отчёты попадают в `./SearchXNG_report/` в той директории, откуда ты запустил
команду, — на Termux это обычно домашняя папка. Если она недоступна для записи,
папка уезжает в `~/.sxng_search/`, а путь печатается в любом случае:

```bash
sxng --input "nasa cosmos" --results-dir ~/reports
```

Фоновая проверка обновлений использует `urllib` из стандартной библиотеки, а не
`curl_cffi`, так что дополнительной настройки не требует и сломаться так же не
может.

### Если на Termux что-то не работает

| Симптом | Что делать |
|---|---|
| `dlopen failed: library "libpython3.X.so" not found` | `pkg upgrade python-curl-cffi`, либо пересборка выше |
| `curl_cffi` собрался, но падает на импорте | пакет собран под другим Python; переустанови после `pkg upgrade python` |
| `externally managed environment` | добавь `--break-system-packages` или используй venv |
| Play Store Termux, не работает ничего | удали и поставь сборку из F-Droid |
| первый поиск медленный | солвер PoW использует все ядра, это нормально; дальше результаты кэшируются |

## Проверки

```bash
python tests/test_pow_parser.py      # движок PoW и HTML-парсер, офлайн
python tests/test_rotation.py        # ротация инстансов, офлайн
python tests/test_cli.py             # проверка обновлений и optional extra, офлайн
python tests/live_audit.py           # живой прогон всех инстансов
```

## Подробнее

- [REFERENCE.md](REFERENCE.md) — как устроен каждый модуль, алгоритмы капч, что
  проверяет лимитер SearXNG и почему спуфинг `X-Forwarded-For` перестал
  работать, устройство кэшей, диагностика.
- **English**: [README.md](README.md)
- Лицензия MIT.