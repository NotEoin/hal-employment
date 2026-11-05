"""Reads the web links a person gives for their record: their own site or blog, GitHub, public Google Docs.

`fetch(urls)` turns each link into `Page`s. A link that cannot be read gives one `Page` with `error` in plain
words, so one bad link never stops the batch. Nothing here calls a model or writes to the record.

Design notes:
- Every request goes through `_open`: Hal's own user agent (`http.user_agent`), a timeout and a size cap.
  `http.get` is not used because a crawl needs the address a redirect ended at, the content type and the
  status and headers of a refused request (GitHub's rate limit).
- A plain site is read breadth first from the given page, only on the same host and under the given page's
  folder, up to `record.web_pages` pages, and only where its robots.txt lets Hal in. Assets are skipped by
  their extension, and a page is read once: its address without the fragment, and its canonical link.
- Pages become Markdown through MarkItDown when the documents extra is installed, else through `_Reader`,
  which keeps headings and lists. Both see only the page's `<main>` (or its `<article>`s) when it has one,
  else the page without its `<nav>`, `<header>`, `<footer>` and `<aside>`.
- GitHub links use the public REST API (`API_BASE`), not the pages. Without a token GitHub allows 60
  requests an hour (docs.github.com, "Rate limits for the REST API", checked 2026-11-05); a used-up limit
  ends that link, and every later GitHub link in the batch, with the time it resets, instead of waiting.
  Forks are skipped: they are mostly other people's code.
- Google Docs and Slides are read through their export address, which answers only for a link shared with
  anyone; a private one answers with Google's sign-in page. robots.txt applies to the crawl only, not to a
  single document or an API the person pointed Hal at.
- Sites that show nothing useful signed out (`LOGIN_WALLED`) are not fetched at all: signed out they show a
  sign-in page, and reading them by program breaks their terms.
"""
from __future__ import annotations

import base64
import binascii
import functools
import io
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import Message
from html.parser import HTMLParser
from typing import Any, Literal

from .. import http

log = logging.getLogger(__name__)

API_BASE = "https://api.github.com"       # the tests point these two at a local server
GOOGLE_BASE = "https://docs.google.com"
GITHUB_WEB = "https://github.com"
TIMEOUT_S = 20                  # per request; a personal site that takes longer is down
MAX_PAGE_BYTES = 2_000_000      # a page of text is far smaller; anything bigger is a file, not a page
MAX_API_BYTES = 5_000_000       # a page of 100 repositories, or a long README
MAX_ROBOTS_BYTES = 500_000      # Google reads only the first 500 KiB of a robots.txt (RFC 9309, 2.5)
DEFAULT_PAGE_CAP = 30           # `record.web_pages` when settings cannot be read
GITHUB_WINDOW_S = 3600          # GitHub's limits reset within the hour; used when no reset time is given
GITHUB_PER_PAGE = 100           # the most the API returns in one page
GITHUB_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
ROBOTS_AGENT = http.PRODUCT.split("/", 1)[0]
TOKEN_SECRET = "record.github_token"

Kind = Literal["page", "repo", "profile", "doc"]

LOGIN_WALLED = ("instagram.com", "linkedin.com", "facebook.com", "fb.com", "x.com", "twitter.com",
                "tiktok.com", "threads.net")
GITHUB_HOSTS = ("github.com", "www.github.com")
# github.com/<first part> addresses that are GitHub's own pages, not a user
GITHUB_OWN = frozenset(("about", "apps", "collections", "explore", "features", "issues", "login", "marketplace",
                        "notifications", "pricing", "pulls", "search", "settings", "sponsors", "topics", "trending"))
GOOGLE_DOC = re.compile(r"^/(document|presentation)/(?:u/\d+/)?d/([A-Za-z0-9_-]+)")
ASSETS = frozenset((
    ".apk", ".atom", ".avif", ".bmp", ".css", ".csv", ".dmg", ".doc", ".docx", ".eot", ".exe", ".gif", ".gz",
    ".ico", ".jpeg", ".jpg", ".js", ".json", ".map", ".mjs", ".mov", ".mp3", ".mp4", ".otf", ".pdf", ".png",
    ".pptx", ".rss", ".svg", ".tar", ".tif", ".tiff", ".ttf", ".wasm", ".webm", ".webp", ".woff", ".woff2",
    ".xlsx", ".xml", ".zip"))
