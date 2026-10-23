"""The setup guide's Phone alerts step: a private ntfy topic, its QR code, and a test alert.

The topic is the only thing that keeps the person's alerts private on the public ntfy.sh server, so it is
random, kept in the secrets store and never sent to the page as text: the page gets only the QR code to
scan. Going through the step again shows the same code, so a phone already subscribed keeps working; a
new topic is made only when none is saved or when the person asks for a new code.

Design notes:
- The QR code holds `https://ntfy.sh/<topic>`, which the phone's camera opens in the ntfy app (or ntfy's
  web page, which offers to subscribe).
- `URLOPEN` is the call that sends, so tests replace it; nothing here reaches the network in a test.
- Making a topic switches phone alerts on, by `setup`; going through the step again never changes that
  setting, so turning alerts off in Settings stays off.
"""
from __future__ import annotations

import io
import logging
import secrets as random
import urllib.request

import segno

from ..core import settings
from ..core.settings import secrets

log = logging.getLogger(__name__)

NTFY = "https://ntfy.sh/"
SECRET = "notify.ntfy_topic"
TOPIC_PREFIX = "hal-"
TOPIC_BYTES = 18                # 24 URL-safe characters: too many to guess (ntfy topics may be 64 long)
SEND_TIMEOUT_S = 10
QR_SCALE = 4                    # pixels per module; the page scales the picture to 150 px
URLOPEN = urllib.request.urlopen

TEST_TITLE = "Hal Employment"
TEST_BODY = "This is a test from Hal. Phone alerts work."
SENT = "Sent. Did your phone buzz?"
NOT_SENT = "Hal could not reach ntfy. Check this computer is online, then press Send a test again."
NO_TOPIC = "Make the code first, then scan it with your phone."


def _new_topic() -> str:
    return TOPIC_PREFIX + random.token_urlsafe(TOPIC_BYTES).replace("_", "").replace("-", "").lower()


def _saved_topic() -> str | None:
    try:
        return secrets.get(SECRET, interactive=True)
    except secrets.SecretsLocked:
        return None


def qr_svg(topic: str) -> str:
    """The QR code for subscribing to `topic`, as SVG text that draws on its own as an image.

    The page shows it as an `<img>`, and an SVG image without the SVG namespace draws nothing, so this is
    not segno's `svg_inline`, which leaves the namespace out (the broken picture of the Windows 11 install test).
    """
    out = io.BytesIO()
    segno.make(NTFY + topic, error="m").save(out, kind="svg", xmldecl=False, svgns=True, nl=False,
                                            scale=QR_SCALE, dark="#111111", light="#ffffff")
    return out.getvalue().decode("ascii")


def topic_qr(*, new: bool = False) -> dict:
    """The QR code of the saved topic, making and saving a topic first when none is saved or `new` is asked."""
    topic = None if new else _saved_topic()
    made = False
    if not topic:
        topic = _new_topic()
        secrets.put(SECRET, topic)
        made = True
        if not settings.get("notify.phone"):
            settings.set("notify.phone", True, by="setup")
    return {"qr_svg": qr_svg(topic), "new": made}


def send_test() -> dict:
    """Sends a test alert to the saved topic; `{ok, words}`."""
    topic = _saved_topic()
    if not topic:
        return {"ok": False, "words": NO_TOPIC}
    request = urllib.request.Request(NTFY + topic, data=TEST_BODY.encode("utf-8"),
                                     headers={"Title": TEST_TITLE, "Tags": "briefcase"})
    try:
        with URLOPEN(request, timeout=SEND_TIMEOUT_S):
            pass
    except OSError as e:
        log.info("the test alert was not sent: %s", type(e).__name__)
        return {"ok": False, "words": NOT_SENT}
    return {"ok": True, "words": SENT}
