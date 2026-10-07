# World map globe server: architecture

This document explains how the local globe viewer works: what each file does, how data gets from
the YAML/JSON files onto the screen, and what happens when you hover over a tile or when the
population data changes.

---

## 1. The big picture

There are two programs that talk over HTTP on your machine:

```
 ┌───────────────────────────── server.py (Python) ─────────────────────────────┐
 │                                                                              │
 │  world_map.json ──► MapColumns ───► World ─────────────┐                     │
 │   (6.4 MB, terrain,  (tile records                      │                     │
 │    rivers)            built on demand)                  │   Handler           │
 │                                                         ├─► (HTTP routes) ◄───┼──── browser requests
 │  world_population_start.json ──► Population snapshot ───┤                     │
 │   (or --population file)    ▲                           │                     │
 │                             │ reread every 1 s          │                     │
 │                       reload thread                     │                     │
 │                                                         │                     │
 │  ne_10m_admin_0_countries.geojson ──► country names ────┘                     │
 │  bmng_200407_<w>x<h>.jpg (3 sizes) ───────────────────────► served as-is      │
 └──────────────────────────────────────────────────────────────────────────────┘

 ┌──────────────────────── static/index.html (browser) ─────────────────────────┐
 │  Three.js scene: globe + overlays (terrain, cover, heatmap, grid, rivers)    │
 │  Side panel: details of the hovered/pinned tile                              │
 │  Polls /api/population/version every second and repaints on change           │
 └──────────────────────────────────────────────────────────────────────────────┘
```

- **`server.py`** loads all data into memory once at startup, keeps the population data fresh, and
  answers HTTP requests. It reads only JSON; PyYAML is needed just because it imports
  `convert_population.py` and `convert_map.py`, which also hold the YAML conversion code.
