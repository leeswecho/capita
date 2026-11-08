"""The decision model: what the people of each segment choose to do with their lives, year by year.

Every year of a turn, after movement and before production, the engine describes each segment's
situation (engine.observe(), a Situation) and asks decide() what its people choose (a Decisions). The
engine then carries the choices out by its own rules: allocate() works the hours the year's food needs,
up to the hours the people chose to give their livelihood, and births() applies the chosen birth rates.
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

The present decide() is a placeholder: fixed schedules by age for each livelihood (starting_hours(),
farming_hours()) and demography.json's birth rates by age, more for settled segments. It ignores most
of what it is told.
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
    farm_max: np.ndarray     # float64 [S]: ... and by farming (each shared by every segment there doing it)
    tile_people: np.ndarray  # float64 [S]: people on the segment's tile, of every segment and nation


@dataclass
class Decisions:
    """What each segment's people choose for the year."""
    schedule: np.ndarray     # float64 [S, AGES, A]: hours a day they will give each activity. Their livelihood's
                             # column is the most they will work for food: allocate() takes only what the
                             # year's food needs and gives the rest to "other".
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
    These are the most hours people will work for food; allocate() takes only what their food needs."""
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

    Placeholder: the schedule of their livelihood (SCHEDULES), the same for every segment that lives by
    it, and the same birth rate by age everywhere (demo: demography.json's "births"), x the settled
    birth_factor for settled segments.
    """
    schedule = np.zeros((len(s.people), AGES, len(ACTIVITIES)))
    for k, make in SCHEDULES.items():
        schedule[s.livelihood == ACTIVITIES.index(k)] = make()
    ages, rates = zip(*demo["births"]["per_person_per_year_by_age"])
    factor = np.where(s.settled, demo["settled"]["birth_factor"], 1.0)
    birth_rate = np.interp(np.arange(AGES), ages, rates)[None, :] * factor[:, None]
    return Decisions(schedule, birth_rate)


def check(d: Decisions, s: Situation):
    """Raise ValueError unless d is usable for s: the right shapes, finite, no negative hours or rates, and
    no age's hours adding up to more than 24 a day."""
    n = len(s.people)
    for name, a, shape in (("schedule", d.schedule, (n, AGES, len(ACTIVITIES))), ("birth_rate", d.birth_rate, (n, AGES))):
        if a.shape != shape:
            raise ValueError(f"decide() gave {name} of shape {list(a.shape)}, not {list(shape)}")
        if not np.isfinite(a).all() or (a < 0).any():
            raise ValueError(f"decide() gave {name} values that are negative or not finite")
    if (d.schedule.sum(axis=2) > 24 + 1e-6).any():
        raise ValueError("decide() gave a schedule of more than 24 hours a day")
