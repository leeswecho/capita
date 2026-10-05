"""The tile grid shared by every script, the server and (as a JavaScript copy) static/index.html.

Tiles are TILE x TILE degrees (0.5), giving WIDTH x HEIGHT = 720 x 360 = 259,200 tiles.
A tile is named after its SOUTH-WEST corner in tenths of a degree: N/S + 3 digits of latitude,
E/W + 4 digits of longitude. N395W1055 spans 39.5..40.0N, 105.5..105.0W; S005E0000 spans 0.5S..0, 0..0.5E.
Latitude 0 and longitude 0 count as N and E. Corner latitudes run -90.0..89.5, longitudes -180.0..179.5.

Grid order (rows north to south, west to east within a row) gives each tile a flat index:
    row = (90 - TILE - lat) / TILE      col = (lon + 180) / TILE      index = row * WIDTH + col
"""
import math
import re

TILE = 0.5                        # degrees per tile side
PER_DEG = 2                       # tiles per degree (1 / TILE)
WIDTH, HEIGHT = 360 * PER_DEG, 180 * PER_DEG
COUNT = WIDTH * HEIGHT
TILE_ID_RE = re.compile(r'^[NS]\d{3}[EW]\d{4}$')
DIRS = (('n', 1, 0), ('ne', 1, 1), ('e', 0, 1), ('se', -1, 1),
        ('s', -1, 0), ('sw', -1, -1), ('w', 0, -1), ('nw', 1, -1))   # (name, d_row_up, d_col_east)


def tile_id(lat, lon):
    """Id of the tile whose south-west corner is (lat, lon), both multiples of TILE."""
    la, lo = round(lat * 10), round(lon * 10)
    return f"{'N' if la >= 0 else 'S'}{abs(la):03d}{'E' if lo >= 0 else 'W'}{abs(lo):04d}"


def parse_tile_id(tid):
    """-> (lat, lon) of the tile's south-west corner."""
    lat = int(tid[1:4]) / 10 * (1 if tid[0] == 'N' else -1)
    lon = int(tid[5:9]) / 10 * (1 if tid[4] == 'E' else -1)
    return lat, lon


def corner(row, col):
    """South-west corner (lat, lon) of the tile at grid row/col."""
    return 90 - (row + 1) * TILE, col * TILE - 180


def row_col(lat, lon):
    """Grid row/col of the tile whose south-west corner is (lat, lon)."""
    return round((90 - TILE - lat) * PER_DEG), round((lon + 180) * PER_DEG)


def index(lat, lon):
    r, c = row_col(lat, lon)
    return r * WIDTH + c


def tile_at(lat, lon):
    """Id of the tile containing any point."""
    # Tiles span [corner, corner + TILE), so a point on a boundary belongs to the tile north/east of it.
    r = min(HEIGHT - 1, max(0, HEIGHT - 1 - math.floor((lat + 90) * PER_DEG)))
    c = math.floor((lon + 180) * PER_DEG) % WIDTH
    return tile_id(*corner(r, c))


def neighbors(row, col):
    """{direction: tile id or None} for the tile at row/col; east-west wraps, the poles don't."""
    out = {}
    for d, up, east in DIRS:
        r = row - up
        out[d] = tile_id(*corner(r, (col + east) % WIDTH)) if 0 <= r < HEIGHT else None
    return out


def fmt(v):
    """Coordinate as YAML text: always one decimal place (39.5, -105.0)."""
    return f'{v:.1f}'


def fmt_center(v):
    """Tile-centre coordinate as YAML text: centres sit at .25 / .75, so two decimals (39.75)."""
    return f'{v:.2f}'
