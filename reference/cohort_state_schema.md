# Game state format (`capita/cohort-state-3`)

The game state holds, for every populated tile, the people of each single year of age, how many hours
a day they spend on each activity, and which nation they belong to. It also holds a record for each
nation, which nation owns each tile, and what each nation can see. The turn engine (`engine/engine.py`) writes it, and the web server
(`server/server.py`) reads it. This document is the contract between the two, written so that an engine
in any language can produce it. `server/cohort_state.py` is the Python implementation.

Version 2 added nations (`nations.json`, `nation.npy`) and visibility (`visible.npy`, `explored.npy`);
version 3 added tile ownership (`owner.npy`). Older states can't be read; delete the state folder and
run `engine.py init` again.

## Directory layout

```
state/
  current.json          pointer to the current turn
  turn_00007/           one directory per turn; never changed once current.json has named it
    manifest.json
    nations.json
    tiles.npy
    people.npy
    hours.npy
    nation.npy
    owner.npy
    visible.npy
    explored.npy
  turn_00008/
  ...
```

Turn directories are named `turn_` + the turn number as 5 digits, zero-padded.

## `current.json`

```json
{ "format": "capita/cohort-state-3", "turn": 7, "dir": "turn_00007" }
```

Readers open the directory named by `dir`. Writers replace this file atomically: write
`current.json.tmp`, then rename it over `current.json`.

## Arrays

All are NumPy `.npy` files (format version 1.0): a 10-byte prefix, a text header, then raw array data.
Use little-endian data in C (row-major) order, `fortran_order: False`. `N` is the number of populated
tiles and `M` is the number of nations + 1.

| file | dtype | shape | meaning |
|------|-------|-------|---------|
| `tiles.npy` | `<u4` (uint32) | `[N]` | The flat grid index of each row's tile: `row * 720 + col`, with row 0 = 89.5–90°N and col 0 = 180–179.5°W (see `server/grid.py`). **Strictly increasing**, so readers can binary-search it. |
| `people.npy` | `<f8` (float64) | `[N, 101]` | People in the tile by single year of age. Column `a` holds age `a` for `a` = 0..99; **column 100 is "100 and over"**. Fractions are allowed and are rounded only for display. |
| `hours.npy` | `<f4` (float32) | `[N, 101, A]` | For each tile, age and activity: the mean hours per person per day spent on that activity, averaged over the year. Activities are in `manifest.activities` order. They needn't sum to 24; any remainder is unassigned time. |
| `nation.npy` | `<u2` (uint16) | `[N]` | The id of the nation the row's people belong to; 0 = no nation. |
| `owner.npy` | `<u2` (uint16) | `[259200]` | The id of the nation that **owns** each tile of the whole grid (flat index), 0 = nobody. Unlike the other per-tile arrays it covers every tile, because future rules may let nations own tiles nobody lives on. |
| `visible.npy` | `<u1` (uint8) | `[M, 32400]` | **Active visibility**: tiles the nation can see this turn. Row `k` is nation id `k`, one bit per tile (below). |
| `explored.npy` | `<u1` (uint8) | `[M, 32400]` | **Explored** tiles: every tile the nation has ever actively seen, including this turn. A tile that is explored but not visible is **passively** visible: its geography is known, nothing else. |

Only tiles with people need a row; a tile with no row has no people. `N` may change from turn to turn.

### Visibility bits

Each visibility row is the grid's 259,200 tiles packed 8 to a byte, **little-endian bit order**: tile
`i` (the flat index above) is bit `i % 8` of byte `i // 8`, where bit 0 is the least significant.
259,200 / 8 = 32,400 bytes per row. In NumPy: `np.packbits(flags, axis=-1, bitorder="little")`.

Row 0 belongs to "no nation" and is all zeros, so nation id `k` is simply row `k`. Every visible bit
must also be set in `explored`. The three states of a tile for a nation are:

| explored | visible | state |
|---|---|---|
| 0 | 0 | unknown: never seen |
| 1 | 0 | passive: the geography is known, nothing else |
| 1 | 1 | active: seen this turn |

### Writing `.npy` without NumPy

```
bytes 0-5   \x93NUMPY
byte  6     1            major version
byte  7     0            minor version
bytes 8-9   HEADER_LEN   uint16, little-endian
header      ASCII, e.g. {'descr': '<f8', 'fortran_order': False, 'shape': (49910, 101), }
            padded with spaces and ending in \n so that 10 + HEADER_LEN is a multiple of 64
data        the array, little-endian, row-major
```

A one-dimensional shape is written `(49910,)`. Ready-made libraries include `ndarray-npy` (Rust),
`cnpy` (C++), `npyio` (Go) and `NPZ.jl` (Julia). Julia and Fortran store arrays column by column, so
transpose before writing, or write `fortran_order: True` with the reversed memory layout.

## `nations.json`

```json
{ "nations": [
  { "id": 1, "code": "AUS", "capital": 180658, "founded_turn": 0, "alive": true },
  { "id": 2, "code": "AUT", "capital": 60152,  "founded_turn": 0, "alive": true }
] }
```

