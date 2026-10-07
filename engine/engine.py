"""Turn engine: creates and advances the game state that server.py displays.

    python engine/engine.py init [--population server/world_population_start.json] [--nations SEED] [--state state]
    python engine/engine.py step [--turns 1] [--state state]
    python engine/engine.py verify [--state state]

(paths shown from the repository root; the script works from any directory). By default it uses the
top-level state/ directory, which server.py also reads, and the population file in server/.

`init` writes turn 0: every populated tile of the population file gets its people split across the
101 ages in a rough hunter-gatherer age structure (starting_age_shares()), and each age gets rough
placeholder activity hours shaped by age (starting_hours()). It also creates the nations
(make_nations()) and their starting visibility.

`step` advances one year per turn. For now the rules are:
- Ageing: each cohort moves up one year and the 99-year-olds join the open-ended 100-and-over group
  (no births or deaths yet). Activity hours move with the people; in the 100+ group the hours of the
  two merged groups are averaged, weighted by people.
- Ownership, recomputed every turn (update_ownership()): first come, first served. A nation owns a tile
  from the turn it is the first to have people living there, and loses it when none of its people live
  there any more.
- Visibility, recomputed every turn (active_visibility()): a nation actively sees every tile where it
  has people, plus the 8 tiles around each. Tiles it has ever seen stay explored (passive visibility).

Each step reads the current turn, computes the next one in memory and writes it as a new turn directory
(cohort_state.py), so the web server can keep reading the old turn until the new one is complete. The
engine is the only writer; it can run while the server is running.
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SERVER = os.path.join(REPO, "server")
STATE = os.path.join(REPO, "state")
sys.path.insert(0, SERVER)   # cohort_state.py and grid.py live with the server, which uses them too

import cohort_state as cs  # noqa: E402
import grid  # noqa: E402
from cohort_state import AGES  # noqa: E402

KEEP_TURNS = 3

# Placeholder activities. Replace with the real list; the state files carry the names, so readers adapt.
ACTIVITIES = ["sleep", "subsistence", "childcare", "other"]


def starting_age_shares():
    """Share of a tile's people at each age at turn 0, as float64 [AGES] summing to 1.

    A rough hunter-gatherer age structure: the steady state of a Siler mortality curve with parameters
    close to Gurven & Kaplan's (2007) forager composite. About 23% of babies die in their first year
    and 57% reach 15, life expectancy at birth is about 34, and adults who reach 15 live to about 58
    on average. That gives roughly 30% under 15, 41% aged 15-44 and 13% aged 65 or over.
    """
    a1, b1, a2, a3, b3 = 0.422, 1.131, 0.013, 0.000047, 0.086   # infant, constant and ageing terms

    def survival(x):   # share of newborns still alive at age x (closed-form integral of the hazard)
        return np.exp(-(a1 / b1) * (1 - np.exp(-b1 * x)) - a2 * x - (a3 / b3) * (np.exp(b3 * x) - 1))

    edges = survival(np.arange(0, 131, dtype=np.float64))
    lived = (edges[:-1] + edges[1:]) / 2                     # people-years lived in [a, a + 1)
    shares = np.concatenate([lived[:AGES - 1], [lived[AGES - 1:].sum()]])   # last age is 100 and over
    return shares / shares.sum()


def starting_hours():
    """Hours per person per day for each age at turn 0, as float32 [AGES, len(ACTIVITIES)].

    Rough, smooth shapes (not data): babies sleep about 14 h, falling to about 7.4 h by 20 and rising
    slowly to 8.5 h at 100; subsistence work starts around age 10, reaches 8.5 h in the 20s and eases
    to about 5.5 h by 100; childcare peaks around 30 (parents) with a smaller bump around 60
    (grandparents); "other" is whatever is left of the 24 hours. Every age gets slightly different values.
    """
    a = np.arange(AGES, dtype=np.float64)
    rise = lambda x: 1 / (1 + np.exp(-x))   # smooth 0 -> 1 step
    sleep = 7.0 + 7.0 * np.exp(-a / 5) + 0.015 * a
    subsistence = 8.5 * rise((a - 14) / 3) * (1 - 0.35 * np.clip((a - 50) / 50, 0, 1) ** 2)
    childcare = rise((a - 11) / 1.5) * (3.0 * np.exp(-((a - 30) / 11) ** 2) + 0.8 * np.exp(-((a - 62) / 14) ** 2))
    other = 24 - sleep - subsistence - childcare
    hours = np.stack([sleep, subsistence, childcare, other], axis=1)
    assert hours.shape == (AGES, len(ACTIVITIES)) and (hours >= 0).all()
    return hours.astype(np.float32)


def make_nations(counts, country, seed):
    """Turn-0 nations and the nation id of every tile, from a population file and its nation seed.

    counts: people per tile [grid.COUNT]; country: ISO code per tile (or None);
    seed: [{"code": "DNK", "capital": "N555E0125"}, ...], one entry per nation in id order.
    A capital's tile belongs to its nation; every other populated tile goes to the nation whose code
    is the tile's country code, and codes not in the seed get no nation.
    Returns (nation records, nation id per tile as uint16 [grid.COUNT]).
    """
    codes = [n["code"] for n in seed]
    if len(set(codes)) != len(codes):
        sys.exit("nation seed lists a code twice")
    capitals = {n["code"]: grid.index(*grid.parse_tile_id(n["capital"])) for n in seed}
    ids = {c: i for i, c in enumerate(codes, 1)}
    tile_nation = np.zeros(grid.COUNT, dtype=np.uint16)
    for t, c in enumerate(country):
        if c in ids:
            tile_nation[t] = ids[c]
    for c, t in capitals.items():
        tile_nation[t] = ids[c]
    tile_nation[counts <= 0] = 0
    nations = [{"id": i, "code": c, "capital": capitals[c], "founded_turn": 0, "alive": True}
               for c, i in ids.items()]
    return nations, tile_nation


def update_ownership(prev_owner, tiles, nation, people):
    """Owner of every tile this turn, uint16 [grid.COUNT] (0 = nobody): first come, first served.

    A tile's owner keeps it while the owner still has people living there. A tile with no owner, or
    whose owner's people are all gone, goes to a nation that has people there now, or to nobody.
    Each populated tile holds one nation's people for now, so a newcomer is never contested; once
    several nations can share a tile, this is where to decide which of them claims it first.
    """
    present = np.zeros(grid.COUNT, dtype=np.uint16)   # the nation living on each tile, 0 = none
    live = (people > 0).any(axis=1)
    present[tiles[live]] = nation[live]
    return np.where((prev_owner != 0) & (present == prev_owner), prev_owner, present).astype(np.uint16)


def active_visibility(tiles, nation, people, n_rows):
    """Packed bits [n_rows, BITS_BYTES]: each nation sees the tiles where it has people and the 8 tiles
    around each (east-west wraps; nothing past the poles)."""
    flags = np.zeros((n_rows, grid.HEIGHT, grid.WIDTH), dtype=bool)
    live = (nation > 0) & (people > 0).any(axis=1)
    r, c = np.divmod(tiles[live].astype(np.int64), grid.WIDTH)
    k = nation[live].astype(np.int64)
    for dr in (-1, 0, 1):
        rr = r + dr
        ok = (rr >= 0) & (rr < grid.HEIGHT)
        for dc in (-1, 0, 1):
            flags[k[ok], rr[ok], (c[ok] + dc) % grid.WIDTH] = True
    return cs.pack_tiles(flags.reshape(n_rows, grid.COUNT))


def find_seed(population_path):
    """The nation seed next to a population file: <name>.nations.json or <name>_<anything>.nations.json
    (e.g. world_population_start_capitals.nations.json for world_population_start.json), or None."""
    folder, name = os.path.split(os.path.splitext(population_path)[0])
    found = sorted(f for f in os.listdir(folder or ".")
                   if f.endswith(".nations.json") and (f == name + ".nations.json" or f.startswith(name + "_")))
    if len(found) > 1:
        sys.exit(f"more than one nation seed for {name}: {', '.join(found)}; choose one with --nations")
    return os.path.join(folder, found[0]) if found else None


def init(args):
    if cs.read_current(args.state):
        sys.exit(f"{args.state} already has a state; delete it first to start over")
    with open(args.population, encoding="utf-8") as f:
        pop = json.load(f)
    counts = np.asarray(pop["population"], dtype=np.float64)
    seed_path = args.nations or find_seed(args.population)
    if not seed_path:
        sys.exit(f"no nation seed for {os.path.basename(args.population)}; make one with "
                 f"server/gen_capitals_seed.py --population ... or pass --nations")
    with open(seed_path, encoding="utf-8") as f:
        seed = json.load(f)["nations"]
    print(f"nations from {os.path.basename(seed_path)}")
    nations, tile_nation = make_nations(counts, pop["country"], seed)

    tiles = np.flatnonzero(counts > 0).astype(cs.DTYPES["tiles"])
    people = counts[tiles, None] * starting_age_shares()[None, :]
    hours = np.broadcast_to(starting_hours(), (len(tiles), AGES, len(ACTIVITIES))).copy()
    nation = tile_nation[tiles]
    tile_owner = update_ownership(np.zeros(grid.COUNT, dtype=np.uint16), tiles, nation, people)
    visible = active_visibility(tiles, nation, people, len(nations) + 1)
    state = cs.State(tiles, people, hours, ACTIVITIES, nation, tile_owner, nations, visible, visible.copy())
    extra = {"initial_population_source": os.path.basename(args.population),
             "initial_nations_source": os.path.basename(seed_path)}
    m = cs.write_turn(args.state, 0, state, extra=extra)
    print(f"turn 0: {m['populated_tiles']} tiles, {m['total_people']:,.1f} people, {m['nations']} nations -> "
          f"{os.path.join(args.state, cs.turn_dir_name(0))}")


def age_one_year(people, hours):
    """Next year's (people, hours). Pure elementwise arithmetic, so results don't depend on the CPU
    or on how NumPy orders a sum, which keeps replays deterministic."""
    new_people = np.zeros_like(people)
    new_people[:, 1:AGES - 1] = people[:, 0:AGES - 2]
    new_people[:, AGES - 1] = people[:, AGES - 2] + people[:, AGES - 1]
    new_hours = hours.copy()                    # age 0 keeps its activity profile for future births
    new_hours[:, 1:AGES - 1] = hours[:, 0:AGES - 2]
    a, b = people[:, AGES - 2, None], people[:, AGES - 1, None]
    total = a + b
    with np.errstate(invalid="ignore", divide="ignore"):
        merged = (hours[:, AGES - 2] * a + hours[:, AGES - 1] * b) / total
    new_hours[:, AGES - 1] = np.where(total > 0, merged, hours[:, AGES - 1]).astype(np.float32)
    return new_people, new_hours


def step(args):
    for _ in range(args.turns):
        cur = cs.open_current(args.state, mmap=False)
        if cur is None:
            sys.exit(f"no state in {args.state}; run: python engine/engine.py init")
        people, hours = age_one_year(cur.people, cur.hours)
        tile_owner = update_ownership(cur.owner, cur.tiles, cur.nation, people)
        visible = active_visibility(cur.tiles, cur.nation, people, len(cur.nations) + 1)
        state = cs.State(cur.tiles, people, hours, cur.activities, cur.nation, tile_owner, cur.nations,
                         visible, cur.explored | visible)
        m = cs.write_turn(args.state, cur.turn + 1, state,
                          extra={k: v for k, v in cur.manifest.items() if k.startswith("initial_")})
        del cur
        print(f"turn {m['turn']}: {m['total_people']:,.1f} people, checksum {m['checksum']['value'][:12]}")
    gone = cs.prune(args.state, keep=KEEP_TURNS)
    if gone:
        print(f"pruned {', '.join(gone)}")


def verify(args):
    cur = cs.open_current(args.state)
    if cur is None:
        sys.exit(f"no state in {args.state}")
    ok = cur.verify()
    print(f"turn {cur.turn}: checksum {'OK' if ok else 'MISMATCH'}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", default=STATE)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="write turn 0")
    p.add_argument("--population", default=os.path.join(SERVER, "world_population_start.json"),
                   help="population file to seed turn 0 from (default: the starting scenario); use the one "
                        "server.py shows")
    p.add_argument("--nations", help="nation seed file (default: the <population name>*.nations.json next to "
                                     "the population file; one is required)")
    p = sub.add_parser("step", help="advance the current turn")
    p.add_argument("--turns", type=int, default=1)
    sub.add_parser("verify", help="check the current turn's checksum")
    args = ap.parse_args()
    return {"init": init, "step": step, "verify": verify}[args.cmd](args) or 0


if __name__ == "__main__":
    sys.exit(main())
