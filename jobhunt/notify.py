"""Desktop and phone alerts. Phone push goes through ntfy.sh when a topic is set."""
import subprocess
import urllib.request

from .config import CONFIG


def notify(title: str, body: str, url: str | None = None, urgent: bool = False) -> None:
    try:
        subprocess.run(["notify-send", "-a", "jobhunt", *(["-u", "critical"] if urgent else []),
                        title, body], check=False, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    topic = CONFIG.get("ntfy_topic")
    if topic:
        headers = {"Title": title.encode("utf-8"), "Tags": "briefcase"}
        if url:
            headers["Click"] = url
        if urgent:
            headers["Priority"] = "high"
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), headers=headers), timeout=10)
        except OSError:
            pass
