"""The decision model: what the people of each segment choose to do with their lives, year by year.

Every year of a turn, after movement and before production, the engine describes each segment's
situation (engine.observe(), a Situation) and asks decide() what its people choose (a Decisions). The
engine then carries the choices out by its own rules: the people spend the hours they chose, produce()
turns their hours of work into food on their tile, and births() applies the chosen birth rates. What
they make may fall short of what they need, or exceed it; either way each segment keeps what it makes.
decide() sees nothing of the state but its Situation and keeps no memory between calls, so the same
situation always gets the same choices, and it can be replaced (by a trained network, say) without
touching the rest of the engine. check() makes sure whatever it returns is usable.

Rates are per year, and hours are per person per day averaged over the year, so the choices don't
depend on how many years a turn lasts (time.json). Every array has one row per segment (S) and, where
it has them, one column per age (AGES, the last being 100 and over) and one per activity (ACTIVITIES).

What decide() is told is only what the engine can say so far. Still to come: the neighbouring tiles,
the segment's nation, what that nation knows of the world, and its technology. What it chooses is only
how people spend their hours and how many children they have. Livelihood isn't a choice yet (the
leader's type sets it), nor is migration: segments move only when their leaders are ordered to.
Migration without a leader will become possible only once the king's policies, and the technology
and infrastructure they need, allow it; from then on Situation will say where a segment's people may
go, and Decisions will hold what share of each age chooses to go there each year.

Segments don't feed each other: each aims at its own food target, which for now is its own need. A
leader's command (to produce for everyone on the tile, say) will raise the target; that is where food
for settling parties and other segments will come from.

The present decide() is a placeholder: people work the hours that should meet their food target on
their tile's curve, up to fixed schedules by age for each livelihood (starting_hours(),
farming_hours()), and have children at demography.json's birth rates by age, more for settled
segments. It ignores the rest of what it is told.
"""
from dataclasses import dataclass

import numpy as np

from cohort_state import AGES

# Placeholder activities. Replace with the real list; the state files carry the names, so readers adapt.
# A segment feeds itself by one of them, its livelihood, which its leader type's "activity" sets
# (leaders.json): subsistence (hunting and gathering) or farming (farming and herding).
ACTIVITIES = ["sleep", "subsistence", "farming", "childcare", "other"]


@dataclass
class Situation:
    """What each segment's people know of their situation at the start of a year."""
    year: int                # years since turn 0 at the start of this year
    people: np.ndarray       # float64 [S, AGES]: people by age
    livelihood: np.ndarray   # int64 [S]: column in ACTIVITIES of the activity the segment feeds itself by
    settled: np.ndarray      # bool [S]: the segment is settled (stays put and farms)
    moving: np.ndarray       # bool [S]: the segment is partway between two tiles
    food_met: np.ndarray     # float64 [S]: share of the year before's food need that was met (1 if none)
    hours: np.ndarray        # float32 [S, AGES, A]: the year before's hours a day on each activity
    forage_max: np.ndarray   # float64 [S]: the most food (kcal) the segment's tile can yield in a year by foraging
    farm_max: np.ndarray     # float64 [S]: ... and by farming (each shared by every segment there doing it, in
                             # proportion to their hours; foragers get only the land the farms leave)
    tile_people: np.ndarray  # float64 [S]: people on the segment's tile, of every segment and nation
    food_target: np.ndarray  # float64 [S]: food (kcal) the segment aims to make this year: what its people need
                             # less what it holds (a leader's command will add to it)
    return_per_hour: np.ndarray  # float64 [S]: food an hour of work at its livelihood yields while labor is light.
                             # H person-hours a year on a tile yield max x (1 - exp(-return_per_hour x H / max)).


@dataclass
class Decisions:
    """What each segment's people choose for the year."""
    hours: np.ndarray        # float64 [S, AGES, A]: hours a day each age spends on each activity this year. Their
                             # livelihood's column is their work for food, which produce() turns into food.
    birth_rate: np.ndarray   # float64 [S, AGES]: children born per person per year (each birth counted half to
                             # each parent, so averaged over men and women)


def _rise(x):   # smooth 0 -> 1 step
    return 1 / (1 + np.exp(-x))