SIGN_IN_PATH = re.compile(r"/(log-?in|sign-?in|sign_in|auth|session)(\b|/)", re.I)

SIGNED_IN = ("This site needs you signed in, so Hal cannot read it. Copy what you need into a document, or "
             "save the page as a PDF, and drop the file in instead.")
GOOGLE_PRIVATE = "Needs you signed in to Google; download it and drop the file in instead."
GOOGLE_OTHER = ("Hal reads Google Docs and Slides links. For other Google files, download the file and drop "
                "it in instead.")
ROBOTS_SAYS_NO = "This site asks programs like Hal not to read this page, so Hal left it."
NOT_A_LINK = "This is not a web link. Links start with https:// or http://."
NOT_A_PAGE = "This link is a file, not a web page. Download it and drop the file in instead."


@dataclass(frozen=True)
class Page:
    """One page read from a link, or the reason a link could not be read (`error` set, `text` empty)."""

    url: str
    title: str
    text: str                   # Markdown
    kind: Kind
    meta: dict[str, Any]
    error: str = ""


class LinkError(Exception):
    """A link cannot be read; the message is the plain words shown to the person."""


class _RateLimited(LinkError):
    """GitHub's request limit is used up."""

    def __init__(self, words: str, reset_at: str) -> None:
        super().__init__(words)
        self.reset_at = reset_at


@dataclass
class _Response:
    url: str                    # where any redirects ended
    status: int
    headers: Message
    body: bytes
    truncated: bool = False

    @property
    def type(self) -> str:
        return (self.headers.get_content_type() if self.headers else "").lower()

    def text(self) -> str:
        charset = (self.headers.get_content_charset() if self.headers else None) or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


@dataclass
class _Run:
    """What one `fetch` call shares between its links."""

    cap: int
    progress: Callable[[int, int, str], None] | None
    links_left: int
    done: int = 0
    github_stop: _RateLimited | None = None
    token: str | None = None
    pages: list[Page] = field(default_factory=list)

    def tick(self, url: str, queued: int = 0) -> None:
        """Reports one more page read, with the estimated total: read, still queued, and links not started."""
        self.done += 1
        if self.progress is not None:
            self.progress(self.done, self.done + queued + self.links_left, url)


def fetch(urls: list[str], *, page_cap: int | None = None,
          progress: Callable[[int, int, str], None] | None = None) -> list[Page]:
    """The pages read from each link, in the order given; a link that fails gives one `Page` with `error`.

    `page_cap` is the most pages read from one link (a site's pages, or a GitHub user's profile and
    repositories); it defaults to the `record.web_pages` setting. `progress(done, total, url)` is called
    after each page, with `total` an estimate that settles as the crawl finds out how big each site is.
    Never raises for a link; raises ValueError for a `page_cap` below 1.
    """
    cap = _page_cap() if page_cap is None else page_cap
    if cap < 1:
        raise ValueError(f"page_cap must be at least 1, not {cap}")
    run = _Run(cap=cap, progress=progress, links_left=len(urls))
    seen: set[str] = set()
    for raw in urls:
        run.links_left -= 1
        try:
            url = canonical(_with_scheme(raw))
        except ValueError:
            run.pages.append(Page(str(raw), "", "", "page", {}, NOT_A_LINK))
            continue
        if url in seen:
            continue
        seen.add(url)
        kind, read = _reader_for(url)
        try:
            read(url, run)
        except LinkError as e:
            run.pages.append(Page(url, "", "", kind, {}, str(e)))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            run.pages.append(Page(url, "", "", kind, {}, _unreachable(url, e)))
        except Exception as e:  # one link's failure is reported on its Page, never raised for the batch
            log.exception("reading a %s link on %s failed", kind, _host(url))
            run.pages.append(Page(url, "", "", kind, {}, f"Hal could not read this link ({type(e).__name__})."))
    return run.pages


