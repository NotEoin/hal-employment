"""Hal's eye as a Windows icon file, drawn with the standard library for the Windows shortcuts.

Windows shortcuts cannot show the app's `icon.svg`, and without an icon they show `pythonw.exe`'s Python
logo, which the person does not know as Hal (seen on a fresh Windows 11 install, 2026-10-23). The
file is drawn at install time, so no picture file is kept in the repository and the eye always matches
`jobhunt/web/static/app/icon.svg`: a dark ring around a radial gradient of gold, red and near black.

Each size is a PNG inside the `.ico`, which Windows reads from Vista on. Edges are smoothed by sampling
each pixel `SAMPLES` x `SAMPLES` times.
"""
from __future__ import annotations

import struct
import zlib
from itertools import pairwise
from pathlib import Path

SIZES = (16, 32, 48, 256)
SAMPLES = 3
VIEW = 64.0                                   # icon.svg's viewBox
RING_R, EYE_R = 31.0, 27.0                    # the ring and the eye in icon.svg
RING = (0x11, 0x11, 0x11)
# icon.svg's gradient stops, as (offset, colour): gold, red, near black
STOPS = ((0.0, (0xFF, 0xD1, 0x66)), (0.15, (0xFF, 0xD1, 0x66)), (0.30, (0xC8, 0x32, 0x2A)),
         (0.58, (0xC8, 0x32, 0x2A)), (0.72, (0x1B, 0x0B, 0x0A)), (1.0, (0x1B, 0x0B, 0x0A)))
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ICO_HEADER = struct.Struct("<HHH")            # reserved, type 1 (icon), count
ICO_ENTRY = struct.Struct("<BBBBHHII")        # width, height, colours, reserved, planes, bits, size, offset


def _gradient(t: float) -> tuple[int, int, int]:
    for (a, ca), (b, cb) in pairwise(STOPS):
        if t <= b:
            f = 0.0 if b == a else (t - a) / (b - a)
            return tuple(round(x + (y - x) * f) for x, y in zip(ca, cb, strict=True))
    return STOPS[-1][1]


def _pixel(x: float, y: float) -> tuple[int, int, int, int]:
    """The colour at a point of the 64-unit view, with alpha 0 outside the ring."""
    d = ((x - VIEW / 2) ** 2 + (y - VIEW / 2) ** 2) ** 0.5
    if d > RING_R:
        return (0, 0, 0, 0)
    if d > EYE_R:
        return (*RING, 255)
    return (*_gradient(d / EYE_R), 255)


def _rgba(size: int) -> bytes:
    """`size` x `size` RGBA rows, each led by PNG's filter byte 0."""
    unit = VIEW / size
    rows = bytearray()
    for py in range(size):
        rows.append(0)
        for px in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    r, g, b, a = _pixel((px + (sx + 0.5) / SAMPLES) * unit, (py + (sy + 0.5) / SAMPLES) * unit)
                    acc[0] += r * a
                    acc[1] += g * a
                    acc[2] += b * a
                    acc[3] += a
            alpha = acc[3]
            if alpha:
                rows += bytes((round(acc[0] / alpha), round(acc[1] / alpha), round(acc[2] / alpha),
                               round(alpha / SAMPLES ** 2)))
            else:
                rows += b"\0\0\0\0"
    return bytes(rows)


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png(size: int) -> bytes:
    """Hal's eye as a `size`-pixel square PNG."""
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)     # 8-bit RGBA
    return (PNG_SIGNATURE + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(_rgba(size), 9))
            + _chunk(b"IEND", b""))


def ico() -> bytes:
    """Hal's eye as a Windows `.ico` holding every size in `SIZES`."""
    images = [png(s) for s in SIZES]
    offset = ICO_HEADER.size + ICO_ENTRY.size * len(images)
    out = bytearray(ICO_HEADER.pack(0, 1, len(images)))
    for size, image in zip(SIZES, images, strict=True):
        side = 0 if size >= 256 else size                           # 0 means 256 in an icon entry
        out += ICO_ENTRY.pack(side, side, 0, 0, 1, 32, len(image), offset)
        offset += len(image)
    for image in images:
        out += image
    return bytes(out)


def write_ico(path: Path) -> Path:
    """Writes Hal's `.ico` to `path` and returns it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ico())
    return path
