"""A made-up website for tests, served from 127.0.0.1: saved synthetic HTML pages by path, each with an ETag.

`FakeSite()` is a context manager; `site.page(path, html)` serves `html` at `path` and `site.url(path)` is its
address. A request with `If-None-Match` equal to the page's ETag gets 304, as a real server would answer.
`FIXTURES` holds the saved pages (`tests/fixtures/pages`).
"""
from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "pages"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.server.seen.append(self.path)
        page = self.server.pages.get(self.path.split("?", 1)[0])
        if page is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = page.encode("utf-8")
        etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


class FakeSite:
    """The made-up site; `pages` maps paths to HTML and `seen` lists the paths asked for."""

    def __enter__(self) -> FakeSite:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.pages, self.httpd.seen = {}, []
        self.pages, self.seen = self.httpd.pages, self.httpd.seen
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def url(self, path: str) -> str:
        """The address of `path` on the made-up site."""
        return f"http://127.0.0.1:{self.httpd.server_port}{path}"

    def page(self, path: str, html: str) -> str:
        """Serves `html` at `path`; returns its address."""
        self.pages[path] = html
        return self.url(path)

    def saved(self, path: str, name: str) -> str:
        """Serves the saved fixture page `name` at `path`; returns its address."""
        return self.page(path, (FIXTURES / name).read_text(encoding="utf-8"))
