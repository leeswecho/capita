"""Read the JRC GHSL GHS-POP 30 arc-second GeoTIFF and sum it onto the tile grid.

The file is a BigTIFF (43202 x 21384 float64 cells, 256 x 256 tiles, LZW-compressed) that Pillow
can't open directly. Each compressed tile is wrapped in a minimal ordinary TIFF that calls it an
8-bit 2048 x 256 strip, so Pillow's LZW decoder unpacks the bytes, and NumPy reads them as float64.
"""
import io
import os
import struct
import zipfile

import numpy as np
from PIL import Image

import grid

HERE = os.path.dirname(os.path.abspath(__file__))
ZIP = os.path.join(HERE, 'data', 'GHS_POP_E2020_GLOBE_R2023A_4326_30ss_V1_0.zip')
TIF = os.path.join(HERE, 'data', 'GHS_POP_E2020_GLOBE_R2023A_4326_30ss_V1_0.tif')


def _read_ifd(f):
    head = f.read(16)
    assert head[:4] == b'II+\x00', 'expected a little-endian BigTIFF'
    f.seek(struct.unpack('<Q', head[8:16])[0])
    tags = {}
    for _ in range(struct.unpack('<Q', f.read(8))[0]):
        tag, typ, cnt = struct.unpack('<HHQ', f.read(12))
        tags[tag] = (typ, cnt, f.read(8))
    return tags


def _values(f, tags, tag):
    typ, cnt, raw = tags[tag]
    fmt, size = {3: ('H', 2), 4: ('I', 4), 12: ('d', 8), 16: ('Q', 8)}[typ]
    if cnt * size > 8:
        f.seek(struct.unpack('<Q', raw)[0])
        raw = f.read(cnt * size)
    return struct.unpack(f'<{cnt}{fmt}', raw[:cnt * size])


def _wrap_strip(data, width, height):
    """A minimal 8-bit greyscale TIFF whose single strip is `data` (LZW)."""
    entries = [(256, width), (257, height), (258, 8), (259, 5), (262, 1), (273, None), (277, 1),
               (278, height), (279, len(data))]
    data_off = 8 + 2 + 12 * len(entries) + 4
    out = bytearray(b'II*\x00' + struct.pack('<IH', 8, len(entries)))
    for tag, v in entries:
        v = data_off if tag == 273 else v
        out += struct.pack('<HHII', tag, 4, 1, v)
    return bytes(out + b'\0\0\0\0' + data)


def population_grid():
    """People per tile as a (HEIGHT, WIDTH) float64 array, plus (south, north) latitude coverage."""
    if not os.path.exists(TIF):
        with zipfile.ZipFile(ZIP) as z:
            z.extract(os.path.basename(TIF), os.path.dirname(TIF))
    with open(TIF, 'rb') as f:
        tags = _read_ifd(f)
        width, height = _values(f, tags, 256)[0], _values(f, tags, 257)[0]
        assert _values(f, tags, 258)[0] == 64 and _values(f, tags, 259)[0] == 5 and _values(f, tags, 339)[0] == 3
        tw, th = _values(f, tags, 322)[0], _values(f, tags, 323)[0]
        offs, sizes = _values(f, tags, 324), _values(f, tags, 325)
        dx, dy = _values(f, tags, 33550)[:2]
        x0, y0 = _values(f, tags, 33922)[3:5]
        across = -(-width // tw)

        # Tile row/col of every source cell, from its centre. Cells whose centres fall outside
        # -180..180 (the raster is two columns wider than the globe) are dropped, not wrapped,
        # so nothing is counted twice.
        lon_c = x0 + (np.arange(width) + 0.5) * dx
        lat_c = y0 - (np.arange(height) + 0.5) * dy
        col_of = np.floor((lon_c + 180) * grid.PER_DEG).astype(np.int64)
        row_of = (grid.HEIGHT - 1 - np.floor((lat_c + 90) * grid.PER_DEG)).astype(np.int64)
        keep_col = (lon_c >= -180) & (lon_c < 180)

        out = np.zeros(grid.COUNT, dtype=np.float64)
        for k, (off, size) in enumerate(zip(offs, sizes)):
            r0, c0 = (k // across) * th, (k % across) * tw
            f.seek(off)
            raw = Image.open(io.BytesIO(_wrap_strip(f.read(size), tw * 8, th))).tobytes()
            v = np.frombuffer(raw, '<f8').reshape(th, tw)
            r1, c1 = min(r0 + th, height), min(c0 + tw, width)
            v = v[:r1 - r0, :c1 - c0]
            cols = slice(c0, c1)
            mask = (v > 0) & keep_col[cols][None, :]
            if not mask.any():
                continue
            idx = row_of[r0:r1, None] * grid.WIDTH + col_of[cols][None, :]
            out += np.bincount(idx[mask], weights=v[mask], minlength=grid.COUNT)
    return out.reshape(grid.HEIGHT, grid.WIDTH), (y0 - height * dy, y0)
