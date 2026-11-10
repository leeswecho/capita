"""Agent-based model (ABM) of one segment's people: individuals living month by month on one tile under
the game's own rules, to make training data for the decision model (engine/decisions.py).

    python abm/abm.py run [--scenarios 16] [--seed 1] [--workers N] [--no-sharing] [--out abm/data/run.npz]
    python abm/abm.py compare [abm/data/run.npz ...]

The game can't afford to simulate individuals, and a cohort doesn't behave like one person, so this
runs offline. Each scenario (a tile, a way of life and a starting population) is lived for
burn_in_years, until its households have formed and settled, and then recorded year by year as pairs
of what the decision model is told (a decisions.Situation, from the people's state at the start of the
year) and what the people did that year (a decisions.Decisions: hours a day by age and activity, and
births per person by age). A network trained on many such pairs can stand in for decide().

The physics are the game's, through the engine's own functions and rule files: each tile's yield
curves (economy.json), what people eat by age, the childcare children need, and the six causes of
death (demography.json), applied to each person at their own age. The ABM's own rules (abm/rules.json)
are biology and behaviour: sleep, how many hours a person can give at each age, how households decide
their work, marriage and fertility.

The interactions so far (version 1):
- Everyone on the tile works one yield curve: each month's food per hour depends on everyone's work,
  and households plan on what the tile yielded the month before.
- Households pool their food, their labour and their childcare: everyone gives the same share of their
  capacity, a nursing child is cared for by its mother first, and food security is the household's.
- Luck: each household's take from the tile varies from month to month, foraging far more than farming.
- Sharing between households: a common pot shared out by need, then food passed along kin ties and
  partnerships to households that are short; partnerships that aren't returned end.
- Marriage pairs people up and forms new households; births and deaths change who is in each, and so
  how many mouths each worker feeds.
Still to come: social learning (norms that spread by imitation), which will let households weigh
working against what sharing brings them.

`compare` puts the recorded situations to the present placeholder decide() and prints the two side by
side (and any other runs given, such as one with --no-sharing, beside them).
"""
import argparse
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "engine"))

import engine  # noqa: E402  (also puts server/ on the path)
import decisions  # noqa: E402
from decisions import ACTIVITIES  # noqa: E402
from cohort_state import AGES  # noqa: E402

RULES = os.path.join(HERE, "rules.json")
DATA = os.path.join(HERE, "data")
OUT = os.path.join(DATA, "run.npz")


def load_rules(path=RULES):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def table(x, pts):
    xs, ys = zip(*pts)
    return np.interp(x, xs, ys)


def pick_scenarios(n, seed, arules, erules, demo):
    """n scenarios at random (rules.json "scenarios"): each a dict of its tile, way of life, the tile's
    yield maxima and infection factor, its starting people and its own random seed."""
    sc = arules["scenarios"]
    world = engine.read_map(engine.MAP)
    tiles = np.arange(engine.grid.COUNT)
    forage = engine.foraging_max(tiles, world, erules)
    farm = engine.farming_max(tiles, world, erules)
    infection = engine.infection_factor(tiles, world, demo["deaths"]["infection"])
    kcal, least = sc["kcal_per_person_year"], sc["min_people_fed"] * sc["kcal_per_person_year"]
    ok = np.flatnonzero(forage >= least)
    rng = np.random.default_rng(seed)
    out = []
    for k in range(n):
        t = int(rng.choice(ok))
        farming = bool(rng.random() < sc["farming_share"] and farm[t] >= least)
        top = farm[t] if farming else forage[t]
        people = int(np.clip(top / kcal * rng.uniform(*sc["start_factor"]), *sc["start_people"]))
        out.append({"id": k, "tile": t, "farming": farming, "forage_max": float(forage[t]), "farm_max": float(farm[t]),
                    "infection": float(infection[t]), "people": people, "seed": int(rng.integers(2 ** 31))})
    return out


