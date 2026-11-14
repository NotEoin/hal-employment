"""The one MarkItDown converter that the record and the web ingest share.

It is built once, with plugins off, so a document is never fetched from or sent anywhere. A converter that
cannot load is not an error for the callers: each has its own plain reader to fall back to.
"""
import functools
import logging
from typing import Any

log = logging.getLogger(__name__)


@functools.cache
def markitdown_converter() -> Any | None:
    """The shared MarkItDown converter, or None when the documents extra is missing or will not load.

    The reason is logged once, when it is first asked for; the answer is then kept for the whole run.
    """
    try:
        from markitdown import MarkItDown

        return MarkItDown(enable_plugins=False)
    except Exception as e:  # a missing extra and a broken C++ runtime both mean: read the plain way
        log.info("MarkItDown is not available (%s); documents are read without it", e)
        return None
