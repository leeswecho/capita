"""Turn engine: creates and advances the game state that server.py displays.

    python engine/engine.py init [--population server/world_population_start.json] [--nations SEED] [--state state]
    python engine/engine.py step [--turns 1] [--state state]
    python engine/engine.py verify [--state state]

(paths shown from the repository root; the script works from any directory). By default it uses the
top-level state/ directory, which server.py also reads, and the population file in server/.

People belong to segments: a segment is the people directly under one leader, and it has a position
(a tile, or partway between two neighbouring tiles). A leader's following is their segment plus those of
every leader below them. The player is their nation's root leader. Leader types are in leaders.json.

`init` writes turn 0: every populated tile of the population file becomes one segment, standing on that
tile under its own leader (make_leaders()), with its people split across the 101 ages in a rough
hunter-gatherer age structure (starting_age_shares()); each age gets rough placeholder activity hours
shaped by age (starting_hours()). It also creates the nations (make_nations()): each nation's root
leader leads the segment on its capital, and the nation's other leaders report to them. Then the
starting visibility.

`step` advances one year per turn. Each segment lives the year on the tile it counts as being in
(cohort_state.segment_tile()). Segments don't move yet. For now the rules are:
- Production, from the year's people and activity hours before they age (produce()): foraging turns
  the year's subsistence hours into food (kcal), saturating at what the tile's land cover, landform,
  climate, water and rivers can sustain (foraging_max()). Segments on the same tile share that limit,
  in proportion to their hours. The rules and their values are in economy.json, read on every step.
- Consumption (consume()): the same people consume each product at a rate per person per day that
  depends on age (economy.json). If a segment holds less than its people need (its stock plus the
  year's production), everyone's consumption is cut by the same share. The need and what was consumed
  are stored, so the shortfall can drive mortality.
- Decay (end_of_year()): whatever each segment holds after consumption loses that product's decay
  rate, and the rest is carried to the next turn as stock.
  Base rates come from economy.json; economy_modifiers() is where future products and infrastructure
  will change them per segment. Food decays completely for now, so none carries over.
- Demographics, after production and consumption, so they can respond to the year's food: births
  (births()) from a rate per person per year by age (demography.json, read on every step), joining
  their parents' segment. The rate is a placeholder; birth_rates() is where a decision model will
  choose instead. Then deaths (mortality()): several causes, each a yearly hazard by segment and age,
  which add (death_hazards()): starvation from the food shortfall, predation and accidents from hours
  of work, cancer and other age-related causes from age, infection from the terrain of the segment's
  tile, and child mortality from age and the childcare shortfall. mortality_modifiers() is where future
  infrastructure will reduce them. Fractions of people die; the deaths by segment and cause are stored.
- Ageing: each cohort moves up one year and the 99-year-olds join the open-ended 100-and-over group.
  The year's surviving newborns become age 0. Activity hours belong to the age, not the cohort: people
  take on the hours of their new age (age_one_year()).
- Ownership, recomputed every turn (update_ownership()): first come, first served, claimed only by
  segments that aren't moving. A nation owns a tile from the turn it is the first to have such a
  segment with people there, and loses it when it has none there any more.
- Visibility, recomputed every turn (active_visibility()): a nation actively sees every tile where one
  of its segments has people, plus the 8 tiles around each. Tiles it has ever seen stay explored
  (passive visibility).

Each step reads the current turn, computes the next one in memory and writes it as a new turn directory
(cohort_state.py), so the web server can keep reading the old turn until the new one is complete. The
engine is the only writer; it can run while the server is running.

The engine also records each turn's phase (cohort_state.write_phase()): a turn is in "orders" while it
accepts the players' commands, and in "resolving" while the engine computes the next turn from it.
`init` opens turn 0 for orders; `step` marks the current turn resolving, writes the next turn, then
opens that one for orders. If a step fails, the turn it was resolving goes back to orders.
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
from convert_map import read_map  # noqa: E402

KEEP_TURNS = 3
RULES = os.path.join(HERE, "economy.json")
DEMOGRAPHY = os.path.join(HERE, "demography.json")
LEADERS = os.path.join(HERE, "leaders.json")
MAP = os.path.join(SERVER, "world_map.json")

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
    nations = [{"id": i, "code": c, "capital": capitals[c], "leader": None, "founded_turn": 0, "alive": True}
               for c, i in ids.items()]
    return nations, tile_nation


def load_leader_rules(path=LEADERS):
    """leaders.json: {"types": {name: {...}}, "start_type": name}."""
    with open(path, encoding="utf-8") as f:
        rules = json.load(f)
    if rules["start_type"] not in rules["types"]:
        sys.exit(f"{os.path.basename(path)}: start_type {rules['start_type']!r} isn't one of the types")
    return rules


def leader_types(rules):
    return list(rules["types"])


def leaders_extra(rules):
    return {"leader_rules_source": os.path.relpath(LEADERS, REPO).replace(os.sep, "/"), "leader_rules": rules}


def make_leaders(tiles, nation, people, nations, start_type):
    """Turn-0 leaders: one per segment (row), with ids 1, 2, ... in row order. Each nation's root leader
    (the player) leads the segment on its capital, or its most populous one if nobody lives on the
    capital; the nation's other leaders report to the root. Segments with no nation get leaders with
    no parent. Sets each nation record's "leader". Returns the leader records."""
    people_per_row = people.sum(axis=1)
    root_row = {}
    for n in nations:
        rows = np.flatnonzero(nation == n["id"])
        if not len(rows):
            continue
        at_capital = rows[tiles[rows] == n["capital"]]
        root_row[n["id"]] = int(at_capital[0] if len(at_capital) else rows[np.argmax(people_per_row[rows])])
        n["leader"] = root_row[n["id"]] + 1
    codes = {n["id"]: n["code"] for n in nations}
    title = start_type.capitalize()
    leaders = []
    for r, (t, k) in enumerate(zip(tiles.tolist(), nation.tolist())):
        root = root_row.get(k)
        place = codes[k] if root == r else grid.tile_id(*grid.corner(*divmod(t, grid.WIDTH)))
        leaders.append({"id": r + 1, "name": f"{title} of {place}", "type": start_type, "nation": k,
                        "parent": 0 if root is None or root == r else root + 1, "founded_turn": 0})
    return leaders