class Sim:
    """One scenario's people, one person a row (ids index the arrays and are never reused), in
    households (ids into the household arrays). Lives month by month; year() lives a year and reports it."""

    def __init__(self, sc, arules, erules, demo):
        self.rng = np.random.default_rng(sc["seed"])
        self.a, self.demo = arules, demo
        self.days = arules["days_per_month"]
        self.settled = sc["farming"]
        live = erules["farming" if sc["farming"] else "foraging"]
        self.livelihood = ACTIVITIES.index(live["activity"])
        self.forage_max, self.farm_max = sc["forage_max"], sc["farm_max"]
        self.top = (sc["farm_max"] if sc["farming"] else sc["forage_max"]) / 12   # a month's curve
        self.r = float(live["return_per_hour"])
        self.rho = self.r                                     # food an hour yielded last month
        self.work_hazard = demo["deaths"]["predation_and_accidents"]["hazard_per_hour"][live["activity"]]
        self.infection = sc["infection"]
        self.infection_factor = demo["settled"]["infection_factor"] if sc["farming"] else 1.0
        self.luck = arules["luck"]["sd_log"][live["activity"]]
        sh = arules["sharing"]
        on = sc.get("sharing", True)
        self.pot = sh["pot"][live["activity"]] if on else 0.0
        self.give = sh["give"][live["activity"]] if on else 0.0
        # Sharing links between households (directed: giver, receiver), rebuilt every year (_links()).
        self.ga = self.rb = np.zeros(0, dtype=np.int64)
        self.w, self.given = np.zeros(0), np.zeros(0)        # each link's weight, and food given along it this year
        self.pair = np.zeros(0, dtype=np.int64)              # its partnership (an index into partners), -1 for kin
        self.partners = np.zeros((0, 2), dtype=np.int64)     # partnerships: pairs of households
        names = [p["name"] for p in engine.product_list(erules)]
        self.eat_day = engine.consumption_rates(names, erules)[:, names.index(erules["foraging"]["product"])]
        ages = np.arange(AGES, dtype=np.float64)
        self.care_need = table(ages, demo["deaths"]["child_mortality"]["care_need_by_age"])
        s = arules["sleep"]
        self.sleep = s["base"] + s["infant"] * np.exp(-ages / s["infant_years"]) + s["per_year"] * ages
        c = arules["capacity"]
        self.cap = c["max_hours"] * table(ages, c["factor_by_age"])
        assert (self.sleep + self.cap <= 24).all()
        self.t = 0                                            # months lived
        self.prev_hours = np.zeros((AGES, len(ACTIVITIES)))   # the year before's, by age
        self.prev_met = 1.0                                   # the year before's share of food need met
        self._populate(sc["people"])

    # People and households.

    def _populate(self, n):
        rng = self.rng
        self.n, size = 0, max(64, 4 * n)
        self.age_m = np.zeros(size, dtype=np.int64)           # age in months
        self.female = np.zeros(size, dtype=bool)
        self.alive = np.zeros(size, dtype=bool)
        self.hh = np.zeros(size, dtype=np.int64)
        self.mother, self.father, self.partner = (np.full(size, -1, dtype=np.int64) for _ in range(3))
        self.preg = np.full(size, -1, dtype=np.int64)         # months pregnant, -1 if not
        self.nursing_until = np.zeros(size, dtype=np.int64)   # month her nursing ends
        years = rng.choice(AGES, size=n, p=engine.starting_age_shares())
        ids = self._add(n, years * 12 + rng.integers(12, size=n), rng.random(n) < 0.5, np.arange(n), -1, -1)
        age = years
        # Couples, each a household; then children to a mother of a plausible age; the rest alone.
        women = ids[self.female[ids] & (age >= 16) & (age < 45)]
        men = ids[~self.female[ids] & (age >= 18) & (age < 60)]
        rng.shuffle(women), rng.shuffle(men)
        k = min(len(men), int(0.85 * len(women)))
        w, m = women[:k], men[:k]
        self.partner[w], self.partner[m] = m, w
        self.hh[m] = self.hh[w]
        for c in ids[age < 15]:
            mothers = w[(age[w] - age[c] >= 15) & (age[w] - age[c] <= 40)]
            if len(mothers):
                mo = int(rng.choice(mothers))
                self.mother[c], self.father[c], self.hh[c] = mo, self.partner[mo], self.hh[mo]
            elif k:
                self.hh[c] = self.hh[int(rng.choice(w))]
        _, self.hh[:n] = np.unique(self.hh[:n], return_inverse=True)
        self.n_hh = int(self.hh[:n].max()) + 1
        self.store = np.zeros(self.n_hh)                      # food each household holds
        self.food = np.ones(self.n_hh)                        # moving average of the share of need met

    def _add(self, k, age_m, female, hh, mother, father):
        """k new people; returns their ids."""
        if self.n + k > len(self.alive):
            grow = max(self.n + k, 2 * len(self.alive)) - len(self.alive)
            for name in ("age_m", "female", "alive", "hh", "mother", "father", "partner", "preg", "nursing_until"):
                a = getattr(self, name)
                fill = -1 if name in ("mother", "father", "partner", "preg") else 0
                setattr(self, name, np.concatenate([a, np.full(grow, fill, dtype=a.dtype)]))
        ids = np.arange(self.n, self.n + k)
        self.age_m[ids], self.female[ids], self.alive[ids], self.hh[ids] = age_m, female, True, hh
        self.mother[ids], self.father[ids], self.partner[ids], self.preg[ids] = mother, father, -1, -1
        self.n += k
        return ids

    def _new_household(self, food):
        self.store, self.food = np.append(self.store, 0.0), np.append(self.food, food)
        self.n_hh += 1
        return self.n_hh - 1

    # A month.

    def month(self, acc):
        a, rng, days = self.a, self.rng, self.days
        i = np.flatnonzero(self.alive[:self.n])
        if not len(i):
            return
        b = np.minimum(self.age_m[i] // 12, AGES - 1)
        age = self.age_m[i] / 12
        hh, H = self.hh[i], self.n_hh
        per_hh = lambda w: np.bincount(hh, weights=w, minlength=H)

        # Work and childcare: everyone gives the same share of their capacity, enough for the month's
        # food (and a buffer) at last month's food per hour, and for the household's childcare.
        h = a["household"]
        need = per_hh(self.eat_day[b] * days)
        care_need = self.care_need[b]
        care_needed, caps = per_hh(care_need), per_hh(self.cap[b])
        work_wanted = np.maximum(need * (1 + h["buffer_months"]) - self.store, 0) / (days * self.rho)
        share = np.clip(np.divide(care_needed + work_wanted, caps, out=np.zeros(H), where=caps > 0), 0, 1)
        effort = self.cap[b] * share[hh]
        pos = np.full(self.n, -1)
        pos[i] = np.arange(len(i))
        nursing = self.age_m[i] < a["fertility"]["nursing_months"]
        mo = self.mother[i][nursing]
        mp = np.where(mo >= 0, pos[np.maximum(mo, 0)], -1)
        by_mother = np.bincount(mp[mp >= 0], weights=care_need[nursing][mp >= 0], minlength=len(i))
        care = np.minimum(by_mother, effort)
        left = np.maximum(care_needed - per_hh(care), 0)
        spare = np.where(age >= h["care_from_age"], effort - care, 0.0)
        spares = per_hh(spare)
        care = care + spare * np.clip(np.divide(left, spares, out=np.zeros(H), where=spares > 0), 0, 1)[hh]
        work = effort - care

        # Food: the tile's curve, shared in proportion to hours; each household eats from what it holds.
        labor = work.sum() * days
        made = self.top * -np.expm1(-self.r * labor / self.top) if self.top > 0 else 0.0
        self.rho = made / labor if labor > 0 else self.r
        hours = per_hh(work) * (rng.lognormal(-self.luck ** 2 / 2, self.luck, H) if self.luck > 0 else 1.0)
        made_h = made * hours / hours.sum() if hours.sum() > 0 else np.zeros(H)
        held, from_others = self._share(self.store + made_h, made_h, need)
        eaten = np.minimum(need, held)
        met = np.divide(eaten, need, out=np.ones(H), where=need > 0)
        self.store = (held - eaten) * (1 - h["spoil_per_month"])
        self.food += (met - self.food) / h["memory_months"]

        # Deaths: the engine's six causes, each person at their own age, a twelfth of a year's hazard.
        short = np.clip(1 - np.divide(per_hh(care), care_needed, out=np.ones(H), where=care_needed > 0), 0, 1)
        risk = (work * self.work_hazard)[:, None]
        infection = np.full(len(i), self.infection)
        hz = [engine.death_hazards(c, age[:, None], risk, self.food[hh], short[hh], infection, self.demo)[:, 0]
              * (self.infection_factor if c == "infection" else 1.0) for c in engine.CAUSES]
        total = np.zeros(len(i))
        for h_c in hz:
            total = total + h_c
        dies = rng.random(len(i)) < -np.expm1(-total / 12)

        if acc is not None:
            other = 24 - self.sleep[b] - work - care
            for col, v in ((ACTIVITIES.index("sleep"), self.sleep[b]), (self.livelihood, work),
                           (ACTIVITIES.index("childcare"), care), (ACTIVITIES.index("other"), other)):
                acc["hours"][:, col] += np.bincount(b, weights=v, minlength=AGES)
            acc["months"] += np.bincount(b, minlength=AGES)
            acc["need"] += need.sum()
            acc["eaten"] += eaten.sum()
            acc["deaths"] += int(dies.sum())
            acc["by_cause"] += [np.divide(h_c, total, out=np.zeros(len(i)), where=total > 0)[dies].sum() for h_c in hz]
            acc["from_others"] += min(from_others.sum(), eaten.sum())
            acc["short_months"] += int((met[hh] < 0.8).sum())

        dead = i[dies]
        self.alive[dead] = False
        widowed = self.partner[dead]
        self.partner[widowed[widowed >= 0]] = -1
        self.partner[dead] = -1
        babies = dead[self.age_m[dead] < a["fertility"]["nursing_months"]]
        mo = self.mother[babies]
        self.nursing_until[mo[mo >= 0]] = np.minimum(self.nursing_until[mo[mo >= 0]], self.t)
        live = i[~dies]
        self._births(live, acc)
        self._marriages()
        self.age_m[:self.n][self.alive[:self.n]] += 1
        self.t += 1

    def _share(self, held, made, need):
        """Food passing between households in a month (rules.json "sharing"): the common pot, then along
        the links. held, made, need: each household's [H]. Returns (what each then holds, how much of that
        came from other households)."""
        H = len(held)
        put = self.pot * made
        pot = put.sum()
        got = pot * need / need.sum() if need.sum() > 0 else np.zeros(H)
        held = held - put + got
        from_others = got * (1 - np.divide(put, pot, out=np.zeros(H), where=pot > 0))
        if len(self.ga) and self.give > 0:
            surplus = np.maximum(held - need * (1 + self.a["household"]["buffer_months"]), 0)
            lack = np.maximum(need - held, 0)
            want = self.w * lack[self.rb]
            ask = np.bincount(self.ga, weights=want, minlength=H)
            offer = self.give * surplus[self.ga] * np.divide(want, ask[self.ga], out=np.zeros(len(want)), where=ask[self.ga] > 0)
            offered = np.bincount(self.rb, weights=offer, minlength=H)
            moved = offer * np.clip(np.divide(lack, offered, out=np.zeros(H), where=offered > 0), 0, 1)[self.rb]
            into = np.bincount(self.rb, weights=moved, minlength=H)
            held = held - np.bincount(self.ga, weights=moved, minlength=H) + into
            from_others = from_others + into
            self.given += moved
        return held, from_others

    def _links(self):
        """Rebuild the year's sharing links (rules.json "sharing"): kin between the households of parents
        and children, siblings, and grandparents and grandchildren; and partnerships, ending those one
        side didn't return last year and choosing new ones at random up to partners_per_household."""
        sh, rng = self.a["sharing"], self.rng
        i = np.flatnonzero(self.alive[:self.n])
        home = np.full(self.n, -1)
        home[i] = self.hh[i]
        at = lambda ids: np.where(ids >= 0, home[np.maximum(ids, 0)], -1)
        kw = sh["kin_weight"]
        xs, ys, ws = [], [], []
        for parent in (self.mother[i], self.father[i]):
            xs.append(self.hh[i]), ys.append(at(parent)), ws.append(np.full(len(i), kw["parent_child"]))
            for grand in (self.mother, self.father):
                g = np.where(parent >= 0, grand[np.maximum(parent, 0)], -1)
                xs.append(self.hh[i]), ys.append(at(g)), ws.append(np.full(len(i), kw["grandparent"]))
        mo = self.mother[i]
        by_mother = {}
        for m, h in zip(mo[mo >= 0].tolist(), self.hh[i][mo >= 0].tolist()):
            by_mother.setdefault(m, set()).add(h)
        sib = [(p, q) for hs in by_mother.values() if len(hs) > 1 for p in hs for q in hs if p < q]
        if sib:
            p, q = np.array(sib).T
            xs.append(p), ys.append(q), ws.append(np.full(len(p), kw["sibling"]))
        x, y, w = np.concatenate(xs), np.concatenate(ys), np.concatenate(ws)
        ok = (x >= 0) & (y >= 0) & (x != y)
        a, b, w = np.minimum(x, y)[ok], np.maximum(x, y)[ok], w[ok]
        key, inv = np.unique(a * self.n_hh + b, return_inverse=True)
        kin_w = np.zeros(len(key))
        np.maximum.at(kin_w, inv, w)
        kin = np.stack(np.divmod(key, self.n_hh), axis=1)

        # Partnerships: keep those still between living households and returned last year.
        lived = np.bincount(self.hh[i], minlength=self.n_hh) > 0
        keep = []
        if len(self.partners):
            mine = self.pair >= 0
            gave = np.zeros((len(self.partners), 2))
            np.add.at(gave, (self.pair[mine], (self.ga[mine] != self.partners[self.pair[mine], 0]).astype(int)),
                      self.given[mine])
            most, least = gave.max(axis=1), gave.min(axis=1)
            keep = lived[self.partners].all(axis=1) & ~((most > 0) & (least < sh["min_return"] * most))
        partners = [tuple(pq) for pq in (self.partners[keep] if len(self.partners) else [])]
        taken = set(partners) | set(map(tuple, kin.tolist()))
        homes = np.flatnonzero(lived)
        count = np.bincount(np.array(partners, dtype=np.int64).ravel(), minlength=self.n_hh) if partners \
            else np.zeros(self.n_hh, dtype=np.int64)
        if len(homes) > 1:
            for h in homes[rng.permutation(len(homes))].tolist():
                for other in rng.choice(homes, size=4 * sh["partners_per_household"]).tolist():
                    if count[h] >= sh["partners_per_household"]:
                        break
                    pq = (min(h, other), max(h, other))
                    if other != h and pq not in taken and count[other] < sh["partners_per_household"]:
                        partners.append(pq), taken.add(pq)
                        count[h] += 1
                        count[other] += 1
        self.partners = np.array(partners, dtype=np.int64).reshape(-1, 2)

        pairs = np.concatenate([kin, self.partners])
        weights = np.concatenate([kin_w, np.full(len(self.partners), sh["partner_weight"])])
        which = np.concatenate([np.full(len(kin), -1), np.arange(len(self.partners))])
        self.ga = np.concatenate([pairs[:, 0], pairs[:, 1]])
        self.rb = np.concatenate([pairs[:, 1], pairs[:, 0]])
        self.w = np.concatenate([weights, weights])
        self.pair = np.concatenate([which, which])
        self.given = np.zeros(len(self.ga))

    def _births(self, live, acc):
        f = self.a["fertility"]
        rng = self.rng
        expecting = live[self.preg[live] >= 0]
        self.preg[expecting] += 1
        due = expecting[self.preg[expecting] >= f["gestation_months"]]
        if len(due):
            father = np.where(self.partner[due] >= 0, self.partner[due], -1)
            self._add(len(due), 0, rng.random(len(due)) < 0.5, self.hh[due], due, father)
            self.preg[due] = -1
            self.nursing_until[due] = self.t + f["nursing_months"]
            if acc is not None:
                mb = np.minimum(self.age_m[due] // 12, AGES - 1)
                fb = np.where(father >= 0, np.minimum(self.age_m[np.maximum(father, 0)] // 12, AGES - 1), mb)
                acc["births"] += 0.5 * np.bincount(mb, minlength=AGES) + 0.5 * np.bincount(fb, minlength=AGES)
        # Conception.
        w = live[self.female[live] & (self.partner[live] >= 0) & (self.preg[live] < 0)]
        age = self.age_m[w] / 12
        w, age = w[(age >= 15) & (age < 50)], age[(age >= 15) & (age < 50)]
        p = (f["fecundability"] * table(age, f["factor_by_age"])
             * table(self.food[self.hh[w]], f["food_factor_by_share_met"])
             * np.where(self.nursing_until[w] > self.t, f["lactation_factor"], 1.0))
        self.preg[w[rng.random(len(w)) < p]] = 0

    def _marriages(self):
        m, rng = self.a["marriage"], self.rng
        i = np.flatnonzero(self.alive[:self.n])
        age = self.age_m[i] / 12
        single = self.partner[i] < 0
        women = i[single & self.female[i] & (age >= m["women_from"]) & (age < m["women_to"])]
        men = i[single & ~self.female[i] & (age >= m["men_from"]) & (age < m["men_to"])]
        rng.shuffle(women), rng.shuffle(men)
        k = min(len(women), len(men))
        w, h = women[:k], men[:k]
        ok = (rng.random(k) < m["per_month"]) & ~((self.mother[w] >= 0) & (self.mother[w] == self.mother[h]))
        if not ok.any():
            return
        kids = i[self.age_m[i] < 15 * 12]
        has_kids = np.zeros(self.n, dtype=bool)
        for parent in (self.mother[kids], self.father[kids]):
            has_kids[parent[parent >= 0]] = True
        for wi, mi in zip(w[ok].tolist(), h[ok].tolist()):
            if has_kids[wi]:
                self.hh[mi] = self.hh[wi]
            elif has_kids[mi]:
                self.hh[wi] = self.hh[mi]
            else:
                self.hh[wi] = self.hh[mi] = self._new_household((self.food[self.hh[wi]] + self.food[self.hh[mi]]) / 2)
            self.partner[wi], self.partner[mi] = mi, wi

    def _rehome_orphans(self):
        """Households left with nobody aged 15 or more join a random household that has someone."""
        i = np.flatnonzero(self.alive[:self.n])
        adult = self.age_m[i] >= 15 * 12
        has_adult = np.bincount(self.hh[i], weights=adult, minlength=self.n_hh) > 0
        members = np.bincount(self.hh[i], minlength=self.n_hh) > 0
        orphaned = np.flatnonzero(members & ~has_adult)
        homes = np.flatnonzero(has_adult)
        if len(orphaned) and len(homes):
            to = dict(zip(orphaned.tolist(), self.rng.choice(homes, size=len(orphaned)).tolist()))
            for p in i[np.isin(self.hh[i], orphaned)].tolist():
                self.hh[p] = to[int(self.hh[p])]

    # A year.

    def year(self):
        """Live a year. Returns the situation at its start and what the people did during it, as a dict
        of the fields recorded (see run_scenario())."""
        i = np.flatnonzero(self.alive[:self.n])
        people = np.bincount(np.minimum(self.age_m[i] // 12, AGES - 1), minlength=AGES).astype(np.float64)
        need = (people * self.eat_day).sum() * 365
        row = {"people": people, "food_met": self.prev_met, "hours_prev": self.prev_hours.copy(),
               "tile_people": people.sum(), "food_target": max(need - self.store.sum(), 0.0)}
        acc = {"hours": np.zeros((AGES, len(ACTIVITIES))), "months": np.zeros(AGES), "births": np.zeros(AGES),
               "need": 0.0, "eaten": 0.0, "deaths": 0, "by_cause": np.zeros(len(engine.CAUSES)),
               "from_others": 0.0, "short_months": 0}
        self._links()
        for _ in range(12):
            self.month(acc)
        self._rehome_orphans()
        months = acc["months"]
        hours = np.divide(acc["hours"], months[:, None], out=np.zeros_like(acc["hours"]), where=months[:, None] > 0)
        births = np.divide(acc["births"], months / 12, out=np.zeros(AGES), where=months > 0)
        self.prev_hours = hours
        self.prev_met = acc["eaten"] / acc["need"] if acc["need"] > 0 else 1.0
        row.update(hours=hours, birth_rate=births, food_met_year=self.prev_met, deaths_year=acc["deaths"],
                   born_year=float(acc["births"].sum()), deaths_by_cause=acc["by_cause"],
                   from_others_year=acc["from_others"] / acc["eaten"] if acc["eaten"] > 0 else 0.0,
                   short_year=acc["short_months"] / months.sum() if months.sum() > 0 else 0.0)
        return row


def run_scenario(sc):
    """Live one scenario (pick_scenarios()) and return its recorded years, a list of dicts: the situation
    at each year's start (people by age, the year before's share of food need met and hours, people on
    the tile, food target) and the year's hours, birth rates by age, share of need met, deaths and births."""
    arules, erules, demo = load_rules(), engine.load_rules(), engine.load_demography()
    s = arules["scenarios"]
    sim = Sim(sc, arules, erules, demo)
    rows = []
    for y in range(s["burn_in_years"] + s["record_years"]):
        row = sim.year()
        if row["tile_people"] == 0:
            break
        if y >= s["burn_in_years"]:
            rows.append({**row, "scenario": sc["id"], "year": y})
    return sc, rows


def situation(d, rows=slice(None)):
    """A decisions.Situation for recorded rows of a run file (d: its arrays)."""
    livelihood = d["livelihood"][rows]
    return decisions.Situation(
        year=0, people=d["people"][rows], livelihood=livelihood, settled=d["settled"][rows],
        moving=np.zeros(len(livelihood), dtype=bool), food_met=d["food_met"][rows],
        hours=d["hours_prev"][rows].astype(np.float32), forage_max=d["forage_max"][rows],
        farm_max=d["farm_max"][rows], tile_people=d["tile_people"][rows], food_target=d["food_target"][rows],
        return_per_hour=d["return_per_hour"][rows])


def run(args):
    arules, erules, demo = load_rules(), engine.load_rules(), engine.load_demography()
    scenarios = [{**sc, "sharing": not args.no_sharing} for sc in pick_scenarios(args.scenarios, args.seed, arules, erules, demo)]
    started, rows = time.time(), []
    with Pool(args.workers) as pool:
        for k, (sc, got) in enumerate(pool.imap_unordered(run_scenario, scenarios), 1):
            rows += [(sc, r) for r in got]
            last = got[-1] if got else None
            print(f"[{k}/{len(scenarios)}] scenario {sc['id']}: {'farming' if sc['farming'] else 'foraging'} "
                  f"tile {sc['tile']}, {sc['people']} people -> "
                  + (f"{last['tile_people']:.0f} after {last['year'] + 1} years, food {last['food_met_year']:.1%} "
                     f"of need met" if last else "died out")
                  + f" ({time.time() - started:.0f}s)", flush=True)
    if not rows:
        sys.exit("every scenario died out")
    erate = {erules[k]["activity"]: erules[k]["return_per_hour"] for k in ("foraging", "farming")}
    col = lambda f, dtype=np.float64: np.array([f(sc, r) for sc, r in rows], dtype=dtype)
    out = {
        "scenario": col(lambda sc, r: sc["id"], np.int64), "tile": col(lambda sc, r: sc["tile"], np.int64),
        "year": col(lambda sc, r: r["year"], np.int64),
        "livelihood": col(lambda sc, r: ACTIVITIES.index("farming" if sc["farming"] else "subsistence"), np.int64),
        "settled": col(lambda sc, r: sc["farming"], bool),
        "forage_max": col(lambda sc, r: sc["forage_max"]), "farm_max": col(lambda sc, r: sc["farm_max"]),
        "return_per_hour": col(lambda sc, r: erate["farming" if sc["farming"] else "subsistence"]),
        "people": np.stack([r["people"] for _, r in rows]),
        "hours_prev": np.stack([r["hours_prev"] for _, r in rows]).astype(np.float32),
        "hours": np.stack([r["hours"] for _, r in rows]),
        "birth_rate": np.stack([r["birth_rate"] for _, r in rows]),
        "deaths_by_cause": np.stack([r["deaths_by_cause"] for _, r in rows]),
    }
    for k in ("food_met", "tile_people", "food_target", "food_met_year", "deaths_year", "born_year",
              "from_others_year", "short_year"):
        out[k] = col(lambda sc, r: r[k])
    d = out
    decisions.check(decisions.Decisions(d["hours"], d["birth_rate"]), situation(d))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    np.savez_compressed(args.out, **out, activities=np.array(ACTIVITIES),
                        causes=np.array(engine.CAUSES),
                        meta=np.array(json.dumps({"scenarios": scenarios, "abm_rules": arules, "seed": args.seed,
                                                  "sharing": not args.no_sharing})))
    print(f"{len(rows)} recorded years from {len({sc['id'] for sc, _ in rows})} scenarios -> {args.out}")


def compare(args):
    """Print the ABM's recorded behaviour in each run given beside what the placeholder decide() chooses
    for the first run's situations, by way of life: hours a day of work and childcare by age group,
    births, deaths, and food."""
    demo = engine.load_demography()
    runs = [(os.path.splitext(os.path.basename(f))[0], dict(np.load(f))) for f in args.files]
    first = runs[0][1]
    placeholder = decisions.decide(situation(first), demo)
    bands = [("5-14", 5, 15), ("15-49", 15, 50), ("50-64", 50, 65), ("65+", 65, AGES)]
    causes = list(first["causes"])
    for name, liv in (("foraging", "subsistence"), ("farming", "farming")):
        col = ACTIVITIES.index(liv)
        pick = [(label, d, d["livelihood"] == col) for label, d in runs]
        if not pick[0][2].any():
            continue
        print(f"\n{name}: " + "; ".join(f"{label}: {rows.sum()} recorded years from "
                                        f"{len(np.unique(d['scenario'][rows]))} scenarios, "
                                        f"{d['people'][rows].sum(axis=1).mean():,.0f} people on average"
                                        for label, d, rows in pick))
        print(f"  {'':32}" + "".join(f"{label:>14}" for label, _, _ in pick) + f"{'placeholder':>14}")

        def line(label, f, ph=None):
            print(f"  {label:32}" + "".join(f"{f(d, rows):>14}" for _, d, rows in pick)
                  + (f"{ph:>14}" if ph is not None else ""))

        p_rows = pick[0][2]
        for label, c in (("work", col), ("childcare", ACTIVITIES.index("childcare"))):
            for band, lo, hi in bands:
                mean = lambda h, d, rows: f"{(h[:, lo:hi, c] * d['people'][rows][:, lo:hi]).sum() / d['people'][rows][:, lo:hi].sum():.2f}"
                line(f"{label} h/day, ages {band}", lambda d, rows: mean(d["hours"][rows], d, rows),
                     mean(placeholder.hours[p_rows], first, p_rows))
        cbr = lambda rate, d, rows: f"{1000 * (rate * d['people'][rows]).sum() / d['people'][rows].sum():.1f}"
        line("births per 1,000 a year", lambda d, rows: cbr(d["birth_rate"][rows], d, rows),
             cbr(placeholder.birth_rate[p_rows], first, p_rows))
        line("children per woman", lambda d, rows: f"{2 * d['birth_rate'][rows].sum(axis=1).mean():.1f}",
             f"{2 * placeholder.birth_rate[p_rows].sum(axis=1).mean():.1f}")
        per_k = lambda x, d, rows: f"{1000 * x.sum() / d['people'][rows].sum():.1f}"
        line("deaths per 1,000 a year", lambda d, rows: per_k(d["deaths_year"][rows], d, rows))
        for c in ("starvation", "child_mortality"):
            line(f"  of {c.replace('_', ' ')}", lambda d, rows: per_k(d["deaths_by_cause"][rows][:, causes.index(c)], d, rows))
        line("food need met", lambda d, rows: f"{d['food_met_year'][rows].mean():.1%}")
        line("people in households <80% fed", lambda d, rows: f"{d['short_year'][rows].mean():.1%}")
        line("food from other households", lambda d, rows: f"{d['from_others_year'][rows].mean():.1%}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="live and record random scenarios")
    p.add_argument("--scenarios", type=int, default=16)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--workers", type=int, default=os.cpu_count())
    p.add_argument("--no-sharing", action="store_true", help="no sharing between households (to compare)")
    p.add_argument("--out", default=OUT)
    p = sub.add_parser("compare", help="compare runs' behaviour with the placeholder decide()")
    p.add_argument("files", nargs="*", default=[OUT])
    args = ap.parse_args()
    return {"run": run, "compare": compare}[args.cmd](args) or 0


if __name__ == "__main__":
    sys.exit(main())
