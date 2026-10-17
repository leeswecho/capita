# capita

A turn-based world simulation on a Civilization-style map of the real Earth. The planet is divided
into a **0.5° × 0.5° grid (720 × 360 = 259,200 tiles)**. Each tile records its landform, land
cover, rivers and population. A turn engine advances age cohorts of people, nations, tile ownership
and visibility one year at a time. A local web server shows it all on an interactive 3D globe.

## Repository layout

| Path | What it is |
|------|------------|
| `server/` | The globe viewer: `server.py` (Python HTTP server), `static/index.html` (Three.js page), the shared tile grid (`grid.py`), the cohort-state reader/writer (`cohort_state.py`), data converters and generators, and the JSON data and globe images the server loads. |
| `engine/` | `engine.py`, the turn engine. It runs as its own process and writes the game state to `state/`. |
| `state/` | The game state, one directory per turn. The engine generates it and it isn't committed. |
| `reference/` | Documentation, plus the full-size YAML source data (`world_map.yaml`, `world_population.yaml`, `world_population_capitals.yaml`). |

## Requirements

- Python 3 with **NumPy** (needed by the server and engine) and **PyYAML** (imported by the
  converters).
- A browser with WebGL. The page loads Three.js from the jsDelivr CDN, so it needs internet access
  the first time.

## Quick start

```sh
cd server
python server.py                      # then open http://localhost:8000/
```

In a second terminal, from `server/`, start a game state and advance it:

```sh
python ../engine/engine.py init       # turn 0 from world_population_start.json and its nation seed
python ../engine/engine.py step       # advance one year (--turns N for more)
python ../engine/engine.py verify     # recheck the current turn's checksum
```

The running server and any open page pick up each new turn within about two seconds. To start over,
delete `state/` and run `init` again.

Useful server options: `--port` (default `8000`), `--host` (default `127.0.0.1`, local only, no
authentication), and `--population <file>`. For example, `--population world_population_current.json`
shows the full modern world instead of the starting scenario. Page URL options: `?texture=8192` caps
the globe texture size, and `?detail=0` turns off the detail-tile pyramid.

## How it fits together

```
world_map.json ─────────────┐
world_population_start.json ┼─► server.py ──HTTP──► static/index.html (3D globe, side panel)
state/turn_NNNNN/ ──────────┘      ▲
        ▲                          │ rereads the population file and state/current.json every second
engine.py init / step ─────────────┘
```

- **The tile grid** is defined once in `server/grid.py` and shared by every script, the server and the
  page. A tile can be named by its id (south-west corner in tenths of a degree, for example
  `N285E0770` for Delhi), its corner lat/lon, or its flat index (`row * 720 + col`, row 0 at the north
  pole). Longitude wraps east to west; latitude stops at the poles.
- **The server** loads columnar JSON copies of the map and population data, not the large YAML files.
  It does no rendering: it serves compact per-tile byte arrays, and the browser draws the globe, the
  terrain, cover and population overlays, rivers, ownership tints and fog of war.
- **The engine** writes each turn as NumPy `.npy` arrays plus a `manifest.json` with a SHA-256
  checksum. Turns are written atomically and never changed afterwards, so the server can read one turn
  while the engine writes the next.
- **Current turn rules** (placeholders): every cohort ages one year, with no births or deaths yet.
  Ownership is first come, first served. Each nation actively sees every tile where it has people,
  plus the 8 tiles around each one.

## Data

| Data | Source | Notes |
|------|--------|-------|
| Landform (water, plains, hills, mountains) | NASA Blue Marble (July 2004), NOAA ETOPO1 relief | Percentages per tile |
| Cover (grass, forest, desert, sand, wetlands, tundra, ice) | Blue Marble colour, Natural Earth glaciers and ice shelves, NSIDC sea ice, GLWD v2 wetlands | Percentages of each tile's land and frozen water |
| Rivers | Natural Earth 1:10m centrelines, filtered to Wikipedia's List of waterways | 51 navigable rivers on 1,806 tiles |
| Population | JRC GHSL GHS-POP R2023A, epoch 2020 (~1 km) | 7,840,952,769 people across 49,910 populated tiles |
| Countries and capitals | Natural Earth 1:10m Admin 0 Countries and Populated Places | ISO 3166-1 alpha-3 codes, de facto boundaries |

The full modern population is prototype and demonstration data. The game starts from
`world_population_start.json`: 200 people in each of 38 start tiles placed at present-day capitals,
with one nation per start tile.

The YAML files are generated. Don't edit them by hand. After regenerating one, rerun its converter
(`convert_map.py` or `convert_population.py`) to rebuild the JSON copy the server reads. The map and
population generators and the downloaded source datasets (`gen_map.py`, `gen_population.py`, `data/`
and so on) aren't in this repository.

## Documentation

All in `reference/`:

- [ARCHITECTURE.md](reference/ARCHITECTURE.md): how the server, page and engine work, the HTTP API,
  workflows, and limits.
- [cohort_state_schema.md](reference/cohort_state_schema.md): the game-state format
  (`capita/cohort-state-3`), written so an engine in any language can produce it.
- [world_map_schema.md](reference/world_map_schema.md): the `world_map.yaml` and `world_map.json`
  formats, tile ids, grid order and guaranteed properties.
- [world_map_generation.md](reference/world_map_generation.md): how the map was built, including
  inputs, calibration, rules and known limitations.
- [world_population_schema.md](reference/world_population_schema.md): the `world_population.yaml`
  format and its sources.

## License

MIT. See [LICENSE](LICENSE).
