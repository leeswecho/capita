"""Per-turn game state: the files the game engine writes and the web server reads.

Layout (see reference/cohort_state_schema.md for the full, language-neutral spec):

    state/
      current.json            {"format": ..., "turn": 7, "dir": "turn_00007"}; replaced atomically
      phase.json              {"format": ..., "turn": 7, "phase": "resolving", "since": ...}: what is
                              happening to the current turn (see read_phase()); replaced atomically
      turn_00007/
        manifest.json         turn number, activity names, units, leader types, array shapes/dtypes, checksum
        nations.json          one record per nation: id, code, capital tile, root leader, founded turn, alive
        leaders.json          one record per row (segment), in row order: id, name, type, nation, parent leader,
                              founded turn
        leader.npy            <u4 [S]          leader id of each row, strictly increasing (= leaders.json's ids)
        tile.npy              <u4 [S]          the tile each segment counts as being in (derived from at/toward/progress)
        at.npy                <u4 [S]          the tile the segment is at, or is leaving
        toward.npy            <u4 [S]          the neighbouring tile it is moving toward; equal to at when it isn't moving
        progress.npy          <f8 [S]          how far it has gone from at to toward, 0 <= progress < 1 (0 when not moving)
        destination.npy       <u4 [S]          where its move ends: toward, or a neighbour of toward to go on to
                              once there; equal to at when it isn't moving
        people.npy            <f8 [S, 101]     people by single year of age; column 100 is "100 and over"
        hours.npy             <f4 [S, 101, A]  mean hours per person per day spent on each activity
        owner.npy             <u2 [259200]     nation id that owns each tile of the whole grid (0 = nobody)
        visible.npy           <u1 [M, 32400]   active visibility: row k = nation id k, one bit per tile
        explored.npy          <u1 [M, 32400]   tiles each nation has ever seen (visible is a subset)
        production.npy        <f8 [S, P]       amount of each product made in the year that ended with this turn
        labor.npy             <f8 [S, P]       person-hours of work that went into each product that year
        production_max.npy    <f8 [S, P]       the most the segment's tile can produce in a year, shared by every
                              segment there (production saturates here)
        need.npy              <f8 [S, P]       how much of each product the segment's people needed that year
        consumed.npy          <f8 [S, P]       how much they consumed: the need, or all there was if that fell short
        decay.npy            <f4 [S, P]       share of each product's holdings lost at the end of that year
        stock.npy             <f8 [S, P]       amount of each product held at the start of this turn, after decay
        deaths.npy            <f8 [S, C]       people who died of each cause in the year that ended with this turn

People belong to segments: a segment is the people directly under one leader, and each row is one
segment. A leader's following is their segment plus the segments of every leader below them (parent
links in leaders.json). A tile can hold any number of segments; the per-tile figures are sums over them.
Products (P columns, in manifest "products" order) are measured in their own units: food in kcal.
Causes of death (C columns) are in manifest "causes" order.
Leader ids are 1, 2, ... and never reused (manifest "next_leader_id" is the next one to hand out).
Nation ids are 1..M-1 and are never reused; row 0 of the visibility arrays belongs to "no nation" and
is all zeros. Bits are packed little-endian: tile i is bit i % 8 of byte i // 8. A turn directory is
complete before current.json names it and is never changed afterwards, so readers can memory-map it
safely. Windows won't delete a file another process has mapped, so old turns are pruned on a
best-effort basis (see prune()).
"""
import hashlib
import json
import os
import shutil
import time

import numpy as np

import grid

FORMAT = "capita/cohort-state-8"
AGES = 101                      # ages 0..99, then 100 = "100 and over"
HOURS_UNIT = "mean hours per person per day, averaged over the year"
BITS_BYTES = (grid.COUNT + 7) // 8          # one visibility row: 259,200 tiles -> 32,400 bytes
DTYPES = {"leader": "<u4", "tile": "<u4", "at": "<u4", "toward": "<u4", "progress": "<f8", "destination": "<u4",
          "people": "<f8", "hours": "<f4", "owner": "<u2", "visible": "<u1", "explored": "<u1",
          "production": "<f8", "labor": "<f8", "production_max": "<f8", "need": "<f8", "consumed": "<f8",
          "decay": "<f4", "stock": "<f8", "deaths": "<f8"}
