"""Players' orders: the commands given to leaders while a turn accepts them.

The control server (control/control.py) writes them during a turn's orders phase; the engine reads them
when it resolves that turn and applies the valid ones. They live beside the turns:

    state/
      orders/
        turn_00007/
          nation_037.json     {"format": "capita/orders-1", "turn": 7, "nation": 37,
                               "commands": [{"leader": 37, "type": "move", "to": "S355E1495"}, ...]}

One file per nation, so players never write the same file; at most one command per leader, in leader id
order (a new command for a leader replaces the old one). Commands name tiles by id (grid.py).

Command types:
    move    {"leader": id, "type": "move", "to": tile id}: the leader takes their segment to `to`. A
            segment that is standing on a tile can be sent to any of the 8 tiles around it that has land.
            One that is already moving from tile A to tile B can be sent to A, B, or any tile with land
            next to either: it first finishes the step to B or walks back to A, whichever makes the whole
            way to `to` shorter (plan_move()), then goes on from there. The engine moves it when the turn
            resolves (engine.py move_segments()); it keeps going on later turns until it arrives.
    found_kingdom  {"leader": id, "type": "found_kingdom", "title": "King"}: the leader's segment settles on
            its tile and the leader becomes a king (leaders.json "commands"), called `title`. The segment
            must be wholly on one tile (not between two), and nobody, of any nation, may have a settled
            segment there: once a leader settles a tile, it is theirs alone. The engine also refuses it if
            another leader settles the same tile in the same turn (engine.py apply_orders()).
    establish_settlement  {"leader": id, "type": "establish_settlement", "title": "Lord"}: the same, for a
            settling party, whose leader becomes a lord.
    form_settlers  {"leader": id, "type": "form_settlers", "people": 250}: splits a settling party of that
            many people off the leader's segment, under a new pioneer who reports to the leader (at least
            min_people, leaving at least min_left; leaders.json "commands").

Which commands a leader can be given depends on their type (leaders.json "types": "commands").

check() is the one place a command is validated: control.py uses it to refuse a bad command at once,
and the engine uses it again on everything it reads, since the files could have been written by hand
or the state could have changed.
"""
import json
import os
import shutil

import numpy as np

import grid
import names
from cohort_state import _retry, _write_json_atomic, segment_latlon, turn_dir_name

FORMAT = "capita/orders-1"
DIR = "orders"
COMMAND_TYPES = ("move", "found_kingdom", "establish_settlement", "form_settlers")
SETTLE = ("found_kingdom", "establish_settlement")   # the commands that settle a segment under a new title


def turn_orders_dir(state_dir: str, turn: int) -> str:
    return os.path.join(state_dir, DIR, turn_dir_name(turn))


def nation_file(state_dir: str, turn: int, nation: int) -> str:
    return os.path.join(turn_orders_dir(state_dir, turn), f"nation_{nation:03d}.json")


def read_nation(state_dir: str, turn: int, nation: int) -> list:
    """One nation's commands for a turn, in leader id order ([] if it has given none)."""
    def read():
        with open(nation_file(state_dir, turn, nation), encoding="utf-8") as f:
            return json.load(f)
    try:
        data = _retry(read)
    except FileNotFoundError:
        return []
    if data.get("format") != FORMAT or data.get("turn") != turn or data.get("nation") != nation:
        raise ValueError(f"{nation_file(state_dir, turn, nation)} isn't {FORMAT} orders for nation {nation}, turn {turn}")
    return data["commands"]


def write_nation(state_dir: str, turn: int, nation: int, commands: list):
    """Replace one nation's commands for a turn (atomically). Only the control server calls this."""
    os.makedirs(turn_orders_dir(state_dir, turn), exist_ok=True)
    commands = sorted(commands, key=lambda c: c["leader"])
    _write_json_atomic(nation_file(state_dir, turn, nation),
                       {"format": FORMAT, "turn": turn, "nation": nation, "commands": commands})


def read_turn(state_dir: str, turn: int) -> list:
    """Every nation's commands for a turn, as (nation id, command) in nation then leader id order."""
    folder = turn_orders_dir(state_dir, turn)
    try:
        names = sorted(n for n in os.listdir(folder) if n.startswith("nation_") and n.endswith(".json"))
    except FileNotFoundError:
        return []
    out = []
    for n in names:
        nation = int(n[len("nation_"):-len(".json")])
        out += [(nation, c) for c in read_nation(state_dir, turn, nation)]
    return out


