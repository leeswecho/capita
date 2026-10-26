"""Web server for the player's page: the game as one nation's player sees it.

    python player/player.py [--port 8000] [--host 127.0.0.1] [--state state]

Then open http://localhost:8000/ in a browser. (Paths shown from the repository root; the script
works from any directory.)

A player starts out knowing almost nothing about the world, or even about their own people, so the
server sends only what their nation knows: the tiles it has explored (cohort_state.py's explored bits),
each with just the names of its landforms and of what covers its land, largest first; which of them it can see now (visible bits);
and the segments on the tiles it can see. Of its own segments it knows the people and how they seem
(descriptions.json); of other nations' segments only roughly how big they look. Tile ids and
coordinates are in the data, since the page has to draw the map, but the page never shows them.
(server.py is the developer view, which shows everything; control/control.py the developer controls.)

The player chooses a nation (and may rename it, names.py), then gives its leaders orders: those their
type allows (leaders.json). A move names a direction from the segment's tile (n, ne, e, ...); the
server turns it into a move command for that neighbouring tile (orders.py). Founding a kingdom settles
the segment where it is, under a title the player chooses. Orders and Commit Turn go through
server/game.py, which the control server shares.

Endpoints:
    /                      the player page (static/index.html)
    /camps.js              the script that draws camps, shared with the developer view (server/static)
    /earth/<width>.jpg     the globe image at a size /api/meta lists
    /globe/<level>/<x>_<y>.jpg  a piece of the detail-tile pyramid (server/make_globe_tiles.py)
    /api/meta              the tile grid, the globe images and the detail-tile pyramid (JSON)
    /api/status            {"turn": t, "phase": p, "since": s} (JSON)
    /api/nations           {"turn": t, "nations": [{"id", "name", "flag"}, ...]}: the nations a player can
                           choose, those that are alive and have a leader, by name; "flag" is the
                           alpha-2 code of the country's flag (JSON)
    POST /api/nation/<id>/name  {"name": "..."} renames the nation (null goes back to its country's
                           name); returns {"id", "name", "flag"}; 400 if the name can't be used (JSON)
    /api/nation/<id>/view  what the nation knows at the current turn (see view_json()) (JSON)
    POST /api/nation/<id>/orders  {"turn": t, "leader": id, "direction": "nw"} orders a leader to move
                           their segment to the neighbouring tile that way; {"turn": t, "leader": id,
                           "found_kingdom": "King"} to found a kingdom where they are, under that title;
                           "direction": null takes the order back. Returns the leader's segment as /view
                           lists it; 400 if the order isn't possible, 409 if turn t isn't accepting
                           orders (JSON)
    POST /api/commit       {"turn": t}: commit turn t, advancing the whole world one year (JSON)
"""
import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
STATIC = os.path.join(HERE, "static")
DESCRIPTIONS = os.path.join(HERE, "descriptions.json")
sys.path.insert(0, os.path.join(REPO, "server"))   # the shared modules live with the developer view

import numpy as np  # noqa: E402

import camps as campsmod  # noqa: E402
import cohort_state as cs  # noqa: E402
import grid  # noqa: E402
import names  # noqa: E402
import orders  # noqa: E402
from game import COUNTRIES, MAP, Game  # noqa: E402
from server import GLOBE_TILES, ROOT as SERVER_DIR, STATIC as SERVER_STATIC, find_textures  # noqa: E402

DIRECTIONS = [d for d, _, _ in grid.DIRS]   # n, ne, e, se, s, sw, w, nw


def load_descriptions(path=DESCRIPTIONS):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def situation(t, r, rules) -> str:
    """How one of the player's own segments seems, in a sentence or two (descriptions.json)."""
    s = rules["situation"]
    food = [p["name"] for p in t.products].index(s["food_product"])
    need, consumed = float(t.need[r, food]), float(t.consumed[r, food])
    met = consumed / need if need > 0 else 1.0
    deaths = np.asarray(t.deaths[r], dtype=np.float64)
    start = float(np.asarray(t.people[r]).sum()) + float(deaths.sum())   # roughly, the people who began the year
    said = []
    for rule in s["food"]:
        if met < rule["met_below"]:
            said.append(rule["text"])
            break
    for rule in s["deaths"]:
        died = deaths.sum() if rule["cause"] == "all" else deaths[t.causes.index(rule["cause"])]
        if start > 0 and died / start > rule["share_above"]:
            said.append(rule["text"])
    said = said[:s["max_sentences"]] or [s["fine"]]
    if t.at[r] != t.toward[r]:
        said.append(s["moving"])
    return " ".join(said)