ARRAYS = tuple(DTYPES)                      # file order, which is also checksum order
ECONOMY = ("production", "labor", "production_max", "need", "consumed", "decay", "stock")   # [S, P] each
PRODUCT_KEYS = ("name", "unit")
NATION_KEYS = ("id", "code", "capital", "leader", "founded_turn", "alive")
LEADER_KEYS = ("id", "name", "type", "nation", "parent", "founded_turn")
CURRENT = "current.json"
PHASE = "phase.json"
ORDERS, RESOLVING = "orders", "resolving"   # the phases of a turn; see read_phase()


def turn_dir_name(turn: int) -> str:
    return f"turn_{turn:05d}"


def pack_tiles(flags):
    """bool [..., grid.COUNT] -> uint8 [..., BITS_BYTES], little-endian bit order."""
    return np.packbits(flags, axis=-1, bitorder="little")


def unpack_tiles(bits):
    """uint8 [..., BITS_BYTES] -> bool [..., grid.COUNT]."""
    return np.unpackbits(bits, axis=-1, count=grid.COUNT, bitorder="little").astype(bool)


def canonical_json(records) -> bytes:
    """Canonical JSON of records (sorted keys, no spaces, UTF-8), as hashed by checksum()."""
    return json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def segment_tile(at, toward, progress):
    """The tile a segment counts as being in: `at` until it is halfway to `toward`, then `toward`.
    Exactly halfway counts as `toward`. Works on numbers or NumPy arrays."""
    return np.where(np.asarray(progress) >= 0.5, toward, at)


def segment_latlon(at, toward, progress):
    """(lat, lon) of a segment's position: the point `progress` of the way from the centre of `at` to
    the centre of `toward`, taking the short way across the 180th meridian. Numbers or NumPy arrays."""
    lat0, lon0 = grid.centre(at)
    lat1, lon1 = grid.centre(toward)
    dlon = (lon1 - lon0 + 180) % 360 - 180
    p = np.asarray(progress, dtype=np.float64)
    return lat0 + (lat1 - lat0) * p, (lon0 + dlon * p + 180) % 360 - 180


