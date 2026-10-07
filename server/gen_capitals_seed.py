"""Write a nation seed with real-life capitals for a population file.

    python gen_capitals_seed.py [--population world_population_current.json]

Writes <population name>_capitals.nations.json next to the population file (for the default,
world_population_current_capitals.nations.json), which engine.py init picks up automatically. There is
one nation for every country code that has a populated tile in the population file (the same nations,
in the same id order, that init would make without a seed), each with its capital tile:

    {"nations": [{"code": "AFG", "capital_name": "Kabul", "capital": "N345E0690"}, ...]}

Capitals come from Natural Earth 1:10m Populated Places (PLACES_URL; downloaded next to this script the
first time). A country's capital is the place Natural Earth marks as its country capital (adm0cap = 1),
or else its Admin-0 capital or region capital (dependencies). CHOICES picks one where Natural Earth
marks several, and EXTRA supplies the territories it doesn't mark at all. If two capitals fall in the
same tile, the larger city keeps the tile (by Natural Earth's pop_max; if neither has one, the country
with most of its land there), and the other capital moves to its own country's nearest populated tile
(the seed entry then gets a "note").
"""
import argparse
import json
import math
import os
import sys
import urllib.request

import grid
from convert_population import FORMAT

HERE = os.path.dirname(os.path.abspath(__file__))
PLACES = os.path.join(HERE, 'ne_10m_populated_places_simple.geojson')
PLACES_URL = ('https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/'
              'geojson/ne_10m_populated_places_simple.geojson')
COUNTRIES = os.path.join(HERE, 'ne_10m_admin_0_countries.geojson')

# Countries where Natural Earth has more than one adm0cap = 1 capital, or none: the one to use.
CHOICES = {
    'BOL': 'Sucre',          # constitutional capital (government in La Paz)
    'CIV': 'Yamoussoukro',   # official capital (government mostly in Abidjan)
    'ZAF': 'Pretoria',       # executive capital (also Cape Town, Bloemfontein)
    'PSE': 'Ramallah',       # seat of the Palestinian Authority
}
# Capitals Natural Earth doesn't mark as a country capital: name, latitude, longitude.
EXTRA = {
    'AIA': ('The Valley', 18.2208, -63.0517),
    'BLM': ('Gustavia', 17.8958, -62.8508),
    'COK': ('Avarua', -21.196, -159.785),
    'FRO': ('Torshavn', 62.03, -6.82),
    'GGY': ('St. Peter Port', 49.4555, -2.5368),
    'JEY': ('St. Helier', 49.1858, -2.1100),
    'MNP': ('Capitol Hill', 15.211, 145.751),
    'MSR': ('Brades', 16.7928, -62.2106),           # de facto capital since Plymouth was abandoned
    'NFK': ('Kingston', -29.0545, 167.9666),
    'NIU': ('Alofi', -19.066, -169.914),
    'NRU': ('Yaren', -0.5477, 166.9209),            # seat of government; Nauru has no official capital
    'PCN': ('Adamstown', -25.0667, -130.1000),
    'PRI': ('San Juan', 18.4655, -66.1057),
    'SHN': ('Jamestown', -15.9244, -5.7181),
    'SPM': ('Saint-Pierre', 46.7811, -56.1764),
    'VGB': ('Road Town', 18.4286, -64.6185),
    'VIR': ('Charlotte Amalie', 18.3419, -64.9307),
    'WLF': ('Mata-Utu', -13.2825, -176.1736),
    'XKX': ('Pristina', 42.667, 21.166),
}


def load_places():
    if not os.path.exists(PLACES):
        print(f'downloading {os.path.basename(PLACES)}')
        urllib.request.urlretrieve(PLACES_URL, PLACES)
    with open(PLACES, encoding='utf-8') as f:
        return json.load(f)['features']


def iso_a3_by_a2():
    """ISO alpha-2 -> the alpha-3 code the population file uses (ISO_A3_EH in the countries file)."""
    with open(COUNTRIES, encoding='utf-8') as f:
        feats = json.load(f)['features']
    out = {}
    for ft in feats:
        p = ft['properties']
        if p['ISO_A2_EH'] not in (None, '-99') and p['ISO_A3_EH'] not in (None, '-99'):
            out[p['ISO_A2_EH']] = p['ISO_A3_EH']
    return out


