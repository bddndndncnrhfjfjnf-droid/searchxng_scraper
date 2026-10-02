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

Результат пишется в `results/searxng_python_api.json`. С `-v` дополнительно
виден живой лог: какие инстансы пробовались и где решилась капча.

| Поле | Значение |
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

r.save()                               # -> results/nasa_cosmos.json
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
   results/<запрос>.json
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
| `-v` | живой лог; без него на stdout только JSON |
| `--update` | спросить PyPI про новую версию прямо сейчас и предложить её |
| `--no-update-check` | никогда не проверять обновления |

По умолчанию программа молчит, так что пайп идёт прямо в `jq`. Ошибки — в
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

По файлу на запрос в `results/`, имя — сам запрос, существующий файл никогда не
перезаписывается (повтор получает `_2`, `_3`). Папку можно удалять целиком.

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

`results/` создаётся рядом с пакетом, а на Termux это значит внутрь
`site-packages`. Работает, но искать неудобно, поэтому передавай свой путь или
просто запускай из домашней директории:

```bash
sxng --input "nasa cosmos" -o ~/nasa.json
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