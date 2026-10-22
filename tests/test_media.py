"""Audio and images the assistant makes: WAV recordings, PNG screenshots, tray icons."""

import io
import wave
import zlib

from assistant.icons import dot
from assistant.llm import RATE, long_enough, wav
from assistant.png import encode_png


def test_wav_wraps_pcm() -> None:
    pcm = bytes(range(256)) * 4
    with wave.open(io.BytesIO(wav(pcm, RATE)), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, RATE)
        assert w.readframes(w.getnframes()) == pcm


def test_long_enough_skips_taps() -> None:
    assert not long_enough(bytes(2 * RATE // 10))  # 0.1 s: an accidental tap
    assert long_enough(bytes(2 * RATE // 2))  # 0.5 s


def decode_png(data: bytes) -> tuple[int, int, bytes]:
    """Width, height and RGB rows (each with its filter byte) of a PNG we wrote."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, dict[bytes, bytes]()
    while pos < len(data):
        length = int.from_bytes(data[pos : pos + 4], "big")
        kind, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        crc = int.from_bytes(data[pos + 8 + length : pos + 12 + length], "big")
        assert crc == zlib.crc32(kind + body)
        chunks[kind] = chunks.get(kind, b"") + body
        pos += 12 + length
    header = chunks[b"IHDR"]
    width, height, depth, color = int.from_bytes(header[0:4], "big"), int.from_bytes(header[4:8], "big"), header[8], header[9]
    assert (depth, color) == (8, 2)  # 8-bit RGB
    assert b"IEND" in chunks
    return width, height, zlib.decompress(chunks[b"IDAT"])


def test_png_turns_bgra_into_rgb() -> None:
    blue, red = bytes([255, 0, 0, 255]), bytes([0, 0, 255, 255])  # BGRA
    width, height, rows = decode_png(encode_png(2, 1, blue + red))
    assert (width, height) == (2, 1)
    assert rows == b"\x00" + bytes([0, 0, 255, 255, 0, 0])  # filter 0, then RGB blue, RGB red


def test_png_rows_each_get_a_filter_byte() -> None:
    _, _, rows = decode_png(encode_png(3, 2, bytes(3 * 2 * 4)))
    assert len(rows) == 2 * (1 + 3 * 3)
    assert rows[0] == 0 and rows[10] == 0


def test_dot_icon_is_a_coloured_circle_on_transparent() -> None:
    size = 32
    pixels = dot(size, (200, 40, 40))
    assert len(pixels) == size * size * 4

    def at(x: int, y: int) -> tuple[int, int, int, int]:
        i = (y * size + x) * 4
        b, g, r, a = pixels[i : i + 4]
        return r, g, b, a

    assert at(0, 0)[3] == 0  # corner: transparent
    r, g, b, a = at(16, 16)
    assert a == 255
    assert r > g and r > b  # centre: the colour (premultiplied, so it may be darker)
