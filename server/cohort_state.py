"""Per-turn game state: the files the game engine writes and the web server reads.

Layout (see reference/cohort_state_schema.md for the full, language-neutral spec):

    state/
      current.json            {"format": ..., "turn": 7, "dir": "turn_00007"}; replaced atomically
      turn_00007/
        manifest.json         turn number, activity names, units, array shapes/dtypes, checksum
        nations.json          one record per nation: id, code, capital tile, founded turn, alive
        tiles.npy             <u4 [N]          flat grid index (grid.py) of each row, strictly increasing
        people.npy            <f8 [N, 101]     people by single year of age; column 100 is "100 and over"
        hours.npy             <f4 [N, 101, A]  mean hours per person per day spent on each activity
        nation.npy            <u2 [N]          nation id the row's people belong to (0 = none)
        owner.npy             <u2 [259200]     nation id that owns each tile of the whole grid (0 = nobody)
        visible.npy           <u1 [M, 32400]   active visibility: row k = nation id k, one bit per tile
        explored.npy          <u1 [M, 32400]   tiles each nation has ever seen (visible is a subset)

Only tiles with people get a row. Nation ids are 1..M-1 and are never reused; row 0 of the visibility
arrays belongs to "no nation" and is all zeros. Bits are packed little-endian: tile i is bit i % 8 of
byte i // 8. A turn directory is complete before current.json names it and is never changed afterwards,
so readers can memory-map it safely. Windows won't delete a file another process has mapped, so old
turns are pruned on a best-effort basis (see prune()).
"""
import hashlib
import json
import os
import shutil

import numpy as np

import grid

FORMAT = "capita/cohort-state-3"
AGES = 101                      # ages 0..99, then 100 = "100 and over"
HOURS_UNIT = "mean hours per person per day, averaged over the year"
BITS_BYTES = (grid.COUNT + 7) // 8          # one visibility row: 259,200 tiles -> 32,400 bytes
DTYPES = {"tiles": "<u4", "people": "<f8", "hours": "<f4", "nation": "<u2", "owner": "<u2",
          "visible": "<u1", "explored": "<u1"}
ARRAYS = tuple(DTYPES)                      # file order, which is also checksum order
NATION_KEYS = ("id", "code", "capital", "founded_turn", "alive")
CURRENT = "current.json"


def turn_dir_name(turn: int) -> str:
    return f"turn_{turn:05d}"


def pack_tiles(flags):
    """bool [..., grid.COUNT] -> uint8 [..., BITS_BYTES], little-endian bit order."""
    return np.packbits(flags, axis=-1, bitorder="little")


def unpack_tiles(bits):
    """uint8 [..., BITS_BYTES] -> bool [..., grid.COUNT]."""
    return np.unpackbits(bits, axis=-1, count=grid.COUNT, bitorder="little").astype(bool)