def _schedule(work_activity, work):
    """Hours per person per day for each age, float32 [AGES, len(ACTIVITIES)], with `work` [AGES] hours of
    `work_activity` and the shapes every way of life shares: babies sleep about 14 h, falling to about
    7.4 h by 20 and rising slowly to 8.5 h at 100; childcare peaks around 30 (parents) with a smaller
    bump around 60 (grandparents); "other" is whatever is left of the 24 hours."""
    a = np.arange(AGES, dtype=np.float64)
    cols = {"sleep": 7.0 + 7.0 * np.exp(-a / 5) + 0.015 * a,
            "childcare": _rise((a - 11) / 1.5) * (3.0 * np.exp(-((a - 30) / 11) ** 2) + 0.8 * np.exp(-((a - 62) / 14) ** 2))}
    cols[work_activity] = work
    hours = np.stack([cols.get(k, np.zeros(AGES)) for k in ACTIVITIES], axis=1)
    hours[:, ACTIVITIES.index("other")] = 24 - hours.sum(axis=1)
    assert (hours >= 0).all()
    return hours.astype(np.float32)


def starting_hours():
    """Hours per person per day for each age at turn 0, as float32 [AGES, len(ACTIVITIES)]: the foragers'
    schedule. Rough, smooth shapes (not data): subsistence work starts around age 10, reaches 8.5 h in the
    20s and eases to about 5.5 h by 100 (_schedule() has the rest). Every age gets slightly different values.
    These are the most hours people will work for food; decide() takes only what their target needs."""
    a = np.arange(AGES, dtype=np.float64)
    return _schedule("subsistence", 8.5 * _rise((a - 14) / 3) * (1 - 0.35 * np.clip((a - 50) / 50, 0, 1) ** 2))


def farming_hours():
    """The farmers' schedule, float32 [AGES, len(ACTIVITIES)]: rough shapes like starting_hours()'s, but
    farming and herding takes more of the day (about 9.5 h for adults), children start helping earlier
    (from about 7, minding animals, weeding and scaring birds) and the old keep working longer."""
    a = np.arange(AGES, dtype=np.float64)
    return _schedule("farming", 9.5 * _rise((a - 10) / 2.2) * (1 - 0.3 * np.clip((a - 55) / 45, 0, 1) ** 2))


# Each livelihood the placeholder knows, and the schedule of the people who live by it.
SCHEDULES = {"subsistence": starting_hours, "farming": farming_hours}
LIVELIHOODS = list(SCHEDULES)


def decide(s: Situation, demo) -> Decisions:
    """What each segment's people choose for the year, from their situation alone.

    Placeholder. Hours: the schedule of their livelihood (SCHEDULES), with every age working the same
    share of its schedule's hours of work: the share that should make the food target, all of them if
    that isn't enough. They reckon on their share of the tile's curve in proportion to their people
    (exact when everyone on the tile lives the same way and needs the same per person), and invert it:
    H = -(max / rate) ln(1 - target / max). The hours they don't work go to "other". Births: the same
    rate by age everywhere (demo: demography.json's "births"), x the settled birth_factor for settled
    segments.
    """
    n = len(s.people)
    hours = np.zeros((n, AGES, len(ACTIVITIES)))
    for k, make in SCHEDULES.items():
        hours[s.livelihood == ACTIVITIES.index(k)] = make()
    work = hours[np.arange(n), :, s.livelihood]                                        # [S, AGES]
    top = np.where(s.livelihood == ACTIVITIES.index("farming"), s.farm_max, s.forage_max)
    mine = top * np.divide(s.people.sum(axis=1), s.tile_people, out=np.zeros(n), where=s.tile_people > 0)
    x = np.divide(s.food_target, mine, out=np.ones(n), where=mine > 0)                 # >= 1: out of reach
    reach = (x < 1) & (s.return_per_hour > 0)
    needed = np.divide(-mine * np.log1p(-np.where(reach, x, 0.0)), s.return_per_hour,
                       out=np.zeros(n), where=reach)                                   # person-hours a year
    most = (s.people * work).sum(axis=1) * 365
    share = np.where(reach, np.clip(np.divide(needed, most, out=np.ones(n), where=most > 0), 0, 1),
                     np.where(s.food_target > 0, 1.0, 0.0))
    hours[np.arange(n), :, s.livelihood] = work * share[:, None]
    hours[:, :, ACTIVITIES.index("other")] += work * (1 - share[:, None])
    ages, rates = zip(*demo["births"]["per_person_per_year_by_age"])
    factor = np.where(s.settled, demo["settled"]["birth_factor"], 1.0)
    birth_rate = np.interp(np.arange(AGES), ages, rates)[None, :] * factor[:, None]
    return Decisions(hours, birth_rate)