def foreign_size(people: float, rules) -> str:
    for rule in rules["foreign_size"]:
        if rule["below"] is None or people < rule["below"]:
            return rule["text"]
    return rules["foreign_size"][-1]["text"]


class Player(Game):
    def __init__(self, state_dir: str, countries_path: str, map_path: str):
        super().__init__(state_dir, map_path, countries_path)
        world = self.world
        self.meta = world.meta
        self.terrain_types = world.terrain_types
        self.terrain = np.stack([np.asarray(c, dtype=np.uint8) for c in world.terrain])   # [types, tiles]
        self.cover_types = world.cover_types
        self.cover = np.stack([np.asarray(c, dtype=np.uint8) for c in world.cover])         # [types, tiles]
        self.textures = find_textures(self.meta["source_image"])
        try:
            with open(os.path.join(GLOBE_TILES, "tiles.json"), encoding="utf-8") as f:
                self.globe_tiles = json.load(f)
        except (OSError, ValueError):
            self.globe_tiles = None

    def meta_json(self) -> bytes:
        return json.dumps({"tile_size_degrees": grid.TILE, "width": grid.WIDTH, "height": grid.HEIGHT,
                           "textures": [{"width": w, "url": f"/earth/{w}.jpg"} for w in self.textures],
                           "globe_tiles": self.globe_tiles}).encode()

    def nations_json(self) -> bytes:
        t = self.current_turn()
        if t is None:
            return json.dumps({"turn": None, "nations": []}).encode()
        out = [self.nation_ref(t, n["id"]) for n in t.nations
               if n["alive"] and n["leader"] is not None]
        return json.dumps({"turn": t.turn, "nations": sorted(out, key=lambda n: n["name"].lower())}).encode()

    def rename(self, nid: int, name):
        t = self.current_turn()
        if t is None or not 1 <= nid <= len(t.nations):
            return 404, {"error": f"no nation {nid}"}
        try:
            names.write(self.state_dir, nid, name)
        except ValueError as e:
            return 400, {"error": str(e)}
        return 200, self.nation_ref(t, nid)

    def leader_ref(self, t, r) -> dict:
        """How the page names a leader: "title" in words ("Iceland Chieftain"), and for showing the
        nation as a flag instead, "kind" (their title, "Chieftain") and "nation" ({"id", "name", "flag"},
        or None). Everyone sees the title a player chose."""
        lead = t.leaders[r]
        return {"title": self.title(t, r), "kind": lead["title"],
                "nation": self.nation_ref(t, lead["nation"]) if lead["nation"] else None}

    def title(self, t, r) -> str:
        """A leader as the player page names them: their nation and title, e.g. "Iceland Chieftain"."""
        lead = t.leaders[r]
        return f"{self.nation_name(t, lead['nation'])} {lead['title']}" if lead["nation"] else lead["title"]

    def found_kingdom(self, t, r) -> dict:
        """Whether the segment can found a kingdom now: {"ok", "why": a sentence if not, "title": the
        default title, "max_length"}. orders.check() is the rule; this puts it in the player's words."""
        rules = t.manifest["leader_rules"]
        spec = rules["commands"]["found_kingdom"]
        why = None
        if t.at[r] != t.toward[r]:
            why = "They are on the move. They must be wholly on one tile to settle."
        elif orders.settled_by_others(t, rules, t.leaders[r]["nation"], int(t.at[r])):
            why = "Another people have already settled here."
        return {"ok": why is None, "why": why, "title": spec["default_title"], "max_length": spec["max_title_length"]}

    def moves(self, t, r):
        """[{"direction", "ok"}] for the 8 tiles around the tile the segment counts as being in. Every
        one is a tile orders.move_options() offers; ok is False for one that is all water."""
        at, toward, here = int(t.at[r]), int(t.toward[r]), int(t.tile[r])
        around = dict(orders.neighbours(here))
        return [{"direction": d, "ok": d in around and (around[d] in (at, toward) or bool(self.walkable[around[d]]))}
                for d in DIRECTIONS]

    def own_segment(self, t, r, commands, rules) -> dict:
        lid = int(t.leader[r])
        order = None
        c = commands.get(lid)
        if c and c["type"] == "move":
            to = orders.tile_index(c["to"])
            order = {"type": "move",
                     "direction": next((d for d, i in orders.neighbours(int(t.tile[r])) if i == to), None)}
        elif c and c["type"] == "found_kingdom":
            order = {"type": "found_kingdom", "title": c["title"]}
        can = t.manifest["leader_rules"]["types"][t.leaders[r]["type"]]["commands"]
        out = {"leader": lid, "own": True, **self.leader_ref(t, r),
               "people": round(float(np.asarray(t.people[r]).sum())), "situation": situation(t, r, rules),
               "moving": bool(t.at[r] != t.toward[r]), "order": order, "commands": can}
        if "move" in can:
            out["moves"] = self.moves(t, r)
        if "found_kingdom" in can:
            out["found_kingdom"] = self.found_kingdom(t, r)
        return out

    def foreign_segment(self, t, r, rules) -> dict:
        # All a nation can tell about another's people for now is how many they look; intelligence will
        # add to "known" as technology advances.
        return {"leader": int(t.leader[r]), "own": False, **self.leader_ref(t, r),
                "known": {"size": foreign_size(float(np.asarray(t.people[r]).sum()), rules)}}

    def segment_json(self, t, r, nid, commands, rules) -> dict:
        lat, lon = cs.segment_latlon(int(t.at[r]), int(t.toward[r]), float(t.progress[r]))
        body = (self.own_segment(t, r, commands, rules) if t.leaders[r]["nation"] == nid
                else self.foreign_segment(t, r, rules))
        return {**body, "tile": orders.tile_name(t.tile[r]), "position": [round(float(lat), 4), round(float(lon), 4)]}

    def view_json(self, nid: int):
        """What nation `nid` knows at the current turn, or None if there is no such nation:
        {"turn", "phase", "nation": {"id", "name", "flag"}, "home": the tile of its root leader's segment,
         "terrain_types": [...], "cover_types": [...],
         "tiles": [{"id", "seen": whether it can see the tile now, "terrain": its landforms and "cover":
                    what covers its land (and frozen water), each as indices into terrain_types or
                    cover_types, largest share first}] for every tile it has explored,
         "segments": [...] on the tiles it can see now: its own with "people", "situation", "order" and
                    "moves"; other nations' with what it knows of them,
         "camps": [{"tile", "nation": {"id", "name", "flag"}, "own", "tepees", "settled"}]: one per nation
                    per tile it can see, drawn as that many tepees (or, if any of the nation's segments
                    there is settled, round huts): all of the nation's people there (on the tile
                    or counted as on it while moving) added up, so a camp's size shows roughly how many
                    live there without giving the number}."""
        t = self.current_turn()
        if t is None or not 1 <= nid <= len(t.nations):
            return None
        rules = load_descriptions()
        visible = cs.unpack_tiles(np.asarray(t.visible[nid]))
        explored = np.flatnonzero(cs.unpack_tiles(np.asarray(t.explored[nid])))
        tiles = []
        def largest_first(share):
            return sorted((k for k in range(len(share)) if share[k] > 0), key=lambda k: (-share[k], k))
        for i in explored.tolist():
            tiles.append({"id": orders.tile_name(i), "seen": bool(visible[i]),
                          "terrain": largest_first(self.terrain[:, i]), "cover": largest_first(self.cover[:, i])})
        commands = {c["leader"]: c for c in orders.read_nation(self.state_dir, t.turn, nid)}
        rows = np.flatnonzero(visible[t.tile])
        rows = sorted(rows.tolist(), key=lambda r: (t.leaders[r]["nation"] != nid, r))   # own first
        segments = [self.segment_json(t, r, nid, commands, rules) for r in rows]
        camps = [{**c, "tile": orders.tile_name(c["tile"]), "own": c["nation"] == nid,
                  "nation": self.nation_ref(t, c["nation"]) if c["nation"] else None}
                 for c in campsmod.camps(t, rows, rules["camp"])]
        root = t.nations[nid - 1]["leader"]
        rr = t.row_of().get(root)
        own = [r for r in rows if t.leaders[r]["nation"] == nid]
        home = int(t.tile[rr]) if rr is not None else int(t.tile[own[0]]) if own else None
        return json.dumps({"turn": t.turn, "phase": self.status()["phase"],
                           "nation": self.nation_ref(t, nid),
                           "home": orders.tile_name(home) if home is not None else None,
                           "terrain_types": self.terrain_types, "cover_types": self.cover_types, "tiles": tiles,
                           "segments": segments, "camps": camps}, separators=(",", ":")).encode()

    def order(self, nid: int, body: dict):
        """Turn a direction into a move command, or a title into a found_kingdom command, and give it
        (Game.give_order()). Returns (status, dict)."""
        t = self.current_turn()
        if t is None:
            return 409, {"error": "no game state"}
        lid, direction = body.get("leader"), body.get("direction")
        r = t.row_of().get(lid) if isinstance(lid, int) and not isinstance(lid, bool) else None
        if r is None or t.leaders[r]["nation"] != nid:
            return 400, {"error": "that isn't one of your leaders"}
        command = None
        if "found_kingdom" in body:
            spec = t.manifest["leader_rules"]["commands"]["found_kingdom"]
            title = body["found_kingdom"]
            try:
                title = names.clean(spec["default_title"] if title is None else title, spec["max_title_length"], "title")
            except ValueError as e:
                return 400, {"error": str(e)[0].upper() + str(e)[1:] + "."}
            if not self.found_kingdom(t, r)["ok"]:
                return 400, {"error": self.found_kingdom(t, r)["why"]}
            command = {"type": "found_kingdom", "title": title}
        elif direction is not None:
            to = dict(orders.neighbours(int(t.tile[r]))).get(direction)
            if to is None:
                return 400, {"error": f"there is no way {direction!r} from here"}
            command = {"type": "move", "to": orders.tile_name(to)}
        status, out = self.give_order(nid, body.get("turn"), lid, command)
        if status == 400:   # orders.check()'s reasons name tiles, which a player doesn't know
            return status, {"error": "They can't do that." if command and command["type"] == "found_kingdom"
                            else "They can't go that way."}
        if status != 200:
            return status, out
        commands = {c["leader"]: c for c in out["commands"]}
        return 200, self.segment_json(t, r, nid, commands, load_descriptions())