def canonical(url: str) -> str:
    """`url` without its fragment, with a lower-case scheme and host, no default port and a path of at least "/".

    Raises ValueError for anything but an http or https address with a host.
    """
    p = urllib.parse.urlsplit(url.strip())
    scheme = p.scheme.lower()
    host = (p.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        raise ValueError(f"not a web address: {url!r}")
    port = p.port
    if ":" in host:
        host = f"[{host}]"
    if port is not None and (scheme, port) not in (("http", 80), ("https", 443)):
        host = f"{host}:{port}"
    return urllib.parse.urlunsplit((scheme, host, p.path or "/", p.query, ""))


def export_url(url: str) -> str | None:
    """The plain-text export address of a Google Docs or Slides link, or None for any other link."""
    p = urllib.parse.urlsplit(url)
    if (p.hostname or "").lower() != "docs.google.com":
        return None
    m = GOOGLE_DOC.match(p.path)
    if not m:
        return None
    what, doc_id = m.groups()
    if what == "document":
        return f"{GOOGLE_BASE}/document/d/{doc_id}/export?format=txt"
    return f"{GOOGLE_BASE}/presentation/d/{doc_id}/export/txt"


def _page_cap() -> int:
    from ..core import settings

    try:
        return int(settings.get("record.web_pages"))
    except (KeyError, TypeError, ValueError):
        log.warning("record.web_pages cannot be read; using %d", DEFAULT_PAGE_CAP)
        return DEFAULT_PAGE_CAP


def _with_scheme(raw: str) -> str:
    text = str(raw or "").strip()
    if text and "://" not in text and re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", text, re.I):
        text = "https://" + text
    return text


def _host(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def _on(host: str, names: tuple[str, ...]) -> bool:
    return any(host == n or host.endswith("." + n) for n in names)


def _reader_for(url: str) -> tuple[Kind, Callable[[str, _Run], None]]:
    host = _host(url)
    if _on(host, LOGIN_WALLED):
        return "page", _login_walled
    if host in GITHUB_HOSTS:
        return "repo", _github
    if host in ("docs.google.com", "drive.google.com"):
        return "doc", _google
    return "page", _site


def _unreachable(url: str, e: BaseException) -> str:
    reason = getattr(e, "reason", e)
    if isinstance(reason, TimeoutError) or isinstance(e, TimeoutError):
        return f"{_host(url)} took too long to answer. Try again later."
    return f"Hal could not reach {_host(url)}. Check the link, or try again later."


def _open(url: str, *, headers: dict[str, str] | None = None, cap: int = MAX_PAGE_BYTES) -> _Response:
    """One GET with Hal's user agent, `TIMEOUT_S` and at most `cap` bytes of body; an HTTP error is returned."""
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"only web addresses are fetched, not {url!r}")
    req = urllib.request.Request(url, headers={"User-Agent": http.user_agent(), **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            body = r.read(cap + 1)
            return _Response(r.geturl(), r.status, r.headers, body[:cap], len(body) > cap)
    except urllib.error.HTTPError as e:
        try:
            body = e.read(cap)
        except OSError:
            body = b""
        finally:
            e.close()
        return _Response(e.geturl() or url, e.code, e.headers, body)


def _status_words(status: int) -> str:
    if status == 401:
        return SIGNED_IN
    if status in (403, 999):     # 999 is LinkedIn's answer to programs
        return "This site turned Hal away; it may need you signed in. Save the page as a PDF and drop it in instead."
    if status in (404, 410):
        return f"There is no page at this link ({status}). Check it was copied whole."
    if status == 429:
        return "This site asked Hal to slow down. Try again later."
    return f"The site answered with an error ({status}). Try again later."


def _login_walled(url: str, run: _Run) -> None:
    run.pages.append(Page(url, "", "", "page", {"site": _host(url)}, SIGNED_IN))


# ---- plain sites


def _scope(url: str) -> tuple[str, str]:
    """(scheme and host, folder) a crawl from `url` stays within; a last part with a dot is a file."""
    p = urllib.parse.urlsplit(url)
    path = p.path or "/"
    last = path.rsplit("/", 1)[-1]
    folder = path.rsplit("/", 1)[0] + "/" if "." in last else path.rstrip("/") + "/"
    return f"{p.scheme}://{p.netloc}", folder


def _in_scope(url: str, scope: tuple[str, str]) -> bool:
    p = urllib.parse.urlsplit(url)
    origin, folder = scope
    path = p.path or "/"
    return f"{p.scheme}://{p.netloc}" == origin and (path.startswith(folder) or path + "/" == folder)


def _asset(url: str) -> bool:
    last = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1].lower()
    return "." in last and last[last.rindex("."):] in ASSETS


def _robots(start: str) -> Callable[[str], bool]:
    """Whether robots.txt on `start`'s site lets Hal read an address; a missing or unreadable file allows all."""
    p = urllib.parse.urlsplit(start)
    parser = urllib.robotparser.RobotFileParser()
    try:
        r = _open(f"{p.scheme}://{p.netloc}/robots.txt", cap=MAX_ROBOTS_BYTES)
    except (urllib.error.URLError, TimeoutError, OSError):
        r = None
    if r is None or (r.status >= 400 and r.status not in (401, 403)):
        parser.allow_all = True
    elif r.status in (401, 403):        # what urllib.robotparser does for a forbidden robots.txt
        parser.disallow_all = True
    else:
        parser.parse(r.text().splitlines())
    parser.modified()
    return lambda url: parser.can_fetch(ROBOTS_AGENT, url)


def _signs_in(start: str, final: str) -> bool:
    """True when the first page redirected to a sign-in page."""
    host = _host(final)
    moved = _host(start) != host
    return (moved and host.startswith(("accounts.", "login.", "signin.", "auth."))) or (
        bool(SIGN_IN_PATH.search(urllib.parse.urlsplit(final).path)) and not SIGN_IN_PATH.search(start))


def _site(start: str, run: _Run) -> None:
    allowed = _robots(start)
    if not allowed(start):
        raise LinkError(ROBOTS_SAYS_NO)
    scope = _scope(start)
    queue = deque([start])
    queued = {start}
    read: set[str] = set()
    count = 0
    while queue and count < run.cap:
        url = queue.popleft()
        if url != start and not allowed(url):
            continue
        r = _open(url)
        final = canonical(r.url)
        if url == start:
            if _signs_in(start, final):
                raise LinkError(SIGNED_IN)
            if r.status >= 400:
                raise LinkError(_status_words(r.status))
            if _host(final) != _host(start):
                scope = _scope(final)
        elif r.status >= 400 or not _in_scope(final, scope):
            continue
        if r.type not in ("text/html", "application/xhtml+xml", "text/plain"):
            if url == start:
                raise LinkError(NOT_A_PAGE)
            continue
        doc = _read_html(r.text()) if r.type != "text/plain" else _Doc("", r.text().strip(), [], "")
        same = {final}
        if doc.canonical:
            try:
                link = canonical(urllib.parse.urljoin(final, doc.canonical))
            except ValueError:
                link = ""
            if link and _host(link) == _host(final):
                same.add(link)
        if same & read:
            continue
        read |= same
        count += 1
        meta: dict[str, Any] = {"site": _host(final)}
        if r.truncated:
            meta["truncated"] = True
        run.pages.append(Page(final, doc.title, doc.text, "page", meta))
        for href in doc.links:
            try:
                link = canonical(urllib.parse.urljoin(final, href))
            except ValueError:      # mailto:, javascript: and tel: links are not pages
                continue
            if link not in queued and _in_scope(link, scope) and not _asset(link):
                queued.add(link)
                queue.append(link)
        run.tick(final, min(len(queue), run.cap - count))


# ---- HTML to text


@dataclass
class _Doc:
    title: str
    text: str
    links: list[str]
    canonical: str


SKIP = frozenset(("script", "style", "noscript", "template", "svg", "nav", "header", "footer", "aside", "form",
                  "iframe", "button", "select"))
BLOCKS = frozenset(("p", "div", "section", "article", "main", "table", "tr", "blockquote", "dl", "dt", "dd",
                    "figure", "figcaption", "hr", "ul", "ol"))
VOID = frozenset(("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track",
                  "wbr"))


class _Reader(HTMLParser):
    """Collects a page's title, canonical link and links, and its text as Markdown headings, lists and paragraphs.

    Text inside `<main>` (or `role="main"`) and `<article>` is also kept apart, so the page's own content can
    be used without the parts around it.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.h1 = ""
        self.canonical = ""
        self.links: list[str] = []
        self.parts: list[str] = []
        self.main: list[str] = []
        self.article: list[str] = []
        self._skip = 0
        self._in_title = False
        self._in_h1 = False
        self._pre = 0
        self._lists = 0
        self._main = 0
        self._article = 0
        self._open: list[tuple[str, str]] = []   # (tag, "main" | "article" | ""), innermost last

    def _emit(self, text: str) -> None:
        self.parts.append(text)
        if self._main:
            self.main.append(text)
        if self._article:
            self.article.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split() and a.get("href"):
            self.canonical = a["href"]
        if tag == "title" and not self._skip:
            self._in_title = True
            return
        if tag in VOID:
            if tag in ("br", "hr") and not self._skip:
                self._emit("\n")
            return
        if tag in SKIP:
            self._skip += 1
            return
        region = "main" if tag == "main" or a.get("role") == "main" else "article" if tag == "article" else ""
        self._main += region == "main"
        self._article += region == "article"
        self._open.append((tag, region))
        if self._skip:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._emit("\n\n" + "#" * int(tag[1]) + " ")
            self._in_h1 = tag == "h1" and not self.h1
        elif tag == "li":
            self._emit("\n" + "  " * max(self._lists - 1, 0) + "- ")
        elif tag == "pre":
            self._pre += 1
            self._emit("\n\n")
        elif tag in BLOCKS:
            self._emit("\n\n")
        if tag in ("ul", "ol"):
            self._lists += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            return
        if tag in VOID:
            return
        if tag in SKIP:
            self._skip = max(self._skip - 1, 0)
            return
        if any(t == tag for t, _ in self._open):
            # pop to the matching tag, because HTML lets `<p>` and `<li>` go unclosed
            while self._open:
                t, region = self._open.pop()
                self._main -= region == "main"
                self._article -= region == "article"
                if t == tag:
                    break
        if self._skip:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._in_h1 = False
            self._emit("\n\n")
        elif tag == "pre":
            self._pre = max(self._pre - 1, 0)
            self._emit("\n\n")
        elif tag in ("ul", "ol"):
            self._lists = max(self._lists - 1, 0)
            self._emit("\n\n")
        elif tag in BLOCKS:
            self._emit("\n\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        if self._in_h1:
            self.h1 += data
        self._emit(data if self._pre else re.sub(r"\s+", " ", data))

    def text(self) -> str:
        """The main content when the page marks it, else everything outside the skipped parts."""
        raw = "".join(self.main or self.article or self.parts)
        lines = [line.rstrip() if line.startswith("    ") else line.strip() for line in raw.split("\n")]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


_MAIN = re.compile(r"<main\b[^>]*>(.*)</main>", re.I | re.S)
_ARTICLE = re.compile(r"<article\b[^>]*>.*?</article>", re.I | re.S)
_AROUND = re.compile(r"<(nav|header|footer|aside|form|script|style|noscript|svg|template)\b[^>]*>.*?</\1\s*>",
                     re.I | re.S)


@functools.cache
def _markitdown() -> Any | None:
    """A MarkItDown converter with plugins off, or None when the documents extra is not installed."""
    try:
        from markitdown import MarkItDown
    except ImportError:
        return None
    return MarkItDown(enable_plugins=False)


def _as_markdown(page_html: str) -> str:
    """The page's own content as Markdown through MarkItDown; "" when it is missing or fails."""
    converter = _markitdown()
    if converter is None:
        return ""
    main = _MAIN.search(page_html)
    articles = _ARTICLE.findall(page_html)
    body = main.group(1) if main else "".join(articles) if articles else page_html
    body = _AROUND.sub(" ", body)
    from markitdown import StreamInfo

    try:
        result = converter.convert_stream(io.BytesIO(f"<html><body>{body}</body></html>".encode()),
                                          stream_info=StreamInfo(extension=".html", mimetype="text/html",
                                                                 charset="utf-8"))
    except Exception:  # a page MarkItDown cannot convert falls back to the stdlib reader
        log.info("MarkItDown could not convert a page; using the plain reader")
        return ""
    return (result.markdown or "").strip()


def _read_html(page_html: str) -> _Doc:
    reader = _Reader()
    reader.feed(page_html)
    reader.close()
    title = re.sub(r"\s+", " ", reader.title or reader.h1).strip()
    return _Doc(title, _as_markdown(page_html) or reader.text(), reader.links, reader.canonical)


# ---- GitHub


def _github_token() -> str | None:
    """The person's GitHub token, or None; a locked keyring counts as none, so reading never prompts."""
    from ..core.settings import secrets

    try:
        return (secrets.get(TOKEN_SECRET, interactive=False) or "").strip() or None
    except secrets.SecretsLocked:
        return None


def _limit(r: _Response, token: bool) -> _RateLimited:
    reset = r.headers.get("X-RateLimit-Reset") if r.headers else None
    wait = r.headers.get("Retry-After") if r.headers else None
    if reset and reset.isdigit():
        at = int(reset)
    elif wait and wait.isdigit():
        at = int(time.time()) + int(wait)
    else:
        at = int(time.time()) + GITHUB_WINDOW_S
    when = time.strftime("%H:%M UTC", time.gmtime(at))
    words = f"GitHub's limit on requests is used up until {when}, so Hal stopped here. Try again after that"
    words += "." if token else ", or add a GitHub token in Settings, CV and documents, which raises the limit."
    return _RateLimited(words, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at)))


