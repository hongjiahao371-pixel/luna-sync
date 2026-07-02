#!/usr/bin/env python3
import argparse
import struct
import zlib
from pathlib import Path


def chunk(kind, data):
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def make_png(path, size=256):
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            dx = x - size / 2
            dy = y - size / 2
            r = (dx * dx + dy * dy) ** 0.5
            if r < size * 0.32:
                color = (255, 255, 255, 255)
            elif r < size * 0.42:
                color = (35, 152, 122, 255)
            else:
                color = (18, 26, 32, 255)
            row.extend(color)
        rows.append(bytes(row))
    raw = b"".join(rows)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(png)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    args = parser.parse_args()
    make_png(args.output)


if __name__ == "__main__":
    main()
