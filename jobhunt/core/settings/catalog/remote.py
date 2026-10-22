"""Settings group "Use Hal from another computer": the SSH steps, the launcher and open sessions."""

from __future__ import annotations

from jobhunt.core.settings.registry import Setting, register

register(
    Setting("remote.how", "info", None, "How to use Hal from another computer", order=10,
            help="For people comfortable with a terminal."),
    Setting("remote.launcher", "button", None, "Download the launcher for your other computer",
            action="remote.launcher", order=20, help="Linux and Mac, or Windows."),
    Setting("remote.sessions", "list", None, "Windows open from another computer", order=30),
)
