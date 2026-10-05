"""Local web server that renders world_map.json and world_population.json on an interactive 3D globe.

    python convert_map.py          # once, and again whenever world_map.yaml changes
    python convert_population.py   # once, and again whenever world_population.yaml changes
    python server.py [--port 8000] [--host 127.0.0.1]

Then open http://localhost:8000/ in a browser.

Endpoints:
    /                      the globe viewer (static/index.html)
    /earth.jpg             the globe texture (map.source_image)
    /earth/<width>.jpg     the same image at another size (e.g. /earth/16384.jpg); /api/meta lists them
    /api/meta              map metadata, all rivers and the available texture sizes (JSON)
    /api/terrain           dominant landform per tile, 259,200 bytes in grid order (grid.py:
                           row 0 = lat 89.5, col 0 = lon -180, 0.5-degree tiles); index into map.terrain_types
    /api/cover             dominant land cover per tile, same layout; index into map.cover_types, 255 = no land
    /api/population        people per tile, 259,200 little-endian uint32 in the same order
    /api/population/version {"version": n}; n goes up each time world_population.json changes
    /api/population/countries {"version": n, "codes": [...], "index": [...]}: each tile's country,
                           as 0 (none) or k = codes[k - 1], in grid order (for the same-country tint)

world_population.json is reread once per second, so edits to it show up in open pages
without restarting the server.
    /api/tile/<id>         one tile record merged with its population record (JSON)
    /api/tile_at?lat=&lon= the tile containing a point (JSON)
"""

import argparse
import json
import os
import re
import struct
import threading
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import grid
from convert_map import read_map
from convert_population import FORMAT as POP_FORMAT
from grid import HEIGHT, TILE_ID_RE, WIDTH, tile_at

ROOT = os.path.dirname(os.path.abspath(__file__))
POP_RELOAD_SECONDS = 1.0
STATIC = os.path.join(ROOT, "static")


def check_grid(meta: dict, name: str):
    """Refuse data made for a different grid (e.g. an old 1-degree file)."""
    got = (meta.get("tile_size_degrees"), meta.get("width"), meta.get("height"))
    if got != (grid.TILE, WIDTH, HEIGHT):
        sys.exit(f"{name} is for a {got} grid, but grid.py is {(grid.TILE, WIDTH, HEIGHT)}; regenerate it")


def check_fresh(json_path: str, converter: str):
    """Exit if the JSON is missing; warn if its YAML source is newer (the converter wasn't rerun)."""
    yaml_path = os.path.splitext(json_path)[0] + ".yaml"
    if not os.path.exists(json_path):
        sys.exit(f"{os.path.basename(json_path)} not found; run: python {converter}")
    if os.path.exists(yaml_path) and os.path.getmtime(yaml_path) > os.path.getmtime(json_path):
        print(f"  WARNING: {os.path.basename(yaml_path)} is newer than {os.path.basename(json_path)}; "
              f"rerun {converter}", flush=True)


def find_textures(source_image: str) -> dict:
    """Width -> file name for every size of the globe texture next to map.source_image.
    The sizes share its name pattern: bmng_200407_8192x4096.jpg -> bmng_200407_<w>x<h>.jpg."""
    m = re.fullmatch(r"(.*_)\d+x\d+(\.jpe?g)", source_image)
    if not m:
        return {}
    pat = re.compile(re.escape(m.group(1)) + r"(\d+)x(\d+)" + re.escape(m.group(2)))
    found = {}
    for name in os.listdir(ROOT):
        t = pat.fullmatch(name)
        if t and int(t.group(1)) == 2 * int(t.group(2)):   # equirectangular images are 2:1
            found[int(t.group(1))] = name
    return dict(sorted(found.items()))


def dominant_grid(cols: list, none: int = None) -> bytes:
    """Per tile, the index of the column with the largest value; `none` where every value is 0."""
    out = bytearray(WIDTH * HEIGHT)
    for i in range(WIDTH * HEIGHT):
        best = max(range(len(cols)), key=lambda k: cols[k][i])
        out[i] = none if none is not None and cols[best][i] == 0 else best
    return bytes(out)


def tile_index(tid: str):
    """Flat index of a tile id, or None if it isn't a tile on this grid (e.g. N003E0000)."""
    if not TILE_ID_RE.match(tid):
        return None
    lat, lon = grid.parse_tile_id(tid)
    if not (-90 <= lat <= 90 - grid.TILE and -180 <= lon <= 180 - grid.TILE):
        return None
    row, col = grid.row_col(lat, lon)
    return row * WIDTH + col if grid.tile_id(*grid.corner(row, col)) == tid else None


