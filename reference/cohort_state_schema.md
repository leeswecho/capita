# Game state format (`capita/cohort-state-9`)

People belong to **segments**. A segment is the people directly under one **leader**: a leader marshals
their segment toward some purpose, whether as a band, a town, an army or a settling party. Leaders form
a tree within their nation; a leader's **following** is their own segment plus the segments of every
leader below them, and the player is their nation's root leader. Every person is in exactly one
segment, and every segment has a position on the map.

The game state holds, for every segment, its leader, its position, the people of each single year of
age, and how many hours a day they spend on each activity, what it produced in the past year and what
it holds of each product. It also holds a record for each nation, which nation owns each tile, and what
each nation can see. The turn engine (`engine/engine.py`) writes it, and the web server
(`server/server.py`), control server (`control/control.py`) and player server (`player/player.py`) read it. This document is the contract
between them, written so that an engine in any language can produce it. `server/cohort_state.py` is the
Python implementation.

Version 2 added nations (`nations.json`, `nation.npy`) and visibility (`visible.npy`, `explored.npy`);
version 3 added tile ownership (`owner.npy`); version 4 added products and production (`production.npy`,
`labor.npy`, `production_max.npy`, `decay.npy`, `stock.npy`, and `products` in the manifest); version 5
added consumption (`need.npy`, `consumed.npy`); version 6 added deaths (`deaths.npy`, and `causes` in
the manifest); version 7 made rows segments instead of tiles (`leaders.json`, `leader.npy`, `tile.npy`,
`at.npy`, `toward.npy`, `progress.npy`, and `leader_types` and `next_leader_id` in the manifest, replacing
`tiles.npy` and `nation.npy`; nations gained `leader`); version 8 added `destination.npy`, so a move
can take a segment one step past the tile it is heading for; version 9 added leaders' `title`,
`received.npy` (food shared between segments) and the `farming` activity, and made `hours.npy` the
hours actually spent in the year. Older states can't be read; delete the state
folder and run `engine.py init` again.

## Directory layout

```
state/
  current.json          pointer to the current turn
  phase.json            the current turn's phase: accepting commands, or resolving
  orders/               the players' commands, by turn (not part of the turns; see "Orders" below)
  names.json            the names players have given their nations (see "Names" below)
  places.json           the names players have given tiles, each nation its own (see "Names" below)
  .lock                 empty; locked by whoever is changing the state (see "The lock" below)
  turn_00007/           one directory per turn; never changed once current.json has named it
    manifest.json
    nations.json
    leaders.json
    leader.npy
    tile.npy
    at.npy
    toward.npy
    progress.npy
    destination.npy
    people.npy
    hours.npy
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
    deaths.npy
  turn_00008/
  ...
```

Turn directories are named `turn_` + the turn number as 5 digits, zero-padded.

## `current.json`

```json
{ "format": "capita/cohort-state-9", "turn": 7, "dir": "turn_00007" }
```

Readers open the directory named by `dir`. Writers replace this file atomically: write
`current.json.tmp`, then rename it over `current.json`.

## `phase.json`

