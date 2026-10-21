# Game state format (`capita/cohort-state-5`)

The game state holds, for every populated tile, the people of each single year of age, how many hours
a day they spend on each activity, and which nation they belong to. It also holds a record for each
nation, which nation owns each tile, and what each nation can see, and for every populated tile what
it produced in the past year and what it holds of each product. The turn engine (`engine/engine.py`) writes it, and the web server
(`server/server.py`) reads it. This document is the contract between the two, written so that an engine
in any language can produce it. `server/cohort_state.py` is the Python implementation.

Version 2 added nations (`nations.json`, `nation.npy`) and visibility (`visible.npy`, `explored.npy`);
version 3 added tile ownership (`owner.npy`); version 4 added products and production (`production.npy`,
`labor.npy`, `production_max.npy`, `decay.npy`, `stock.npy`, and `products` in the manifest); version 5
added consumption (`need.npy`, `consumed.npy`). Older states can't be read; delete the state folder and
run `engine.py init` again.

## Directory layout

```
state/
  current.json          pointer to the current turn
  phase.json            the current turn's phase: accepting commands, or resolving
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
    production.npy
    labor.npy
    production_max.npy
    need.npy
    consumed.npy
    decay.npy
    stock.npy
  turn_00008/
  ...
```

Turn directories are named `turn_` + the turn number as 5 digits, zero-padded.

## `current.json`

```json
{ "format": "capita/cohort-state-5", "turn": 7, "dir": "turn_00007" }
```

Readers open the directory named by `dir`. Writers replace this file atomically: write
`current.json.tmp`, then rename it over `current.json`.

## `phase.json`

```json
{ "format": "capita/cohort-state-5", "turn": 7, "phase": "resolving", "since": "2026-10-21T20:28:13Z" }
```

Each turn goes through two phases:

| phase | meaning |
|-------|---------|
| `orders` | The turn is open: players can enter commands for it, and commit it. |
| `resolving` | The turn's commands are closed. The engine is applying them, along with a year's passage of time, to compute the next turn. |

The turn number always names a finished turn; the phase says what is happening to it. Resolving turn
7 ends when turn 8 is written, which opens in `orders`. `since` is when the phase began (UTC, ISO 8601).

The file is separate from the turn directories and is not part of any checksum, because it changes
while the turn itself doesn't. It applies **only to the turn it names**: if it is missing, or names a
turn other than the current one, the current turn is in `orders`. That lets the writer replace
`current.json` first and `phase.json` second, with no wrong reading in between. `cohort_state.read_phase()`
applies this rule. Writers replace the file atomically, like `current.json`.

A turn left `resolving` by an engine that was killed outright stays so until `engine.py step` runs
again, which resolves it normally. A step that fails with an error puts its turn back in `orders`.

## Arrays

All are NumPy `.npy` files (format version 1.0): a 10-byte prefix, a text header, then raw array data.
Use little-endian data in C (row-major) order, `fortran_order: False`. `N` is the number of populated
tiles, `M` is the number of nations + 1, and `P` is the number of products.

| file | dtype | shape | meaning |
|------|-------|-------|---------|
| `tiles.npy` | `<u4` (uint32) | `[N]` | The flat grid index of each row's tile: `row * 720 + col`, with row 0 = 89.5–90°N and col 0 = 180–179.5°W (see `server/grid.py`). **Strictly increasing**, so readers can binary-search it. |
| `people.npy` | `<f8` (float64) | `[N, 101]` | People in the tile by single year of age. Column `a` holds age `a` for `a` = 0..99; **column 100 is "100 and over"**. Fractions are allowed and are rounded only for display. |
| `hours.npy` | `<f4` (float32) | `[N, 101, A]` | For each tile, age and activity: the mean hours per person per day spent on that activity, averaged over the year. Activities are in `manifest.activities` order. They needn't sum to 24; any remainder is unassigned time. |
| `nation.npy` | `<u2` (uint16) | `[N]` | The id of the nation the row's people belong to; 0 = no nation. |
| `owner.npy` | `<u2` (uint16) | `[259200]` | The id of the nation that **owns** each tile of the whole grid (flat index), 0 = nobody. Unlike the other per-tile arrays it covers every tile, because future rules may let nations own tiles nobody lives on. |
| `visible.npy` | `<u1` (uint8) | `[M, 32400]` | **Active visibility**: tiles the nation can see this turn. Row `k` is nation id `k`, one bit per tile (below). |
| `explored.npy` | `<u1` (uint8) | `[M, 32400]` | **Explored** tiles: every tile the nation has ever actively seen, including this turn. A tile that is explored but not visible is **passively** visible: its geography is known, nothing else. |
| `production.npy` | `<f8` (float64) | `[N, P]` | How much of each product the tile made in the **year that ended with this turn**, in the product's unit. All zeros at turn 0, which follows no year. |
| `labor.npy` | `<f8` (float64) | `[N, P]` | Person-hours of work that went into each product that year. |
| `production_max.npy` | `<f8` (float64) | `[N, P]` | The most the tile can produce of each product in a year: production levels off here however much labor goes in. |
| `need.npy` | `<f8` (float64) | `[N, P]` | How much of each product the tile's people needed that year: people × the per-person daily amount for their age × 365. 0 for products nobody consumes. |
| `consumed.npy` | `<f8` (float64) | `[N, P]` | How much they actually consumed: the need, or all the tile held if that was less. A shortfall is shared evenly, so people of every age consumed `consumed / need` of their need. |
| `decay.npy` | `<f4` (float32) | `[N, P]` | The share of the tile's holdings of each product lost at the end of that year, 0–1. Per tile, so that future rules can change it locally. |
| `stock.npy` | `<f8` (float64) | `[N, P]` | How much of each product the tile holds at the start of this turn, after decay. |

