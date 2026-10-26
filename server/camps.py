"""Camps: how the maps show where people live. Each nation's people on a tile, all its segments there
added up (on the tile or counted as on it while moving), are one camp, drawn as a handful of tepees,
or of round huts if any of those segments is settled (leaders.json). The count grows with the people
without giving the number away. The player server (player/player.py) and the developer view
(server.py) both send camps; static/camps.js draws them on both pages.
"""
import json
import os

import numpy as np

import orders

SERVER = os.path.dirname(os.path.abspath(__file__))
DESCRIPTIONS = os.path.join(os.path.dirname(SERVER), "player", "descriptions.json")


def load_rule(path: str = DESCRIPTIONS) -> dict:
    """descriptions.json's "camp": {"base", "base_people", "min", "max"}."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)["camp"]


def tepees(people: float, rule: dict) -> int:
    """How many tepees (or huts) a camp of `people` shows: `base` for `base_people`, growing with the
    square root of the people, between `min` and `max`."""
    n = round(rule["base"] * (max(people, 0) / rule["base_people"]) ** 0.5)
    return int(min(rule["max"], max(rule["min"], n)))


def camps(t, rows, rule: dict) -> list:
    """The camps of the segments in `rows` of turn `t`: [{"tile": index, "nation": id (0 = none),
    "tepees", "settled"}], one per tile and nation with people, in tile then nation order."""
    is_settled = orders.settled(t, t.manifest["leader_rules"])
    people, settled = {}, set()
    for r in rows:
        key = (int(t.tile[r]), t.leaders[r]["nation"])
        people[key] = people.get(key, 0.0) + float(np.asarray(t.people[r]).sum())
        if is_settled[r]:
            settled.add(key)
    return [{"tile": i, "nation": k, "tepees": tepees(p, rule), "settled": (i, k) in settled}
            for (i, k), p in sorted(people.items()) if p > 0]
