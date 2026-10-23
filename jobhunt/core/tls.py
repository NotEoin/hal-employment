"""How Hal checks the certificates of the websites it reaches over HTTPS.

On Windows, Python's `ssl` trusts only the root certificates already copied into the computer's store, and
a fresh Windows copies most of them in only when Windows itself first checks a site that needs one (its
automatic root update). So on a new laptop every job site, ntfy and Gmail failed with
`CERTIFICATE_VERIFY_FAILED` until Edge happened to visit them (seen on a fresh Windows 11 install,
2026-10-23). `truststore` makes Python ask Windows to check each certificate, which fetches a missing root
the same way Edge does and keeps any root the person's antivirus or employer added.

Linux keeps Python's own check: uv's Python reads the system's certificate folder, which is complete.
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)


def use_system_trust(platform: str = sys.platform) -> bool:
    """On Windows, makes every HTTPS connection in this process trust what Windows trusts; True when it did.

    Must run before the first HTTPS request: `urllib` builds its TLS settings once, on first use.
    """
    if platform != "win32":
        return False
    try:
        import truststore
    except ImportError:
        # a checkout synced before truststore was added; the next `uv sync` brings it
        log.error("truststore is missing, so websites may fail with CERTIFICATE_VERIFY_FAILED; run uv sync")
        return False
    truststore.inject_into_ssl()
    return True