def update_ownership(prev_owner, tiles, nation, people, stationary):
    """Owner of every tile this turn, uint16 [grid.COUNT] (0 = nobody): first come, first served.

    Only segments with people that aren't moving (stationary [S]) claim tiles. A tile's owner keeps it
    while one of the owner's claiming segments is still there. A tile with no owner, or whose owner has
    none there any more, goes to the nation with the most people in claiming segments there (the lowest
    nation id on a tie), or to nobody. Segments with no nation (0) never claim.
    """
    live = (people > 0).any(axis=1) & stationary & (nation > 0)
    t, k = tiles[live].astype(np.int64), nation[live].astype(np.int64)
    pairs, inv = np.unique(t * 65536 + k, return_inverse=True)        # (tile, nation) pairs present, sorted
    weight = np.bincount(inv, weights=people[live].sum(axis=1)) if len(pairs) else np.zeros(0)
    pt, pk = np.divmod(pairs, 65536)
    keep = np.zeros(grid.COUNT, dtype=bool)
    keep[pt[prev_owner[pt] == pk]] = True                             # the owner is still there
    order = np.lexsort((pk, -weight, pt))                             # by tile, then most people, then lowest id
    first = order[np.r_[True, pt[order][1:] != pt[order][:-1]]] if len(order) else order
    present = np.zeros(grid.COUNT, dtype=np.uint16)
    present[pt[first]] = pk[first]
    return np.where(keep, prev_owner, present).astype(np.uint16)


def active_visibility(tiles, nation, people, n_rows):
    """Packed bits [n_rows, BITS_BYTES]: each nation sees the tiles where its segments have people and
    the 8 tiles around each (east-west wraps; nothing past the poles)."""
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