- **`static/index.html`** is a single self-contained page (HTML + CSS + one JavaScript module). It
  draws the globe with [Three.js](https://threejs.org/), loaded from the jsDelivr CDN, so the browser
  needs internet access the first time.

The server does no rendering. It hands the browser compact data, and the browser does all the 3D work.

---

## 2. Files

| File | Role |
|------|------|
| `server.py` | The web server (≈300 lines). |
| `static/index.html` | The globe viewer page (≈650 lines). |
| `convert_map.py` | Converts `world_map.yaml` → `world_map.json`, and verifies the result. Also defines the JSON format id and the `MapColumns` reader the server uses. |
| `convert_population.py` | Converts `world_population.yaml` → `world_population_current.json` (the full modern world, prototype data), and verifies the result. Also defines the columnar format id the server checks and the loader other scripts use. |
| `../engine/engine.py` | The turn engine, in its own directory and run as its own process: `init` writes turn 0 of the cohort state, `step` advances it a year at a time, `verify` checks the current turn's checksum (§4.6). It imports `cohort_state.py` and `grid.py` from `server/`, and by default uses the top-level `state/` folder and `server/world_population_start.json` with its nation seed. |
| `cohort_state.py` | Reads and writes the cohort state (`../state/`): the format id, atomic turn writing, memory-mapped reading, checksums and pruning. Shared by `engine/engine.py` and the server. |
| `../state/` | The cohort state written by `engine/engine.py`, in its own top-level folder next to `server/` and `engine/`: `current.json` plus one `turn_NNNNN/` directory per recent turn (see `cohort_state_schema.md`). Generated; not committed. |
| `gen_start_capitals.py` | Writes the game's starting scenario: `world_population_start.json` (200 people in each of 38 start tiles, 0 elsewhere; the start tiles are placeholder locations at present-day capitals; country and area copied from `world_population_current.json`), `../reference/world_population_capitals.yaml` (the same as YAML) and `world_population_start_capitals.nations.json`, the nation seed (one nation per start tile, with that tile as its capital) that `engine.py init` picks up automatically. |
| `demo_delhi.py` | Prototype demo (not in the repo): rewrites the full-world population file every second with a random population for the Delhi tile, to show live updating. Restores the original value on Ctrl+C. |
| `grid.py` | The tile grid (0.5° tiles, ids, lookups, flat index, neighbours), shared by every script and the server. `static/index.html` has a JavaScript copy. |
| `world_map.yaml` | Landform (`terrain`), cover including ice (`cover`), rivers and neighbour links for 259,200 tiles (see `world_map_schema.md`). Source for the JSON; the server doesn't read it. |
| `world_map.json` | Columnar copy of the map that the server reads (made by `convert_map.py`). |
| `world_population_start.json` | **The game's starting scenario** and the server's default population file: what the server reads and watches, and what `engine.py init` seeds turn 0 from. Made by `gen_start_capitals.py`. |
| `world_population_start_capitals.nations.json` | The starting scenario's nation seed: each nation's code and capital tile. |
| `world_population_current.json` | The full modern world (GHSL 2020), columnar. Prototype and demonstration data only: `python server.py --population world_population_current.json` shows it. Made by `convert_population.py`. |
| `world_population_current_capitals.nations.json` | The full world's nation seed: one nation per country code with a populated tile (224), each with its real-life capital tile. Made by `gen_capitals_seed.py`. |
| `gen_capitals_seed.py` | Writes `<population name>_capitals.nations.json` for a population file (default `world_population_current.json`) from the capitals in Natural Earth's populated places, with hand-picked choices for countries with several capitals and coordinates for territories it doesn't mark. Where two capitals share a tile, the larger city keeps it and the other moves to its own country's nearest populated tile. |
| `ne_10m_populated_places_simple.geojson` | Natural Earth populated places, read by `gen_capitals_seed.py` (downloaded by it if missing). |
| `world_population.yaml` | The full modern world per tile (see `world_population_schema.md`), the source of `world_population_current.json`. Kept in `reference/`; the server doesn't read it. |
| `ne_10m_admin_0_countries.geojson` | Used only to turn country codes (`IND`) into names (`India`). Optional. |
| `bmng_200407_4096x2048.jpg`, `bmng_200407_8192x4096.jpg`, `bmng_200407_16384x8192.jpg` | The image wrapped around the globe (NASA Blue Marble, July 2004) at three sizes; the page loads the largest the graphics card supports (§5.2). The 8192 one is `map.source_image` and the default. |
| `gen_map.py`, `etopo_blocks.py`, `glwd.py`, `validate_map.py` | Build `world_map.yaml` from the datasets in `data/` (elevation blocks and GLWD wetland shares are cached there; see `world_map_generation.md`), and check it. |
| `gen_population.py`, `ghsl.py` | Build `world_population.yaml` from the GHSL population raster in `data/` (see `world_population_schema.md`). |
| `data/` | Downloaded source datasets; `data/SOURCES.txt` lists where each came from. |

---

## 3. One idea used everywhere: the tile grid

Every data source describes the same **720 × 360 grid of 0.5° × 0.5° tiles** (259,200 tiles), defined
once in `grid.py`. A tile can be named three interchangeable ways:

| Form | Example (Delhi) | Used by |
|------|-----------------|---------|
| Tile id (south-west corner, tenths of a degree) | `N285E0770` | YAML keys, `/api/tile/<id>`, the side panel |
| Corner lat/lon | `lat 28.5, lon 77.0` | tile records |
| Flat index | `(89.5 − lat) × 2 × 720 + (lon + 180) × 2` = `88354` | the JSON columns, the binary grids, the overlay images |

Ids are `N`/`S` plus three digits of latitude × 10, then `E`/`W` plus four digits of longitude × 10:
`N395W1055` is the tile from 39.5°N to 40.0°N and 105.5°W to 105.0°W. Python code uses `grid.tile_id()`,
`grid.tile_at()` and `grid.index()`; the page uses `tileId()`, `parseTileId()` and `tileAt()`.

The flat index walks the grid **row by row from the north pole, west to east within a row**. This is
the same order the YAML files list their tiles in, and it is also the pixel order of a 720 × 360 image
whose top-left pixel is the tile at 89.5°N, 180°W. That coincidence is what makes the overlays cheap:
a per-tile array *is* an image, and an image wrapped around a sphere lines up with the tiles
automatically.

---

## 4. The server (`server.py`)

### 4.1 Startup sequence

`main()` does this, in order:

1. **Parse arguments**: `--host` (default `127.0.0.1`, so only your machine can connect), `--port`
   (`8000`), `--map`, `--population`, `--countries`.
2. **Build a `World`** (§4.2), which loads all data. This takes about 0.6 s.
3. **Start the reload thread** (§4.4) as a daemon thread, so it dies with the process.
4. **Start a `ThreadingHTTPServer`**, which handles each request on its own thread, so a slow request
   never blocks hover lookups.

### 4.2 `World`: everything held in memory

`World.__init__` loads and precomputes:

| Attribute | What it is | Built from |
|-----------|------------|------------|
| `map` | a `MapColumns` (from `convert_map.py`): terrain and cover columns plus a tile → rivers index; `map.tile(i)` builds one tile record | `world_map.json` |
| `rivers` | dict `river name → river record` (scalerank, tiles, segments) | `world_map.json` |
| `meta` | the map's `map` section | `world_map.json` |
| `terrain_grid` | 259,200 bytes: index of each tile's largest landform type | computed once |
| `cover_grid` | 259,200 bytes: index of each tile's largest cover type, 255 for open-sea tiles (no cover) | computed once |
| `pop` | the current `Population` snapshot (§4.3) | the population file (`--population`, default `world_population_start.json`) |
| `country_names` | dict `ISO code → name` | the GeoJSON (optional) |

**Loading the map fast.** Parsing the 87 MB `world_map.yaml` takes about 70 s even with PyYAML's C
loader, so the server reads `world_map.json` instead. That file stores the terrain and cover as one array of
259,200 percentages per type, plus the `map` and `rivers` sections unchanged (6.4 MB, about
0.3 s to load). Everything else in a tile record follows from the tile's position or the river lists,
so `MapColumns.tile(i)` builds a record only when `/api/tile/<id>` asks for one. `convert_map.py`
checks that rebuilding every record this way reproduces the YAML exactly.

**Startup checks.** If `world_map.json` or the population file is missing, the server exits with a
message telling you to run `convert_map.py` or `convert_population.py`. If a YAML file is newer than
its JSON, it prints a warning that the JSON is stale. If either file was made for a different grid than `grid.py` (for example an old
1° file), the server exits and asks for it to be regenerated.

### 4.3 `Population`: an immutable snapshot

The population file (`world_population_start.json` by default) stores each field as one array of 259,200 values in flat-index order:

```json
{ "format": "world_population/columnar-2",
  "map": { "total_population": 7840952769, ... },
  "country": ["GRL", null, ...],
  "population": [0, 1520, ...],
  "area_km2": [26.9, ...],
  "density_per_km2": [0.0, ...] }
```

`lat`, `lon`, `center` and `neighbors` are not stored, because they follow from the index. This is
why the file is 4.9 MB instead of 74 MB and parses in about 0.15 s.

A `Population` object parses those bytes, validates them (format id, array lengths) and precomputes:

- `grid`: the population array packed as 259,200 little-endian `uint32`s (≈1 MB), ready to send
  to the browser for the heatmap.
- `rank`: each populated tile's rank by population (#1 = most people).
- `populated_tiles`: how many tiles have anyone in them.
- `version`: a counter that goes up by one each time the file's contents change.

**Snapshots are never modified after they're built.** That is the whole thread-safety story: request
threads read `world.pop` once into a local variable and use that object for the rest of the request,
while the reload thread builds a *new* snapshot and swaps it in with a single assignment. A request
therefore sees either all-old or all-new data, never a mix, and no locks are needed.

### 4.4 The reload thread

`World.reload_population_forever()` loops forever:

```
sleep 1 s
read the population file as raw bytes
if bytes are identical to the current snapshot's bytes → nothing to do
else → build Population(raw, version + 1) and assign it to world.pop
on any read/parse/validation error → keep the old snapshot, log the error once
```

Comparing raw bytes first means the file is *read* every second but only *parsed* when it changed.
The error path matters because a program writing the file may be caught halfway through; the server
just keeps serving the last good data and tries again a second later.

### 4.5 HTTP routes (`Handler.do_GET`)

| Route | Returns | Used for |
|-------|---------|----------|
| `/` | `static/index.html` | the page |
| `/earth.jpg` | the default globe texture, `map.source_image` (browser may cache for 1 h) | fallback if no sizes are listed |
| `/earth/<width>.jpg` | the globe texture at one of the sizes listed in `/api/meta` → `textures` (cached 1 h) | globe surface |
| `/api/meta` | JSON: map metadata, all rivers, population metadata, `populated_tiles`, `population_version`, `turn` (the cohort state's current turn, or `null`) | page startup, and after each population change |
| `/api/terrain` | 259,200 bytes, dominant landform index per tile (into `map.terrain_types`) | terrain overlay |
| `/api/cover` | 259,200 bytes, dominant cover index per tile (into `map.cover_types`), 255 = open sea (no cover) | cover overlay |
| `/api/population` | 259,200 `uint32` people counts; header `X-Population-Version` | heatmap |
| `/api/population/version` | `{"version": n, "turn": t}`: the population file's version and the cohort state's current turn | the page's once-a-second poll |
| `/api/population/countries` | `{"version": n, "codes": [...], "index": [...]}`: each tile's country as 0 (none) or k → `codes[k − 1]`, in grid order (≈650 KB) | same-country heatmap tint |
| `/api/tile/<id>` | JSON: the tile record merged with its population fields and country name, plus `nation` (the nation whose people live there at the current turn) and `owner` (the nation that owns the tile), each with `is_capital` and `null` if none | side panel, on hover |
| `/api/tile/<id>/cohorts` | JSON: the tile's `people` (101 values) and `hours` (101 × activities) at the current turn, plus `turn`, `activities`, `hours_unit`; `people`/`hours` are `null` if nobody lives there | the Cohorts section, only after "Show age cohorts" |
| `/api/nations` | JSON: the current turn's nations (`id`, `code`, `name`, `capital` tile id, `alive`, `founded_turn`, `people`, `owned_tiles`, `visible_tiles`, `explored_tiles`); built once per turn | the "View as" list |
| `/api/nation/<id>/visibility` | 64,800 bytes: the nation's active-visibility bits, then its explored bits (tile i = bit i % 8 of byte i // 8); header `X-Turn` | the fog of war for "View as" |
| `/api/tile_at?lat=&lon=` | same as above, for the tile containing a point | convenience for scripts; the page doesn't use it |

Everything except the texture is sent with `Cache-Control: no-cache`, so the browser always asks for
current data. Hover lookups and version polls are left out of the console log to keep it readable.

A `/api/tile/N285E0770` response looks like:

```json
{ "id": "N285E0770", "lat": 28.5, "lon": 77.0, "center": [28.75, 77.25],
  "terrain": {"water": 0, "plains": 100, "hills": 0, "mountains": 0},
  "cover": {"grass": 88, "forest": 0, "desert": 0, "sand": 0, "wetlands": 12, "tundra": 0, "ice": 0},
  "river": false,
  "neighbors": {"n": "N290E0770", "ne": "N290E0775", ...},
  "population": { "country": "IND", "country_name": "India", "population": 25640493,
                  "area_km2": 2710.0, "density_per_km2": 9461.34, "rank": 1, "version": 1 } }
```

---

### 4.6 The game state and the engine

The game state below the tile totals is kept by a separate process, `engine/engine.py`, which writes it to
the top-level `state/` folder as NumPy `.npy` arrays plus a `manifest.json` per turn (format: `cohort_state_schema.md`):

```
engine.py step ──► state/.tmp_turn_00008/ ──rename──► state/turn_00008/ ──► state/current.json (atomic replace)
                                                                                │ checked every 1 s
server.py ◄── memory-maps the arrays of the turn current.json names ◄──────────────┘
```

- **The server reads almost nothing.** Switching turns reads only `current.json`, the manifest and
  `tiles.npy` (the populated-tile list). The big arrays are memory-mapped, so a cohort request reads just
  that tile's rows from disk (about 25 KB). A full-world state is about 120 MB per turn.
- **Turns are never changed after they are written**, so the server can read one while the engine
  writes the next. The engine keeps the newest 3 turn directories and deletes older ones. Windows won't
  delete a file another process has mapped, so a turn the server still has open is skipped and removed
  on a later step.
- **One turn = one year.** `engine/engine.py step` currently applies only ageing: every cohort moves up a
  year, the 99-year-olds join the open-ended 100+ group (their hours are averaged, weighted by people),
  and age 0 is left empty (no births or deaths yet). The activity list and the starting hours
  for each age (rough curves in `starting_hours()`) are placeholders in `engine/engine.py`.
- **Nations.** Each turn holds a record per nation (`nations.json`: id, code, capital tile, founded
  turn, alive), the nation of each populated tile (`nation.npy`), the owner of every tile
  (`owner.npy`) and two bit grids per nation:
  `visible.npy` (active visibility) and `explored.npy` (every tile ever seen; explored but not visible
  = passive visibility). Ids are never reused. The turn-0 nations and their capitals come from the
  population file's nation seed, which `init` requires. Each turn the
  engine recomputes ownership (first come, first served: the first nation to live on a tile owns it
  until its last inhabitants there are gone) and active visibility (tiles where the nation has people,
  plus the 8 around each). A
  full world (224 nations) adds 14 MB per turn.
- **Determinism.** Each manifest carries a SHA-256 checksum of the arrays; `engine/engine.py verify` rechecks
  it. The yearly step uses only elementwise arithmetic, so it doesn't depend on how NumPy orders sums.
- **The heatmap still comes from the population file (`world_population_start.json`),** not from the game state. Initialise the
  state from the same population file the server shows (`engine/engine.py init --population ...`) to keep the
  two consistent.

## 5. The page (`static/index.html`)

The JavaScript is one module, organised into sections marked `// ---------- name ----------`.

### 5.1 Coordinate helpers

`latLonToVec()` and `vecToLatLon()` convert between latitude/longitude and 3D points on the sphere.
They are written to match exactly how `THREE.SphereGeometry` lays out its texture coordinates, so a
point on the globe, the texture pixel under it and the tile id computed from it all agree.
`tileId()`, `parseTileId()` and `tileAt()` are JavaScript copies of the schema's Python helpers.

### 5.2 The 3D scene

The globe is built from several spheres nested a hair apart, each drawn on top of the previous one:

| Layer | Radius | What it is |
|-------|--------|------------|
| Globe | 1.0000 | the Blue Marble image (size chosen below), lit by a light that follows the camera (always daylight where you look, like Google Earth) |
| Terrain overlay | 1.0005 | a 720 × 360 image, one pixel per tile, coloured by dominant landform; off by default |
| Cover overlay | 1.0006 | the same for the dominant cover (including ice); open-sea tiles are transparent; off by default |
| Population heatmap | 1.0007 | a 720 × 360 image, one pixel per tile, off-white with varying transparency (§5.4) |
| Tile grid | 1.0008 | lines along every tile edge (every 0.5°), fading in only when the camera is close |
| Hover highlight | 1.0012 | translucent fill and outline of the current tile (yellow; orange when pinned) |
| Rivers | 1.0016 | one line-segments object per river, through the tile centres of each of its `segments` |
| Atmosphere | 1.06 | a blue glow around the edge (a small custom shader) |

Plus a field of 4,000 random stars far away. The overlay images use *nearest-neighbour* filtering
with no mipmaps, so each tile stays a flat block of colour instead of blending into its neighbours.
`renderOrder` values make the transparent layers draw in the order above.

### 5.3 Startup (`loadData()`)

1. `GET /api/meta`: rivers become line objects; metadata is kept in `META`.
2. `GET /api/terrain` and `GET /api/cover`: painted into the terrain and cover overlay images
   (`dominantOverlay()`).
3. `GET /api/population`: painted into the heatmap image (`loadPopulationGrid()`).

The globe image starts loading as soon as `/api/meta` arrives, in parallel with the rest; the
"Loading map…" message disappears when it arrives.

**Which globe image.** WebGL can't use a texture wider than the graphics card's `MAX_TEXTURE_SIZE`
(16,384 on most desktop browsers, 4,096–8,192 on older or low-end hardware). The server finds every
size of the image next to `map.source_image` (same name pattern, `bmng_200407_<w>x<h>.jpg`) and lists
them in `/api/meta` as `textures`. `chooseTexture()` picks the largest that fits the card. Adding
`?texture=8192` (or any width) to the page URL caps it, which is useful on laptops: the 16384 image
takes about 700 MB of graphics memory, against about 180 MB for 8192. The side panel's last line shows
which size was loaded and the card's limit. To add a size, save another 2:1 copy with the same name
pattern and restart the server.

### 5.4 The heatmap

Each tile's people count maps to one of six levels. The steps are defined per 1° × 1° of area
(`HEAT_LEVELS_PER_DEG2`: 1M, 2M, 4M, 8M, 16M) and scaled by the tile's area, so with 0.5° tiles they are:

| People in tile | Level | Tint opacity |
|----------------|-------|--------------|
| under 0.25M | 0 | none |
| 0.25M–0.5M | 1 | 10% |
| 0.5M–1M | 2 | 20% |
| 1M–2M | 3 | 30% |
| 2M–4M | 4 | 40% |
| 4M+ | 5 | 50% |

The colour is always off-white (`HEAT_RGB = [232, 228, 220]`, meant to suggest concrete). Only the
opacity changes, capped at 50% so the terrain always shows through. To retune it, edit
`HEAT_LEVELS_PER_DEG2`, `HEAT_MAX_ALPHA` or `HEAT_RGB` near the top of the script. The same constants drive
the legend and the level swatches in the side panel.

**Same-country tint.** The heatmap tiles of the **pinned** tile's country turn **cyan**
(`PINNED_TINT_RGB`), and those of the **hovered** tile's country turn **bright yellow** (`HOVER_TINT_RGB`,
pure yellow 255, 255, 0). Every other heatmap tile keeps its colour.
- **Both at once:** while a tile is pinned, hovering another country shows that country in yellow
  alongside the cyan one. If they're the same country, cyan wins.
- **How it's drawn:** a second pass over the heatmap's kept pixels (`heatImg`). Only the colours of the
  affected countries' tiles are rewritten, never the opacity, so tiles too sparsely populated for a
  tint stay invisible. `updateCountryTints()` runs whenever the hovered or pinned tile changes, and
  repaints only if either country changed.
- **Where the data comes from:** each tile's country arrives with the population data
  (`/api/population/countries`) and is turned into a tile list per country (`setCountryIndex()`).
- **After a population reload,** the tint is reapplied.
- **Legend:** names the tinted countries, in their tint colours.

### 5.5 Hovering: from mouse to side panel

```
mouse moves ──► needsPick = true
                    │
next animation frame (at most once per frame)
                    │
raycast from camera through the cursor ──► hit point on the globe
                    │
vecToLatLon() ──► tileAt() ──► "N285E0770"
                    │
tile changed? ──no──► just update the cursor coordinates
                    │yes
showTile(id) ──► highlight drawn immediately
                    │
fetchTile(id) ──► GET /api/tile/N285E0770 (or reuse cached response)
                    │
renderTile() ──► side panel HTML
```

Details:

- **Throttling.** Mouse events only set a flag; the raycast runs once per frame, so fast mouse
  movement doesn't flood the server. Camera movement also sets the flag, because the tile under a
  still cursor changes when the globe turns.
- **Caching.** Tile responses are cached in the browser under the key `"<id>@<population version>"`,
  so moving back over a tile is instant, and a population change automatically invalidates old entries.
- **Races.** If you move to a new tile before the previous response arrives, the old response is
  ignored (`shownTid !== tid`).
- **Pinning.** A click (not a drag) pins the tile, so the panel stops following the mouse. Click it
  again or press Esc to unpin. The "Go to" box also pins the tile it flies to.

The side panel shows, top to bottom: tile id and bounds with cursor position; **Population** (country,
people, heat level, tile area, and a note for tiles outside the data's 89.1°S–89.1°N coverage);
**Owner** (the nation that owns the tile, marked "capital" on its capital tile; "nobody" if unowned),
**Inhabitants** (only when the people living there belong to a different nation than the owner) and,
while viewing as a nation, how that nation sees the tile (active, passive or unknown);
**Cohorts** (see below); **Terrain** (the landform) and **Cover** (what is on the land and frozen water; "Open water only" when
there is none), each as a stacked bar plus a bar per non-zero type (`percentBlock()`); **River**
(yes/no and names).

**Cohorts** are loaded only on request. On a pinned tile the section shows a "Show age cohorts" button;
clicking it fetches `/api/tile/<id>/cohorts` and draws people by year of age, with a bar per
activity for hours per person per day below it. The hours bars show the average of all ages
(weighted by people), and switch to a single age while its bar on the chart is hovered. The section then stays open for every tile you pin until you click
"Hide". Responses are cached per tile and turn. People counts are fractions in the state and are
rounded only for display.

**View as** (toolbar) lists the current turn's nations. Choosing one fetches its visibility bits and
paints a fog layer over the globe: unknown tiles dark, passive tiles dimmed, actively visible tiles
clear, with the nation's capital outlined. "Everyone" removes the fog. The fog is only a view: the
server still sends every tile's data, so it is not yet a real fog of war for players. Rivers are drawn
above the fog. The list and fog reload when the engine advances a turn.

### 5.6 Live population updates

```
every 1 s:  GET /api/population/version
            same as popVersion? ──► stop
            different? ──► GET /api/meta          (new total, populated-tile count)
                           GET /api/population    (repaint heatmap image)
                           drop stale cached tiles
                           re-render the hovered or pinned tile
                           flash "Data version n · loaded hh:mm:ss" in the legend
            turn changed? ──► drop cached cohorts, re-render the panel (reloading open cohorts)
```

So the end-to-end delay from saving the file to seeing it on screen is at most about two seconds
(up to one second for the server's reread, plus up to one for the page's poll). Polling was chosen
over push (WebSockets or server-sent events) because it needs no extra libraries, survives server
restarts without special handling, and one tiny request per second is negligible on localhost.

### 5.7 Camera and controls

Three.js's `OrbitControls` handle drag-to-rotate and scroll-to-zoom, with panning disabled. Each frame,
rotation and zoom speed are scaled by the camera's altitude, so the globe doesn't whip past when
you're close to the surface. Double-click and the "Go to" box (`N275E0865` or `27.9, 86.9`) both use
`flyTo()`, which swings the camera along an arc and eases its altitude over 1.2 s.

---

## 6. Workflows

**Run the viewer**

```
python server.py            # then open http://localhost:8000/
```

**Run the turn engine (cohorts)**

```
python ../engine/engine.py init      # turn 0 from world_population_start.json and its nation seed,
                                     # with a hunter-gatherer age structure
python ../engine/engine.py step      # advance one year (--turns N for more)
python ../engine/engine.py verify    # recheck the current turn's checksum
```

The running server and any open page pick up each new turn within about two seconds. To start over,
delete the top-level `state/` folder and run `init` again.

**After changing the starting scenario**

```
python gen_start_capitals.py    # writes world_population_start.json, its nation seed and the YAML copy
```

The running server picks up the new JSON within a second; no restart needed. Start a new game state
(delete `state/`, run `init`) to use it in the engine.

**After regenerating the full-world prototype data**

```
python gen_population.py        # writes world_population.yaml
python convert_population.py    # writes and verifies world_population_current.json
```

**After regenerating the map**

```
python gen_map.py               # writes world_map.yaml
python convert_map.py           # writes and verifies world_map.json (≈90 s, mostly reading the YAML)
```

Then restart the server.

**See live updates in action**

```
python demo_delhi.py        # Ctrl+C to stop; restores Delhi's real value
```

**When do I need to restart the server?** Only after changing `server.py`, `cohort_state.py`,
`world_map.json` or the GeoJSON. Changes to `static/index.html` need only a browser refresh; changes to
the population file need nothing.

---

## 7. Limits and things to know

- **Local only.** The server listens on `127.0.0.1` and has no authentication. Pass `--host 0.0.0.0`
  only on a network you trust.
- **Population JSON goes stale silently.** The server watches only its population file. Edits to
  `world_population.yaml` do nothing until you rerun `convert_population.py`, and changes to
  `gen_start_capitals.py` do nothing until you rerun it.
- **Map JSON goes stale silently too.** Edits to `world_map.yaml` do nothing until you rerun
  `convert_map.py` and restart the server. You get a warning only at server startup.
- **`world_map.json` is not live.** It is loaded once; terrain/river changes need a restart.
- **Write the JSON atomically.** Anything that rewrites the population file should write a
  temporary file and rename it over the original (as `demo_delhi.py` does). The server tolerates
  half-written files, but atomic writes avoid the logged errors.
- **`demo_delhi.py` restores only on Ctrl+C.** If it is killed another way (closing the window,
  Task Manager), Delhi keeps its last random value. Rerun `convert_population.py` to restore the
  file from the YAML.
- **Needs internet for Three.js.** The page loads Three.js from a CDN. For offline use, download
  `three.module.js` and `OrbitControls.js` into `static/` and point the import map at them; the
  server would then need a route to serve them.
- **NumPy is now required** by the server and the engine (for the cohort state).
- **Thin lines.** WebGL draws lines one pixel wide on most systems, so rivers and the grid look thin
  on high-resolution screens.
