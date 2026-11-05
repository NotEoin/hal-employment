"""Reading web links into the record (`jobhunt/ingest/web.py`).

Every page here is made up and served from 127.0.0.1: a small portfolio site, a fake GitHub API (`API_BASE`
pointed at it) and a fake Google export (`GOOGLE_BASE`). `support.offline` makes any real lookup fail at once.
"""
from __future__ import annotations

import base64
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import support  # noqa: F401 - must come before jobhunt

from jobhunt.core import settings
from jobhunt.ingest import web

PERSON = "robin-made-up"
RESET_AT = 1893456000           # 2030-01-01T00:00:00Z

HOME = """<!doctype html><html><head><title>Robin Example, work</title></head><body>
<nav><a href="/portfolio/about.html">About</a> Menu words</nav>
<main>
<h1>Robin's work</h1>
<p>I restore old maps for the Riverside Trust.</p>
<ul><li>Map cleaning</li><li>Archive cataloguing</li></ul>
<a href="a.html">Project A</a> <a href="a.html#top">Project A again</a> <a href="copy-of-a.html">Copy</a>
<a href="b/">Folder B</a> <a href="private/secret.html">Private</a> <a href="style.css">Style</a>
<a href="/other/x.html">Elsewhere on the host</a> <a href="https://elsewhere.example/">Another site</a>
<a href="mailto:robin@example.org">Mail</a>
</main>
<footer>Footer words</footer>
</body></html>"""
PAGE_A = ('<html><head><title>Project A</title><link rel="canonical" href="/portfolio/a.html"></head>'
          '<body><h2>Project A</h2><p>A map of the river.</p><a href="./">Home</a></body></html>')
COPY_OF_A = '<html><head><link rel="canonical" href="/portfolio/a.html"></head><body>Same as A</body></html>'
PAGE_B = '<html><head><title>Folder B</title></head><body><p>B</p><a href="c.html">C</a></body></html>'
PAGE_C = "<html><head><title>Page C</title></head><body><p>C</p></body></html>"
ROBOTS = "User-agent: *\nDisallow: /portfolio/private/\nDisallow: /closed/\n"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _repo(name: str, **over) -> dict:
    r = {"name": name, "full_name": f"{PERSON}/{name}", "html_url": f"https://github.com/{PERSON}/{name}",
         "description": f"{name} description", "topics": ["maps"], "stargazers_count": 3,
         "homepage": "https://example.org", "pushed_at": "2026-09-01T10:00:00Z", "fork": False}
    r.update(over)
    return r


def _json(data) -> tuple[int, dict, bytes]:
    return 200, {"Content-Type": "application/json"}, json.dumps(data).encode()


def _html(text: str) -> tuple[int, dict, bytes]:
    return 200, {"Content-Type": "text/html; charset=utf-8"}, text.encode()


def _routes() -> dict[str, tuple[int, dict, bytes]]:
    repos = [_repo("tidepool"), _repo("atlas", topics=[], stargazers_count=0), _repo("forked", fork=True)]
    return {
        "/robots.txt": (200, {"Content-Type": "text/plain"}, ROBOTS.encode()),
        "/portfolio/": _html(HOME),
        "/portfolio/a.html": _html(PAGE_A),
        "/portfolio/copy-of-a.html": _html(COPY_OF_A),
        "/portfolio/b/": _html(PAGE_B),
        "/portfolio/b/c.html": _html(PAGE_C),
        "/portfolio/private/secret.html": _html("<p>secret</p>"),
        "/portfolio/style.css": (200, {"Content-Type": "text/css"}, b"p{}"),
        "/other/x.html": _html("<p>other</p>"),
        "/closed/": _html("<p>closed</p>"),
        f"/users/{PERSON}": _json({"login": PERSON, "name": "Robin Example", "bio": "Map restorer",
                                   "location": "Leeds", "public_repos": 3,
                                   "html_url": f"https://github.com/{PERSON}"}),
        f"/users/{PERSON}/repos": _json(repos),
        f"/repos/{PERSON}/tidepool": _json(repos[0]),
        f"/repos/{PERSON}/tidepool/languages": _json({"Python": 9000, "Shell": 100}),
        f"/repos/{PERSON}/tidepool/readme": _json({"encoding": "base64",
                                                    "content": _b64("# Tidepool\n\nReads tides.")}),
        f"/repos/{PERSON}/atlas/languages": _json({"JavaScript": 500}),
        f"/repos/{PERSON}/atlas/readme": _json({"encoding": "base64", "content": _b64("Atlas readme")}),
        "/users/limited": (403, {"Content-Type": "application/json", "X-RateLimit-Remaining": "0",
                                 "X-RateLimit-Reset": str(RESET_AT)}, b'{"message": "API rate limit exceeded"}'),
        "/document/d/public123/export": (200, {"Content-Type": "text/plain; charset=utf-8",
                                               "Content-Disposition": 'attachment; filename="Volunteer log.txt"'},
                                         "\ufeffVolunteer log\r\nSorted 400 maps.".encode()),
        "/document/d/private456/export": _html("<html><body>Sign in to continue to Docs</body></html>"),
    }


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        self.server.seen.append(self.path)
        self.server.headers_seen.append(dict(self.headers))
        status, headers, body = self.server.routes.get(path, (404, {"Content-Type": "text/plain"}, b"no"))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


