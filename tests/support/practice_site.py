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
import concurrent.futures
import functools
import http.server
import os
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

PRACTICE = Path(__file__).resolve().parent.parent / "site" / "practice"
THANKS = (b"<!doctype html><html><head><meta charset='utf-8'><title>Application received</title></head><body>"
          b"<h1>Thank you for applying</h1><p>Your application has been received.</p></body></html>")
# What a post answers with, when it is not the site's thanks: the next page of that form.
FOLLOWS = {"/next/next-submits": "next-submits-2.html", "/sent/signed-in": "after-apply.html"}
HELPER_WAIT_S = 30   # the helper's first poll can take a while in a fresh browser
POLL_S = 0.25
LAUNCH_S = 60        # starting or closing the helper's browser
CALL_S = 120         # one call into the helper's browser: a Playwright action waits 30 s at most by default
PUMP_MS = 50         # how long the browser's thread services Playwright between calls
TAB_WAIT_S = 10      # a tab the helper just opened can take a moment to reach Playwright


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

    The browser has a thread of its own that keeps Playwright serviced between calls. Playwright's sync API
    reads its driver only while its thread is inside a Playwright call, and a test waits minutes in plain
    Python while Hal works through the helper. On Windows the unread driver then stopped reading the browser
    as well (its protocol log fell silent for 2.5 minutes), and the form post of Hal's own Next press waited
    for it, so the form "would not move on" (CI, 2026-10-23). Every Playwright call goes through `on_helper`.
    """

    @classmethod
    def start_helper(cls, tmp: Path) -> None:
        """Starts the helper's browser and waits for its first poll."""
        from jobhunt import extension
        from jobhunt.web import server

        from .legacy import EXTENSION

        server.SERVABLE, cls.servable = (*server.SERVABLE, tmp.resolve()), server.SERVABLE
        cls.helper = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.helper.serve_forever, name="helper-api", daemon=True).start()
        os.environ["JOBHUNT_APPLY_BROWSER"] = "extension"
        cls._calls: queue.Queue = queue.Queue()
        ready: concurrent.futures.Future = concurrent.futures.Future()
        cls._owner = threading.Thread(target=cls._own_browser, args=(tmp, EXTENSION, ready), name="helper-browser",
                                      daemon=True)
        cls._owner.start()
        worker = ready.result(LAUNCH_S)
        address = f"http://127.0.0.1:{cls.helper.server_address[1]}"
        cls.on_helper(lambda: worker.evaluate("url => chrome.storage.local.set({hal: url})", address))
        deadline = time.monotonic() + HELPER_WAIT_S
        # the helper started here polls as an old (unpaired) helper; another one still in touch must not count
        while not extension.connected(extension.OLD) and time.monotonic() < deadline:
            time.sleep(POLL_S)
        if not extension.connected(extension.OLD):
            raise RuntimeError("the helper never polled the test server")

    @classmethod
    def _own_browser(cls, tmp: Path, unpacked: Path, ready: concurrent.futures.Future) -> None:
        """The browser's thread: launches it, then runs each queued call and services Playwright in between."""
        from playwright.sync_api import sync_playwright

        try:
            pw = sync_playwright().start()
            cls.ctx = pw.chromium.launch_persistent_context(
                str(tmp / "helper-browser"), channel="chromium", headless=True,
                args=[f"--disable-extensions-except={unpacked}", f"--load-extension={unpacked}"])
            worker = cls.ctx.service_workers[0] if cls.ctx.service_workers else cls.ctx.wait_for_event("serviceworker")
        except Exception as e:  # raised again in the test's thread by `start_helper`
            ready.set_exception(e)
            return
        cls.worker = worker
        ready.set_result(worker)
        try:
            while True:
                try:
                    call = cls._calls.get_nowait()
                except queue.Empty:
                    cls.ctx.pages[0].wait_for_timeout(PUMP_MS)   # the first tab is never closed
                    continue
                if call is None:
                    return
                fn, done = call
                try:
                    done.set_result(fn())
                except Exception as e:  # raised again in the test's thread by `on_helper`
                    done.set_exception(e)
        finally:
            cls.ctx.close()
            pw.stop()

    @classmethod
    def on_helper(cls, fn: Callable[[], Any]) -> Any:
        """Runs `fn()` on the browser's own thread and returns its result; Playwright objects live there."""
        done: concurrent.futures.Future = concurrent.futures.Future()
        cls._calls.put((fn, done))
        return done.result(CALL_S)

    @classmethod
    def stop_helper(cls) -> None:
        from jobhunt.web import server

        cls._calls.put(None)
        cls._owner.join(LAUNCH_S)
        cls.helper.shutdown()
        cls.helper.server_close()
        server.SERVABLE = cls.servable
        os.environ["JOBHUNT_APPLY_BROWSER"] = "headless"

    def in_tab(self, address: str, act: Callable[[Any], Any]) -> Any:
        """Runs `act(tab)` in the helper's newest tab whose address holds `address`, as the person would."""
        def find_and_act():
            deadline = time.monotonic() + TAB_WAIT_S
            while not (tabs := [t for t in self.ctx.pages if address in t.url]):
                if time.monotonic() > deadline:
                    open_tabs = [t.url for t in self.ctx.pages]
                    raise AssertionError(f"no tab at {address} in the helper's browser: {open_tabs}")
                self.ctx.pages[0].wait_for_timeout(PUMP_MS)
            return act(tabs[-1])

        return self.on_helper(find_and_act)

    def close_tabs(self) -> None:
        """Closes every tab but the first, so the next test meets no tab at an address it uses."""
        self.on_helper(lambda: [t.close() for t in self.ctx.pages[1:]])
