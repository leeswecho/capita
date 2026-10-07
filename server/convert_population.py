"""Convert world_population.yaml into world_population_current.json, a columnar file that loads in a fraction
of the time.

    python convert_population.py

Rerun it whenever world_population.yaml is regenerated. The script reloads the JSON and checks that it
reproduces every tile of the YAML exactly before finishing.

The full modern-world population is prototype and demonstration data; the game itself starts from
world_population_start.json (made by gen_start_capitals.py), which uses the same layout:

    {
      "format": "world_population/columnar-2",
      "map": { ... },                        # the YAML's `map` section, unchanged
      "order": "...",                        # how array index i maps to a tile (below)
      "country": ["GRL", null, ...],         # 259,200 entries each, one per tile
      "population": [0, 1520, ...],
      "area_km2": [26.9, ...],
      "density_per_km2": [0.0, ...]
    }

Index i covers the tile at row = i // WIDTH, col = i % WIDTH (grid.py: 720 x 360 tiles of 0.5 degrees),
i.e. south-west corner lat = 89.5 - 0.5 * row, lon = 0.5 * col - 180 (row 0 = northernmost, col 0 =
westernmost; the YAML's own file order). `lat`, `lon`, `center` and `neighbors` are not stored because
they follow from the index; load_population() rebuilds them.
"""

import json
import os
import sys
import time

import yaml

import grid

HERE = os.path.dirname(os.path.abspath(__file__))
FORMAT = "world_population/columnar-2"     # -1 was the 1-degree grid
FIELDS = ("country", "population", "area_km2", "density_per_km2")
ORDER = (f"index i -> row i//{grid.WIDTH}, col i%{grid.WIDTH}; south-west corner "
         f"lat = {90 - grid.TILE} - {grid.TILE}*row, lon = {grid.TILE}*col - 180")


def grid_order():
    """(index, row, col, lat, lon) for every tile, in file order."""
    for row in range(grid.HEIGHT):
        for col in range(grid.WIDTH):
            lat, lon = grid.corner(row, col)
            yield row * grid.WIDTH + col, row, col, lat, lon


def load_population(path: str) -> dict:
    """Load a columnar population file into the same structure yaml.load gives for world_population.yaml."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("format") != FORMAT:
        raise ValueError(f"{path}: expected format {FORMAT!r}, got {data.get('format')!r}")
    cols = [data[k] for k in FIELDS]
    half = grid.TILE / 2
    tiles = {}
    for i, row, col, lat, lon in grid_order():
        tiles[grid.tile_id(lat, lon)] = {
            "lat": lat, "lon": lon, "center": [lat + half, lon + half],
            **{k: c[i] for k, c in zip(FIELDS, cols)},
            "neighbors": grid.neighbors(row, col),
        }
    return {"map": data["map"], "tiles": tiles}


def main():
    src = os.path.join(HERE, "world_population.yaml")
    dst = os.path.join(HERE, "world_population_current.json")

    t0 = time.perf_counter()
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    with open(src, encoding="utf-8") as f:
        world = yaml.load(f, Loader=loader)
    print(f"read {os.path.basename(src)} in {time.perf_counter() - t0:.1f}s")

    tiles = world["tiles"]
    ids = [grid.tile_id(lat, lon) for _, _, _, lat, lon in grid_order()]
    assert len(tiles) == len(ids) and list(tiles) == ids, "tiles are not in the expected grid order"
    out = {"format": FORMAT, "map": world["map"], "order": ORDER,
           **{k: [tiles[t][k] for t in ids] for k in FIELDS}}
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, separators=(",", ":"))

    t0 = time.perf_counter()
    back = load_population(dst)
    elapsed = time.perf_counter() - t0
    if back != world:
        bad = next(t for t in ids if back["tiles"][t] != tiles[t]) if back["map"] == world["map"] else "map"
        sys.exit(f"round-trip mismatch at {bad}; {os.path.basename(dst)} is not faithful")
    print(f"wrote {os.path.basename(dst)} ({os.path.getsize(dst) / 1e6:.1f} MB); "
          f"verified identical to the YAML; loads in {elapsed:.2f}s")


if __name__ == "__main__":
    main()
