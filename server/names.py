"""Names the players have given their nations, kept beside the turns:

    state/
      names.json      {"format": "capita/names-1", "nations": {"37": "The River People", ...}}

A nation without an entry goes by its country's name. Only the player server writes the file (under
the state's lock, cohort_state.state_lock()); the engine never reads it, since a name changes nothing
in the simulation. Every server shows the given names.
"""
import os

from cohort_state import _read_json, _write_json_atomic, state_lock

FORMAT = "capita/names-1"
FILE = "names.json"
MAX_LENGTH = 40

_cache = {}   # state dir -> (mtime, names)


def read(state_dir: str) -> dict:
    """{nation id: given name}; {} if none have been given. Rereads the file only when it changes."""
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
    names = {int(k): v for k, v in data["nations"].items()}
    _cache[state_dir] = (mtime, names)
    return names


def clean(name, max_length: int = MAX_LENGTH, what: str = "name") -> str:
    """The name as it will be saved: spaces collapsed and trimmed. Raises ValueError if it can't be one.
    (Leaders' titles follow the same rules, with their own length, orders.py.)"""
    if not isinstance(name, str):
        raise ValueError(f"a {what} must be text")
    name = " ".join(name.split())
    if not name:
        raise ValueError(f"a {what} can't be empty")
    if len(name) > max_length:
        raise ValueError(f"a {what} can be at most {max_length} characters")
    if any(not c.isprintable() for c in name):
        raise ValueError(f"a {what} can't contain control characters")
    return name


def write(state_dir: str, nation: int, name):
    """Give nation `nation` a name (None goes back to its country's name). Returns the saved name."""
    name = clean(name) if name is not None else None
    with state_lock(state_dir):
        names = dict(read(state_dir))
        if name is None:
            names.pop(nation, None)
        else:
            names[nation] = name
        _write_json_atomic(os.path.join(state_dir, FILE),
                           {"format": FORMAT, "nations": {str(k): v for k, v in sorted(names.items())}})
    return name
