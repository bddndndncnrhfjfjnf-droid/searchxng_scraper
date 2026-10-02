"""searxng_search - the public import name for the library.

    from searxng_search import search
    r = search("nasa cosmos", limit=5)

A thin wrapper around the main.py engine: all of the code lives in that one
file, this is only a convenient name to import. The wrapper loads the engine
from the absolute path of the sibling file, so it depends neither on sys.path
nor on whether your project happens to have its own main.py.

To install:
    pip install .    # -> import searxng_search, plus an sxng command on PATH
or copy both files (main.py + searxng_search.py) anywhere.
"""

from __future__ import annotations

import importlib.util as _ilu
import sys as _sys

_ENGINE_PATH = __import__("pathlib").Path(__file__).with_name("main.py")

_spec = _ilu.spec_from_file_location("sxng_engine", _ENGINE_PATH)
if _spec is None or _spec.loader is None:                    # pragma: no cover
    raise ImportError(f"search engine not found next to {__file__}: {_ENGINE_PATH}")

_engine = _ilu.module_from_spec(_spec)
# Registering in sys.modules is mandatory: @dataclass (SearchResult) looks its
# own module up here, and fails with "'NoneType' object has no attribute
# '__dict__'" otherwise.
_sys.modules["sxng_engine"] = _engine
_spec.loader.exec_module(_engine)

__all__ = list(_engine.__all__)
globals().update({name: getattr(_engine, name) for name in __all__})

__version__ = "0.2.0"