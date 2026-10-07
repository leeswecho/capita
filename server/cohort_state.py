"""Per-turn cohort state: the files the game engine writes and the web server reads.

Layout (see reference/cohort_state_schema.md for the full, language-neutral spec):

    state/
      current.json            {"format": ..., "turn": 7, "dir": "turn_00007"}; replaced atomically
      turn_00007/
        manifest.json         turn number, activity names, units, array shapes/dtypes, checksum
        tiles.npy             <u4 [N]          flat grid index (grid.py) of each row, strictly increasing
        people.npy            <f8 [N, 101]     people by single year of age; column 100 is "100 and over"
        hours.npy             <f4 [N, 101, A]  mean hours per person per day spent on each activity

Only tiles with people get a row. A turn directory is complete before current.json names it and is
never changed afterwards, so readers can memory-map it safely. Windows won't delete a file another
process has mapped, so old turns are pruned on a best-effort basis (see prune()).
"""
import hashlib
import json
import os
import shutil

import numpy as np

import grid

FORMAT = "capita/cohort-state-1"
AGES = 101                      # ages 0..99, then 100 = "100 and over"
HOURS_UNIT = "mean hours per person per day, averaged over the year"
DTYPES = {"tiles": "<u4", "people": "<f8", "hours": "<f4"}
CURRENT = "current.json"


def turn_dir_name(turn: int) -> str:
    return f"turn_{turn:05d}"


def checksum(tiles, people, hours) -> str:
    """SHA-256 of the three arrays' raw little-endian bytes, in that order (C order)."""
    h = hashlib.sha256()
    for a, dt in ((tiles, DTYPES["tiles"]), (people, DTYPES["people"]), (hours, DTYPES["hours"])):
        h.update(np.ascontiguousarray(a, dtype=dt).tobytes())
    return h.hexdigest()


def validate(tiles, people, hours, activities):
    n = len(tiles)
    if tiles.dtype != np.dtype(DTYPES["tiles"]) or tiles.ndim != 1:
        raise ValueError(f"tiles must be {DTYPES['tiles']} [N]")
    if n and (np.any(np.diff(tiles.astype(np.int64)) <= 0) or tiles[-1] >= grid.COUNT):
        raise ValueError("tiles must be strictly increasing grid indices")
    if people.dtype != np.dtype(DTYPES["people"]) or people.shape != (n, AGES):
        raise ValueError(f"people must be {DTYPES['people']} [{n}, {AGES}], got {people.dtype} {people.shape}")
    if hours.dtype != np.dtype(DTYPES["hours"]) or hours.shape != (n, AGES, len(activities)):
        raise ValueError(f"hours must be {DTYPES['hours']} [{n}, {AGES}, {len(activities)}], "
                         f"got {hours.dtype} {hours.shape}")
    if len(set(activities)) != len(activities):
        raise ValueError("activity names must be unique")


def _write_json_atomic(path: str, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def write_turn(state_dir: str, turn: int, tiles, people, hours, activities, extra: dict = None) -> dict:
    """Write one complete turn, then point current.json at it. Returns the manifest."""
    validate(tiles, people, hours, activities)
    os.makedirs(state_dir, exist_ok=True)
    name = turn_dir_name(turn)
    final = os.path.join(state_dir, name)
    if os.path.exists(final):
        raise FileExistsError(f"{final} already exists; turns are never rewritten")
    tmp = os.path.join(state_dir, f".tmp_{name}")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)

    arrays = {"tiles": tiles, "people": people, "hours": hours}
    for k, a in arrays.items():
        np.save(os.path.join(tmp, f"{k}.npy"), np.ascontiguousarray(a, dtype=DTYPES[k]), allow_pickle=False)
    manifest = {
        "format": FORMAT,
        "turn": turn,
        "grid": {"tile_size_degrees": grid.TILE, "width": grid.WIDTH, "height": grid.HEIGHT},
        "ages": {"count": AGES, "last_is_open_ended": True},
        "activities": list(activities),
        "hours_unit": HOURS_UNIT,
        "arrays": {k: {"file": f"{k}.npy", "dtype": DTYPES[k], "shape": list(a.shape)}
                   for k, a in arrays.items()},
        "populated_tiles": len(tiles),
        "total_people": float(people.sum(dtype=np.float64)),
        "checksum": {"algorithm": "sha256", "of": "tiles, people, hours raw bytes in that order",
                     "value": checksum(tiles, people, hours)},
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
        raise ValueError(f"{CURRENT}: expected format {FORMAT!r}, got {cur.get('format')!r}")
    return cur


class Turn:
    """One turn's arrays. mmap=True maps people/hours without reading them, so opening is instant
    and a lookup reads only that tile's rows (what the web server wants); mmap=False loads
    writable copies (what the engine wants)."""

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
        self.activities = m["activities"]
        mode = "r" if mmap else None
        self.tiles = np.load(os.path.join(self.path, "tiles.npy"))   # small: always read in full
        self.people = np.load(os.path.join(self.path, "people.npy"), mmap_mode=mode)
        self.hours = np.load(os.path.join(self.path, "hours.npy"), mmap_mode=mode)
        validate(self.tiles, self.people, self.hours, self.activities)

    def row(self, index: int):
        """Row of a flat grid index, or None if that tile has no people."""
        r = int(np.searchsorted(self.tiles, index))
        return r if r < len(self.tiles) and self.tiles[r] == index else None

    def verify(self) -> bool:
        return checksum(self.tiles, self.people, self.hours) == self.manifest["checksum"]["value"]


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
