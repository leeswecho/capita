# Cohort state format (`capita/cohort-state-1`)

The cohort state holds, for every populated tile, the people of each single year of age and how many
hours a day they spend on each activity. The turn engine (`engine/engine.py`) writes it, and the web
server (`server/server.py`) reads it. This document is the contract between the two, written so that
an engine in any language can produce it. `server/cohort_state.py` is the Python implementation.

## Directory layout

```
state/
  current.json          pointer to the current turn
  turn_00007/           one directory per turn; never changed once current.json has named it
    manifest.json
    tiles.npy
    people.npy
    hours.npy
  turn_00008/
  ...
```

Turn directories are named `turn_` + the turn number as 5 digits, zero-padded.

## `current.json`

```json
{ "format": "capita/cohort-state-1", "turn": 7, "dir": "turn_00007" }
```

Readers open the directory named by `dir`. Writers replace this file atomically: write
`current.json.tmp`, then rename it over `current.json`.

## Arrays

All three are NumPy `.npy` files (format version 1.0): a 10-byte prefix, a text header, then raw
array data. Use little-endian data in C (row-major) order, `fortran_order: False`.

| file | dtype | shape | meaning |
|------|-------|-------|---------|
| `tiles.npy` | `<u4` (uint32) | `[N]` | The flat grid index of each row's tile: `row * 720 + col`, with row 0 = 89.5–90°N and col 0 = 180–179.5°W (see `server/grid.py`). **Strictly increasing**, so readers can binary-search it. |
| `people.npy` | `<f8` (float64) | `[N, 101]` | People in the tile by single year of age. Column `a` holds age `a` for `a` = 0..99; **column 100 is "100 and over"**. Fractions are allowed and are rounded only for display. |
| `hours.npy` | `<f4` (float32) | `[N, 101, A]` | For each tile, age and activity: the mean hours per person per day spent on that activity, averaged over the year. Activities are in `manifest.activities` order. They needn't sum to 24; any remainder is unassigned time. |

Only tiles with people need a row; a tile with no row has no people. `N` may change from turn to turn.

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

## `manifest.json`

```json
{
  "format": "capita/cohort-state-1",
  "turn": 7,
  "grid": { "tile_size_degrees": 0.5, "width": 720, "height": 360 },
  "ages": { "count": 101, "last_is_open_ended": true },
  "activities": ["sleep", "subsistence", "childcare", "other"],
  "hours_unit": "mean hours per person per day, averaged over the year",
  "arrays": {
    "tiles":  { "file": "tiles.npy",  "dtype": "<u4", "shape": [49910] },
    "people": { "file": "people.npy", "dtype": "<f8", "shape": [49910, 101] },
    "hours":  { "file": "hours.npy",  "dtype": "<f4", "shape": [49910, 101, 4] }
  },
  "populated_tiles": 49910,
  "total_people": 7840952769.0,
  "checksum": { "algorithm": "sha256", "of": "tiles, people, hours raw bytes in that order",
                "value": "7681ea31..." },
  "initial_population_source": "world_population.json"
}
```

- `checksum.value` is the SHA-256 of the raw data bytes (not the `.npy` headers) of `tiles`, then
  `people`, then `hours`, concatenated. It shows whether a deterministic replay reproduced a turn
  exactly. `total_people` is informational only and isn't part of the checksum.
- Readers must check `format` and `grid`, and must ignore keys they don't know. Writers may add keys,
  such as `initial_population_source`.

## Writing a turn

1. Write all four files into `state/.tmp_turn_NNNNN/`.
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