class State:
    """Everything in one turn. The engine builds one, write_turn() saves it, Turn reads it back."""

    def __init__(self, leaders, at, toward, progress, destination, people, hours, activities, owner, nations, visible,
                 explored, products, economy, causes, deaths, leader_types, next_leader_id):
        """leaders: one record per row (LEADER_KEYS), ids increasing; at/toward/progress: each segment's
        position; destination: where its move ends; products: [{"name": "food", "unit": "kcal"}, ...]; economy: {name: array [S, P]} for each
        name in ECONOMY, with columns in products order; causes: names of the causes of death; deaths:
        [S, C]; leader_types: the names a leader's type can take; next_leader_id: the next id to hand out."""
        self.leaders, self.at, self.toward, self.progress = leaders, at, toward, progress
        self.destination = destination
        self.people, self.hours, self.owner = people, hours, owner
        self.visible, self.explored = visible, explored
        self.activities, self.nations, self.products = list(activities), nations, list(products)
        for k in ECONOMY:
            setattr(self, k, economy[k])
        self.causes, self.deaths = list(causes), deaths
        self.leader_types, self.next_leader_id = list(leader_types), int(next_leader_id)
        # Derived from the records and positions; written too, so readers needn't derive them.
        self.leader = np.array([r["id"] for r in leaders], dtype=DTYPES["leader"])
        self.tile = segment_tile(at, toward, progress).astype(DTYPES["tile"])
        self.nation = np.array([r["nation"] for r in leaders], dtype=np.uint16)   # each row's nation id
        self.parent = np.array([r["parent"] for r in leaders], dtype=np.uint32)   # each row's parent leader, 0 = none

    def arrays(self):
        return {k: getattr(self, k) for k in ARRAYS}

    def checksum(self) -> str:
        """SHA-256 of every array's raw little-endian bytes in ARRAYS order (C order), then the canonical
        JSON of nations.json's records, then of leaders.json's records."""
        h = hashlib.sha256()
        for k, a in self.arrays().items():
            h.update(np.ascontiguousarray(a, dtype=DTYPES[k]).tobytes())
        h.update(canonical_json(self.nations))
        h.update(canonical_json(self.leaders))
        return h.hexdigest()

    def row_of(self):
        """{leader id: row}."""
        return {int(i): r for r, i in enumerate(self.leader)}

    def validate(self):
        n, m = len(self.leaders), len(self.nations) + 1
        for k, a in self.arrays().items():
            if a.dtype != np.dtype(DTYPES[k]):
                raise ValueError(f"{k} must be {DTYPES[k]}, got {a.dtype}")
        shapes = {k: (n,) for k in ("leader", "tile", "at", "toward", "progress", "destination")}
        shapes.update({"people": (n, AGES), "hours": (n, AGES, len(self.activities)), "owner": (grid.COUNT,),
                       "visible": (m, BITS_BYTES), "explored": (m, BITS_BYTES)})
        shapes.update({k: (n, len(self.products)) for k in ECONOMY})
        shapes["deaths"] = (n, len(self.causes))
        for k, shape in shapes.items():
            if getattr(self, k).shape != shape:
                raise ValueError(f"{k} must have shape {list(shape)}, got {list(getattr(self, k).shape)}")
        self.validate_leaders()
        self.validate_positions()
        if len(set(self.activities)) != len(self.activities):
            raise ValueError("activity names must be unique")
        if any(tuple(sorted(p)) != tuple(sorted(PRODUCT_KEYS)) for p in self.products) or \
                len({p["name"] for p in self.products}) != len(self.products):
            raise ValueError(f"products must have unique names and keys {PRODUCT_KEYS}: {self.products}")
        for k in ("production", "labor", "production_max", "need", "consumed", "stock"):
            if np.any(getattr(self, k) < 0):
                raise ValueError(f"{k} must not be negative")
        if np.any(self.consumed > self.need * (1 + 1e-9)):
            raise ValueError("consumed must not exceed need")
        if len(set(self.causes)) != len(self.causes) or not all(isinstance(c, str) for c in self.causes):
            raise ValueError(f"causes must be unique names: {self.causes}")
        if np.any(self.deaths < 0):
            raise ValueError("deaths must not be negative")
        if np.any((self.decay < 0) | (self.decay > 1)):
            raise ValueError("decay must be between 0 and 1")
        rows = self.row_of()
        for i, rec in enumerate(self.nations, 1):
            if tuple(sorted(rec)) != tuple(sorted(NATION_KEYS)) or rec["id"] != i:
                raise ValueError(f"nation record {i} must have keys {NATION_KEYS} and id {i}: {rec}")
            if rec["capital"] is not None and not 0 <= rec["capital"] < grid.COUNT:
                raise ValueError(f"nation {i}: capital {rec['capital']} is not a tile index")
            if rec["leader"] is not None:
                r = rows.get(rec["leader"])
                if r is None or self.nation[r] != i or self.parent[r] != 0:
                    raise ValueError(f"nation {i}: leader {rec['leader']} must be one of its leaders, with no parent")
        if self.owner.max(initial=0) >= m:
            raise ValueError("owner.npy refers to a nation id with no record")
        if np.any(self.visible[0]) or np.any(self.explored[0]):
            raise ValueError("visibility row 0 (no nation) must be all zeros")
        if np.any(self.visible & ~self.explored):
            raise ValueError("every visible tile must also be explored")

    def validate_leaders(self):
        """Ids increase and are below next_leader_id; types are known; nations exist; every parent is a
        leader of the same nation, and following parents never loops."""
        if any(tuple(sorted(r)) != tuple(sorted(LEADER_KEYS)) for r in self.leaders):
            raise ValueError(f"leader records must have keys {LEADER_KEYS}")
        ids = self.leader.astype(np.int64)
        if len(ids) and (ids[0] < 1 or np.any(np.diff(ids) <= 0) or ids[-1] >= self.next_leader_id):
            raise ValueError("leader ids must be strictly increasing, from 1, and below next_leader_id")
        if len(set(self.leader_types)) != len(self.leader_types):
            raise ValueError(f"leader types must be unique: {self.leader_types}")
        rows, m = self.row_of(), len(self.nations) + 1
        for r, rec in enumerate(self.leaders):
            if rec["type"] not in self.leader_types:
                raise ValueError(f"leader {rec['id']}: unknown type {rec['type']!r}")
            if not isinstance(rec["name"], str) or not rec["name"]:
                raise ValueError(f"leader {rec['id']}: needs a name")
            if not 0 <= rec["nation"] < m:
                raise ValueError(f"leader {rec['id']}: no nation {rec['nation']}")
            if rec["parent"]:
                p = rows.get(rec["parent"])
                if p is None or self.nation[p] != rec["nation"]:
                    raise ValueError(f"leader {rec['id']}: parent {rec['parent']} must be a leader of the same nation")
        for r in range(len(self.leaders)):   # each walk up the tree must reach a root within len() steps
            p, steps = r, 0
            while self.parent[p]:
                p, steps = rows[int(self.parent[p])], steps + 1
                if steps > len(self.leaders):
                    raise ValueError(f"leader {self.leaders[r]['id']}: its parents loop")

    def validate_positions(self):
        """Tiles are on the grid; a moving segment heads for a neighbouring tile and is partway there;
        a segment that isn't moving has progress 0 and no destination; a destination is toward or one of
        its neighbours."""
        if len(self.at) and max(int(self.at.max()), int(self.toward.max()), int(self.destination.max())) >= grid.COUNT:
            raise ValueError("at, toward and destination must be tile indices")
        moving = self.at != self.toward
        if np.any(self.progress[~moving] != 0):
            raise ValueError("a segment that isn't moving (toward = at) must have progress 0")
        if np.any((self.progress < 0) | (self.progress >= 1)):
            raise ValueError("progress must be at least 0 and below 1")
        if not np.all(grid.adjacent(self.at[moving], self.toward[moving])):
            raise ValueError("a moving segment's toward must be a neighbour of its at")
        if np.any(self.destination[~moving] != self.at[~moving]):
            raise ValueError("a segment that isn't moving must have destination = at")
        last = self.destination == self.toward
        if not np.all(last | grid.adjacent(self.toward, self.destination)):
            raise ValueError("a destination must be toward or a neighbour of toward")


