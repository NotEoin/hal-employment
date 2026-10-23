"""Desktop pop-ups, phone alerts and, for the owner's Hal, spoken alerts, each as the person's settings say.

Pop-ups go through the platform layer (notify-send on Linux, a toast on Windows) when `notify.desktop` is
on. Phone alerts go to ntfy.sh only when `notify.phone` is on and a topic is saved in the secrets store;
the topic is read with `interactive=False`, so a locked keyring never prompts from the background.
Outside urgent alerts, nothing pops up or buzzes during `notify.quiet_hours`. Urgent alerts are also
spoken by the Hal add-on's `addon.speak_command` when the add-on is on.

Every alert is also published as a `notify` event, so an open window shows it as a toast and, when the
person allowed it, a browser notification. While a window opened from another computer is showing Hal, the
desktop pop-up is held back: the person is at the other computer, and a pop-up on the desktop
would only tell whoever sits there.
"""
import logging
import urllib.request

from .core import clock

log = logging.getLogger(__name__)

NTFY = "https://ntfy.sh/"
NTFY_TIMEOUT_S = 10
TOPIC_SECRET = "notify.ntfy_topic"


def _get(key: str):
    from .core import settings

    return settings.get(key)


def _minutes(hm: str) -> int:
    hour, minute = str(hm).split(":")
    return int(hour) * 60 + int(minute)


def quiet_now(now=None) -> bool:
    """True inside `notify.quiet_hours` (which may run past midnight); never when they are off."""
    hours = _get("notify.quiet_hours")
    if not hours or len(hours) != 2:
        return False
    local = clock.local(now)
    at, start, end = local.hour * 60 + local.minute, _minutes(hours[0]), _minutes(hours[1])
    return start <= at < end if start <= end else (at >= start or at < end)


def _topic() -> str | None:
    from .core.settings import secrets

    try:
        return secrets.get(TOPIC_SECRET, interactive=False)
    except secrets.SecretsLocked:
        log.warning("the keyring is locked, so the phone alert was not sent")
        return None


def _phone(title: str, body: str, url: str | None, urgent: bool) -> None:
    topic = _topic()
    if not topic:
        return
    headers = {"Title": title.encode("utf-8"), "Tags": "briefcase"}
    if url:
        headers["Click"] = url
    if urgent:
        headers["Priority"] = "high"
    try:
        urllib.request.urlopen(urllib.request.Request(NTFY + topic, data=body.encode("utf-8"), headers=headers),
                               timeout=NTFY_TIMEOUT_S)
    except OSError as e:
        log.warning("the phone alert was not sent: %s", e)


def _away() -> bool:
    """True while a window from another computer is showing Hal, so the desktop pop-up goes there instead."""
    from .remote import sessions

    try:
        return sessions.window_open()
    except OSError as e:
        log.warning("could not tell whether a window from another computer is open: %s", e)
        return False


def notify(title: str, body: str, url: str | None = None, urgent: bool = False) -> None:
    """Sends a desktop and phone alert as the settings say; urgent alerts are also spoken with the Hal add-on."""
    from .core import events

    held = not urgent and quiet_now()
    events.publish("notify", title=title, body=body, urgent=urgent)
    if _get("notify.desktop") and not held and not _away():
        from .core import platform

        try:
            platform.current().notify(title, body, urgent=urgent)
        except OSError as e:
            log.warning("the pop-up was not shown: %s", e)
    if _get("notify.phone") and not held:
        _phone(title, body, url, urgent)
    if urgent:
        from .addons import hal

        hal.speak(f"{title}. {body}")
