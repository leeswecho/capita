# capita

A turn-based world simulation on a Civilization-style map of the real Earth. The planet is divided
into a **0.5° × 0.5° grid (720 × 360 = 259,200 tiles)**. Each tile records its landform, land
cover, rivers and population. A turn engine advances age cohorts of people, nations, tile ownership
and visibility one year at a time. Players play from the player page, which shows only what their
nation knows; a developer view shows everything on an interactive 3D globe.

A civilization is its people. People are organised under **leaders** (chieftain, king,
pioneer, lord, mayor, superintendent, admiral, general), each of whom marshals a **segment**: the people directly
under them. A leader's **following** is their segment plus the segments of every leader below them,
and the player is the nation's root leader. Units and cities are the same idea: an army is a segment
under a general, a town is a segment under a mayor that has stopped moving, and a settling party is a
new leader given part of a segment. Each segment has a position on the map: a tile, or partway between
two neighbouring tiles.

## Repository layout

| Path | What it is |
|------|------------|
| `server/` | The developer view, a globe that shows the whole game state and its mechanics: `server.py` (Python HTTP server), `static/index.html` (Three.js page), the shared tile grid (`grid.py`), the cohort-state reader/writer (`cohort_state.py`), data converters and generators, and the JSON data and globe images the server loads. |
| `player/` | The player page: `player.py` (Python HTTP server), `static/index.html` and `descriptions.json` (how the page puts a segment's state into words). A player sees only what their nation knows, on a map locked to a handful of tiles. |
| `engine/` | `engine.py`, the turn engine. It runs as its own process and writes the game state to `state/`. |
| `control/` | The developer's control page, served separately from the developer view: `control.py` (Python HTTP server) and `static/index.html`. It lists the nation's leaders, each of whom can be ordered to move, and has a **Commit Turn** button, which advances the engine one turn. |
| `deploy/` | How the public server at `capita.leeswecho.net` is set up: systemd services for the three servers, and the Caddy config that serves them over HTTPS. |
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
python server.py                      # then open http://localhost:9000/
```

In a second terminal, from `server/`, start a game state and advance it:

```sh
python ../engine/engine.py init       # turn 0 from world_population_start.json and its nation seed
python ../engine/engine.py step       # advance one year (--turns N for more)
python ../engine/engine.py verify     # recheck the current turn's checksum
```

The running server and any open page pick up each new turn within about two seconds. To start over,
delete `state/` and run `init` again.

To play, start the player server (from the repository root):

```sh
python player/player.py               # then open http://localhost:8000/
```

The page starts black, with only a menu to choose the nation you will lead, and rename it if you like
(`state/names.json`; every page shows the new name). Then you see the land around your people and
nothing else: the map is locked to a handful of tiles, and you can't zoom or pan to see more than a
couple of tiles past what your nation has explored. Tiles you have seen before but can't see now are dimmed, and the rest are
black. Choose a tile to see its landforms and what covers its land (each largest first, sized roughly by
how much of the tile they cover), its name (yours alone: click "Unnamed land" to name it) and the
leaders there. Nations are shown by their flags, and landforms and cover by small icons; hover over one for its name. Your own leader shows
their title and people; choosing them offers the orders they can take: **Move**, to one of the eight
directions around, and **Found a kingdom**, which settles your people where they stand to farm and herd,
and makes your chieftain a king under the title you choose. A king can **Form settlers**: a party of
200 or more people (keeping at least 200) sets out under a Pioneer, drawn as covered wagons, who can
move and **Establish a settlement**, becoming a lord under a title you choose. A box under the leaders tells what is known about one of them: for your own,
their people and how they seem (from `player/descriptions.json`); for another nation's, roughly how
big their group looks. It shows your leader with the most people there, or whichever leader you
point at or last clicked. The side bar is only
about the chosen tile. A small **Orders** box at the top left of the map lists the orders given this
turn, and **Commit Turn**, at the bottom left, resolves the turn for the whole world. The panels are stone, for the stone age; the page is built to change its
look as society advances.

To drive turns from the developer's side, start the control server (from the repository root):

```sh
python control/control.py             # then open http://localhost:8001/
```

First choose the nation you're playing (or open `http://localhost:8001/?nation=AUS`). The page lists
the nation's leaders. **Move** next to a leader lists the tiles around their segment, and choosing one
saves a move command in the turn's orders (`state/orders/`). The orders are listed above Commit Turn,
one per leader: a new command for a leader replaces their old one, and each can be cancelled until you
commit. **Commit Turn** means the player has entered all their commands for the turn. It runs `engine.py step` as its own
process, and the viewer on port 9000 shows the new turn by itself. The control page and the viewer are
kept separate on purpose for now. Below it, the page lists the food each of the nation's segments
produced in the past year, how much of its people's need that met, and how many people died of each
cause.

Each turn has two phases, which the engine records in `state/phase.json` and both pages show:
**orders** (the turn accepts commands) and then **resolving** (commands are closed while the engine
applies them and a year's passage of time to compute the next turn, which opens for orders).

Useful server options: `--port` (default `9000`), `--host` (default `127.0.0.1`, local only, no
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
- **Current turn rules** (placeholders): turn 0 has one segment per populated tile, each under its
  own chieftain, reporting to the chieftain on the nation's capital. A move command sends a
  segment to a neighbouring tile that isn't all water, on foot, at a pace set by its people's free
  hours (`engine/movement.json`). That is thousands of kilometres a year, so a one-tile move finishes
  within the turn. The segment then lives the year where it ends up. Founding a kingdom settles a
  segment for good: it farms instead of foraging, can't move, has more children and more infection, and
  no other nation can settle its tile. A king can send out settling parties, which settle in turn. Each segment's people forage or farm for food (kcal),
  with returns that level off at what their tile's terrain can sustain for each, shared among the
  segments there, and they work only the hours their food needs. Farmers feed their nation's other
  segments on the tile with what they don't need; foragers work the land the farms leave. People eat according to
  their age, everyone in a segment eating less when it hasn't enough, and food that isn't eaten decays
  completely. The rules and their
  values are in `engine/economy.json`. Children are born at a placeholder rate by age
  (`engine/demography.json`), and people die of starvation, predation and accidents, cancer,
  other age-related causes, infection and child mortality, each with its own rule in the same file.
  Then every cohort ages one year. Ownership is first come, first served, claimed by segments that
  aren't moving. Each nation actively sees every tile where its segments have people, plus the 8 around
  each (`engine/visibility.json`). Leader types are in `engine/leaders.json`.

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
  (`capita/cohort-state-8`), written so an engine in any language can produce it.
- [world_map_schema.md](reference/world_map_schema.md): the `world_map.yaml` and `world_map.json`
  formats, tile ids, grid order and guaranteed properties.
- [world_map_generation.md](reference/world_map_generation.md): how the map was built, including
  inputs, calibration, rules and known limitations.
- [world_population_schema.md](reference/world_population_schema.md): the `world_population.yaml`
  format and its sources.

## License

MIT. See [LICENSE](LICENSE).
