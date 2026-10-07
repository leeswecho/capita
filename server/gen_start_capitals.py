"""Make the game's starting scenario: 200 people in each start tile, 0 elsewhere.

    python gen_start_capitals.py

This is the game's starting scenario. Writes ../reference/world_population_capitals.yaml, in the same
layout as ../reference/world_population.yaml, and world_population_start.json (here, next to
world_population_current.json), in the columnar format convert_population.py makes. Also writes
world_population_start_capitals.nations.json, the nation seed engine.py init picks up automatically:
one nation per start tile, in ISO-code order, with that tile as its capital. To show it on the globe:

    python server.py          # world_population_start.json is the server's default

Each tile's `country` and `area_km2` are copied from world_population_current.json (the columnar copy
of ../reference/world_population.yaml), so it must be present.
Only `population`, `density_per_km2` and the population keys of the `map` section change.

Start tiles: CAPITALS, 38 placeholder locations (present-day national capitals) for the start tribes.
Their coordinates are the "Admin-0 capital" points of Natural Earth 1:10m Populated Places
(CAPITALS_URL). Two choices follow that source: Amsterdam for the Netherlands (the constitutional
capital; the government sits in The Hague) and Jerusalem for Israel (the capital Israel designates;
many countries keep their embassies in Tel Aviv).
"""
import json
import os
import sys

import yaml

import grid
from convert_population import FIELDS, FORMAT, ORDER, load_population

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, 'world_population_current.json')
OUT_YAML = os.path.join(HERE, '..', 'reference', 'world_population_capitals.yaml')
OUT_JSON = os.path.join(HERE, 'world_population_start.json')
OUT_NATIONS = os.path.join(HERE, 'world_population_start_capitals.nations.json')
CAPITALS_URL = ('https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/'
                'geojson/ne_10m_populated_places_simple.geojson')
PEOPLE = 200

# ISO alpha-3 -> (capital, latitude, longitude), from Natural Earth Populated Places.
CAPITALS = {
    'AUS': ('Canberra', -35.283029, 149.129026),
    'AUT': ('Vienna', 48.201961, 16.364693),
    'BEL': ('Brussels', 50.835263, 4.331371),
    'CAN': ('Ottawa', 45.418643, -75.701961),
    'CHE': ('Bern', 46.916683, 7.466976),
    'CHL': ('Santiago', -33.448068, -70.668987),
    'COL': ('Bogota', 4.598369, -74.08529),
    'CRI': ('San Jose', 9.936958, -84.085997),
    'CZE': ('Prague', 50.085283, 14.464034),
    'DEU': ('Berlin', 52.523765, 13.399603),
    'DNK': ('Copenhagen', 55.68051, 12.56154),
    'ESP': ('Madrid', 40.401972, -3.685298),
    'EST': ('Tallinn', 59.433877, 24.728041),
    'FIN': ('Helsinki', 60.177509, 24.932181),
    'FRA': ('Paris', 48.868639, 2.33139),
    'GBR': ('London', 51.501941, -0.118668),
    'GRC': ('Athens', 37.985272, 23.731375),
    'HUN': ('Budapest', 47.501952, 19.081375),
    'IRL': ('Dublin', 53.335007, -6.250852),
    'ISL': ('Reykjavik', 64.150024, -21.950015),
    'ISR': ('Jerusalem', 31.778408, 35.206626),
    'ITA': ('Rome', 41.897902, 12.481313),
    'JPN': ('Tokyo', 35.686963, 139.749462),
    'KOR': ('Seoul', 37.568295, 126.997785),
    'LTU': ('Vilnius', 54.683366, 25.316635),
    'LUX': ('Luxembourg', 49.61166, 6.130003),
    'LVA': ('Riga', 56.950024, 24.099965),
    'MEX': ('Mexico City', 19.444388, -99.132934),
    'NLD': ('Amsterdam', 52.351915, 4.914694),
    'NOR': ('Oslo', 59.918636, 10.748033),
    'NZL': ('Wellington', -41.299988, 174.783266),
    'POL': ('Warsaw', 52.251947, 20.998054),
    'PRT': ('Lisbon', 38.724669, -9.146812),
    'SVK': ('Bratislava', 48.150018, 17.116981),
    'SVN': ('Ljubljana', 46.055288, 14.514969),
    'SWE': ('Stockholm', 59.352706, 18.095389),
    'TUR': ('Ankara', 39.929184, 32.862446),
    'USA': ('Washington, D.C.', 38.901495, -77.011364),
}


