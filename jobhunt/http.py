"""Stdlib HTTP helpers with an identifying user agent, timeouts and retries."""
import json
import time
import urllib.error
import urllib.request

UA = "jobhunt/0.1 (personal job search; contact firstname.lastname@example.com)"


class NotFound(Exception):
    pass


def get(url: str, timeout: float = 20, retries: int = 1, headers: dict | None = None) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
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
    return json.loads(get(url, **kw))
