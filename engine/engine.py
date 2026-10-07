"""Turn engine: creates and advances the cohort state that server.py displays.

    python engine/engine.py init [--population server/world_population.json] [--state server/state]
    python engine/engine.py step [--turns 1] [--state server/state]
    python engine/engine.py verify [--state server/state]

(paths shown from the repository root; the script works from any directory). By default it uses the
state directory and population file in ../server, the ones server.py reads.

`init` writes turn 0: every populated tile of the population file gets its people spread evenly over
the 101 ages, and every cohort gets the same placeholder activity hours (ACTIVITIES). `step` advances
one year per turn. For now the only rule is ageing: each cohort moves up one year and the 99-year-olds
join the open-ended 100-and-over group (no births or deaths yet). Activity hours move with the people;
in the 100+ group the hours of the two merged groups are averaged, weighted by people.

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
SERVER = os.path.join(os.path.dirname(HERE), "server")
sys.path.insert(0, SERVER)   # cohort_state.py and grid.py live with the server, which uses them too

import cohort_state as cs  # noqa: E402
from cohort_state import AGES  # noqa: E402

KEEP_TURNS = 3

# Placeholder activities and the hours per day every cohort starts with (they sum to 24).
# Replace with the real activity list; the state files carry the names, so readers adapt.
ACTIVITIES = {"sleep": 8.0, "subsistence": 6.0, "childcare": 2.0, "other": 8.0}


def init(args):
    if cs.read_current(args.state):
        sys.exit(f"{args.state} already has a state; delete it first to start over")
    with open(args.population, encoding="utf-8") as f:
        pop = json.load(f)
    counts = np.asarray(pop["population"], dtype=np.float64)
    tiles = np.flatnonzero(counts > 0).astype(cs.DTYPES["tiles"])
    people = np.repeat(counts[tiles, None] / AGES, AGES, axis=1)
    hours = np.broadcast_to(np.array(list(ACTIVITIES.values()), dtype=np.float32),
                            (len(tiles), AGES, len(ACTIVITIES))).copy()
    m = cs.write_turn(args.state, 0, tiles, people, hours, list(ACTIVITIES),
                      extra={"initial_population_source": os.path.basename(args.population)})
    print(f"turn 0: {m['populated_tiles']} tiles, {m['total_people']:,.1f} people -> "
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
        m = cs.write_turn(args.state, cur.turn + 1, cur.tiles, people, hours, cur.activities,
                          extra={k: v for k, v in cur.manifest.items() if k == "initial_population_source"})
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
    ap.add_argument("--state", default=os.path.join(SERVER, "state"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="write turn 0")
    p.add_argument("--population", default=os.path.join(SERVER, "world_population.json"),
                   help="population file to seed turn 0 from; use the one server.py shows")
    p = sub.add_parser("step", help="advance the current turn")
    p.add_argument("--turns", type=int, default=1)
    sub.add_parser("verify", help="check the current turn's checksum")
    args = ap.parse_args()
    return {"init": init, "step": step, "verify": verify}[args.cmd](args) or 0


if __name__ == "__main__":
    sys.exit(main())