class Handler(BaseHTTPRequestHandler):
    player: Player = None

    def log_message(self, fmt, *args):   # skip the status polls and the map pieces
        if not (self.path.startswith("/api/status") or self.path.startswith("/globe/") or self.path.startswith("/earth/")):
            super().log_message(fmt, *args)

    def send(self, status: int, body: bytes, ctype: str, cache: bool = False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: str, ctype: str, cache: bool = False):
        try:
            with open(path, "rb") as f:
                self.send(200, f.read(), ctype, cache)
        except FileNotFoundError:
            self.send_error(404)

    def send_json(self, body, status: int = 200):
        self.send(status, body if isinstance(body, bytes) else json.dumps(body).encode(), "application/json")

    def do_GET(self):
        path = urlparse(self.path).path
        p = self.player
        if path in ("/", "/index.html"):
            return self.send_file(os.path.join(STATIC, "index.html"), "text/html; charset=utf-8")
        if path == "/camps.js":
            return self.send_file(os.path.join(SERVER_STATIC, "camps.js"), "text/javascript; charset=utf-8")
        m = re.fullmatch(r"/earth/(\d+)\.jpg", path)
        if m:
            name = p.textures.get(int(m.group(1)))
            return self.send_file(os.path.join(SERVER_DIR, name), "image/jpeg", cache=True) if name else self.send_error(404)
        m = re.fullmatch(r"/globe/(\d{1,2})/(\d{1,4})_(\d{1,4})\.jpg", path)
        if m:
            return self.send_file(os.path.join(GLOBE_TILES, m.group(1), f"{m.group(2)}_{m.group(3)}.jpg"),
                                  "image/jpeg", cache=True)
        if path == "/api/meta":
            return self.send_json(p.meta_json())
        if path == "/api/status":
            return self.send_json(p.status())
        if path == "/api/nations":
            return self.send_json(p.nations_json())
        m = re.fullmatch(r"/api/nation/(\d+)/view", path)
        if m:
            body = p.view_json(int(m.group(1)))
            return self.send_json(body) if body else self.send_json({"error": f"no nation {m.group(1)}"}, 404)
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return self.send_json({"error": "send a JSON object"}, 400)
        p = self.player
        if path == "/api/commit":
            status, body = p.commit(data.get("turn"))
            return self.send_json({k: v for k, v in body.items() if k != "output"}, status)   # no engine log for players
        m = re.fullmatch(r"/api/nation/(\d+)/(name|orders)", path)
        if m:
            nid = int(m.group(1))
            status, body = p.rename(nid, data.get("name")) if m.group(2) == "name" else p.order(nid, data)
            return self.send_json(body, status)
        self.send_error(404)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--state", default=os.path.join(REPO, "state"),
                    help="game state written by engine/engine.py (default: the top-level state/ folder)")
    ap.add_argument("--countries", default=COUNTRIES, help="Natural Earth countries, for nation names (optional)")
    ap.add_argument("--map", default=MAP, help="world_map.json")
    args = ap.parse_args()

    Handler.player = Player(os.path.abspath(args.state), args.countries, args.map)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving on http://{args.host}:{args.port}/  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
