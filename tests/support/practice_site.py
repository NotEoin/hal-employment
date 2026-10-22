"""The practice site of gate G6: application forms that really post, and a server that counts every post.

Hal never presses Submit, so after Hal fills a form, the person approves it and Hal moves on,
no post to `/sent/...` may have arrived. A post to `/next/...` is a page of a form that moves on with a
real submit, which Hal may press. The pages are in `tests/site/practice/`; every name, employer and
answer on them is made up.

`HelperBrowser` runs a test class with the browser helper loaded unpacked in Playwright's Chromium, as
the applicant's own browser loads it, so each flow can run in both modes.
"""
from __future__ import annotations

import collections
import functools
import http.server
import os
import threading
import time
from pathlib import Path

PRACTICE = Path(__file__).resolve().parent.parent / "site" / "practice"
THANKS = (b"<!doctype html><html><head><meta charset='utf-8'><title>Application received</title></head><body>"
          b"<h1>Thank you for applying</h1><p>Your application has been received.</p></body></html>")
# What a post answers with, when it is not the site's thanks: the next page of that form.
FOLLOWS = {"/next/next-submits": "next-submits-2.html", "/sent/signed-in": "after-apply.html"}
HELPER_WAIT_S = 30   # the helper's first poll can take a while in a fresh browser
POLL_S = 0.25


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Serves the practice pages and counts each post by its path."""

    site: PracticeSite

    def log_message(self, fmt, *args):  # keeps the test output to the results
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        path = self.path.split("?")[0]
        with self.site.lock:
            self.site.posts[path] += 1
            self.site.bodies.append((path, body))
        follow = FOLLOWS.get(path)
        page = (PRACTICE / follow).read_bytes() if follow else THANKS
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)


class PracticeSite:
    """The practice site on a free local port; use as a context manager."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.posts: collections.Counter = collections.Counter()
        self.bodies: list[tuple[str, bytes]] = []
        handler = type("Handler", (_Handler,), {"site": self})
        serve = functools.partial(handler, directory=str(PRACTICE))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), serve)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self) -> PracticeSite:
        threading.Thread(target=self.httpd.serve_forever, name="practice-site", daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def sent(self, page: str | None = None) -> int:
        """Posts that would send an application: to one page's `/sent/<page>`, or to any."""
        with self.lock:
            return sum(n for path, n in self.posts.items()
                       if path.startswith("/sent/") and (page is None or path == f"/sent/{page}"))

    def steps(self, page: str) -> int:
        """Posts that moved a form to its next page."""
        with self.lock:
            return self.posts[f"/next/{page}"]

    def reset(self) -> None:
        with self.lock:
            self.posts.clear()
            self.bodies.clear()


class HelperBrowser:
    """Mixin for a `support.legacy.Practice` class: forms are filled through the unpacked helper.

    Starts the legacy web helper on a free port, Playwright's Chromium with the helper loaded, and
    points the helper at that port, as tests/test_extension.py does.
    """

    @classmethod
    def start_helper(cls, tmp: Path) -> None:
        """Starts the helper's browser and waits for its first poll."""
        from playwright.sync_api import sync_playwright

        from jobhunt import extension
        from jobhunt.web import server

        from .legacy import EXTENSION

        server.SERVABLE, cls.servable = (*server.SERVABLE, tmp.resolve()), server.SERVABLE
        cls.helper = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.helper.serve_forever, name="helper-api", daemon=True).start()
        os.environ["JOBHUNT_APPLY_BROWSER"] = "extension"
        cls.pw = sync_playwright().start()
        cls.ctx = cls.pw.chromium.launch_persistent_context(
            str(tmp / "helper-browser"), channel="chromium", headless=True,
            args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}"])
        worker = cls.ctx.service_workers[0] if cls.ctx.service_workers else cls.ctx.wait_for_event("serviceworker")
        worker.evaluate("url => chrome.storage.local.set({hal: url})", f"http://127.0.0.1:{cls.helper.server_address[1]}")
        deadline = time.monotonic() + HELPER_WAIT_S
        while not extension.connected() and time.monotonic() < deadline:
            cls.ctx.pages[0].wait_for_timeout(500)
        if not extension.connected():
            raise RuntimeError("the helper never polled the test server")

    @classmethod
    def stop_helper(cls) -> None:
        from jobhunt.web import server

        cls.ctx.close()
        cls.pw.stop()
        cls.helper.shutdown()
        cls.helper.server_close()
        server.SERVABLE = cls.servable
        os.environ["JOBHUNT_APPLY_BROWSER"] = "headless"

    def helper_tab(self, address: str):
        """The helper's tab whose address holds `address`, newest first."""
        # Playwright hears of tabs the helper opened only while it is waiting on something.
        self.ctx.pages[0].wait_for_timeout(300)
        return next(t for t in reversed(self.ctx.pages) if address in t.url)