def load_rules(path=RULES):
    """economy.json: {"products": {name: {"unit": ..., "decay": ...}}, "consumption": {name: {...}}, "foraging": {...}}."""
    with open(path, encoding="utf-8") as f:
        rules = json.load(f)
    name = os.path.basename(path)
    if rules["foraging"]["product"] not in rules["products"]:
        sys.exit(f"{name}: foraging makes {rules['foraging']['product']!r}, which isn't a product")
    for product, c in rules["consumption"].items():
        if product == "about":
            continue
        if product not in rules["products"]:
            sys.exit(f"{name}: consumption lists {product!r}, which isn't a product")
        ages = [a for a, _ in c["per_person_per_day_by_age"]]
        if ages != sorted(set(ages)) or any(v < 0 for _, v in c["per_person_per_day_by_age"]):
            sys.exit(f"{name}: {product} consumption needs increasing ages and amounts of at least 0")
    return rules


def product_list(rules):
    return [{"name": name, "unit": p["unit"]} for name, p in rules["products"].items()]


def foraging_max(tiles, world, rules):
    """The most food each row's tile can sustainably yield in a year, float64 [S] (kcal).

    Land: its area x the cover-weighted yield per km2 x the landform factor (plains, hills, mountains)
    x a climate factor that falls with latitude. Water: its area x a near-shore fishing yield. The total
    is raised by river_factor when a navigable river runs through the tile. Cover shares cover land and
    frozen water (world_map_schema.md), so ice on frozen sea simply yields nothing.
    """
    f = rules["foraging"]
    idx = tiles.astype(np.int64)
    terrain = {t: np.asarray(c, dtype=np.float64)[idx] / 100 for t, c in zip(world.terrain_types, world.terrain)}
    cover = {t: np.asarray(c, dtype=np.float64)[idx] / 100 for t, c in zip(world.cover_types, world.cover)}
    rows = idx // grid.WIDTH
    area = grid.area_km2(rows)
    landform = sum(terrain[t] * k for t, k in f["landform_factor"].items())   # land share x landform factor
    cover_yield = sum(cover[t] * f["yield_per_km2"][t] for t in world.cover_types)
    c = f["climate"]
    lat = np.abs(90 - (rows + 0.5) * grid.TILE)   # tile centre
    cold = np.clip((lat - c["full_to_latitude"]) / (c["min_at_latitude"] - c["full_to_latitude"]), 0, 1)
    climate = 1 - (1 - c["min_factor"]) * cold
    on_land = area * landform * cover_yield * climate
    on_water = area * terrain["water"] * f["water_yield_per_km2"]
    river = np.array([grid.tile_id(*grid.corner(*divmod(int(i), grid.WIDTH))) in world.rivers_at for i in idx], dtype=bool)
    return (on_land + on_water) * np.where(river, f["river_factor"], 1.0)


def production_max(tiles, products, rules, world):
    """The most each row's tile can produce of each product in a year, float64 [S, P] in products order."""
    m = np.zeros((len(tiles), len(products)))
    m[:, products.index(rules["foraging"]["product"])] = foraging_max(tiles, world, rules)
    return m


def produce(people, hours, activities, tiles, prod_max, products, rules):
    """The year's production and the labor behind it, each float64 [S, P] in products order.

    Foraging: labor H is person-hours a year of the foraging activity (people x hours per day x 365,
    summed over ages). A tile yields max x (1 - exp(-r H / max)) for the labor of every segment on it,
    about r kcal per hour while labor is light, levelling off at the tile's maximum; each segment gets
    a share in proportion to its own labor. (A sum over ages and exp() could differ in the last bit
    between NumPy builds or CPUs; checksums compare replays on the same machine.)
    """
    f = rules["foraging"]
    if f["activity"] not in activities:
        sys.exit(f"foraging uses the activity {f['activity']!r}, which the state doesn't have: {activities}")
    col = products.index(f["product"])
    labor_h = (people * hours[:, :, activities.index(f["activity"])]).sum(axis=1) * 365
    _, inv = np.unique(tiles, return_inverse=True)
    tile_labor = np.bincount(inv, weights=labor_h)[inv] if len(tiles) else labor_h   # all labor on each row's tile
    m = prod_max[:, col]
    ratio = np.divide(f["return_per_hour"] * tile_labor, m, out=np.zeros_like(m), where=m > 0)
    share = np.divide(labor_h, tile_labor, out=np.zeros_like(m), where=tile_labor > 0)
    production, labor = np.zeros_like(prod_max), np.zeros_like(prod_max)
    production[:, col] = m * -np.expm1(-ratio) * share
    labor[:, col] = labor_h
    return production, labor


