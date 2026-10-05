"""Convert world_map.yaml into world_map.json, a columnar file that loads in a fraction of the time.

    python convert_map.py

Rerun it whenever world_map.yaml is regenerated (gen_map.py). The script reloads the JSON and checks
that it reproduces every tile and river of the YAML exactly before finishing.

world_map.json layout:

    {
      "format": "world_map/columnar-2",
      "map": { ... },                        # the YAML's `map` section, unchanged
      "order": "...",                        # how array index i maps to a tile (below)
      "rivers": { "Nile": {"scalerank": 1, "natural_earth_rivernum": [4],
                           "tiles": [...], "segments": [[...], ...]}, ... },   # the YAML's `rivers`, unchanged
      "terrain": { "water": [100, ...], "ice": [...], ... },     # 259,200 percentages per landform type
      "cover": { "grass": [0, ...], "forest": [...], ... }       # 259,200 percentages per cover type
    }

Index i covers the tile at row = i // WIDTH, col = i % WIDTH (grid.py), the YAML's own file order.
Everything else in a tile record follows from the index or the rivers, so it isn't stored:
`lat`, `lon`, `center` and `neighbors` come from grid.py, and `river` / `rivers` from the river
lists (a tile's `rivers` are the rivers whose `tiles` include it, in `rivers` order).
MapColumns rebuilds any single tile record on demand; load_map() rebuilds them all.
"""

import json
import os
import sys
import time

import yaml

import grid

HERE = os.path.dirname(os.path.abspath(__file__))
FORMAT = "world_map/columnar-2"     # -1 had a single combined terrain mapping
ORDER = (f"index i -> row i//{grid.WIDTH}, col i%{grid.WIDTH}; south-west corner "
         f"lat = {90 - grid.TILE} - {grid.TILE}*row, lon = {grid.TILE}*col - 180")


class MapColumns:
    """world_map.json held as columns. tile(i) builds the same record yaml.load gives for that tile."""

    def __init__(self, data: dict):
        if data.get("format") != FORMAT:
            raise ValueError(f"expected format {FORMAT!r}, got {data.get('format')!r}")
        self.meta = data["map"]
        self.rivers = data["rivers"]
        self.terrain_types = self.meta["terrain_types"]
        self.cover_types = self.meta["cover_types"]
        self.terrain = self._columns(data, "terrain", self.terrain_types)
        self.cover = self._columns(data, "cover", self.cover_types)
        self.rivers_at = {}                       # tile id -> river names, in rivers order
        for name, r in self.rivers.items():
            for tid in r["tiles"]:
                self.rivers_at.setdefault(tid, []).append(name)

    @staticmethod
    def _columns(data: dict, group: str, types: list) -> list:
        cols = data[group]
        if list(cols) != types or any(len(cols[t]) != grid.COUNT for t in types):
            raise ValueError(f"{group} should have {grid.COUNT} values for each of {types}")
        return [cols[t] for t in types]

    def tile(self, i: int) -> dict:
        row, col = divmod(i, grid.WIDTH)
        lat, lon = grid.corner(row, col)
        half = grid.TILE / 2
        rec = {"lat": lat, "lon": lon, "center": [lat + half, lon + half],
               "terrain": {t: c[i] for t, c in zip(self.terrain_types, self.terrain)},
               "cover": {t: c[i] for t, c in zip(self.cover_types, self.cover)}}
        names = self.rivers_at.get(grid.tile_id(lat, lon))
        rec["river"] = bool(names)
        if names:
            rec["rivers"] = list(names)
        rec["neighbors"] = grid.neighbors(row, col)
        return rec


def read_map(path: str) -> MapColumns:
    with open(path, encoding="utf-8") as f:
        return MapColumns(json.load(f))


def load_map(path: str) -> dict:
    """Load world_map.json into the same structure yaml.load gives for world_map.yaml."""
    m = read_map(path)
    tiles = {grid.tile_id(*grid.corner(*divmod(i, grid.WIDTH))): m.tile(i) for i in range(grid.COUNT)}
    return {"map": m.meta, "rivers": m.rivers, "tiles": tiles}


def main():
    src = os.path.join(HERE, "world_map.yaml")
    dst = os.path.join(HERE, "world_map.json")

    t0 = time.perf_counter()
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    with open(src, encoding="utf-8") as f:
        world = yaml.load(f, Loader=loader)
    print(f"read {os.path.basename(src)} in {time.perf_counter() - t0:.1f}s")

    tiles = world["tiles"]
    ids = [grid.tile_id(*grid.corner(*divmod(i, grid.WIDTH))) for i in range(grid.COUNT)]
    assert len(tiles) == len(ids) and list(tiles) == ids, "tiles are not in the expected grid order"

    out = {"format": FORMAT, "map": world["map"], "order": ORDER, "rivers": world["rivers"],
           **{group: {t: [tiles[tid][group][t] for tid in ids] for t in world["map"][group + "_types"]}
              for group in ("terrain", "cover")}}
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)

    t0 = time.perf_counter()
    back = load_map(dst)
    elapsed = time.perf_counter() - t0
    if back != world:
        if back["map"] != world["map"]:
            bad = "map"
        elif back["rivers"] != world["rivers"]:
            bad = "rivers"
        else:
            bad = next(t for t in ids if back["tiles"][t] != tiles[t])
        sys.exit(f"round-trip mismatch at {bad}; {os.path.basename(dst)} is not faithful")
    t0 = time.perf_counter()
    read_map(dst)
    columns = time.perf_counter() - t0
    print(f"wrote {os.path.basename(dst)} ({os.path.getsize(dst) / 1e6:.1f} MB); verified identical to the "
          f"YAML; loads as columns in {columns:.2f}s (all tile records rebuilt: {elapsed:.1f}s)")


if __name__ == "__main__":
    main()
