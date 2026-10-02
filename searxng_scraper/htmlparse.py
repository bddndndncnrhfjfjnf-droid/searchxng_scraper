"""Turn a SearXNG HTML results page into the same dicts the JSON API returns.

Used whenever an instance has format=json switched off, which is common on
public instances.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin

# htmlparse.py - stdlib parser for SearXNG HTML result pages
# ===========================================================================
"""Stdlib-only parser for SearXNG HTML result pages (simple/oscar themes).

SearXNG renders results as
    <article class="result result-default">
      <h3><a href="https://...">Title</a></h3>
      <p class="content">snippet text</p>
      <p class="engines">google, bing</p>
    </article>
A result is only counted when it has a title link inside an <h3> - this
mirrors the old CSS selector (``article.result h3 a``) and filters out
image/video articles that merely carry a "result" class. Unlike naive
``.text`` extraction this collects ALL descendant text of the title link
and snippet paragraph (titles like ``<a>Py<em>thon</em> docs</a>`` and
snippets containing nested tags come out complete).
"""

# capture tag/class -> result dict key
_KEY = {"title": "title", "content": "snippet", "engines": "engine"}


class _ResultParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._art: dict | None = None
        self._h3 = False            # inside the article's <h3>
        self._link_done = False     # title link already captured
        self._cap: str | None = None  # "title" | "content" | "engines"
        self._buf: list[str] = []

    def _flush(self):
        if self._art is not None and self._cap:
            self._art[_KEY[self._cap]] = "".join(self._buf).strip()
        self._cap = None
        self._buf = []

    def _close_article(self):
        if self._cap:
            self._flush()
        if self._art is not None and self._art["url"]:
            self.results.append(self._art)
        self._art = None
        self._h3 = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self._art is None:
            if tag == "article" and "result" in (a.get("class") or "").split():
                self._art = {"title": "", "url": "", "snippet": "", "engine": ""}
                self._h3 = False
                self._link_done = False
                self._cap = None
                self._buf = []
            return
        if tag == "h3":
            self._h3 = True
        elif tag == "a":
            if self._h3 and not self._link_done:
                self._link_done = True
                self._art["url"] = a.get("href") or ""
                self._cap, self._buf = "title", []
        elif tag == "p" and self._cap is None:
            cls = (a.get("class") or "").split()
            if "content" in cls:
                self._cap, self._buf = "content", []
            elif "engines" in cls:
                self._cap, self._buf = "engines", []

    def handle_endtag(self, tag):
        if self._art is None:
            return
        if tag == "article":
            self._close_article()
        elif tag == "h3":
            if self._cap == "title":
                self._flush()
            self._h3 = False
        elif tag == "a":
            if self._cap == "title":
                self._flush()
        elif tag == "p":
            if self._cap in ("content", "engines"):
                self._flush()

    def handle_data(self, data):
        if self._art is not None and self._cap:
            self._buf.append(data)


def parse_results_html(html: str, base_url: str | None = None) -> list[dict]:
    """Parse a results page into JSON-API-like dicts (title/url/snippet/engine)."""
    p = _ResultParser()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    out = []
    for r in p.results:
        if base_url and r["url"].startswith("/"):
            r["url"] = urljoin(base_url, r["url"])
        out.append(r)
    return out


# ===========================================================================