def _api(path: str, run: _Run) -> Any:
    """GitHub's answer to GET `path` as JSON; None for 404. Raises _RateLimited or LinkError."""
    headers = dict(GITHUB_HEADERS)
    if run.token:
        headers["Authorization"] = f"Bearer {run.token}"
    r = _open(API_BASE + path, headers=headers, cap=MAX_API_BYTES)
    limited = r.headers is not None and (r.headers.get("X-RateLimit-Remaining") == "0" or r.headers.get("Retry-After"))
    if r.status == 429 or (r.status == 403 and limited):
        raise _limit(r, bool(run.token))
    if r.status == 404:
        return None
    if r.status == 401:
        raise LinkError("GitHub did not accept your GitHub token. Check it in Settings, CV and documents.")
    if r.status >= 400:
        raise LinkError(f"GitHub answered with an error ({r.status}). Try again later.")
    if r.truncated:
        raise LinkError("GitHub sent more than Hal reads at once.")
    return json.loads(r.body.decode("utf-8", errors="replace"))


def _github(url: str, run: _Run) -> None:
    parts = [urllib.parse.unquote(p) for p in urllib.parse.urlsplit(url).path.split("/") if p]
    if not parts or parts[0] in GITHUB_OWN:
        raise LinkError("This is one of GitHub's own pages. Give the link to your profile or a repository.")
    if run.token is None:
        run.token = _github_token() or ""
    try:
        if run.github_stop is not None:
            raise run.github_stop
        if parts[0] == "orgs" and len(parts) > 1:
            _github_user(parts[1], run)
        elif len(parts) > 1:
            _github_one_repo(parts[0], parts[1].removesuffix(".git"), run)
        else:
            _github_user(parts[0], run)
    except _RateLimited as e:
        run.github_stop = e
        run.pages.append(Page(url, "", "", "repo", {"reset_at": e.reset_at}, str(e)))


