"""Camps: how the maps show where people live. Each nation's people on a tile are drawn the way their
leaders' types say (leaders.json "camp"): a band's as tepees, a settled segment's as round huts, a
settling party's as covered wagons. All of a nation's segments on a tile drawn the same way (on the
tile, or counted as on it while moving) are added up into one camp, whose count grows with the people
without giving the number away. Wagons sharing a tile with huts are drawn smaller. A camp with a
leader whose type has a palace (a king) also shows the palace: a hut flying the nation's flag. The player server
(player/player.py) and the developer view (server.py) both send camps; static/camps.js draws them.
"""
import json
import os

import numpy as np

SERVER = os.path.dirname(os.path.abspath(__file__))
DESCRIPTIONS = os.path.join(os.path.dirname(SERVER), "player", "descriptions.json")
LOOKS = ("huts", "tepees", "wagons")   # the order camps of one tile and nation are listed in


def load_rules(path: str = DESCRIPTIONS) -> dict:
    """{look: {"base", "base_people", "min", "max"}} from descriptions.json: "camp" for tepees and huts,
    "wagons" for wagons."""
    with open(path, encoding="utf-8") as f:
        return rules_from(json.load(f))


def rules_from(descriptions: dict) -> dict:
    return {"tepees": descriptions["camp"], "huts": descriptions["camp"], "wagons": descriptions["wagons"]}


def count(people: float, rule: dict) -> int:
    """How many tepees, huts or wagons a camp of `people` shows: `base` for `base_people`, growing with the
    square root of the people, between `min` and `max`."""
    n = round(rule["base"] * (max(people, 0) / rule["base_people"]) ** 0.5)
    return int(min(rule["max"], max(rule["min"], n)))


def camps(t, rows, rules: dict) -> list:
    """The camps of the segments in `rows` of turn `t`: [{"tile": index, "nation": id (0 = none), "look":
    "huts", "tepees" or "wagons", "count", "small": wagons on a tile with huts, "palace": it has one
    besides}], one per tile, nation and look with people, in tile, nation and look order."""
    types = t.manifest["leader_rules"]["types"]
    people, palaces = {}, set()
    for r in rows:
        kind = types[t.leaders[r]["type"]]
        key = (int(t.tile[r]), t.leaders[r]["nation"], LOOKS.index(kind["camp"]))
        people[key] = people.get(key, 0.0) + float(np.asarray(t.people[r]).sum())
        if kind.get("palace"):
            palaces.add(key)
    huts = {i for (i, _, look), p in people.items() if p > 0 and LOOKS[look] == "huts"}
    return [{"tile": i, "nation": k, "look": LOOKS[look], "count": count(p, rules[LOOKS[look]]),
             "small": LOOKS[look] == "wagons" and i in huts, "palace": (i, k, look) in palaces}
            for (i, k, look), p in sorted(people.items()) if p > 0]