def _retry(fn, tries: int = 20, wait: float = 0.025):
    """Call fn(), retrying briefly on PermissionError. On Windows, replacing a file fails while another
    process has it open, and opening it fails while it is being replaced; both last only milliseconds,
    and current.json and phase.json are read every second by both servers."""
    for i in range(tries):
        try:
            return fn()
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(wait)


def _read_json(path: str):
    def read():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return _retry(read)


def _write_json_atomic(path: str, obj, indent=2):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=indent, ensure_ascii=False)
        f.write("\n")
    _retry(lambda: os.replace(tmp, path))


def write_turn(state_dir: str, turn: int, s: State, extra: dict = None) -> dict:
    """Write one complete turn, then point current.json at it. Returns the manifest."""
    s.validate()
    os.makedirs(state_dir, exist_ok=True)
    name = turn_dir_name(turn)
    final = os.path.join(state_dir, name)
    if os.path.exists(final):
        raise FileExistsError(f"{final} already exists; turns are never rewritten")
    tmp = os.path.join(state_dir, f".tmp_{name}")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)

    for k, a in s.arrays().items():
        np.save(os.path.join(tmp, f"{k}.npy"), np.ascontiguousarray(a, dtype=DTYPES[k]), allow_pickle=False)
    _write_json_atomic(os.path.join(tmp, "nations.json"), {"nations": s.nations}, indent=1)
    _write_json_atomic(os.path.join(tmp, "leaders.json"), {"leaders": s.leaders}, indent=None)
    manifest = {
        "format": FORMAT,
        "turn": turn,
        "grid": {"tile_size_degrees": grid.TILE, "width": grid.WIDTH, "height": grid.HEIGHT},
        "ages": {"count": AGES, "last_is_open_ended": True},
        "activities": s.activities,
        "hours_unit": HOURS_UNIT,
        "products": s.products,
        "causes": s.causes,
        "leader_types": s.leader_types,
        "next_leader_id": s.next_leader_id,
        "visibility_bits": "row k = nation id k; tile i is bit i % 8 of byte i // 8",
        "arrays": {k: {"file": f"{k}.npy", "dtype": DTYPES[k], "shape": list(a.shape)} for k, a in s.arrays().items()},
        "segments": len(s.leaders),
        "populated_tiles": int(len(np.unique(s.tile[(s.people > 0).any(axis=1)]))),
        "nations": len(s.nations),
        "total_people": float(s.people.sum(dtype=np.float64)),
        "checksum": {"algorithm": "sha256",
                     "of": f"raw bytes of {', '.join(ARRAYS)} in that order, then nations.json's records, then "
                           "leaders.json's records, each as canonical JSON (sorted keys, no whitespace, UTF-8)",
                     "value": s.checksum()},
        **(extra or {}),
    }
    _write_json_atomic(os.path.join(tmp, "manifest.json"), manifest)
    os.rename(tmp, final)
    _write_json_atomic(os.path.join(state_dir, CURRENT), {"format": FORMAT, "turn": turn, "dir": name})
    return manifest