```json
{ "format": "capita/cohort-state-9", "turn": 7, "phase": "resolving", "since": "2026-10-21T20:28:13Z" }
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
Use little-endian data in C (row-major) order, `fortran_order: False`. Each row is one segment, in
the order of `leaders.json`. `S` is the number of segments, `M` is the number of nations + 1, and `P`
is the number of products. A tile is named by its flat grid index: `row * 720 + col`, with row 0 =
89.5–90°N and col 0 = 180–179.5°W (see `server/grid.py`).

| file | dtype | shape | meaning |
|------|-------|-------|---------|
| `leader.npy` | `<u4` (uint32) | `[S]` | The id of each row's leader, the same as `leaders.json`'s ids. **Strictly increasing**, so readers can binary-search it. |
| `tile.npy` | `<u4` (uint32) | `[S]` | The tile each segment **counts as being in**, derived from its position (below). Stored so that readers needn't derive it. Several segments can be in the same tile. |
| `at.npy` | `<u4` (uint32) | `[S]` | The tile the segment is at, or is leaving. |
| `toward.npy` | `<u4` (uint32) | `[S]` | The tile it is moving toward, one of the 8 neighbours of `at`; **equal to `at` when it isn't moving**. |
| `progress.npy` | `<f8` (float64) | `[S]` | How far it has gone from the centre of `at` to the centre of `toward`, as a share of that distance: at least 0 and below 1, and 0 when it isn't moving. |
| `destination.npy` | `<u4` (uint32) | `[S]` | Where its move ends: `toward`, or one of the 8 neighbours of `toward`, which it goes on to once it gets there. **Equal to `at` when it isn't moving.** |
| `people.npy` | `<f8` (float64) | `[S, 101]` | People in the segment by single year of age. Column `a` holds age `a` for `a` = 0..99; **column 100 is "100 and over"**. Fractions are allowed and are rounded only for display. |
| `hours.npy` | `<f4` (float32) | `[S, 101, A]` | For each segment, age and activity: the mean hours per person per day spent on that activity, averaged over the **year that ended with this turn** (at turn 0, the starting schedule). Activities are in `manifest.activities` order. They needn't sum to 24; any remainder is unassigned time. |
| `owner.npy` | `<u2` (uint16) | `[259200]` | The id of the nation that **owns** each tile of the whole grid (flat index), 0 = nobody. Ownership belongs to tiles, not segments, and nations can own tiles nobody is on. |
| `visible.npy` | `<u1` (uint8) | `[M, 32400]` | **Active visibility**: tiles the nation can see this turn. Row `k` is nation id `k`, one bit per tile (below). |
| `explored.npy` | `<u1` (uint8) | `[M, 32400]` | **Explored** tiles: every tile the nation has ever actively seen, including this turn. A tile that is explored but not visible is **passively** visible: its geography is known, nothing else. |
| `production.npy` | `<f8` (float64) | `[S, P]` | How much of each product the segment made in the **year that ended with this turn**, in the product's unit. All zeros at turn 0, which follows no year. |
| `labor.npy` | `<f8` (float64) | `[S, P]` | Person-hours of work that went into each product that year. |
| `production_max.npy` | `<f8` (float64) | `[S, P]` | The most the segment's tile could produce of each product in a year by the segment's activity (farming, or foraging on the land the farms leave): production levels off here however much labor goes in. **Shared** by every segment on the tile that does the same, so add it up once per tile and activity, not per segment. |
| `need.npy` | `<f8` (float64) | `[S, P]` | How much of each product the segment's people needed that year: people × the per-person daily amount for their age × 365. 0 for products nobody consumes. |
| `consumed.npy` | `<f8` (float64) | `[S, P]` | How much they actually consumed: the need, or all the segment held if that was less. A shortfall is shared evenly, so people of every age consumed `consumed / need` of their need. |
| `received.npy` | `<f8` (float64) | `[S, P]` | How much of each product other segments gave the segment that year, less what it gave them (negative for a giver). The segment held its stock + production + received. |
| `decay.npy` | `<f4` (float32) | `[S, P]` | The share of the segment's holdings of each product lost at the end of that year, 0–1. Per segment, so that future rules can change it locally. |
| `stock.npy` | `<f8` (float64) | `[S, P]` | How much of each product the segment holds at the start of this turn, after decay. Holdings belong to the segment and travel with it. |
| `deaths.npy` | `<f8` (float64) | `[S, C]` | How many people of the segment died of each cause in the **year that ended with this turn**, newborns included; `C` is the number of causes, in `manifest.causes` order. Fractions are allowed: people die in fractions, even below one. All zeros at turn 0. |

Products are columns in `manifest.products` order.

`S` may change from turn to turn, as leaders are created and removed. A tile's people, production and
deaths are the sums over the segments in it (`tile.npy`).

### Positions

A segment is either **on a tile** (`toward = at`, `progress = 0`) or **between two neighbouring tiles**,
`progress` of the way from the centre of `at` to the centre of `toward`. Neighbours share an edge or a
corner; east–west wraps around, and nothing is past the poles. Progress is a share of the actual
distance between the two centres, which varies with latitude and is longer for diagonal steps.

- **The tile it counts as being in** (`tile.npy`) is `at` while `progress < 0.5`, and `toward` from
  `progress >= 0.5` (exactly halfway counts as `toward`). `cohort_state.segment_tile()` applies this.
- **Its latitude and longitude**, when needed, are the point `progress` of the way along the straight
  line between the two centres, taking the short way across the 180th meridian
  (`cohort_state.segment_latlon()`). The format never stores them.

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
  { "id": 1, "code": "AUS", "capital": 180658, "leader": 37, "founded_turn": 0, "alive": true },
  { "id": 2, "code": "AUT", "capital": 60152,  "leader": 10, "founded_turn": 0, "alive": true }
] }
```

