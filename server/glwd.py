"""Wetland share of every 2.5' analysis pixel, from the Global Lakes and Wetlands Database v2 (GLWD v2).

GLWD v2 (Lehner et al. 2025, CC BY 4.0) maps 33 lake and wetland classes at 15 arc-seconds. Its
"combined classes" package has, per 15" cell, the main wetland class (GLWD_v2_0_main_class.tif) and the
share of the cell that is wetland of any kind (GLWD_v2_0_area_pct.tif). Both are BigTIFFs of 86400 x 33600
uint8 cells (84N..56S), 128 x 128 tiles, LZW; they are read with the helpers in ghsl.py.

A cell counts as wetland when its main class is a vegetated wetland (WETLAND_CLASSES); its wetland share is
then its wetland percentage. Ten by ten GLWD cells make one 2.5' pixel of gen_map.py's analysis grid
(8640 x 4320), whose wetland percentage is the mean over those 100 cells. The result is cached in
data/glwd_wetlands_2p5min.bin.
"""
import io
import os
import zipfile

import numpy as np
from PIL import Image

from ghsl import _read_ifd, _values, _wrap_strip

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
ZIP = os.path.join(DATA, 'GLWD_v2_0_combined_classes_tif.zip')
MAIN = os.path.join(DATA, 'GLWD_v2_0_main_class.tif')
PCT = os.path.join(DATA, 'GLWD_v2_0_area_pct.tif')
CACHE = os.path.join(DATA, 'glwd_wetlands_2p5min.bin')
SOURCE_URL = 'https://doi.org/10.6084/m9.figshare.28519994'

# Vegetated wetlands where water stands: lacustrine (8-9), riverine regularly or seasonally flooded (10-13),
# palustrine regularly flooded (16-17), peatlands (22-27), mangrove (28), saltmarsh (29), large river delta
# (30) and other coastal wetland (31). Not counted: open water (1-7, already water in the satellite image);
# "seasonally saturated" soils (14-15, 18-19), which in Europe are mostly farmland and towns (they made
# Paris 30% wetlands); ephemeral wetlands (20-21, dry most of the time); salt pans (32); rice paddies (33).
WETLAND_CLASSES = [8, 9, 10, 11, 12, 13, 16, 17] + list(range(22, 32))
CELLS = 10                     # GLWD cells per analysis pixel side (15" -> 2.5')
TOP_LAT = 84                   # GLWD's first row starts here; the analysis grid starts at 90N
PX_PER_DEG = 24


class _Tiles:
    """Tile-by-tile reader for one GLWD BigTIFF."""

    def __init__(self, path):
        self.f = open(path, 'rb')
        tags = _read_ifd(self.f)
        self.width, self.height = _values(self.f, tags, 256)[0], _values(self.f, tags, 257)[0]
        assert _values(self.f, tags, 258)[0] == 8 and _values(self.f, tags, 259)[0] == 5
        self.tw, self.th = _values(self.f, tags, 322)[0], _values(self.f, tags, 323)[0]
        self.offs, self.sizes = _values(self.f, tags, 324), _values(self.f, tags, 325)
        self.across = -(-self.width // self.tw)
        self.down = -(-self.height // self.th)

    def band(self, k):
        """Tile row k as a (rows, width) uint8 array."""
        out = np.empty((self.th, self.across * self.tw), dtype=np.uint8)
        for j in range(self.across):
            n = k * self.across + j
            self.f.seek(self.offs[n])
            raw = Image.open(io.BytesIO(_wrap_strip(self.f.read(self.sizes[n]), self.tw, self.th))).tobytes()
            out[:, j * self.tw:(j + 1) * self.tw] = np.frombuffer(raw, np.uint8).reshape(self.th, self.tw)
        rows = min(self.th, self.height - k * self.th)
        return out[:rows, :self.width]


def _extract():
    with zipfile.ZipFile(ZIP) as z:
        for path in (MAIN, PCT):
            if not os.path.exists(path):
                name = next(n for n in z.namelist() if n.endswith(os.path.basename(path)))
                with z.open(name) as src, open(path, 'wb') as dst:
                    while chunk := src.read(1 << 24):
                        dst.write(chunk)


def build():
    _extract()
    main, pct = _Tiles(MAIN), _Tiles(PCT)
    assert (main.width, main.height) == (pct.width, pct.height) == (360 * PX_PER_DEG * CELLS, 140 * PX_PER_DEG * CELLS)
    is_wetland = np.zeros(256, dtype=bool)
    is_wetland[WETLAND_CLASSES] = True
    w, h = 360 * PX_PER_DEG, 180 * PX_PER_DEG
    total = np.zeros((h, w), dtype=np.uint32)        # sum of wetland % over each pixel's 100 cells
    row0 = (90 - TOP_LAT) * PX_PER_DEG                # analysis row of GLWD's first row
    carry = np.zeros((0, main.width), dtype=np.uint16)
    done = 0                                          # GLWD rows already added to `total`
    for k in range(main.down):
        m, p = main.band(k), pct.band(k)
        share = np.where(is_wetland[m] & (p <= 100), p, 0).astype(np.uint16)
        rows = np.vstack([carry, share])
        full = rows.shape[0] // CELLS * CELLS
        if full:
            block = rows[:full].reshape(full // CELLS, CELLS, w, CELLS).sum(axis=(1, 3), dtype=np.uint32)
            r = row0 + done // CELLS
            total[r:r + block.shape[0]] += block
            done += full
        carry = rows[full:]
    out = np.rint(total / (CELLS * CELLS)).astype(np.uint8)
    out.tofile(CACHE)


def wetland_percent(w, h):
    """Wetlands percentage (0..100) of every analysis pixel, as bytes indexed [y * w + x]."""
    assert (w, h) == (360 * PX_PER_DEG, 180 * PX_PER_DEG)
    if not os.path.exists(CACHE):
        build()
    with open(CACHE, 'rb') as f:
        data = f.read()
    assert len(data) == w * h
    return data


if __name__ == '__main__':
    import time
    t = time.time()
    build()
    print(f'built {CACHE} in {time.time() - t:.0f}s')