def nations_bytes(nations) -> bytes:
    """Canonical JSON of the nation records (sorted keys, no spaces, UTF-8), as hashed by checksum()."""
    return json.dumps(nations, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class State:
    """Everything in one turn. The engine builds one, write_turn() saves it, Turn reads it back."""

    def __init__(self, tiles, people, hours, activities, nation, owner, nations, visible, explored):
        self.tiles, self.people, self.hours, self.nation, self.owner = tiles, people, hours, nation, owner
        self.visible, self.explored = visible, explored
        self.activities, self.nations = list(activities), nations

    def arrays(self):
        return {k: getattr(self, k) for k in ARRAYS}

    def checksum(self) -> str:
        """SHA-256 of every array's raw little-endian bytes in ARRAYS order (C order), then nations_bytes()."""
        h = hashlib.sha256()
        for k, a in self.arrays().items():
            h.update(np.ascontiguousarray(a, dtype=DTYPES[k]).tobytes())
        h.update(nations_bytes(self.nations))
        return h.hexdigest()

    def validate(self):
        n, m = len(self.tiles), len(self.nations) + 1
        for k, a in self.arrays().items():
            if a.dtype != np.dtype(DTYPES[k]):
                raise ValueError(f"{k} must be {DTYPES[k]}, got {a.dtype}")
        shapes = {"tiles": (n,), "people": (n, AGES), "hours": (n, AGES, len(self.activities)), "nation": (n,), "owner": (grid.COUNT,),
                  "visible": (m, BITS_BYTES), "explored": (m, BITS_BYTES)}
        for k, shape in shapes.items():
            if getattr(self, k).shape != shape:
                raise ValueError(f"{k} must have shape {list(shape)}, got {list(getattr(self, k).shape)}")
        if n and (np.any(np.diff(self.tiles.astype(np.int64)) <= 0) or self.tiles[-1] >= grid.COUNT):
            raise ValueError("tiles must be strictly increasing grid indices")
        if len(set(self.activities)) != len(self.activities):
            raise ValueError("activity names must be unique")
        for i, rec in enumerate(self.nations, 1):
            if tuple(sorted(rec)) != tuple(sorted(NATION_KEYS)) or rec["id"] != i:
                raise ValueError(f"nation record {i} must have keys {NATION_KEYS} and id {i}: {rec}")
            if rec["capital"] is not None and not 0 <= rec["capital"] < grid.COUNT:
                raise ValueError(f"nation {i}: capital {rec['capital']} is not a tile index")
        if n and self.nation.max(initial=0) >= m:
            raise ValueError("nation.npy refers to a nation id with no record")
        if self.owner.max(initial=0) >= m:
            raise ValueError("owner.npy refers to a nation id with no record")
        if np.any(self.visible[0]) or np.any(self.explored[0]):
            raise ValueError("visibility row 0 (no nation) must be all zeros")
        if np.any(self.visible & ~self.explored):
            raise ValueError("every visible tile must also be explored")


def _write_json_atomic(path: str, obj, indent=2):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=indent, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


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
    manifest = {
        "format": FORMAT,
        "turn": turn,
        "grid": {"tile_size_degrees": grid.TILE, "width": grid.WIDTH, "height": grid.HEIGHT},
        "ages": {"count": AGES, "last_is_open_ended": True},
        "activities": s.activities,
        "hours_unit": HOURS_UNIT,
        "visibility_bits": "row k = nation id k; tile i is bit i % 8 of byte i // 8",
        "arrays": {k: {"file": f"{k}.npy", "dtype": DTYPES[k], "shape": list(a.shape)} for k, a in s.arrays().items()},
        "populated_tiles": len(s.tiles),
        "nations": len(s.nations),
        "total_people": float(s.people.sum(dtype=np.float64)),
        "checksum": {"algorithm": "sha256",
                     "of": f"raw bytes of {', '.join(ARRAYS)} in that order, then nations.json's records "
                           "as canonical JSON (sorted keys, no whitespace, UTF-8)",
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
        with open(os.path.join(state_dir, CURRENT), encoding="utf-8") as f:
            cur = json.load(f)
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
        small = ("tiles", "nation", "owner")   # always read in full; the rest are mapped when mmap=True
        a = {k: np.load(os.path.join(self.path, f"{k}.npy"), mmap_mode=None if mmap is False or k in small else "r")
             for k in ARRAYS}
        super().__init__(a["tiles"], a["people"], a["hours"], m["activities"], a["nation"], a["owner"], nations,
                         a["visible"], a["explored"])
        if len(self.tiles) != len(self.people) or len(self.visible) != len(nations) + 1:
            raise ValueError(f"{dir_name}: array sizes don't match the manifest")

    def row(self, index: int):
        """Row of a flat grid index, or None if that tile has no people."""
        r = int(np.searchsorted(self.tiles, index))
        return r if r < len(self.tiles) and self.tiles[r] == index else None

    def verify(self) -> bool:
        return self.checksum() == self.manifest["checksum"]["value"]


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
