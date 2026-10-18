"""PNG files from screen pixels, with only the standard library."""

from __future__ import annotations

import struct
import zlib


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def encode_png(width: int, height: int, bgra: bytes) -> bytes:
    """An RGB PNG from BGRA pixels, top row first (what a screen capture gives)."""
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]  # slicing runs in C: fast
    stride = width * 3
    rows = b"".join(b"\x00" + rgb[y * stride : (y + 1) * stride] for y in range(height))  # filter 0 per row
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB, no interlace
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(rows, 6)) + _chunk(b"IEND", b"")