| key | meaning |
|-----|---------|
| `id` | The nation's number, 1, 2, 3... in list order. Ids are never reused: a nation that disappears keeps its record with `alive: false`, so old references (such as player decisions in a replay log) stay valid. |
| `code` | A short identifier. At turn 0 it's the ISO 3166-1 alpha-3 code the nation was seeded from. The server shows the country name for it. |
| `capital` | The flat grid index of the capital tile, or `null`. |
| `leader` | The id of the nation's **root leader**, the player, or `null` if it has no leaders. The root leader belongs to the nation and has no parent. |
| `founded_turn` | The turn the nation came into being. |
| `alive` | Whether the nation still exists. |

Every record has exactly these keys. Add new per-nation fields here when they are descriptive. Fields
the engine computes over all nations at once (stores, technology levels...) should become arrays
indexed by nation id instead.

## `leaders.json`

```json
{ "leaders": [
  { "id": 1, "name": "Chieftain of ISL", "type": "chieftain", "title": "Chieftain", "nation": 20, "parent": 0, "founded_turn": 0 },
  { "id": 2, "name": "High King", "type": "king", "title": "High King", "nation": 14, "parent": 0, "founded_turn": 0 }
] }
```

One record per row of the arrays, in the same order. A leader is an abstraction, not a person: leaders
don't age or die.

| key | meaning |
|-----|---------|
| `id` | The leader's number. **Strictly increasing** down the list, and never reused: `manifest.next_leader_id` is the next one to hand out, so a leader that is removed (say, merged back into another segment) leaves a gap. |
| `name` | What the leader is called. |
| `type` | The leader's internal type, one of `manifest.leader_types`: `chieftain`, `pioneer`, `lord`, `mayor`, `superintendent`, `admiral`, `general` or `king`. It decides what they can do (`engine/leaders.json`). |
| `title` | What a leader of their type is called: the type's `title` in `leaders.json` (`Chieftain`), or one their player chose on founding a kingdom (at most 20 characters). The pages show it; nothing in the rules reads it. |
| `nation` | The id of the leader's nation, and so of their segment's people; 0 = no nation. |
| `parent` | The id of the leader they report to, of the same nation, or 0 for none. Following parents never loops. A nation's root leader has none; so may leaders with no nation. |
| `founded_turn` | The turn the leader was created. |

Every record has exactly these keys.
## `manifest.json`