class _Server:
    def __enter__(self) -> _Server:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.routes = _routes()
        self.httpd.seen = []
        self.httpd.headers_seen = []
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05},
                                       daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    @property
    def seen(self) -> list[str]:
        return [p.split("?", 1)[0] for p in self.httpd.seen]


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.server = _Server().__enter__()
        self.addCleanup(self.server.__exit__)
        for name, value in (("API_BASE", self.server.base), ("GOOGLE_BASE", self.server.base)):
            patcher = mock.patch.object(web, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(web, "_github_token", return_value=None)
        self.token = patcher.start()
        self.addCleanup(patcher.stop)

    def url(self, path: str) -> str:
        return self.server.base + path


class PlainSites(Base):
    def test_a_crawl_stays_on_the_site_and_under_the_given_path(self) -> None:
        pages = web.fetch([self.url("/portfolio/")], page_cap=10)
        self.assertEqual(sorted(p.url.removeprefix(self.server.base) for p in pages),
                         ["/portfolio/", "/portfolio/a.html", "/portfolio/b/", "/portfolio/b/c.html"])
        self.assertNotIn("/other/x.html", self.server.seen)
        self.assertTrue(all(p.kind == "page" and not p.error for p in pages))

    def test_assets_and_a_page_reached_twice_are_not_read_again(self) -> None:
        web.fetch([self.url("/portfolio/")], page_cap=10)
        self.assertNotIn("/portfolio/style.css", self.server.seen)
        self.assertEqual(self.server.seen.count("/portfolio/a.html"), 1)
        pages = web.fetch([self.url("/portfolio/")], page_cap=10)
        self.assertNotIn(self.url("/portfolio/copy-of-a.html"), [p.url for p in pages])

    def test_the_page_cap_stops_the_crawl(self) -> None:
        pages = web.fetch([self.url("/portfolio/")], page_cap=2)
        self.assertEqual([p.url for p in pages], [self.url("/portfolio/"), self.url("/portfolio/a.html")])

    def test_the_page_cap_defaults_to_the_setting(self) -> None:
        real = settings.get
        with mock.patch.object(settings, "get", side_effect=lambda key: 1 if key == "record.web_pages" else real(key)):
            self.assertEqual(len(web.fetch([self.url("/portfolio/")])), 1)

    def test_robots_txt_keeps_hal_off_a_disallowed_page(self) -> None:
        web.fetch([self.url("/portfolio/")], page_cap=10)
        self.assertNotIn("/portfolio/private/secret.html", self.server.seen)
        [page] = web.fetch([self.url("/closed/")])
        self.assertEqual(page.error, web.ROBOTS_SAYS_NO)
        self.assertNotIn("/closed/", self.server.seen)

    def test_a_page_keeps_its_title_headings_and_lists_and_drops_the_menu(self) -> None:
        [home] = web.fetch([self.url("/portfolio/")], page_cap=1)
        self.assertEqual(home.title, "Robin Example, work")
        self.assertIn("# Robin's work", home.text)
        self.assertIn("Archive cataloguing", home.text)
        self.assertNotIn("Menu words", home.text)
        self.assertNotIn("Footer words", home.text)

    def test_without_markitdown_the_plain_reader_keeps_headings_and_lists(self) -> None:
        with mock.patch.object(web, "_markitdown", return_value=None):
            [home] = web.fetch([self.url("/portfolio/")], page_cap=1)
        self.assertIn("# Robin's work", home.text)
        self.assertIn("- Map cleaning\n- Archive cataloguing", home.text)
        self.assertNotIn("Menu words", home.text)
        self.assertNotIn("Footer words", home.text)

    def test_one_bad_link_never_stops_the_batch(self) -> None:
        pages = web.fetch(["not a link", "http://127.0.0.1:1/", self.url("/missing/"), self.url("/portfolio/b/c.html")])
        self.assertEqual([bool(p.error) for p in pages], [True, True, True, False])
        self.assertEqual(pages[0].error, web.NOT_A_LINK)
        self.assertIn("404", pages[2].error)
        self.assertEqual(pages[3].title, "Page C")

    def test_progress_is_reported_for_each_page(self) -> None:
        calls = []
        web.fetch([self.url("/portfolio/")], page_cap=3, progress=lambda done, total, url: calls.append((done, total)))
        self.assertEqual([done for done, _ in calls], [1, 2, 3])
        self.assertTrue(all(total >= done for done, total in calls))


class GitHub(Base):
    def test_a_github_user_gives_their_profile_and_own_repos_with_readmes(self) -> None:
        pages = web.fetch([f"https://github.com/{PERSON}"])
        self.assertEqual([p.kind for p in pages], ["profile", "repo", "repo"])
        profile, tidepool, atlas = pages
        self.assertEqual(profile.title, "Robin Example")
        self.assertIn("Map restorer", profile.text)
        self.assertEqual(tidepool.meta["languages"], ["Python", "Shell"])
        self.assertEqual(tidepool.meta["topics"], ["maps"])
        self.assertEqual(tidepool.meta["stars"], 3)
        self.assertEqual(tidepool.meta["pushed_at"], "2026-09-01T10:00:00Z")
        self.assertIn("Reads tides.", tidepool.text)
        self.assertIn("Atlas readme", atlas.text)
        self.assertNotIn("forked", [p.title for p in pages])

    def test_a_github_repo_link_gives_that_one_repo(self) -> None:
        [page] = web.fetch([f"https://github.com/{PERSON}/tidepool"])
        self.assertEqual((page.kind, page.title), ("repo", "tidepool"))
        self.assertNotIn(f"/users/{PERSON}/repos", self.server.seen)

    def test_a_github_token_is_sent_only_to_the_api(self) -> None:
        self.token.return_value = "made-up-token"
        web.fetch([f"https://github.com/{PERSON}/tidepool"])
        self.assertTrue(all(h.get("Authorization") == "Bearer made-up-token" for h in self.server.httpd.headers_seen))

    def test_a_used_up_rate_limit_stops_with_the_reset_time(self) -> None:
        pages = web.fetch(["https://github.com/limited", f"https://github.com/{PERSON}"])
        self.assertEqual(len(pages), 2)
        self.assertIn("used up until 00:00 UTC", pages[0].error)
        self.assertIn("add a GitHub token", pages[0].error)
        self.assertEqual(pages[0].meta["reset_at"], "2030-01-01T00:00:00Z")
        self.assertEqual(pages[1].error, pages[0].error)
        self.assertEqual(self.server.seen, ["/users/limited"])


class OtherSites(Base):
    def test_a_login_walled_site_says_it_needs_you_signed_in_and_is_not_fetched(self) -> None:
        for url in ("https://www.instagram.com/robin.made.up/", "https://www.linkedin.com/in/robin-made-up"):
            [page] = web.fetch([url])
            self.assertEqual(page.error, web.SIGNED_IN)
            self.assertTrue(page.error.startswith("This site needs you signed in"))

    def test_google_docs_and_slides_links_become_export_addresses(self) -> None:
        with mock.patch.object(web, "GOOGLE_BASE", "https://docs.google.com"):
            self.assertEqual(web.export_url("https://docs.google.com/document/d/abc_D-1/edit?usp=sharing"),
                             "https://docs.google.com/document/d/abc_D-1/export?format=txt")
            self.assertEqual(web.export_url("https://docs.google.com/document/u/0/d/abc/view"),
                             "https://docs.google.com/document/d/abc/export?format=txt")
            self.assertEqual(web.export_url("https://docs.google.com/presentation/d/xyz/edit#slide=id.p"),
                             "https://docs.google.com/presentation/d/xyz/export/txt")
            self.assertIsNone(web.export_url("https://docs.google.com/spreadsheets/d/xyz/edit"))
            self.assertIsNone(web.export_url("https://example.org/document/d/xyz/edit"))

    def test_a_public_google_doc_is_read_as_a_doc(self) -> None:
        [page] = web.fetch(["https://docs.google.com/document/d/public123/edit"])
        self.assertEqual((page.kind, page.title, page.error), ("doc", "Volunteer log", ""))
        self.assertEqual(page.text, "Volunteer log\nSorted 400 maps.")

    def test_a_private_google_doc_asks_you_to_download_it(self) -> None:
        [page] = web.fetch(["https://docs.google.com/document/d/private456/edit"])
        self.assertEqual(page.error, web.GOOGLE_PRIVATE)


if __name__ == "__main__":
    unittest.main()