class Population:
    """One snapshot of world_population.json (made by convert_population.py) plus what the server
    derives from it. Snapshots are never modified, so request threads can use one while the
    reload thread builds the next."""

    def __init__(self, raw: bytes, version: int):
        data = json.loads(raw)
        if data.get("format") != POP_FORMAT:
            raise ValueError(f"expected format {POP_FORMAT!r}, got {data.get('format')!r}")
        n = WIDTH * HEIGHT
        for k in ("country", "population", "area_km2", "density_per_km2"):
            if len(data.get(k) or ()) != n:
                raise ValueError(f"{k!r} should have {n} entries")
        self.raw = raw
        self.version = version
        self.meta = data["map"]
        self.country = data["country"]
        self.population = data["population"]
        self.area_km2 = data["area_km2"]
        self.density_per_km2 = data["density_per_km2"]

        # Dense population grid for the client's heatmap, plus each tile's rank by population.
        people = self.population
        self.grid = struct.pack(f"<{n}I", *people)
        populated = sorted((i for i, v in enumerate(people) if v > 0), key=lambda i: -people[i])
        self.rank = {i: r + 1 for r, i in enumerate(populated)}
        self.populated_tiles = len(populated)

        # Country of every tile for the page's "same country" tint: codes listed once, then one small
        # number per tile (0 = no country, k = codes[k - 1]).
        codes = sorted({c for c in self.country if c})
        pos = {c: k + 1 for k, c in enumerate(codes)}
        self.country_json = json.dumps({"version": version, "codes": codes,
                                        "index": [pos.get(c, 0) for c in self.country]},
                                       separators=(",", ":")).encode()


def read_population(path: str, version: int) -> Population:
    with open(path, "rb") as f:
        return Population(f.read(), version)


def load_country_names(path: str) -> dict:
    """ISO alpha-3 code -> country name, from the Natural Earth file used by gen_population.py."""
    names = {"XKX": "Kosovo"}
    try:
        with open(path, encoding="utf-8") as f:
            features = json.load(f)["features"]
    except (OSError, ValueError, KeyError):
        print(f"  (no country names: couldn't read {os.path.basename(path)})", flush=True)
        return names
    for feat in features:
        p = feat["properties"]
        code = p.get("ISO_A3_EH")
        if code and code != "-99" and code not in names:
            names[code] = p.get("NAME") or p.get("ADMIN")
    return names


class World:
    def __init__(self, path: str, pop_path: str, countries_path: str):
        # Map columns (convert_map.py). Tile records are built on demand, so startup stays fast.
        check_fresh(path, "convert_map.py")
        t0 = time.time()
        try:
            self.map = read_map(path)
        except (OSError, ValueError) as e:
            sys.exit(f"couldn't load {path}: {e}")
        self.meta = self.map.meta
        self.rivers = self.map.rivers
        check_grid(self.meta, os.path.basename(path))
        self.textures = find_textures(self.meta["source_image"])
        print(f"  globe textures: {', '.join(f'{w}px' for w in self.textures) or 'only ' + self.meta['source_image']}",
              flush=True)

        # Dense dominant-type grids for the client's terrain and cover overlays (first type wins a tie;
        # 255 = no cover, i.e. a tile with no land).
        self.terrain_grid = dominant_grid(self.map.terrain)
        self.cover_grid = dominant_grid(self.map.cover, none=255)
        print(f"Loaded {os.path.basename(path)} in {time.time() - t0:.2f}s", flush=True)

        # Population columns, indexed like the terrain grid (grid.py order).
        self.pop_path = pop_path
        check_fresh(pop_path, "convert_population.py")
        t0 = time.time()
        try:
            self.pop = read_population(pop_path, version=1)
        except (OSError, ValueError) as e:
            sys.exit(f"couldn't load {pop_path}: {e}")
        print(f"Loaded {os.path.basename(pop_path)} in {time.time() - t0:.2f}s", flush=True)
        check_grid(self.pop.meta, os.path.basename(pop_path))
        self.country_names = load_country_names(countries_path)

    def reload_population_forever(self):
        """Reread the population file every POP_RELOAD_SECONDS; swap in a new snapshot when it changes."""
        last_error = None
        while True:
            time.sleep(POP_RELOAD_SECONDS)
            try:
                with open(self.pop_path, "rb") as f:
                    raw = f.read()
                if raw == self.pop.raw:
                    last_error = None
                    continue
                self.pop = Population(raw, self.pop.version + 1)
                last_error = None
                print(f"Reloaded {os.path.basename(self.pop_path)} (version {self.pop.version})", flush=True)
            except (OSError, ValueError) as e:
                # Often a half-written file; keep serving the last good data and retry next second.
                if str(e) != last_error:
                    print(f"  couldn't reload {os.path.basename(self.pop_path)}, keeping version "
                          f"{self.pop.version}: {e}", flush=True)
                last_error = str(e)

    def meta_json(self):
        pop = self.pop
        return json.dumps({
            "map": self.meta, "rivers": self.rivers, "population_map": pop.meta,
            "textures": [{"width": w, "url": f"/earth/{w}.jpg"} for w in self.textures],
            "populated_tiles": pop.populated_tiles, "population_version": pop.version,
        }).encode()

    def tile_json(self, tid: str):
        i = tile_index(tid)
        if i is None:
            return None
        body = {"id": tid, **self.map.tile(i)}
        pop = self.pop
        country = pop.country[i]
        body["population"] = {
            "country": country,
            "country_name": self.country_names.get(country) if country else None,
            "population": pop.population[i],
            "area_km2": pop.area_km2[i],
            "density_per_km2": pop.density_per_km2[i],
            "rank": pop.rank.get(i),
            "version": pop.version,
        }
        return json.dumps(body).encode()