```json
{
  "format": "capita/cohort-state-9",
  "turn": 7,
  "grid": { "tile_size_degrees": 0.5, "width": 720, "height": 360 },
  "ages": { "count": 101, "last_is_open_ended": true },
  "activities": ["sleep", "subsistence", "farming", "childcare", "other"],
  "hours_unit": "mean hours per person per day, averaged over the year",
  "products": [{ "name": "food", "unit": "kcal" }],
  "causes": ["starvation", "predation_and_accidents", "cancer", "other_age_related", "infection", "child_mortality"],
  "leader_types": ["chieftain", "pioneer", "lord", "mayor", "superintendent", "admiral", "general", "king"],
  "next_leader_id": 49911,
  "visibility_bits": "row k = nation id k; tile i is bit i % 8 of byte i // 8",
  "arrays": {
    "leader":   { "file": "leader.npy",   "dtype": "<u4", "shape": [49910] },
    "tile":     { "file": "tile.npy",     "dtype": "<u4", "shape": [49910] },
    "at":       { "file": "at.npy",       "dtype": "<u4", "shape": [49910] },
    "toward":   { "file": "toward.npy",   "dtype": "<u4", "shape": [49910] },
    "progress": { "file": "progress.npy", "dtype": "<f8", "shape": [49910] },
    "destination": { "file": "destination.npy", "dtype": "<u4", "shape": [49910] },
    "people":   { "file": "people.npy",   "dtype": "<f8", "shape": [49910, 101] },
    "hours":    { "file": "hours.npy",    "dtype": "<f4", "shape": [49910, 101, 5] },
    "owner":    { "file": "owner.npy",    "dtype": "<u2", "shape": [259200] },
    "visible":  { "file": "visible.npy",  "dtype": "<u1", "shape": [225, 32400] },
    "explored": { "file": "explored.npy", "dtype": "<u1", "shape": [225, 32400] },
    "production":     { "file": "production.npy",     "dtype": "<f8", "shape": [49910, 1] },
    "labor":          { "file": "labor.npy",          "dtype": "<f8", "shape": [49910, 1] },
    "production_max": { "file": "production_max.npy", "dtype": "<f8", "shape": [49910, 1] },
    "need":           { "file": "need.npy",           "dtype": "<f8", "shape": [49910, 1] },
    "consumed":       { "file": "consumed.npy",       "dtype": "<f8", "shape": [49910, 1] },
    "decay":          { "file": "decay.npy",          "dtype": "<f4", "shape": [49910, 1] },
    "stock":          { "file": "stock.npy",          "dtype": "<f8", "shape": [49910, 1] },
    "deaths":         { "file": "deaths.npy",         "dtype": "<f8", "shape": [49910, 6] }
  },
  "segments": 49910,
  "populated_tiles": 49910,
  "nations": 224,
  "total_people": 7840952769.0,
  "checksum": { "algorithm": "sha256", "of": "...", "value": "845b2053..." },
  "initial_population_source": "world_population_start.json",
  "initial_nations_source": "world_population_start_capitals.nations.json",
  "economy_rules_source": "engine/economy.json",
  "economy_rules": { "products": { ... }, "foraging": { ... }, "farming": { ... }, "sharing": { ... } },
  "demography_rules_source": "engine/demography.json",
  "demography_rules": { "births": { ... }, "deaths": { ... } },
  "leader_rules_source": "engine/leaders.json",
  "leader_rules": { "types": { ... }, "commands": { ... }, "start_type": "chieftain" },
  "movement_rules_source": "engine/movement.json",
  "movement_rules": { "km_per_hour": 2.5, "not_travel_activities": ["sleep", "subsistence", "farming"] },
  "visibility_rules_source": "engine/visibility.json",
  "visibility_rules": { "radius_tiles": 1 },
  "orders": { "turn": 6, "applied": [ { "nation": 1, "leader": 37, "type": "move", "to": "S355E1495" } ],
              "rejected": [ { "nation": 23, "leader": 31, "type": "move", "to": "N100E0100",
                              "reason": "N100E0100 isn't next to N355E1395" } ] }
}
```

- `products` lists each product's `name` and `unit`, in the order of the product columns.
- `economy_rules` is a copy of the rules the engine used to compute this turn's production and decay
  (`engine/economy.json` at the time), so a replay can tell whether they changed. `demography_rules`
  is the same for births and deaths (`engine/demography.json`), `leader_rules` for leaders
  (`engine/leaders.json`), `movement_rules` for movement (`engine/movement.json`) and
  `visibility_rules` for visibility (`engine/visibility.json`).
- `orders` (every turn after 0) lists the commands of the previous turn that the engine applied to
  compute this one, and those it rejected with the reason.
- `leader_types` lists the types a leader can have; `next_leader_id` is the id the next new leader
  gets.
- `causes` lists the causes of death, in the order of the `deaths.npy` columns.
- `checksum.value` is the SHA-256 of the raw data bytes (not the `.npy` headers) of `leader`, `tile`,
  `at`, `toward`, `progress`, `destination`, `people`, `hours`, `owner`, `visible`, `explored`, `production`, `labor`,
  `production_max`, `need`, `consumed`, `received`, `decay`, `stock` and `deaths`, in that order, followed by the
  `nations` list and then the `leaders` list, each as canonical JSON: sorted keys, no whitespace (`","`
  and `":"` separators), UTF-8. It shows whether a deterministic replay reproduced a turn exactly.
  `segments`, `populated_tiles` (tiles that segments with people count as being in) and `total_people`
  are informational only and aren't part of the checksum.
- Readers must check `format` and `grid`, and must ignore keys they don't know. Writers may add keys,
  such as `initial_population_source` and `initial_nations_source`.

## Orders

Players' commands are kept beside the turns, not in them, because a turn never changes once written.
The control and player servers write them while a turn is in `orders`, one file per nation:

```
state/orders/turn_00007/nation_037.json
{ "format": "capita/orders-1", "turn": 7, "nation": 37,
  "commands": [ { "leader": 37, "type": "move", "to": "S355E1495" } ] }
```

