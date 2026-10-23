"""The setup guide's Email step: try a Gmail app password before keeping it.

The password is checked by signing in to Gmail's IMAP server read-only and signing out again; only a
password Gmail accepted is saved (in the secrets store, never in a setting), so a typing slip can never
leave Hal with a password that fails at every email check. The address is saved with it, by `setup`, only
when it differs from the one already set.

Design notes:
- `IMAP` is the class used to connect, so tests put a fake server in its place; nothing here reaches the
  network in a test.
- The words never repeat the password, and the log names only the kind of failure.
"""
from __future__ import annotations

import imaplib
import logging
import re
from dataclasses import dataclass

from ..core import settings
from ..core.settings import secrets
from ..core.settings.registry import SettingError

log = logging.getLogger(__name__)

IMAP = imaplib.IMAP4_SSL
IMAP_HOST = "imap.gmail.com"
LOGIN_TIMEOUT_S = 20           # Gmail answers a sign-in in about a second; this bounds a stalled network
APP_PASSWORD_LETTERS = 16      # Google's app passwords are 16 letters, shown in four groups of four
SECRET = "email.app_password"

CONNECTED = "Connected. Hal can read your Gmail (read only)."
WRONG = ("Gmail did not accept that app password. Check that 2-Step Verification is on, copy the 16 letters "
         "again, and press Test.")
SHORT = "An app password is 16 letters. Copy all of them from Google's page, then press Test."
NO_ADDRESS = "Type your Gmail address first."
OFFLINE = "Hal could not reach Gmail. Check this computer is online, then press Test again."
LOCKED = ("Gmail accepted the password, but Hal could not save it: your computer's password store is locked. "
          "Unlock it, then press Test again.")


@dataclass(frozen=True)
class Result:
    """What the test found, in words for the page."""

    ok: bool
    words: str
    field: str | None = None

    def as_dict(self) -> dict:
        """The API shape: `{ok, words}`, with the field to mark when one is wrong."""
        return {"ok": self.ok, "words": self.words, "field": self.field}


def _clean(password: str) -> str:
    return re.sub(r"\s+", "", password or "")


def sign_in_works(address: str, password: str) -> bool:
    """True when Gmail accepts the address and app password; raises `OSError` when Gmail cannot be reached."""
    con = IMAP(IMAP_HOST, timeout=LOGIN_TIMEOUT_S)
    try:
        con.login(address, password)
    except imaplib.IMAP4.error:
        return False
    finally:
        try:
            con.logout()
        except (OSError, imaplib.IMAP4.error):
            log.debug("the test connection to Gmail closed itself")
    return True


def try_and_save(address: str, password: str) -> Result:
    """Tries the password and saves it, with the address, only when Gmail accepted it."""
    address = (address or "").strip()
    password = _clean(password)
    if not address:
        return Result(False, NO_ADDRESS, "address")
    try:
        settings.check("email.address", address)
    except SettingError as e:
        return Result(False, str(e), "address")
    if len(password) != APP_PASSWORD_LETTERS or not password.isalpha():
        return Result(False, SHORT, "password")
    try:
        accepted = sign_in_works(address, password)
    except OSError as e:     # a timeout is an OSError too
        log.info("the Gmail test could not connect: %s", type(e).__name__)
        return Result(False, OFFLINE)
    if not accepted:
        return Result(False, WRONG, "password")
    try:
        secrets.put(SECRET, password)
    except secrets.SecretsLocked:
        return Result(False, LOCKED)
    if (settings.get("email.address") or "").strip().lower() != address.lower():
        settings.set("email.address", address, by="setup")
    return Result(True, CONNECTED)