def economy_modifiers(n, rules):
    """Each segment's decay rate for each product, float32 [S, P] in products order.

    For now every segment gets the base rate from economy.json. This is where products and
    infrastructure will change it, segment by segment.
    """
    base = np.array([p["decay"] for p in rules["products"].values()], dtype=np.float32)
    return np.tile(base, (n, 1))


def consumption_rates(products, rules):
    """Amount of each product a person of each age consumes a day, float64 [AGES, P] in products order:
    economy.json's points, filled in linearly between them (0 for products that aren't consumed)."""
    rates = np.zeros((AGES, len(products)))
    for k, name in enumerate(products):
        c = rules["consumption"].get(name)
        if c:
            ages, amounts = zip(*c["per_person_per_day_by_age"])
            rates[:, k] = np.interp(np.arange(AGES), ages, amounts)
    return rates


def consume(people, held, rates):
    """The year's need and consumption of each product, each float64 [S, P].

    need = people x the age's daily rate x 365, summed over ages. Consumption is the need, or, if the
    segment holds less (held: its stock plus the year's production), all it holds: everyone's consumption
    is cut by the same share, so each age eats rate x consumed / need.
    """
    need = (people[:, :, None] * rates[None, :, :]).sum(axis=1) * 365   # not people @ rates: BLAS may reorder sums
    return need, np.minimum(need, held)


def end_of_year(held, consumed, decay):
    """What each segment holds of each product at the start of the next turn, float64 [S, P]: what was held
    (last stock plus the year's production) less what was consumed, less its decay."""
    return np.maximum(held - consumed, 0) * (1 - decay.astype(np.float64))


def economy_extra(rules):
    return {"economy_rules_source": os.path.relpath(RULES, REPO).replace(os.sep, "/"), "economy_rules": rules}


def load_demography(path=DEMOGRAPHY):
    """demography.json: {"births": {"per_person_per_year_by_age": [[age, rate], ...]}, "deaths": {cause: {...}}}."""
    with open(path, encoding="utf-8") as f:
        demo = json.load(f)
    d = demo["deaths"]
    tables = [("births", demo["births"]["per_person_per_year_by_age"]),
              ("child_mortality care_need_by_age", d["child_mortality"]["care_need_by_age"]),
              ("starvation hazard_by_share_of_need_met", d["starvation"]["hazard_by_share_of_need_met"])]
    for label, pts in tables:
        xs = [x for x, _ in pts]
        if xs != sorted(set(xs)) or any(v < 0 for _, v in pts):
            sys.exit(f"{os.path.basename(path)}: {label} needs increasing first values and second values of at least 0")
    if death_causes(demo) != CAUSES:
        sys.exit(f"{os.path.basename(path)}: deaths must list {CAUSES} in that order, got {death_causes(demo)}")
    return demo


# The causes of death the engine models, in demography.json and deaths.npy column order. Adding one means
# adding its rules and a branch in death_hazards().
CAUSES = ["starvation", "predation_and_accidents", "cancer", "other_age_related", "infection", "child_mortality"]


def death_causes(demo):
    return [k for k in demo["deaths"] if k != "about"]


def birth_rates(people, demo):
    """Births per person per year at each age, float64 [S, AGES], for each segment's people.

    Placeholder: the same rate by age everywhere (demography.json), averaged over men and women and
    counting each birth half to each parent. This is where a decision model (a trained neural net,
    say) will choose instead, from whatever it is given about each segment: food, labor, age structure...
    """
    ages, rates = zip(*demo["births"]["per_person_per_year_by_age"])
    return np.broadcast_to(np.interp(np.arange(AGES), ages, rates), people.shape)


