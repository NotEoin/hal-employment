"""Keeps the test run on this computer: a lookup or connection that would leave it fails at once.

A test that reached a real site passed or failed with that site: the cover letter's company research asked
DuckDuckGo from inside `docs.prepare`, and on a slow CI runner the search outlived the test's wait. Here,
looking up any name but this computer's own raises `NoLookup` (a `socket.gaierror`) and connecting a stream
socket to a public address raises `NoRoute` (an `OSError`), so code under test sees exactly what it sees on a
computer with no network, without waiting for a timeout.

Allowed: loopback, private and other non-public addresses (a test proves the server refuses this computer's
own network address), `localhost` and this computer's own name, Unix sockets, and datagram sockets, whose
`connect` sends nothing (a test finds the network address that way). Child processes, the browser among
them, are not covered; their pages are served from 127.0.0.1.
"""
from __future__ import annotations

import errno
import ipaddress
import socket
from typing import Any

_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_installed = False


class NoLookup(socket.gaierror):
    """A test asked for the address of a name off this computer."""


class NoRoute(OSError):
    """A test tried to connect to a public address."""


def _own_names() -> set[str]:
    return {"localhost", socket.gethostname().lower()}


def _is_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


def local_name(host: Any) -> bool:
    """True when looking up `host` stays on this computer: an address, `localhost` or this computer's name."""
    if host is None:
        return True
    name = (host.decode() if isinstance(host, bytes) else str(host)).strip("[]").lower()
    return not name or _is_address(name) or name in _own_names() or name.endswith(".localhost")


def public_address(address: Any) -> bool:
    """True when `address` (a socket address tuple) is on the public internet."""
    if not isinstance(address, tuple) or not address:
        return False
    host = str(address[0]).strip("[]").split("%", 1)[0]
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return not local_name(host)


def _getaddrinfo(host, *args, **kwargs):
    if not local_name(host):
        raise NoLookup(socket.EAI_NONAME, f"the tests stay on this computer, so {host!r} is not looked up")
    return _real_getaddrinfo(host, *args, **kwargs)


def _leaves(sock: socket.socket, address: Any) -> bool:
    return sock.family in (socket.AF_INET, socket.AF_INET6) and sock.type == socket.SOCK_STREAM \
        and public_address(address)


def _connect(self: socket.socket, address: Any) -> None:
    if _leaves(self, address):
        raise NoRoute(errno.ENETUNREACH, f"the tests stay on this computer, so {address!r} is not reached")
    _real_connect(self, address)


def _connect_ex(self: socket.socket, address: Any) -> int:
    if _leaves(self, address):
        return errno.ENETUNREACH
    return _real_connect_ex(self, address)


def install() -> None:
    """Puts the guard in place for the rest of the process; calling it again changes nothing."""
    global _installed
    if _installed:
        return
    socket.getaddrinfo = _getaddrinfo
    socket.socket.connect = _connect
    socket.socket.connect_ex = _connect_ex
    _installed = True