At most one command per leader, in leader id order. The only type so far is `move`: the leader takes
their segment to `to`, a tile id. A segment on a tile can go to any of its 8 neighbours that isn't all
water (`water` 100 in `world_map.json`). One already moving from `at` (A) to `toward` (B) can go to A,
B, or any neighbour of either that isn't all water: it first finishes the step to B or walks back to A,
whichever makes the whole way to `to` shorter (finishing the step on a tie), then steps on to `to`.
A writer checks every command against the turn it is for (`server/orders.py` `check()`),
and so does the engine when it reads them, skipping any that fail. Old turns' orders may be deleted
along with the turns.

## Names

The names players give their nations are kept beside the turns, since a name changes nothing in the
simulation and the engine never reads it:

```
state/names.json
{ "format": "capita/names-1", "nations": { "1": "The River People" } }
```

Keys are nation ids. A nation without an entry goes by its country's name (from its `code`). The
player server writes the file (atomically, under the lock); every server shows the names.

Names players give tiles are kept the same way, each nation's to itself, so a tile can have a name
for every nation. Only named tiles are listed; the player page calls the rest "Unnamed land":

```
state/places.json
{ "format": "capita/places-1", "nations": { "14": { "N600E0245": "Home Vale" } } }
```

## The lock

Whoever changes the state holds an exclusive OS lock on `state/.lock` while doing it (`flock` on
Linux, `msvcrt.locking` on Windows; `cohort_state.state_lock()`): the engine for a whole `init` or
`step`, and the servers while they write orders or names (of nations or places). So no order lands in the middle of a step,
and two steps can't run at once; the OS releases the lock if its holder dies. `engine.py step` doesn't
wait for it: if it is held, the step exits with status 3. Readers don't take it.

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

