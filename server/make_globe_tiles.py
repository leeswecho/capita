"""Cut the globe image into a tile pyramid for level-of-detail display (static/index.html, "detail tiles").

    python make_globe_tiles.py [--source ../../data/world.200407.3x21600x10800.jpg]

The source is an equirectangular world image twice as wide as it is tall (default: NASA Blue Marble:
Next Generation, July 2004, 21,600 x 10,800 px, about 1.85 km per pixel at the equator). Level L covers
the world with 2^(L+1) x 2^L square tiles of TILE_PX pixels, each spanning 180 / 2^L degrees, so every
level doubles the detail and the finest level is the source at full size:

    level 0:  2 x 1 tiles,  1,350 px around the world
    level 4: 32 x 16 tiles, 21,600 px around the world (the source, uncut)

Each tile also carries a GUTTER-pixel border copied from its neighbours (wrapping around the 180
degree line; repeating the edge row at the poles), so the page can blend across tile edges without
visible seams: it samples only the inner TILE_PX x TILE_PX pixels.

Writes globe_tiles/<level>/<x>_<y>.jpg (x counts east from 180 W, y south from 90 N) and
globe_tiles/tiles.json, which server.py reads to offer the tiles at /globe/<level>/<x>_<y>.jpg.
"""
import argparse
import json
import os
import time

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "globe_tiles")
DEFAULT_SOURCE = os.path.join(HERE, "..", "..", "data", "world.200407.3x21600x10800.jpg")
TILE_PX = 675            # 21,600 = 675 x 32, so the finest level uses the source pixels unchanged
GUTTER = 2               # border pixels on each side, copied from the neighbouring tiles
QUALITY = 88


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default=DEFAULT_SOURCE)
    args = ap.parse_args()

    Image.MAX_IMAGE_PIXELS = None
    t0 = time.time()
    src = Image.open(args.source).convert("RGB")
    w, h = src.size
    if w != 2 * h or w % (2 * TILE_PX):
        raise SystemExit(f"{args.source} is {w} x {h}; need an equirectangular image whose width is twice its "
                         f"height and a multiple of {2 * TILE_PX}")
    levels = (w // (2 * TILE_PX)).bit_length()          # 21,600 / 1,350 = 16 = 2^4 -> 5 levels
    if 2 * TILE_PX * 2 ** (levels - 1) != w:
        raise SystemExit(f"width {w} is not {2 * TILE_PX} x a power of two")
    print(f"read {os.path.basename(args.source)} ({w} x {h}) in {time.time() - t0:.0f}s")

    total = 0
    for level in range(levels):
        cols, rows = 2 ** (level + 1), 2 ** level
        img = src if level == levels - 1 else src.resize((cols * TILE_PX, rows * TILE_PX), Image.LANCZOS)
        iw, ih = img.size
        # Pad the level image: GUTTER columns wrapped from the far side, GUTTER rows repeating the poles.
        padded = Image.new("RGB", (iw + 2 * GUTTER, ih + 2 * GUTTER))
        padded.paste(img, (GUTTER, GUTTER))
        padded.paste(img.crop((iw - GUTTER, 0, iw, ih)), (0, GUTTER))
        padded.paste(img.crop((0, 0, GUTTER, ih)), (iw + GUTTER, GUTTER))
        for g in range(GUTTER):
            padded.paste(padded.crop((0, GUTTER, iw + 2 * GUTTER, GUTTER + 1)), (0, g))
            padded.paste(padded.crop((0, GUTTER + ih - 1, iw + 2 * GUTTER, GUTTER + ih)), (0, GUTTER + ih + g))
        folder = os.path.join(OUT, str(level))
        os.makedirs(folder, exist_ok=True)
        side = TILE_PX + 2 * GUTTER
        for y in range(rows):
            for x in range(cols):
                tile = padded.crop((x * TILE_PX, y * TILE_PX, x * TILE_PX + side, y * TILE_PX + side))
                tile.save(os.path.join(folder, f"{x}_{y}.jpg"), quality=QUALITY, optimize=True)
        total += cols * rows
        print(f"level {level}: {cols} x {rows} tiles, {cols * TILE_PX} px around the world")

    info = {"tile_px": TILE_PX, "gutter": GUTTER, "levels": levels, "level0": {"cols": 2, "rows": 1},
            "width": w, "source": os.path.basename(args.source),
            "url": "/globe/{level}/{x}_{y}.jpg"}
    with open(os.path.join(OUT, "tiles.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(info, f, indent=2)
        f.write("\n")
    size = sum(os.path.getsize(os.path.join(dp, n)) for dp, _, ns in os.walk(OUT) for n in ns)
    print(f"wrote {total} tiles ({size / 1e6:.0f} MB) to {OUT} in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
