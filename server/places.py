"""Names the players have given tiles, kept beside the turns. Each nation names tiles for itself, so a
tile can have a name for every nation, or none:

    state/
      places.json     {"format": "capita/places-1", "nations": {"14": {"N600E0245": "Home Vale", ...}, ...}}

Only named tiles are listed; the player page calls the rest "Unnamed land". Only the player server
writes the file (under the state's lock, cohort_state.state_lock()); the engine never reads it, since a
name changes nothing in the simulation.
"""
import os

import names
from cohort_state import _read_json, _write_json_atomic, state_lock

FORMAT = "capita/places-1"
FILE = "places.json"
MAX_LENGTH = 30

_cache = {}   # state dir -> (mtime, places)


def read(state_dir: str) -> dict:
    """{nation id: {tile id: name}}; {} if none have been given. Rereads the file only when it changes."""
    path = os.path.join(state_dir, FILE)
    try:
        mtime = os.stat(path).st_mtime_ns
    except FileNotFoundError:
        return {}
    hit = _cache.get(state_dir)
    if hit and hit[0] == mtime:
        return hit[1]
    data = _read_json(path)
    if data.get("format") != FORMAT:
        raise ValueError(f"{path}: expected format {FORMAT!r}, got {data.get('format')!r}")
    places = {int(k): v for k, v in data["nations"].items()}
    _cache[state_dir] = (mtime, places)
    return places


def write(state_dir: str, nation: int, tile: str, name):
    """Nation `nation`'s name for a tile (None takes it back). Returns the saved name. Raises ValueError
    if the name can't be one (names.clean())."""
    name = names.clean(name, MAX_LENGTH) if name is not None else None
    with state_lock(state_dir):
        places = {k: dict(v) for k, v in read(state_dir).items()}
        mine = places.setdefault(nation, {})
        if name is None:
            mine.pop(tile, None)
        else:
            mine[tile] = name
        if not mine:
            del places[nation]
        _write_json_atomic(os.path.join(state_dir, FILE),
                           {"format": FORMAT, "nations": {str(k): dict(sorted(v.items())) for k, v in sorted(places.items())}})
    return name