def main():
    assert len(CAPITALS) == 38
    with open(BASE, encoding='utf-8') as f:
        base = json.load(f)
    if base.get('format') != FORMAT:
        sys.exit(f'{BASE}: expected format {FORMAT!r}, got {base.get("format")!r}')
    country, area = base['country'], base['area_km2']

    population = [0] * grid.COUNT
    for iso, (name, lat, lon) in CAPITALS.items():
        i = grid.index(*grid.parse_tile_id(grid.tile_at(lat, lon)))
        assert population[i] == 0, f'{name} shares a tile with another capital'
        if country[i] != iso:
            print(f'note: {name} ({iso}) is in tile {grid.tile_at(lat, lon)}, which is mostly {country[i]}')
        population[i] = PEOPLE
    density = [round(p / a, 2) if p else 0.0 for p, a in zip(population, area)]

    meta = {k: v for k, v in base['map'].items() if not k.startswith('population_') and k != 'total_population'}
    meta.update({
        'population_source_data': CAPITALS_URL,
        'population_source_dataset': f'Starting scenario: {PEOPLE} people in each of the {len(CAPITALS)} '
                                     f'start tiles; 0 elsewhere',
        'population_coverage': {'south_lat': -90.0, 'north_lat': 90.0},
        'total_population': sum(population),
    })
    keys = list(base['map'])  # keep the original key order; population keys sit where they did
    meta = {k: meta[k] for k in keys if k in meta} | {k: v for k, v in meta.items() if k not in keys}

    half = grid.TILE / 2
    with open(OUT_YAML, 'w', encoding='utf-8', newline='\n') as f:
        f.write('# Starting scenario population map: one tile per 0.5x0.5 degree of latitude/longitude, with the\n'
                '# same tile ids, order, neighbours, country and area_km2 as world_population.yaml (see grid.py).\n'
                f'# population: {PEOPLE} in each of the {len(CAPITALS)} start tiles, 0 in every other tile. Start\n'
                '#   tiles are placeholder locations at present-day capitals: Natural Earth 1:10m Populated\n'
                '#   Places, "Admin-0 capital" (Amsterdam for NLD, Jerusalem for ISR).\n'
                '#   density_per_km2 = population / whole tile area (land and water).\n'
                '# country: ISO 3166-1 alpha-3 code of the country/territory with the most land in the tile\n'
                '#   (Natural Earth 1:10m Admin 0 Countries, de facto boundaries); null = no land, or only\n'
                '#   unclaimed/contested land. XKX (Kosovo) is a user-assigned code, not official ISO.\n')
        f.write('map:\n')
        for k, v in meta.items():
            if isinstance(v, dict):
                v = '{' + ', '.join(f'{a}: {b}' for a, b in v.items()) + '}'
            elif isinstance(v, bool):
                v = 'true' if v else 'false'
            elif isinstance(v, str) and any(s in v for s in (': ', ' #', '|')):
                v = json.dumps(v)
            f.write(f'  {k}: {v}\n')
        f.write('tiles:\n')
        for row in range(grid.HEIGHT):
            for col in range(grid.WIDTH):
                i = row * grid.WIDTH + col
                lat, lon = grid.corner(row, col)
                nb = ', '.join(f'{d}: {v or "null"}' for d, v in grid.neighbors(row, col).items())
                f.write(f'  {grid.tile_id(lat, lon)}:\n'
                        f'    lat: {grid.fmt(lat)}\n    lon: {grid.fmt(lon)}\n'
                        f'    center: [{grid.fmt_center(lat + half)}, {grid.fmt_center(lon + half)}]\n'
                        f'    country: {country[i] or "null"}\n'
                        f'    population: {population[i]}\n'
                        f'    area_km2: {area[i]:.1f}\n'
                        f'    density_per_km2: {density[i]:.2f}\n'
                        '    neighbors: {' + nb + '}\n')

    out = {'format': FORMAT, 'map': meta, 'order': ORDER, 'country': country, 'population': population,
           'area_km2': area, 'density_per_km2': density}
    assert set(FIELDS) <= set(out)
    with open(OUT_JSON, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(out, f, separators=(',', ':'))

    nations = [{'code': iso, 'capital_name': name, 'capital': grid.tile_at(lat, lon)}
               for iso, (name, lat, lon) in sorted(CAPITALS.items())]
    with open(OUT_NATIONS, 'w', encoding='utf-8', newline='\n') as f:
        json.dump({'nations': nations}, f, indent=1)
        f.write('\n')

    # The YAML and JSON must describe the same map.
    with open(OUT_YAML, encoding='utf-8') as f:
        from_yaml = yaml.load(f, Loader=getattr(yaml, 'CSafeLoader', yaml.SafeLoader))
    if load_population(OUT_JSON) != from_yaml:
        sys.exit('YAML and JSON outputs differ')
    print(f'wrote {os.path.basename(OUT_YAML)}, {os.path.basename(OUT_JSON)} and {os.path.basename(OUT_NATIONS)}: '
          f'{sum(1 for p in population if p)} populated tiles, {sum(population)} people (verified identical)')


if __name__ == '__main__':
    main()
