"""Stdlib HTTP helpers with an identifying user agent, timeouts and retries.

The user agent names the app and its version only. A contact address is added only when the person sets
`sites.contact`, because every job site sees it.
"""
import json
import time
import urllib.error
import urllib.request

from . import __version__

PRODUCT = f"HalEmployment/{__version__}"


class NotFound(Exception):
    """The page is gone (HTTP 404 or 410)."""


def user_agent() -> str:
    """What job sites see: the app and version, and the `sites.contact` address only if the person set one."""
    from .core import settings

    contact = (settings.get("sites.contact") or "").strip()
    return f"{PRODUCT} (job search; contact {contact})" if contact else PRODUCT


def get(url: str, timeout: float = 20, retries: int = 1, headers: dict | None = None) -> str:
    """The page's text, retried once on a server error or a dropped connection; NotFound when it is gone."""
    req = urllib.request.Request(url, headers={"User-Agent": user_agent(), **(headers or {})})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                raise NotFound(url) from e
            if attempt == retries or e.code < 500 and e.code != 429:
                raise
            if e.code == 429:
                time.sleep(int(e.headers.get("Retry-After") or 10))
                continue
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries:
                raise
        time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def get_json(url: str, **kw):
    """The page parsed as JSON."""
    return json.loads(get(url, **kw))