def check(d: Decisions, s: Situation):
    """Raise ValueError unless d is usable for s: the right shapes, finite, no negative hours or rates, and
    no age's hours adding up to more than 24 a day."""
    n = len(s.people)
    for name, a, shape in (("hours", d.hours, (n, AGES, len(ACTIVITIES))), ("birth_rate", d.birth_rate, (n, AGES))):
        if a.shape != shape:
            raise ValueError(f"decide() gave {name} of shape {list(a.shape)}, not {list(shape)}")
        if not np.isfinite(a).all() or (a < 0).any():
            raise ValueError(f"decide() gave {name} values that are negative or not finite")
    if (d.hours.sum(axis=2) > 24 + 1e-6).any():
        raise ValueError("decide() gave more than 24 hours a day")


# The trained decision model: a small network that learns, from the agent-based model's records
# (abm/abm.py, abm/train.py), what a segment's people do in a situation. It isn't used by the game
# yet. features() is what it is told, as numbers; it gives, for each segment, coefficients of
# AGE_CURVES smooth curves over age (so its choices change smoothly from one age to the next) for each
# of OUTPUTS; network_decide() runs it with nothing but NumPy.

OUTPUTS = ["sleep", "work", "childcare", "births"]   # work: hours of the segment's livelihood
AGE_CURVES = 16
BIRTH_SCALE = 0.1                                    # births per person per year, at a curve's value of 1


def age_curves():
    """The smooth curves the network's outputs are made of, float64 [AGE_CURVES, AGES]: bumps centred
    evenly from age 0 to the last age, each as wide as the gap between centres."""
    centres = np.linspace(0, AGES - 1, AGE_CURVES)
    width = centres[1] - centres[0]
    return np.exp(-0.5 * ((np.arange(AGES)[None, :] - centres[:, None]) / width) ** 2)


def features(s: Situation) -> np.ndarray:
    """What the network is told of each segment's situation, float32 [S, AGES x 3 + 9]: the share of its
    people at each age; the year before's hours a day of work (at its livelihood) and of childcare at each
    age, / 10; and, as numbers of about 1: how many people (log), whether they farm, are settled or are
    moving, the share of last year's food need met, the most their tile yields by their livelihood (log),
    their food target as a share of that, their share of the people on the tile, and an hour's yield
    while labor is light / 1000."""
    n = len(s.people)
    total = s.people.sum(axis=1)
    shares = np.divide(s.people, total[:, None], out=np.zeros(s.people.shape), where=total[:, None] > 0)
    farming = s.livelihood == ACTIVITIES.index("farming")
    top = np.where(farming, s.farm_max, s.forage_max)
    work = s.hours[np.arange(n), :, s.livelihood] / 10
    care = s.hours[:, :, ACTIVITIES.index("childcare")] / 10
    scalars = np.stack([np.log1p(total) / 10, farming, s.settled, s.moving, s.food_met, np.log1p(top) / 25,
                        np.clip(np.divide(s.food_target, top, out=np.zeros(n), where=top > 0), 0, 5),
                        np.clip(np.divide(total, s.tile_people, out=np.zeros(n), where=s.tile_people > 0), 0, 1),
                        s.return_per_hour / 1000], axis=1)
    return np.concatenate([shares, work, care, scalars], axis=1).astype(np.float32)


def load_network(path):
    """A trained network (abm/train.py's file): {"mean", "std" (of the features it was trained on), "W0",
    "b0", "W1", "b1", ...}."""
    with np.load(path) as f:
        return {k: f[k] for k in f.files}


def network_decide(s: Situation, net) -> Decisions:
    """What the trained network chooses for each segment, as decide() does. Layers are dense, with ReLU
    between them; each output is softplus(its coefficients . age_curves()). Hours of sleep, work and
    childcare are scaled down together if they come to more than 24, and the rest is "other"."""
    n = len(s.people)
    x = (features(s) - net["mean"]) / net["std"]
    layers = sum(1 for k in net if k.startswith("W"))
    for k in range(layers):
        x = x @ net[f"W{k}"] + net[f"b{k}"]
        if k < layers - 1:
            x = np.maximum(x, 0)
    v = np.logaddexp(0, x.reshape(n, len(OUTPUTS), AGE_CURVES) @ age_curves())    # softplus, [S, OUTPUTS, AGES]
    sleep, work, care = v[:, 0], v[:, 1], v[:, 2]
    scale = np.minimum(1, np.divide(24, sleep + work + care, out=np.ones((n, AGES)), where=sleep + work + care > 0))
    hours = np.zeros((n, AGES, len(ACTIVITIES)))
    hours[:, :, ACTIVITIES.index("sleep")] = sleep * scale
    hours[np.arange(n), :, s.livelihood] = work * scale
    hours[:, :, ACTIVITIES.index("childcare")] = care * scale
    hours[:, :, ACTIVITIES.index("other")] = np.maximum(24 - hours.sum(axis=2), 0)
    return Decisions(hours, v[:, 3] * BIRTH_SCALE)
