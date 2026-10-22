"""Settings group "Privacy and data": display rows built from `GET /privacy`; nothing here is stored."""

from __future__ import annotations

from jobhunt.core.settings.registry import Setting, register

register(
    Setting("privacy.where", "info", None, "Where your data is kept", order=10),
    Setting("privacy.leaves", "list", None, "What leaves this computer", order=20),
    Setting("privacy.export", "button", None, "Export everything", action="privacy.export", order=30,
            help="Make a zip. Passwords and private answers are left out."),
    Setting("privacy.delete", "danger", None, "Delete all your data from this computer", action="privacy.delete",
            help="Delete everything. Hal's own program files are not touched.", order=40),
)