One turn is one year. First the turn's orders are applied (`apply_orders()`). A leader can be given only
the commands their type lists (`engine/leaders.json`). `found_kingdom` turns a leader whose segment is
wholly on one tile into a `king` (the type's `becomes`), with the title their player gave as both
`title` and `name`; a king's segment is **settled**: it farms, takes no move commands, and keeps every
other leader, of any nation, from settling its tile. `establish_settlement` does the same for a `pioneer`, who becomes a
`lord`. If two or more leaders settle the same tile in the same turn, none of them does.
`form_settlers` splits a settling party of the given number of people off a king's or lord's segment (at least
`min_people`, leaving at least `min_left`): the same share of every age and of the stock goes to a new
row at the end, standing on their tile, under a new `pioneer` (id `next_leader_id`, `parent` the
leader, `founded_turn` the new turn), which lives the year as its own segment. A leader takes one
command a turn. A `move` given to a
standing segment sets `toward` and `destination` to the named neighbour. Given to a moving one, it sets
`destination` to the named tile and either keeps the step under way or turns back (swapping `at` and
`toward` and keeping `1 - progress`), whichever way is shorter, measuring the rest of the current step
plus the step from its end to the destination. Then every moving segment travels `km`, which is
`km_per_hour × 365 ×` its people-weighted mean of the hours a day not spent on `not_travel_activities`
in the year before (`hours.npy` of the current turn; `engine/movement.json`; a segment with nobody in it
doesn't move). Distances are great circles between
tile centres. When it reaches `toward`, it sets `at = toward` and `progress = 0`. If that tile is its
`destination`, it has arrived and stops for the year; otherwise `toward = destination` and it carries
on with what is left of `km`. Whatever step it is on when `km` runs out, it is left partway along. Each segment
then lives the year on the tile it counts as being in where it ended up. There are no commands yet to
create or merge segments. Each turn every surviving cohort
moves up one year of age, and the 99-year-olds
join the 100+ group. Age 0 holds the children born during the year who survived it (below). Activity
hours belong to the age, not the cohort: each year's hours come from the schedule for each age, so
that people born in the game work as adults once they grow up. The activity list and the schedules
are placeholders in `engine/engine.py`: rough smooth curves by age, one for each way a segment can feed
itself (its leader type's `activity`). Foragers (`starting_hours()`, also turn 0's hours) start
`subsistence` work around age 10, peaking at 8.5 hours a day in adulthood; farmers (`farming_hours()`)
start `farming` around 7, peak at about 9.5 hours and keep working longer. Babies sleep more, and
childcare peaks around age 30. A schedule's hours of work are the most people give: they work only
what their food needs (below), and the hours they don't need go to `other`.

**Production, before ageing** (`allocate()`): the year that is ending is worked by the current turn's
people, so production is computed before they age, and stored in the new turn. The rules and all their
values are in `engine/economy.json`, which the engine reads on every step. For now the only product is
**food**, in kcal, made by **foraging** (hunting and gathering, the `subsistence` activity) or
**farming** (farming and herding as one activity, `farming`), whichever the segment's leader type has.

- Labor `H` is a year's person-hours of the activity: people × hours a day × 365, summed over ages. Its
  most is what the segment's schedule allows.
- Each activity on a tile has a curve, `max × (1 − e^(−r·H / max))`, where `H` is the labor of every
  segment working it there. While labor is light, each hour yields about `r` kcal (`return_per_hour`:
  1,000 foraging, 1,200 farming). As labor approaches what the tile can sustain, extra hours yield less,
  and production levels off at `max`. Each segment gets a share in proportion to its own labor.
- Foraging's `max` (`foraging_max()`) is the tile's sustainable yearly yield: its land area × the yield
  per km² of its land cover (from 15,000 kcal for tundra to 400,000 for wetlands, worked back from
  observed forager densities) × a landform factor (hills 0.8, mountains 0.5) × a climate factor (falling
  from 1 at 40° latitude to 0.4 at 70°), plus its water area × a near-shore fishing yield, all × 1.2 if
  a navigable river runs through it.
- Farming's `max` (`farming_max()`) is a Neolithic baseline: its land area × farming's yield per km² of
  the cover (12 million kcal for grassland, about 15 people a km², down to none for tundra and ice) × a
  landform factor (hills 0.6, mountains 0.2), plus 2 million kcal per km² of the whole tile if a
  navigable river runs through it, all × a climate factor (1 to 40° latitude, 0.05 at 65°), plus the
  same fishing as foragers. `yield_modifiers()` is where technology will raise both.
- People work only the hours their food needs. Each segment first counts what it holds. On each tile,
  a nation's farmers farm for what all of that nation's segments there still need, as far as their hours
  allow; they keep what they need and give the rest to the others in proportion to their needs
  (`received.npy`). Then every forager there works for what it still needs, all of them, of every
  nation, sharing the foraging curve on the land the farms leave: foraging's `max` × (1 − the share of
  farming's `max` produced). Segments of different nations never share food. The labor each segment
  needs is found by bisection on the curve's average return per hour, since that falls as the labor on
  it rises. The hours of work they don't need go to `other` in `hours.npy`.

**Consumption** (`consume()`): the same people (before ageing) consume each product listed under
`consumption` in `economy.json`. Each product has a table of `[age, amount per person per day]` points,
filled in linearly between them; food starts at 700 kcal a day for infants (standing for their mothers'
extra intake), peaks at 2,700 at 18 and falls to 1,800 at 100, about 2,200 on average across a forager
age structure. A segment's `need` is its people × those amounts × 365. It consumes its need, or, if
it holds less (its stock plus the year's production plus what it received), everything it holds:
everyone's consumption is cut by the same share. The shortfall, `need − consumed`, drives starvation.

**Decay, at the end of the year** (`end_of_year()`): what each segment holds of each product after
consumption loses that product's decay rate, and the rest is the new turn's `stock`. The base rate is
each product's `decay` in `economy.json`, and `economy_modifiers()` turns it into a rate per segment
(`decay.npy`). For now every segment gets the base rate; that function is
where future products and infrastructure will change it. A product that keeps better, such as
preserved food, is a separate product with its own rate. Food's rate is 1: food that isn't eaten is all
lost by the next turn.

**Demographics, after production and consumption** (so that they can respond to the year's food):
first births. Each segment's births are its people × a birth rate per person per year for their age,
summed over ages (`births()`), and become age 0 of the same segment in the new turn. The rate
(`birth_rates()`) is a placeholder from `engine/demography.json`, read on every step: one table of
`[age, rate]` points, filled in linearly, that counts men and women alike, with each birth counting
half to each parent at their own ages. Over a lifetime it adds up to half the children per woman,
about 5.8 for foragers, and gives about 38 births per 1,000 people a year at the turn-0 age structure.
Settled segments have 1.25 times as many (`settled.birth_factor`).
`birth_rates()` is where a decision model will choose instead.

Then **deaths** (`mortality()`), among the same people. Each cause is a yearly hazard (deaths per
person-year) for every segment and age (`death_hazards()`), and the causes add: a cohort's share dying in
the year is `1 − e^(−total)`, and its deaths are split among the causes in proportion to their
hazards. Each cohort lives the year at its mid-year age; children born during the year face half a
year at age 0.25. People die in fractions. `mortality_modifiers()` multiplies each cause per segment:
infection × 1.5 for settled segments (`settled.infection_factor`), else 1; it is where future
infrastructure will reduce them. The causes, with their rules and values
in the `deaths` section of `engine/demography.json`:

| cause | depends on | rule |
|---|---|---|
| `starvation` | the year's food | Hazard by the segment's share of food need met (`consumed / need`): none at 90% or more, about 20% dying a year at half the need, about 60% at a quarter, filled in linearly. Doubled for children under 5 and people 65 and older. Includes infections that kill people because they are starving. |
| `predation_and_accidents` | hours of work | Animal attacks, snakebite, falls, drowning: 1.3 × 10⁻⁶ per hour of `subsistence` and 0.7 × 10⁻⁶ per hour of `farming`, about 0.4% a year for an adult foraging 8.5 hours a day. |
| `cancer` | age | `a × e^(b × age)`: a fifth of the Siler ageing term. |
| `other_age_related` | age | Heart disease, organ failure, frailty...: the other four fifths of it. |
| `infection` | terrain | Infections that kill otherwise healthy people: 0.009 × a climate factor (1 in the tropics, falling to 0.6 at 60° latitude and beyond) × (1 + 0.5 × the wetland share of the segment's tile). |
| `child_mortality` | age, childcare | The Siler infant term `0.422 × e^(−1.131 × age)` × (1 + 2 × the segment's childcare shortfall). The shortfall is `1 − hours of childcare given / hours needed`, at least 0, with the hours each child needs by age (6 a day for a newborn, none by 10). The turn-0 hours give about 1.8 times the need. |

Well fed, the causes together reproduce the Siler forager curve that turn 0's age structure comes from
(Gurven & Kaplan 2007; life expectancy at birth about 34): about 27 deaths per 1,000 a year at that
structure against 38 births, so a well-fed population grows about 1% a year until food runs short. In
the starting scenario the population settles after about 150 years at about 1.8 times its start, with
births and deaths at about 39 per 1,000 and each segment meeting about 88% of its food need.

**Nations at turn 0** (`make_nations()`) come from a **nation seed**, which `init` requires: `--nations`,
or the `<population name>.nations.json` / `<population name>_*.nations.json` next to the population file
(`world_population_start_capitals.nations.json`, `world_population_current_capitals.nations.json`). It
lists the nations in id order with their capital tiles:
`{"nations": [{"code": "DNK", "capital": "N555E0125"}, ...]}`; other keys (`capital_name`, `note`) are
ignored. A capital's tile belongs to its nation; every other populated tile goes to the nation whose
code is the tile's country code, and codes not in the seed get no nation.

**Leaders at turn 0** (`make_leaders()`): every populated tile becomes one segment, standing on it, with
leader ids 1, 2, 3... in grid order. Every leader has `start_type` from `engine/leaders.json`
(`chieftain`), titled as that type is (`Chieftain`). Each nation's root leader leads the segment on its capital (or its most populous segment
if nobody lives on the capital) and is named after the nation, such as `Chieftain of AUS`; the nation's
other leaders report to the root and are named after their tile. Leaders of segments with no nation
have no parent.

**Ownership, recomputed every turn** (`update_ownership()`): first come, first served, and only
segments that aren't moving claim tiles (a passing army or settling party doesn't). A nation owns a tile
from the turn it is the first to have such a segment with people there, and keeps it while it has one
there. When it has none left (for example, they all died, or moved on), the tile passes to the nation
with the most people in such segments there (the lowest id on a tie), or to nobody. Segments with no
nation never claim. At turn 0 every populated tile belongs to the nation living on it. The rule is expected to change (treaties, contested areas...); readers only use
`owner.npy`.

**Visibility, recomputed every turn** (`active_visibility()`): a nation actively sees every tile within
`radius_tiles` rows and columns (`engine/visibility.json`; 1, so the tile and the 8 around it) of a tile one of its
segments with people counts as being in (east–west wraps around; nothing past the poles). Then
`explored = explored OR visible`. The rule is expected to change as the game develops; readers only
use the stored bits.
