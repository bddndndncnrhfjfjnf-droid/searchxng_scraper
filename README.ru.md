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

**Termux / Android:**

```bash
pkg upgrade python-curl-cffi     # сборка Termux должна совпадать с твоим Python
pip install searchxng_scraper
sxng --input "nasa cosmos"
```

Если `sxng` падает с `dlopen failed: library "libpython3.X.so" not found`, значит
`curl_cffi` в Termux собран под другой версией Python. Лечится
`pkg upgrade python-curl-cffi`, запасной вариант —
`pip install --force-reinstall curl_cffi`.

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

По умолчанию программа молчит, так что пайп идёт прямо в `jq`. Ошибки — в
stderr, код возврата 1.

Запрос из нескольких слов бери в кавычки (`--input "nasa cosmos"`), иначе
PowerShell отдаст второе слово отдельным аргументом.

## Куда сохраняются результаты

По файлу на запрос в `results/`, имя — сам запрос, существующий файл никогда не
перезаписывается (повтор получает `_2`, `_3`). Папку можно удалять целиком.

## Проверки

```bash
python tests/test_pow_parser.py      # движок PoW и HTML-парсер, офлайн
python tests/test_rotation.py        # ротация инстансов, офлайн
python tests/live_audit.py           # живой прогон всех инстансов
```

## Подробнее

- [REFERENCE.md](REFERENCE.md) — как устроен каждый модуль, алгоритмы капч, что
  проверяет лимитер SearXNG и почему спуфинг `X-Forwarded-For` перестал
  работать, устройство кэшей, диагностика.
- **English**: [README.md](README.md)
- Лицензия MIT.