| key | meaning |
|-----|---------|
| `id` | The nation's number, 1, 2, 3... in list order. Ids are never reused: a nation that disappears keeps its record with `alive: false`, so old references (such as player decisions in a replay log) stay valid. |
| `code` | A short identifier. At turn 0 it's the ISO 3166-1 alpha-3 code the nation was seeded from. The server shows the country name for it. |
| `capital` | The flat grid index of the capital tile, or `null`. |
| `founded_turn` | The turn the nation came into being. |
| `alive` | Whether the nation still exists. |

Every record has exactly these keys. Add new per-nation fields here when they are descriptive. Fields
the engine computes over all nations at once (stores, technology levels...) should become arrays
indexed by nation id instead.

## `manifest.json`

```json
{
  "format": "capita/cohort-state-3",
  "turn": 7,
  "grid": { "tile_size_degrees": 0.5, "width": 720, "height": 360 },
  "ages": { "count": 101, "last_is_open_ended": true },
  "activities": ["sleep", "subsistence", "childcare", "other"],
  "hours_unit": "mean hours per person per day, averaged over the year",
  "visibility_bits": "row k = nation id k; tile i is bit i % 8 of byte i // 8",
  "arrays": {
    "tiles":    { "file": "tiles.npy",    "dtype": "<u4", "shape": [49910] },
    "people":   { "file": "people.npy",   "dtype": "<f8", "shape": [49910, 101] },
    "hours":    { "file": "hours.npy",    "dtype": "<f4", "shape": [49910, 101, 4] },
    "nation":   { "file": "nation.npy",   "dtype": "<u2", "shape": [49910] },
    "owner":    { "file": "owner.npy",    "dtype": "<u2", "shape": [259200] },
    "visible":  { "file": "visible.npy",  "dtype": "<u1", "shape": [225, 32400] },
    "explored": { "file": "explored.npy", "dtype": "<u1", "shape": [225, 32400] }
  },
  "populated_tiles": 49910,
  "nations": 224,
  "total_people": 7840952769.0,
  "checksum": { "algorithm": "sha256", "of": "...", "value": "845b2053..." },
  "initial_population_source": "world_population_start.json",
  "initial_nations_source": "world_population_start_capitals.nations.json"
}
```

- `checksum.value` is the SHA-256 of the raw data bytes (not the `.npy` headers) of `tiles`, `people`,
  `hours`, `nation`, `owner`, `visible` and `explored`, in that order, followed by the `nations` list as
  canonical JSON: sorted keys, no whitespace (`","` and `":"` separators), UTF-8. It shows whether a
  deterministic replay reproduced a turn exactly. `total_people` is informational only and isn't part
  of the checksum.
- Readers must check `format` and `grid`, and must ignore keys they don't know. Writers may add keys,
  such as `initial_population_source` and `initial_nations_source`.

## Writing a turn

1. Write all the files into `state/.tmp_turn_NNNNN/`.
2. Rename that directory to `state/turn_NNNNN/`. A turn that already exists is never overwritten.
3. Replace `current.json` atomically so it points at the new directory.
4. Optionally delete old turn directories, never the current one. On Windows, deleting a directory
   fails while a reader still has its files memory-mapped; skip it and try again later.

## Turn rules (`engine/engine.py`, for now)

One turn is one year. Each turn every cohort moves up one year of age. The 99-year-olds join the
100+ group, whose hours become the average of the two groups weighted by people. Age 0 is left empty,
because there are no births or deaths yet. Activity hours move with the people. The activity list and
its starting hours are placeholders in `engine/engine.py` (`starting_hours()`): rough smooth curves by
age, so that babies sleep more, subsistence work starts around age 10 and peaks in adulthood, and
childcare peaks around age 30.

**Nations at turn 0** (`make_nations()`) come from a **nation seed**, which `init` requires: `--nations`,
or the `<population name>.nations.json` / `<population name>_*.nations.json` next to the population file
(`world_population_start_capitals.nations.json`, `world_population_current_capitals.nations.json`). It
lists the nations in id order with their capital tiles:
`{"nations": [{"code": "DNK", "capital": "N555E0125"}, ...]}`; other keys (`capital_name`, `note`) are
ignored. A capital's tile belongs to its nation; every other populated tile goes to the nation whose
code is the tile's country code, and codes not in the seed get no nation.

**Ownership, recomputed every turn** (`update_ownership()`): first come, first served. A nation owns a
tile from the turn it is the first to have people living there, and keeps it while any of its people
live there. When the owner's last inhabitants are gone (for example, they all died), the tile passes to
a nation that has people there now, or to nobody. At turn 0 every populated tile belongs to the nation
living on it. The rule is expected to change (treaties, contested areas...); readers only use
`owner.npy`.

**Visibility, recomputed every turn** (`active_visibility()`): a nation actively sees every tile where
it has people, plus the 8 tiles around each one (east–west wraps around; nothing past the poles). Then
`explored = explored OR visible`. The rule is expected to change as the game develops; readers only
use the stored bits.