Products are columns in `manifest.products` order.

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
  "format": "capita/cohort-state-5",
  "turn": 7,
  "grid": { "tile_size_degrees": 0.5, "width": 720, "height": 360 },
  "ages": { "count": 101, "last_is_open_ended": true },
  "activities": ["sleep", "subsistence", "childcare", "other"],
  "hours_unit": "mean hours per person per day, averaged over the year",
  "products": [{ "name": "food", "unit": "kcal" }],
  "visibility_bits": "row k = nation id k; tile i is bit i % 8 of byte i // 8",
  "arrays": {
    "tiles":    { "file": "tiles.npy",    "dtype": "<u4", "shape": [49910] },
    "people":   { "file": "people.npy",   "dtype": "<f8", "shape": [49910, 101] },
    "hours":    { "file": "hours.npy",    "dtype": "<f4", "shape": [49910, 101, 4] },
    "nation":   { "file": "nation.npy",   "dtype": "<u2", "shape": [49910] },
    "owner":    { "file": "owner.npy",    "dtype": "<u2", "shape": [259200] },
    "visible":  { "file": "visible.npy",  "dtype": "<u1", "shape": [225, 32400] },
    "explored": { "file": "explored.npy", "dtype": "<u1", "shape": [225, 32400] },
    "production":     { "file": "production.npy",     "dtype": "<f8", "shape": [49910, 1] },
    "labor":          { "file": "labor.npy",          "dtype": "<f8", "shape": [49910, 1] },
    "production_max": { "file": "production_max.npy", "dtype": "<f8", "shape": [49910, 1] },
    "need":           { "file": "need.npy",           "dtype": "<f8", "shape": [49910, 1] },
    "consumed":       { "file": "consumed.npy",       "dtype": "<f8", "shape": [49910, 1] },
    "decay":          { "file": "decay.npy",          "dtype": "<f4", "shape": [49910, 1] },
    "stock":          { "file": "stock.npy",          "dtype": "<f8", "shape": [49910, 1] }
  },
  "populated_tiles": 49910,
  "nations": 224,
  "total_people": 7840952769.0,
  "checksum": { "algorithm": "sha256", "of": "...", "value": "845b2053..." },
  "initial_population_source": "world_population_start.json",
  "initial_nations_source": "world_population_start_capitals.nations.json",
  "economy_rules_source": "engine/economy.json",
  "economy_rules": { "products": { ... }, "foraging": { ... } },
  "demography_rules_source": "engine/demography.json",
  "demography_rules": { "births": { ... } }
}
```

- `products` lists each product's `name` and `unit`, in the order of the product columns.
- `economy_rules` is a copy of the rules the engine used to compute this turn's production and decay
  (`engine/economy.json` at the time), so a replay can tell whether they changed. `demography_rules`
  is the same for births (`engine/demography.json`).
- `checksum.value` is the SHA-256 of the raw data bytes (not the `.npy` headers) of `tiles`, `people`,
  `hours`, `nation`, `owner`, `visible`, `explored`, `production`, `labor`, `production_max`, `need`, `consumed`, `decay` and
  `stock`, in that order, followed by the `nations` list as
  canonical JSON: sorted keys, no whitespace (`","` and `":"` separators), UTF-8. It shows whether a
  deterministic replay reproduced a turn exactly. `total_people` is informational only and isn't part
  of the checksum.
- Readers must check `format` and `grid`, and must ignore keys they don't know. Writers may add keys,
  such as `initial_population_source` and `initial_nations_source`.

## Writing a turn

1. Set the current turn's phase to `resolving`, before reading it.
2. Write all the files into `state/.tmp_turn_NNNNN/`.
3. Rename that directory to `state/turn_NNNNN/`. A turn that already exists is never overwritten.
4. Replace `current.json` atomically so it points at the new directory.
5. Set the new turn's phase to `orders`. (If anything before step 4 fails, set the old turn back to
   `orders` instead.)
6. Optionally delete old turn directories, never the current one. On Windows, deleting a directory
   fails while a reader still has its files memory-mapped; skip it and try again later.

## Turn rules (`engine/engine.py`, for now)

One turn is one year. Each turn every cohort moves up one year of age. The 99-year-olds join the
100+ group, whose hours become the average of the two groups weighted by people. Age 0 holds the
children born during the year (below); there are no deaths yet. Activity hours move with the people,
and age 0 keeps its own. The activity list and
its starting hours are placeholders in `engine/engine.py` (`starting_hours()`): rough smooth curves by
age, so that babies sleep more, subsistence work starts around age 10 and peaks in adulthood, and
childcare peaks around age 30.

**Production, before ageing** (`produce()`): the year that is ending is worked by the current turn's
people with their current hours, so production is computed before they age, and stored in the new
turn. The rules and all their values are in `engine/economy.json`, which the engine reads on every
step. For now the only product is **food**, in kcal, made by **foraging** (hunting and gathering):

- Labor `H` is the year's person-hours of the `subsistence` activity: people × hours a day × 365,
  summed over ages.
- Production is `max × (1 − e^(−r·H / max))`. While labor is light, each hour yields about `r` kcal
  (`return_per_hour`, 1,000). As labor approaches what the tile can sustain, extra hours yield less,
  and production levels off at `max`.
- `max` (`production_max.npy`) is the tile's sustainable yearly yield (`foraging_max()`): its land area
  × the yield per km² of its land cover (from 15,000 kcal for tundra to 400,000 for wetlands, worked back
  from observed forager densities) × a landform factor (hills 0.8, mountains 0.5) × a climate factor
  (falling from 1 at 40° latitude to 0.4 at 70°), plus its water area × a near-shore fishing yield, all
  × 1.2 if a navigable river runs through it.

**Consumption** (`consume()`): the same people (before ageing) consume each product listed under
`consumption` in `economy.json`. Each product has a table of `[age, amount per person per day]` points,
filled in linearly between them; food starts at 700 kcal a day for infants (standing for their mothers'
extra intake), peaks at 2,700 at 18 and falls to 1,800 at 100, about 2,200 on average across a forager
age structure. A tile's `need` is its people × those amounts × 365. It consumes its need, or, if it
holds less (its stock plus the year's production), everything it holds: everyone's consumption is cut
by the same share. The shortfall, `need − consumed`, is meant to drive mortality.

**Decay, at the end of the year** (`end_of_year()`): what each tile holds of each product after
consumption loses that product's decay rate, and the rest is the new turn's `stock`. The base rate is each product's `decay` in `economy.json`, and `economy_modifiers()`
turns it into a rate per tile (`decay.npy`). For now every tile gets the base rate; that function is
where future products and infrastructure will change it. A product that keeps better, such as
preserved food, is a separate product with its own rate. Food's rate is 1: food that isn't eaten is all
lost by the next turn.

**Demographics, after production and consumption** (so that they can respond to the year's food):
for now, births. Each tile's births are its people × a birth rate per person per year for their age,
summed over ages (`births()`), and become age 0 of the new turn, in the tile's nation. The rate
(`birth_rates()`) is a placeholder from `engine/demography.json`, read on every step: one table of
`[age, rate]` points, filled in linearly, that counts men and women alike, with each birth counting
half to each parent at their own ages. Over a lifetime it adds up to half the children per woman,
about 5.8 for foragers, and gives about 38 births per 1,000 people a year at the turn-0 age structure.
`birth_rates()` is where a decision model will choose instead. Deaths are still to come, so for now
the population only grows.

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