def prune(state_dir: str, before_turn: int) -> list:
    """Delete the orders of turns before `before_turn`. Returns the folder names deleted."""
    folder = os.path.join(state_dir, DIR)
    try:
        names = sorted(n for n in os.listdir(folder) if n.startswith("turn_"))
    except FileNotFoundError:
        return []
    gone = []
    for n in names:
        if n < turn_dir_name(before_turn):
            try:
                shutil.rmtree(os.path.join(folder, n))
                gone.append(n)
            except OSError:
                pass
    return gone


def walkable(world) -> np.ndarray:
    """Whether a segment can walk onto each tile, bool [grid.COUNT]: any tile that isn't all water.
    (Crossing water will need boats.) world: a convert_map.MapColumns."""
    water = np.asarray(world.terrain[world.terrain_types.index("water")])
    return water < 100


def tile_index(tid) -> int:
    if not isinstance(tid, str) or not grid.TILE_ID_RE.match(tid):
        raise ValueError(f"not a tile id: {tid!r}")
    lat, lon = grid.parse_tile_id(tid)
    if not (-90 <= lat < 90 and -180 <= lon < 180) or round(lat * 10) % 5 or round(lon * 10) % 5:
        raise ValueError(f"not a tile id: {tid!r}")
    return grid.index(lat, lon)


def tile_name(index) -> str:
    return grid.tile_id(*grid.corner(*divmod(int(index), grid.WIDTH)))


def neighbours(index: int):
    """[(direction, tile index)] of the 8 tiles around a tile, in grid.DIRS order (none past the poles)."""
    row, col = divmod(int(index), grid.WIDTH)
    return [(d, (row - up) * grid.WIDTH + (col + east) % grid.WIDTH)
            for d, up, east in grid.DIRS if 0 <= row - up < grid.HEIGHT]


def compass(lat0, lon0, index) -> str:
    """The 8-point compass direction ("n", "ne", ...) from a point to the centre of a tile."""
    lat1, lon1 = grid.centre(index)
    dlon = (lon1 - lon0 + 180) % 360 - 180
    angle = np.degrees(np.arctan2(dlon * np.cos(np.radians((lat0 + lat1) / 2)), lat1 - lat0))   # 0 = north, clockwise
    return grid.DIRS[int(round(float(angle) / 45)) % 8][0]


def move_options(at: int, toward: int, progress: float):
    """Where a segment can be sent: [(direction, tile index)]. A segment standing on a tile can go to any
    of its 8 neighbours (direction "n", "ne", ...). One moving from at to toward can go on to toward
    ("on"), back to at ("back"), or to any other neighbour of either (its compass direction from where the
    segment is now)."""
    at, toward = int(at), int(toward)
    if at == toward:
        return neighbours(at)
    lat, lon = segment_latlon(at, toward, progress)
    others = {i for _, i in neighbours(toward) + neighbours(at)} - {at, toward}
    order = [d for d, _, _ in grid.DIRS]
    rest = sorted(((compass(float(lat), float(lon), i), i) for i in others),
                  key=lambda o: (order.index(o[0]), o[1]))   # clockwise from north
    return [("on", toward), ("back", at)] + rest


def plan_move(at: int, toward: int, progress: float, to: int) -> dict:
    """How a segment gets to tile `to`, a tile move_options() offers it. A standing segment steps
    straight there. One moving from A (at) to B (toward) either finishes the step to B or walks back to A,
    then steps on to `to` (no step if `to` is that tile): whichever whole way is shorter, finishing the
    step on a tie. Returns {"via": the tile it passes first (None if it goes straight), "km": the whole
    way, and the new "at", "toward", "progress" and "destination"}."""
    at, toward, to = int(at), int(toward), int(to)
    if at == toward:
        return {"via": None, "km": float(grid.distance_km(at, to)), "at": at, "toward": to, "progress": 0.0,
                "destination": to}
    ab = float(grid.distance_km(at, toward))
    routes = []
    if to == toward or bool(grid.adjacent(toward, to)):   # finish the step to B
        km = (1 - progress) * ab + (0.0 if to == toward else float(grid.distance_km(toward, to)))
        routes.append({"via": None if to == toward else toward, "km": km, "at": at, "toward": toward,
                       "progress": float(progress), "destination": to})
    if to == at or bool(grid.adjacent(at, to)):           # walk back to A
        km = progress * ab + (0.0 if to == at else float(grid.distance_km(at, to)))
        back = 1.0 - progress
        if back < 1.0:
            r = {"at": toward, "toward": at, "progress": back}
        else:   # it hadn't left A
            r = {"at": at, "toward": to, "progress": 0.0}
        routes.append({"via": None if to == at else at, "km": km, **r, "destination": to})
    if not routes:
        raise ValueError(f"{tile_name(to)} isn't next to {tile_name(at)} or {tile_name(toward)}")
    return min(routes, key=lambda r: r["km"])   # the first (finishing the step) wins a tie