def _github_one_repo(owner: str, name: str, run: _Run) -> None:
    data = _api(f"/repos/{_q(owner)}/{_q(name)}", run)
    if not data:
        raise LinkError("GitHub has no public repository at this link. Check it was copied whole.")
    run.pages.append(_repo_page(data, run))
    run.tick(run.pages[-1].url)


def _github_user(login: str, run: _Run) -> None:
    profile = _api(f"/users/{_q(login)}", run)
    if not profile:
        raise LinkError(f"GitHub has no user called {login}. Check the link was copied whole.")
    run.pages.append(_profile_page(profile))
    run.tick(run.pages[-1].url, min(int(profile.get("public_repos") or 0), run.cap - 1))
    repos: list[dict] = []
    page = 1
    while len(repos) < run.cap - 1:
        batch = _api(f"/users/{_q(login)}/repos?type=owner&sort=pushed&per_page={GITHUB_PER_PAGE}&page={page}",
                     run) or []
        repos += [r for r in batch if not r.get("fork")]
        if len(batch) < GITHUB_PER_PAGE:
            break
        page += 1
    repos = repos[:run.cap - 1]
    for n, repo in enumerate(repos, 1):
        run.pages.append(_repo_page(repo, run))
        run.tick(run.pages[-1].url, len(repos) - n)