def read_current(state_dir: str):
    """The current.json pointer, or None if there is no state yet."""
    try:
        cur = _read_json(os.path.join(state_dir, CURRENT))
    except FileNotFoundError:
        return None
    if cur.get("format") != FORMAT:
        raise ValueError(f"{CURRENT}: expected format {FORMAT!r}, got {cur.get('format')!r}; "
                         "delete the state folder and run engine.py init again")
    return cur


class Turn(State):
    """One saved turn. mmap=True maps the big arrays without reading them, so opening is instant and
    a lookup reads only what it needs (what the web server wants); mmap=False loads writable copies
    (what the engine wants)."""

    def __init__(self, state_dir: str, dir_name: str, mmap: bool = True):
        self.path = os.path.join(state_dir, dir_name)
        with open(os.path.join(self.path, "manifest.json"), encoding="utf-8") as f:
            self.manifest = m = json.load(f)
        if m.get("format") != FORMAT:
            raise ValueError(f"{dir_name}: expected format {FORMAT!r}, got {m.get('format')!r}")
        g = m["grid"]
        if (g["tile_size_degrees"], g["width"], g["height"]) != (grid.TILE, grid.WIDTH, grid.HEIGHT):
            raise ValueError(f"{dir_name} is for a different grid: {g}")
        self.turn = m["turn"]
        with open(os.path.join(self.path, "nations.json"), encoding="utf-8") as f:
            nations = json.load(f)["nations"]
        with open(os.path.join(self.path, "leaders.json"), encoding="utf-8") as f:
            leaders = json.load(f)["leaders"]
        big = ("people", "hours", "visible", "explored")   # mapped when mmap=True; the rest are read in full
        a = {k: np.load(os.path.join(self.path, f"{k}.npy"), mmap_mode="r" if mmap and k in big else None)
             for k in ARRAYS}
        super().__init__(leaders, a["at"], a["toward"], a["progress"], a["destination"], a["people"], a["hours"],
                         m["activities"],
                         a["owner"], nations, a["visible"], a["explored"], m["products"], {k: a[k] for k in ECONOMY},
                         m["causes"], a["deaths"], m["leader_types"], m["next_leader_id"])
        n = len(leaders)
        if any(len(a[k]) != n for k in ("leader", "tile", "at", "toward", "progress", "destination", "people", "hours",
                                        "deaths")) or \
                len(self.visible) != len(nations) + 1 or any(a[k].shape != (n, len(self.products)) for k in ECONOMY) or \
                a["deaths"].shape != (n, len(self.causes)):
            raise ValueError(f"{dir_name}: array sizes don't match the manifest")
        if not (np.array_equal(a["leader"], self.leader) and np.array_equal(a["tile"], self.tile)):
            raise ValueError(f"{dir_name}: leader.npy or tile.npy doesn't match leaders.json and the positions")
        self.by_tile = np.argsort(self.tile, kind="stable")   # rows in tile order, for rows_at()

    def rows_at(self, index: int):
        """The rows (segments) at a flat grid index, in row order; empty if none are there."""
        lo, hi = np.searchsorted(self.tile[self.by_tile], [index, index + 1])
        return self.by_tile[lo:hi]

    def verify(self) -> bool:
        return self.checksum() == self.manifest["checksum"]["value"]