def capitals_by_code(codes):
    """code -> (name, lat, lon, city population or None) for every code."""
    a3 = iso_a3_by_a2()
    classes = ('Admin-0 capital', 'Admin-0 capital alt', 'Admin-0 region capital')   # dependencies use the last
    ranked = {}
    for ft in load_places():
        p = ft['properties']
        code = a3.get(p['iso_a2'])
        if p['featurecla'] in classes and code in codes:
            rank = (p['adm0cap'] == 1, p['featurecla'] == 'Admin-0 capital')
            ranked.setdefault(code, []).append((rank, (p['nameascii'], p['latitude'], p['longitude'], p['pop_max'])))
    found = {code: [c for r, c in cands if r == max(r for r, _ in cands)] for code, cands in ranked.items()}
    out = {}
    for code in codes:
        if code in EXTRA:
            out[code] = (*EXTRA[code], None)   # no population figure
            continue
        cands = found.get(code, [])
        if code in CHOICES:
            cands = [c for c in cands if c[0] == CHOICES[code]] or [
                (p['properties']['nameascii'], p['properties']['latitude'], p['properties']['longitude'],
                 p['properties']['pop_max'])
                for p in load_places() if p['properties']['nameascii'] == CHOICES[code]]
        if len(cands) != 1:
            sys.exit(f'{code}: {len(cands)} capitals found {[c[0] for c in cands]}; add it to CHOICES or EXTRA')
        out[code] = cands[0]
    return out


def distance(lat1, lon1, lat2, lon2):
    """Great-circle distance in radians."""
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    return math.acos(max(-1, min(1, math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl))))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--population', default=os.path.join(HERE, 'world_population_current.json'))
    args = ap.parse_args()
    with open(args.population, encoding='utf-8') as f:
        pop = json.load(f)
    if pop.get('format') != FORMAT:
        sys.exit(f'{args.population}: expected format {FORMAT!r}')
    country, people = pop['country'], pop['population']
    codes = sorted({c for c, p in zip(country, people) if p > 0 and c})
    caps = capitals_by_code(codes)

    tile_of = {code: grid.index(*grid.parse_tile_id(grid.tile_at(lat, lon))) for code, (_, lat, lon, _) in caps.items()}
    notes = {}
    shared = {}
    for code, t in tile_of.items():
        shared.setdefault(t, []).append(code)
    for t, here in shared.items():
        if len(here) < 2:
            continue
        keep = max(here, key=lambda c: (caps[c][3] or -1, country[t] == c))
        for code in here:
            if code == keep:
                continue
            _, lat, lon, _ = caps[code]
            mine = [i for i, (c, p) in enumerate(zip(country, people)) if c == code and p > 0 and i != t]
            best = min(mine, key=lambda i: distance(lat, lon, *[v + grid.TILE / 2 for v in grid.corner(*divmod(i, grid.WIDTH))]))
            notes[code] = (f'{caps[code][0]} shares tile {grid.tile_id(*grid.corner(*divmod(t, grid.WIDTH)))} with '
                           f'{caps[keep][0]} ({keep}); moved to its nearest populated {code} tile')
            tile_of[code] = best

    nations = []
    for code in codes:
        t = tile_of[code]
        entry = {'code': code, 'capital_name': caps[code][0], 'capital': grid.tile_id(*grid.corner(*divmod(t, grid.WIDTH)))}
        if code in notes:
            entry['note'] = notes[code]
        nations.append(entry)
    out = os.path.splitext(args.population)[0] + '_capitals.nations.json'
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        json.dump({'nations': nations}, f, indent=1)
        f.write('\n')

    print(f'wrote {os.path.basename(out)}: {len(nations)} nations')
    for code, note in notes.items():
        print(f'  {code}: {note}')
    other = [(n['code'], n['capital_name'], country[tile_of[n['code']]]) for n in nations
             if country[tile_of[n['code']]] != n['code']]
    if other:
        print(f'  {len(other)} capital tiles are mostly another country\'s land; their people join the capital\'s nation:')
        for code, name, c in other:
            t = tile_of[code]
            print(f'    {code} {name}: tile {grid.tile_id(*grid.corner(*divmod(t, grid.WIDTH)))} is mostly {c} '
                  f'({people[t]:,} people)')
    empty = [n['code'] for n in nations if people[tile_of[n['code']]] == 0]
    if empty:
        print(f'  capital tiles with no people: {", ".join(empty)}')


if __name__ == '__main__':
    main()