def _q(part: str) -> str:
    return urllib.parse.quote(part, safe="")


def _profile_page(p: dict) -> Page:
    keys = ("login", "name", "bio", "company", "location", "blog", "public_repos", "followers", "created_at",
            "html_url")
    meta = {k: p.get(k) for k in keys}
    name = p.get("name") or p.get("login") or ""
    lines = [f"# {name}", ""]
    if p.get("bio"):
        lines += [p["bio"], ""]
    for label, key in (("GitHub", "login"), ("Works at", "company"), ("Lives in", "location"),
                       ("Website", "blog"), ("Public repositories", "public_repos")):
        if p.get(key) not in (None, ""):
            lines.append(f"- {label}: {p[key]}")
    return Page(p.get("html_url") or f"{GITHUB_WEB}/{p.get('login')}", name, "\n".join(lines).strip(),
                "profile", meta)


def _readme(full_name: str, run: _Run) -> str:
    data = _api(f"/repos/{full_name}/readme", run)
    if not data or data.get("encoding") != "base64":
        return ""
    try:
        return base64.b64decode(data.get("content") or "").decode("utf-8", errors="replace").strip()
    except (binascii.Error, ValueError):
        return ""


def _repo_page(r: dict, run: _Run) -> Page:
    full = r.get("full_name") or ""
    path = "/".join(_q(p) for p in full.split("/"))
    languages = list((_api(f"/repos/{path}/languages", run) or {}).keys())
    readme = _readme(path, run)
    meta = {"full_name": full, "description": r.get("description") or "", "topics": list(r.get("topics") or []),
            "languages": languages, "stars": int(r.get("stargazers_count") or 0), "homepage": r.get("homepage") or "",
            "pushed_at": r.get("pushed_at") or "", "html_url": r.get("html_url") or "",
            "archived": bool(r.get("archived"))}
    lines = [f"# {r.get('name') or full}", ""]
    if meta["description"]:
        lines += [meta["description"], ""]
    for label, value in (("Languages", ", ".join(languages)), ("Topics", ", ".join(meta["topics"])),
                         ("Stars", str(meta["stars"])), ("Website", meta["homepage"]),
                         ("Last changed", meta["pushed_at"][:10])):
        if value:
            lines.append(f"- {label}: {value}")
    if readme:
        lines += ["", "## README", "", readme]
    return Page(meta["html_url"] or f"{GITHUB_WEB}/{full}", r.get("name") or full, "\n".join(lines).strip(),
                "repo", meta)


# ---- Google Docs and Slides


def _google(url: str, run: _Run) -> None:
    export = export_url(url)
    if export is None:
        raise LinkError(GOOGLE_OTHER)
    r = _open(export)
    if r.status == 404:
        raise LinkError("Google has no document at this link. Check it was copied whole.")
    if r.status in (401, 403) or _host(r.url).startswith("accounts.") or r.type == "text/html":
        raise LinkError(GOOGLE_PRIVATE)
    if r.status >= 400:
        raise LinkError(f"Google answered with an error ({r.status}). Try again later.")
    text = r.body.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").strip()
    name = (r.headers.get_filename() if r.headers else None) or ""
    title = re.sub(r"\.txt$", "", name, flags=re.I).strip() or (text.split("\n", 1)[0].strip() if text else "")
    meta: dict[str, Any] = {"export": export, "format": "txt"}
    if r.truncated:
        meta["truncated"] = True
    run.pages.append(Page(url, title, text, "doc", meta))
    run.tick(url)