def write_phase(state_dir: str, turn: int, phase: str):
    """Record that `turn` is in `phase`. Only the engine calls this."""
    if phase not in (ORDERS, RESOLVING):
        raise ValueError(f"unknown phase {phase!r}")
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_json_atomic(os.path.join(state_dir, PHASE), {"format": FORMAT, "turn": turn, "phase": phase, "since": since})


def read_phase(state_dir: str, cur: dict = None):
    """The phase of the current turn, {"turn": t, "phase": p, "since": s}, or None if there is no state.

    A turn is in ORDERS while it accepts the players' commands, and in RESOLVING once they are closed
    and the engine is applying them, along with a year's passage of time, to compute the next turn.
    phase.json applies only to the turn it names: if it is missing, or names an older turn, the current
    turn is in ORDERS. So the engine can write the new turn first and open it for orders second, and a
    reader in between still sees the right thing. `cur` is a current.json just read, to save rereading it.
    """
    cur = cur or read_current(state_dir)
    if cur is None:
        return None
    try:
        ph = _read_json(os.path.join(state_dir, PHASE))
    except FileNotFoundError:
        ph = {}
    if ph.get("turn") != cur["turn"] or ph.get("phase") not in (ORDERS, RESOLVING):
        return {"turn": cur["turn"], "phase": ORDERS, "since": None}
    return {"turn": cur["turn"], "phase": ph["phase"], "since": ph.get("since")}


def open_current(state_dir: str, mmap: bool = True):
    cur = read_current(state_dir)
    return Turn(state_dir, cur["dir"], mmap=mmap) if cur else None


def prune(state_dir: str, keep: int = 3) -> list:
    """Delete all but the newest `keep` turn directories (never the current one), plus leftover
    .tmp_ directories. Directories still mapped by a reader (Windows) are skipped and retried on
    the next call. Returns the names deleted."""
    cur = read_current(state_dir)
    names = sorted(n for n in os.listdir(state_dir) if n.startswith("turn_"))
    doomed = [n for n in names[:-keep] if not cur or n != cur["dir"]]
    doomed += [n for n in os.listdir(state_dir) if n.startswith(".tmp_turn_")]
    gone = []
    for n in doomed:
        try:
            shutil.rmtree(os.path.join(state_dir, n))
            gone.append(n)
        except OSError:
            pass
    return gone