class Handler(BaseHTTPRequestHandler):
    world: World = None  # set in main()

    def log_message(self, fmt, *args):  # quieter: skip per-hover tile requests and version polls
        line = str(args[0]) if args else ""
        if "/api/tile" not in line and "/api/population/version" not in line:
            super().log_message(fmt, *args)

    def send(self, status: int, body: bytes, ctype: str, cache: bool = False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: str, ctype: str, cache: bool = False):
        try:
            with open(path, "rb") as f:
                self.send(200, f.read(), ctype, cache)
        except FileNotFoundError:
            self.send_error(404)

    def send_json(self, body: bytes, status: int = 200):
        self.send(status, body, "application/json")

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        w = self.world

        if path in ("/", "/index.html"):
            return self.send_file(os.path.join(STATIC, "index.html"), "text/html; charset=utf-8")
        if path == "/earth.jpg":
            return self.send_file(os.path.join(ROOT, w.meta["source_image"]), "image/jpeg", cache=True)
        m = re.fullmatch(r"/earth/(\d+)\.jpg", path)
        if m:
            name = w.textures.get(int(m.group(1)))   # only the sizes find_textures() listed
            if name is None:
                return self.send_error(404)
            return self.send_file(os.path.join(ROOT, name), "image/jpeg", cache=True)
        if path == "/api/meta":
            return self.send_json(w.meta_json())
        if path == "/api/terrain":
            return self.send(200, w.terrain_grid, "application/octet-stream")
        if path == "/api/cover":
            return self.send(200, w.cover_grid, "application/octet-stream")
        if path == "/api/population":
            pop = w.pop
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(pop.grid)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Population-Version", str(pop.version))
            self.end_headers()
            return self.wfile.write(pop.grid)
        if path == "/api/population/version":
            return self.send_json(json.dumps({"version": w.pop.version}).encode())
        if path == "/api/population/countries":
            return self.send_json(w.pop.country_json)
        if path.startswith("/api/tile/"):
            tid = path[len("/api/tile/"):].upper()
            body = w.tile_json(tid) if TILE_ID_RE.match(tid) else None
            if body is None:
                return self.send_json(json.dumps({"error": f"no tile {tid!r}"}).encode(), 404)
            return self.send_json(body)
        if path == "/api/tile_at":
            q = parse_qs(url.query)
            try:
                lat, lon = float(q["lat"][0]), float(q["lon"][0])
            except (KeyError, ValueError):
                return self.send_json(b'{"error": "need numeric lat and lon"}', 400)
            return self.send_json(w.tile_json(tile_at(lat, lon)))
        self.send_error(404)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--map", default=os.path.join(ROOT, "world_map.json"))
    ap.add_argument("--population", default=os.path.join(ROOT, "world_population.json"))
    ap.add_argument("--countries", default=os.path.join(ROOT, "ne_10m_admin_0_countries.geojson"))
    args = ap.parse_args()

    Handler.world = World(args.map, args.population, args.countries)
    threading.Thread(target=Handler.world.reload_population_forever, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving on http://{args.host}:{args.port}/  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
