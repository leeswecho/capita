"""Cut the globe image into a tile pyramid for level-of-detail display (static/index.html, "detail tiles").

    python make_globe_tiles.py                      # 500 m source if its 8 images are in ../../data, else 1.85 km
    python make_globe_tiles.py --source IMAGE.jpg   # any single equirectangular world image

Sources (NASA Blue Marble: Next Generation, July 2004, land surface and shallow water, no relief shading;
https://visibleearth.nasa.gov/images/74092):
- 500 m: world.200407.3x21600x21600.{A1,B1,C1,D1,A2,B2,C2,D2}.jpg, eight 21,600 x 21,600 images that
  each cover 90 x 90 degrees (A..D from 180 W eastwards; 1 = north, 2 = south), together 86,400 x 43,200 px.
- 1.85 km: world.200407.3x21600x10800.jpg, one 21,600 x 10,800 image.

Level L covers the world with 2^(L+1) x 2^L square tiles of TILE_PX pixels, each spanning 180 / 2^L
degrees, so every level doubles the detail. The finest level is the source at full size (level 6 for
the 500 m source, level 4 for the 1.85 km one); each coarser level halves the one below it.

Each tile also carries a GUTTER-pixel border copied from its neighbours (wrapping around the 180
degree line; repeating the edge row at the poles), so the page can blend across tile edges without
visible seams: it samples only the inner TILE_PX x TILE_PX pixels.

Writes globe_tiles/<level>/<x>_<y>.jpg (x counts east from 180 W, y south from 90 N) and
globe_tiles/tiles.json, which server.py reads to offer the tiles at /globe/<level>/<x>_<y>.jpg. The
pyramid is built in globe_tiles.new/ and swapped in at the end, so a running server never sees half of it.
"""
import argparse
import json
import os
import shutil
import time

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "globe_tiles")
DATA = os.path.join(HERE, "..", "..", "data")
SOURCE_500M = [os.path.join(DATA, f"world.200407.3x21600x21600.{c}{r}.jpg") for r in "12" for c in "ABCD"]
SOURCE_1KM = os.path.join(DATA, "world.200407.3x21600x10800.jpg")
TILE_PX = 675            # divides both 21,600 and 86,400, so the finest level uses the source pixels unchanged
GUTTER = 2               # border pixels on each side, copied from the neighbouring tiles
QUALITY = 88


def load_source(path):
    """The world image, and the names of the files it came from."""
    Image.MAX_IMAGE_PIXELS = None
    if path:
        return Image.open(path).convert("RGB"), [os.path.basename(path)]
    if all(os.path.exists(p) for p in SOURCE_500M):
        side = Image.open(SOURCE_500M[0]).size[0]
        world = Image.new("RGB", (4 * side, 2 * side))
        for i, p in enumerate(SOURCE_500M):          # A1 B1 C1 D1 across the top, then A2 .. D2
            with Image.open(p) as part:
                world.paste(part.convert("RGB"), ((i % 4) * side, (i // 4) * side))
            print(f"  pasted {os.path.basename(p)}", flush=True)
        return world, [os.path.basename(p) for p in SOURCE_500M]
    return Image.open(SOURCE_1KM).convert("RGB"), [os.path.basename(SOURCE_1KM)]


def crop_wrapped(img, left, top, side):
    """A side x side crop that wraps around east-west and repeats the edge rows past the poles."""
    w, h = img.size
    if left >= 0 and top >= 0 and left + side <= w and top + side <= h:
        return img.crop((left, top, left + side, top + side))
    tile = Image.new("RGB", (side, side))
    x, dst = left, 0
    while dst < side:
        sx = x % w
        n = min(side - dst, w - sx)
        y0, y1 = max(top, 0), min(top + side, h)
        tile.paste(img.crop((sx, y0, sx + n, y1)), (dst, y0 - top))
        for ty in range(0, y0 - top):
            tile.paste(img.crop((sx, 0, sx + n, 1)), (dst, ty))
        for ty in range(y1 - top, side):
            tile.paste(img.crop((sx, h - 1, sx + n, h)), (dst, ty))
        x += n
        dst += n
    return tile


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", help="a single equirectangular world image (default: the 500 m Blue Marble "
                                     "images if present, else the 1.85 km one)")
    args = ap.parse_args()

    t0 = time.time()
    img, names = load_source(args.source)
    w, h = img.size
    if w != 2 * h or w % (2 * TILE_PX):
        raise SystemExit(f"source is {w} x {h}; need an equirectangular image whose width is twice its "
                         f"height and a multiple of {2 * TILE_PX}")
    levels = (w // (2 * TILE_PX)).bit_length()          # 86,400 / 1,350 = 64 = 2^6 -> 7 levels
    if 2 * TILE_PX * 2 ** (levels - 1) != w:
        raise SystemExit(f"width {w} is not {2 * TILE_PX} x a power of two")
    print(f"source {w:,} x {h:,} px ({40075 / w:.2f} km per pixel at the equator), {levels} levels, "
          f"read in {time.time() - t0:.0f}s", flush=True)

    tmp = OUT + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    side = TILE_PX + 2 * GUTTER
    total = 0
    for level in range(levels - 1, -1, -1):              # finest first; each coarser level halves it
        cols, rows = 2 ** (level + 1), 2 ** level
        assert img.size == (cols * TILE_PX, rows * TILE_PX)
        folder = os.path.join(tmp, str(level))
        os.makedirs(folder)
        for y in range(rows):
            for x in range(cols):
                tile = crop_wrapped(img, x * TILE_PX - GUTTER, y * TILE_PX - GUTTER, side)
                tile.save(os.path.join(folder, f"{x}_{y}.jpg"), quality=QUALITY, optimize=True)
        total += cols * rows
        print(f"level {level}: {cols} x {rows} tiles, {cols * TILE_PX:,} px around the world "
              f"({time.time() - t0:.0f}s)", flush=True)
        if level:
            img = img.reduce(2)

    info = {"tile_px": TILE_PX, "gutter": GUTTER, "levels": levels, "level0": {"cols": 2, "rows": 1},
            "width": w, "km_per_px_equator": round(40075 / w, 3), "source": names,
            "url": "/globe/{level}/{x}_{y}.jpg"}
    with open(os.path.join(tmp, "tiles.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(info, f, indent=2)
        f.write("\n")
    size = sum(os.path.getsize(os.path.join(dp, n)) for dp, _, ns in os.walk(tmp) for n in ns)
    shutil.rmtree(OUT, ignore_errors=True)
    os.rename(tmp, OUT)
    print(f"wrote {total:,} tiles ({size / 1e6:,.0f} MB) to {OUT} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
