"""searxng_search - публичное имя библиотеки.

    from searxng_search import search
    r = search("nasa cosmos", limit=5)

Это тонкая обёртка над движком main.py: весь код живёт в одном файле, здесь
только удобное имя для импорта. Обёртка грузит движок по абсолютному пути к
соседнему файлу, поэтому не зависит ни от sys.path, ни от того, есть ли в
твоём проекте собственный main.py.

Как поставить:
    pip install .            # -> import searxng_search + команда sxng в PATH
или скопировать оба файла (main.py + searxng_search.py) куда угодно.
"""

from __future__ import annotations

import importlib.util as _ilu
import sys as _sys

_ENGINE_PATH = __import__("pathlib").Path(__file__).with_name("main.py")

_spec = _ilu.spec_from_file_location("sxng_engine", _ENGINE_PATH)
if _spec is None or _spec.loader is None:                    # pragma: no cover
    raise ImportError(f"движок поиска не найден рядом с {__file__}: {_ENGINE_PATH}")

_engine = _ilu.module_from_spec(_spec)
# Регистрация в sys.modules обязательна: @dataclass (SearchResult) ищет свой
# модуль именно здесь, иначе падает с "'NoneType' object has no attribute
# '__dict__'".
_sys.modules["sxng_engine"] = _engine
_spec.loader.exec_module(_engine)

__all__ = list(_engine.__all__)
globals().update({name: getattr(_engine, name) for name in __all__})

__version__ = "0.2.0"