def births(people, rates):
    """Children born in each segment during the year, float64 [S]: people x rate, summed over ages.
    They join that segment and become age 0 at the next turn."""
    return (people * rates).sum(axis=1)


def interp_table(x, pts):
    xs, ys = zip(*pts)
    return np.interp(x, xs, ys)


def infection_factor(tiles, world, spec):
    """Each row's terrain multiplier for infection, float64 [S]: a climate factor (1 in the tropics,
    falling toward the poles) x (1 + wetlands_extra x the tile's wetland share)."""
    idx = tiles.astype(np.int64)
    lat = np.abs(90 - (idx // grid.WIDTH + 0.5) * grid.TILE)
    c = spec["climate"]
    cold = np.clip((lat - c["full_to_latitude"]) / (c["min_at_latitude"] - c["full_to_latitude"]), 0, 1)
    wet = np.asarray(world.cover[world.cover_types.index("wetlands")], dtype=np.float64)[idx] / 100
    return (1 - (1 - c["min_factor"]) * cold) * (1 + spec["wetlands_extra"] * wet)


def childcare_shortfall(people, hours, activities, spec):
    """Each segment's childcare shortfall, float64 [S] in 0..1: 1 - (hours given / hours needed), 0 when
    enough is given. Given: everyone's hours of the care activity; needed: each child's hours by age."""
    given = (people * hours[:, :, activities.index(spec["care_activity"])]).sum(axis=1)
    needed = (people * interp_table(np.arange(AGES), spec["care_need_by_age"])).sum(axis=1)
    ratio = np.divide(given, needed, out=np.ones_like(given), where=needed > 0)
    return np.clip(1 - ratio, 0, 1)


def death_hazards(cause, x, work_hours, food_met, care_short, infection, demo):
    """One cause's yearly hazard, broadcastable to [S, K], for exact ages x [K].

    work_hours: [S, K] hours a day of the predation activity at those ages; food_met: [S] share of food
    need met in the year; care_short: [S] childcare shortfall; infection: [S] terrain factor.
    """
    d = demo["deaths"][cause]
    x = x[None, :]
    if cause == "starvation":
        vulnerable = np.where((x < d["vulnerable_under_age"]) | (x >= d["vulnerable_over_age"]), d["vulnerable_factor"], 1.0)
        return interp_table(food_met, d["hazard_by_share_of_need_met"])[:, None] * vulnerable
    if cause == "predation_and_accidents":
        return work_hours * 365 * d["hazard_per_hour"]
    if cause in ("cancer", "other_age_related"):
        return d["a"] * np.exp(d["b"] * x)
    if cause == "infection":
        return d["base"] * infection[:, None] + 0 * x
    if cause == "child_mortality":
        return d["a"] * np.exp(-d["b"] * x) * (1 + d["shortfall_factor"] * care_short[:, None])
    raise ValueError(f"no hazard for cause {cause!r}")


def mortality_modifiers(n, causes):
    """Multiplier on each segment's hazard for each cause, float64 [S, C]. All 1 for now; this is where
    future infrastructure will reduce causes segment by segment (medicine for infection, say)."""
    return np.ones((n, len(causes)))


def mortality(people, born, hours, activities, tiles, world, food_met, demo):
    """The year's deaths. Returns (survivors [S, AGES], surviving newborns [S], deaths by cause [S, C]).

    Each cohort lives the year at its mid-year age (a + 1 for the cohort aged a at the start of the
    turn, which turns a + 1 during it); children born during the year live half of it, at age 0.25.
    The causes' hazards h_c add up to H; a share 1 - exp(-H) dies, split among the causes as h_c / H.
    Pure elementwise arithmetic and sums over ages, so results don't depend on BLAS.
    """
    d = demo["deaths"]
    pred = d["predation_and_accidents"]["activity"]
    work = hours[:, :, activities.index(pred)]
    care_short = childcare_shortfall(people, hours, activities, d["child_mortality"])
    infection = infection_factor(tiles, world, d["infection"])
    mods = mortality_modifiers(len(tiles), CAUSES)

    def die(count, x, work_h, exposure):
        hz = [death_hazards(c, x, work_h, food_met, care_short, infection, demo) * mods[:, k:k + 1] * exposure
              for k, c in enumerate(CAUSES)]
        total = np.zeros(count.shape)
        for h in hz:
            total = total + h
        dead = count * -np.expm1(-total)
        share = lambda h: np.divide(h, total, out=np.zeros(total.shape), where=total > 0)
        by_cause = np.stack([(dead * share(h)).sum(axis=1) for h in hz], axis=1)
        return count - dead, by_cause

    survivors, deaths = die(people, np.arange(AGES) + 1.0, work, 1.0)
    newborns, newborn_deaths = die(born[:, None], np.array([0.25]), work[:, :1], 0.5)
    return survivors, newborns[:, 0], deaths + newborn_deaths


def demography_extra(demo):
    return {"demography_rules_source": os.path.relpath(DEMOGRAPHY, REPO).replace(os.sep, "/"), "demography_rules": demo}


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

    # One segment per populated tile, in grid order, standing on it.
    tiles = np.flatnonzero(counts > 0).astype(cs.DTYPES["at"])
    people = counts[tiles, None] * starting_age_shares()[None, :]
    hours = np.broadcast_to(starting_hours(), (len(tiles), AGES, len(ACTIVITIES))).copy()
    nation = tile_nation[tiles]
    lrules = load_leader_rules()
    leaders = make_leaders(tiles, nation, people, nations, lrules["start_type"])
    stationary = np.ones(len(tiles), dtype=bool)
    tile_owner = update_ownership(np.zeros(grid.COUNT, dtype=np.uint16), tiles, nation, people, stationary)
    visible = active_visibility(tiles, nation, people, len(nations) + 1)
    rules = load_rules()
    products = product_list(rules)
    zeros = np.zeros((len(tiles), len(products)))   # turn 0 follows no year: nothing made or held yet
    economy = {"production": zeros, "labor": zeros.copy(), "stock": zeros.copy(),
               "need": zeros.copy(), "consumed": zeros.copy(),
               "production_max": production_max(tiles, [p["name"] for p in products], rules, read_map(MAP)),
               "decay": economy_modifiers(len(tiles), rules)}
    demo = load_demography()
    state = cs.State(leaders, tiles, tiles.copy(), np.zeros(len(tiles)), people, hours, ACTIVITIES, tile_owner,
                     nations, visible, visible.copy(), products, economy, CAUSES, np.zeros((len(tiles), len(CAUSES))),
                     leader_types(lrules), len(leaders) + 1)
    extra = {"initial_population_source": os.path.basename(args.population),
             "initial_nations_source": os.path.basename(seed_path), **economy_extra(rules),
             **demography_extra(demo), **leaders_extra(lrules)}
    m = cs.write_turn(args.state, 0, state, extra=extra)
    cs.write_phase(args.state, 0, cs.ORDERS)
    print(f"turn 0: {m['segments']} segments on {m['populated_tiles']} tiles, {m['total_people']:,.1f} people, "
          f"{m['nations']} nations -> "
          f"{os.path.join(args.state, cs.turn_dir_name(0))}")


def age_one_year(people):
    """Next year's people: every cohort moves up a year, and the 99-year-olds join the 100-and-over
    group. Age 0 is left for the year's newborns. Pure elementwise arithmetic, so results don't depend
    on the CPU or on how NumPy orders a sum, which keeps replays deterministic.

    Activity hours belong to the age, not the cohort, so they stay where they are: people take on the
    hours of their new age. (Moving them with the cohort would leave everyone born in the game with a
    newborn's hours for life, since nothing yet decides how a cohort's activities change as it grows.)
    """
    new_people = np.zeros_like(people)
    new_people[:, 1:AGES - 1] = people[:, 0:AGES - 2]
    new_people[:, AGES - 1] = people[:, AGES - 2] + people[:, AGES - 1]
    return new_people


def step(args):
    for _ in range(args.turns):
        ptr = cs.read_current(args.state)
        if ptr is None:
            sys.exit(f"no state in {args.state}; run: python engine/engine.py init")
        cs.write_phase(args.state, ptr["turn"], cs.RESOLVING)   # close this turn's commands before reading it
        try:
            cur = cs.Turn(args.state, ptr["dir"], mmap=False)
            rules = load_rules()
            products = product_list(rules)
            if products != cur.products:
                sys.exit(f"economy.json's products {[p['name'] for p in products]} don't match the state's "
                         f"{[p['name'] for p in cur.products]}; start a new state to change them")
            names = [p["name"] for p in products]
            lrules = load_leader_rules()
            if leader_types(lrules) != cur.leader_types:
                sys.exit(f"leaders.json's types {leader_types(lrules)} don't match the state's {cur.leader_types}; "
                         "start a new state to change them")
            # Segments don't move yet: each lives the year on the tile it counts as being in.
            tiles = cur.tile
            # Production: the year that is ending, so this turn's people and hours, before they age.
            world = read_map(MAP)
            prod_max = production_max(tiles, names, rules, world)
            production, labor = produce(cur.people, cur.hours, cur.activities, tiles, prod_max, names, rules)
            held = cur.stock + production
            need, consumed = consume(cur.people, held, consumption_rates(names, rules))
            decay = economy_modifiers(len(tiles), rules)
            economy = {"production": production, "labor": labor, "production_max": prod_max, "need": need,
                       "consumed": consumed, "decay": decay, "stock": end_of_year(held, consumed, decay)}

            # Demographics: the year's births and deaths, among the people who lived it.
            demo = load_demography()
            born = births(cur.people, birth_rates(cur.people, demo))
            fc = names.index(demo["deaths"]["starvation"]["product"])
            food_met = np.divide(consumed[:, fc], need[:, fc], out=np.ones(len(tiles)), where=need[:, fc] > 0)
            survivors, born_alive, deaths = mortality(cur.people, born, cur.hours, cur.activities, tiles, world,
                                                      food_met, demo)

            people, hours = age_one_year(survivors), cur.hours
            people[:, 0] = born_alive
            tile_owner = update_ownership(cur.owner, tiles, cur.nation, people, cur.at == cur.toward)
            visible = active_visibility(tiles, cur.nation, people, len(cur.nations) + 1)
            state = cs.State(cur.leaders, cur.at, cur.toward, cur.progress, people, hours, cur.activities,
                             tile_owner, cur.nations, visible, cur.explored | visible, products, economy, CAUSES,
                             deaths, cur.leader_types, cur.next_leader_id)
            extra = {k: v for k, v in cur.manifest.items() if k.startswith("initial_")}
            m = cs.write_turn(args.state, cur.turn + 1, state,
                              extra={**extra, **economy_extra(rules), **demography_extra(demo),
                                     **leaders_extra(lrules)})
        except BaseException:
            cs.write_phase(args.state, ptr["turn"], cs.ORDERS)   # no new turn was written; reopen this one
            raise
        cs.write_phase(args.state, m["turn"], cs.ORDERS)
        del cur
        made = ", ".join(f"{production[:, i].sum():,.0f} {p['unit']} of {p['name']}" for i, p in enumerate(products))
        short = ", ".join(f"{p['name']} {consumed[:, i].sum() / need[:, i].sum():.1%} of need met "
                          f"({np.count_nonzero(consumed[:, i] < need[:, i] * (1 - 1e-9))} segments short)"
                          for i, p in enumerate(products) if need[:, i].sum() > 0)
        died = ", ".join(f"{c} {deaths[:, k].sum():,.1f}" for k, c in enumerate(CAUSES))
        print(f"turn {m['turn']}: {m['total_people']:,.1f} people ({born.sum():,.1f} born, {deaths.sum():,.1f} died: "
              f"{died}), made {made}; {short}; "
              f"checksum {m['checksum']['value'][:12]}")
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