def make(lid: int, command: dict) -> dict:
    """A command as it is stored: the leader, the type and that type's fields (nothing else is kept)."""
    if command.get("type") in SETTLE:
        return {"leader": lid, "type": command["type"], "title": command.get("title")}
    if command.get("type") == "form_settlers":
        return {"leader": lid, "type": "form_settlers", "people": command.get("people")}
    return {"leader": lid, "type": command.get("type"), "to": command.get("to")}


def settled(state, rules) -> np.ndarray:
    """Whether each row's segment is settled, bool [S]: its leader's type is (leaders.json)."""
    types = rules["types"]
    return np.array([bool(types[lead["type"]]["settled"]) for lead in state.leaders], dtype=bool)


def settled_by(state, rules, tile: int):
    """The nation of a settled segment with people on a tile (its own nation counts too: once a leader
    settles a tile, it is theirs alone), or None if nobody has settled it."""
    here = np.flatnonzero(np.asarray(state.tile) == tile)
    types = rules["types"]
    return next((state.leaders[r]["nation"] for r in here.tolist() if types[state.leaders[r]["type"]]["settled"]
                 and float(np.asarray(state.people[r]).sum()) > 0), None)


def check(state, rows: dict, nation: int, command: dict, can_walk, rules) -> str:
    """Why a nation's command can't be carried out at this state, or None if it can.

    state: a cohort_state.State (the turn the command is for); rows: state.row_of(); can_walk: walkable();
    rules: leaders.json's rules (the turn's manifest keeps a copy).
    """
    if not isinstance(command, dict) or command.get("type") not in COMMAND_TYPES:
        return f"unknown command type {command.get('type') if isinstance(command, dict) else command!r}"
    lid = command.get("leader")
    r = rows.get(lid) if isinstance(lid, int) and not isinstance(lid, bool) else None
    if r is None:
        return f"no leader {lid!r}"
    if state.leaders[r]["nation"] != nation:
        return f"leader {lid} doesn't belong to nation {nation}"
    kind = state.leaders[r]["type"]
    if command["type"] not in rules["types"][kind]["commands"]:
        return f"a {kind} can't be given {command['type']} commands"
    if command["type"] in SETTLE:
        spec = rules["commands"][command["type"]]
        title = command.get("title")
        try:
            if names.clean(title, spec["max_title_length"], "title") != title:
                return "a title must have no spaces at either end, nor more than one in a row"
        except ValueError as e:
            return str(e)
        if state.at[r] != state.toward[r]:
            return "a segment can't settle while on the move: it must be on one tile"
        if settled_by(state, rules, int(state.at[r])) is not None:
            return f"{tile_name(state.at[r])} is already settled"
    if command["type"] == "form_settlers":
        spec = rules["commands"]["form_settlers"]
        n, have = command.get("people"), float(np.asarray(state.people[r]).sum())
        if not isinstance(n, int) or isinstance(n, bool):
            return "people must be a whole number"
        if n < spec["min_people"]:
            return f"a settling party needs at least {spec['min_people']} people"
        if have - n < spec["min_left"]:
            return f"the segment has {have:.0f} people and must keep at least {spec['min_left']}"
    if command["type"] == "move":
        try:
            to = tile_index(command.get("to"))
        except ValueError as e:
            return str(e)
        at, toward = int(state.at[r]), int(state.toward[r])
        if to not in [i for _, i in move_options(at, toward, float(state.progress[r]))]:
            return (f"{command['to']} isn't next to {tile_name(at)}" if at == toward else
                    f"{command['to']} isn't next to {tile_name(at)} or {tile_name(toward)}, between which the "
                    f"segment is moving")
        if to not in (at, toward) and not can_walk[to]:
            return f"{command['to']} is all water"
    return None
