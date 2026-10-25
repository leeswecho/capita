"""Local web server for the player's control page, kept separate from the globe viewer (server.py).

    python control/control.py [--port 8001] [--host 127.0.0.1] [--state state]

Then open http://localhost:8001/ in a browser. (Paths shown from the repository root; the script
works from any directory.)

The page is where a player enters their commands for the turn. It lists the leaders of the player's
nation, and each can be ordered to move their segment to a neighbouring tile; the commands are saved as
the nation's orders for the turn (server/orders.py) and carried out when the turn resolves. "Commit
Turn" means the player has entered everything for this turn, so the engine advances to the next one.
Committing runs `engine/engine.py step` as its own process, so the engine stays the only writer of the
state; the viewer picks up the new turn by itself within about two seconds.

The player first chooses the nation they control (the page remembers it, and ?nation=AUS in the URL
chooses one); the player is that nation's root leader. The page then shows what each of the nation's
segments (the people directly under one of its leaders) produced and consumed in the year that ended
with the current turn, and how many of their people died.

Each turn has a phase (cohort_state.read_phase()), which the engine records: "orders" while the turn
accepts commands, and "resolving" while the engine applies them and a year's passage of time to
compute the next turn. Commands, and commits, are refused while the turn is resolving, however the
step was started (from this page or from a terminal).

Endpoints:
    /                      the control page (static/index.html)
    /api/status            {"turn": t, "phase": p, "since": s}: the state's current turn and its
                           phase, "orders" or "resolving" (both null if there is no state), and when
                           that phase began (JSON)
    /api/nations           {"turn": t, "nations": [{"id", "code", "name", "alive", "leader"}, ...]}: the
                           current turn's nations, each with its root leader {"id", "name", "type"} (JSON)
    /api/nation/<id>/leaders  the nation's leaders, root first, each with its segment's position and
                           people and the command it has been given this turn (or null) (JSON)
    /api/leader/<id>/moves  where a leader's segment can be sent: each tile with its direction, land,
                           main cover, owner, whether it can be walked onto, and the way there (the tile it
                           passes first, if any, and the km) (JSON)
    POST /api/nation/<id>/orders  {"turn": t, "leader": id, "command": {"type": "move", "to": tile id}}
                           gives a leader a command for turn t, replacing any it had; "command": null
                           takes it back. Returns {"turn": t, "commands": [...]}, the nation's commands;
                           400 if the command isn't valid, 409 if turn t isn't accepting orders (JSON)
    /api/nation/<id>/production  what each of the nation's segments made and consumed in the year that
                           ended with the current turn: per product, the amount made, the labor hours
                           behind it, the yearly maximum of its tile (shared by every segment there),
                           its people's need and what they consumed, with totals; the REPORT_ROWS most
                           productive segments (JSON)
    /api/nation/<id>/deaths  how many people died of each cause in each of the nation's segments in the
                           year that ended with the current turn, with totals; the REPORT_ROWS segments
                           with the most deaths (JSON)
    POST /api/commit       commit the turn: advance the engine one step and return
                           {"turn": t, "phase": p, "output": "..."}; 409 if there is no state or the
                           turn is already resolving, 500 with the engine's output if the step fails (JSON)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
STATIC = os.path.join(HERE, "static")
ENGINE = os.path.join(REPO, "engine", "engine.py")
sys.path.insert(0, os.path.join(REPO, "server"))   # cohort_state.py lives with the viewer, which uses it too

import numpy as np  # noqa: E402

import cohort_state as cs  # noqa: E402
import grid  # noqa: E402
import orders  # noqa: E402
from convert_map import read_map  # noqa: E402

COUNTRIES = os.path.join(REPO, "server", "ne_10m_admin_0_countries.geojson")
MAP = os.path.join(REPO, "server", "world_map.json")
REPORT_ROWS = 100   # segments listed per nation; a full-world nation can have thousands


def load_country_names(path: str) -> dict:
    """ISO alpha-3 code -> country name, from the Natural Earth file (the same lookup as server.py's)."""
    names = {"XKX": "Kosovo"}
    try:
        with open(path, encoding="utf-8") as f:
            features = json.load(f)["features"]
    except (OSError, ValueError, KeyError):
        print(f"  (no country names: couldn't read {os.path.basename(path)})", flush=True)
        return names
    for feat in features:
        p = feat["properties"]
        code = p.get("ISO_A3_EH")
        if code and code != "-99" and code not in names:
            names[code] = p.get("NAME") or p.get("ADMIN")
    return names


class Control:
    """Runs the engine for commits, one at a time, and reports the current turn to the page."""

    def __init__(self, state_dir: str, countries_path: str, map_path: str):
        self.state_dir = state_dir
        self.commit_lock = threading.Lock()   # held while a commit of ours runs
        self.lock = threading.Lock()   # held while the engine step runs or orders are written, never both
        self.country_names = load_country_names(countries_path)
        self.turn_cache = None         # the last Turn opened, reused until current.json names another
        world = read_map(map_path)
        self.walkable = orders.walkable(world)
        self.water = np.asarray(world.terrain[world.terrain_types.index("water")], dtype=np.uint8)
        self.cover_types = world.cover_types
        self.cover = np.stack([np.asarray(c, dtype=np.uint8) for c in world.cover])   # [cover types, tiles]
        self.rivers_at = world.rivers_at

    def current_turn(self):
        """The current turn, memory-mapped, or None if there is no state."""
        cur = cs.read_current(self.state_dir)
        if cur is None:
            return None
        t = self.turn_cache
        if t is None or os.path.basename(t.path) != cur["dir"]:
            t = self.turn_cache = cs.Turn(self.state_dir, cur["dir"], mmap=True)
        return t

    def nations_json(self) -> bytes:
        t = self.current_turn()
        if t is None:
            return json.dumps({"turn": None, "nations": []}).encode()
        rows = t.row_of()
        out = []
        for n in t.nations:
            lead = t.leaders[rows[n["leader"]]] if n["leader"] is not None else None
            out.append({"id": n["id"], "code": n["code"], "name": self.country_names.get(n["code"]) or n["code"],
                        "alive": n["alive"],
                        "leader": {"id": lead["id"], "name": lead["name"], "type": lead["type"]} if lead else None})
        return json.dumps({"turn": t.turn, "nations": out}).encode()

    def segment_refs(self, t, rows):
        """{"leader", "name", "type", "tile", "moving"} for each row, as the reports list segments."""
        return [{"leader": t.leaders[r]["id"], "name": t.leaders[r]["name"], "type": t.leaders[r]["type"],
                 "tile": grid.tile_id(*grid.corner(*divmod(int(t.tile[r]), grid.WIDTH))),
                 "moving": bool(t.at[r] != t.toward[r])} for r in rows]

    def nation_ref(self, t, nid: int):
        n = t.nations[nid - 1]
        return {"id": nid, "code": n["code"], "name": self.country_names.get(n["code"]) or n["code"]}

    def leaders_json(self, nid: int):
        """JSON for /api/nation/<id>/leaders, or None if there is no such nation."""
        t = self.current_turn()
        if t is None or not 1 <= nid <= len(t.nations):
            return None
        root = t.nations[nid - 1]["leader"]
        rows = np.flatnonzero(t.nation == nid)
        rows = sorted(rows.tolist(), key=lambda r: (t.leaders[r]["id"] != root, r))   # root first, then by id
        names = {lead["id"]: lead["name"] for lead in t.leaders}
        commands = {c["leader"]: c for c in orders.read_nation(self.state_dir, t.turn, nid)}
        out = []
        for r in rows:
            lead = t.leaders[r]
            at, toward = int(t.at[r]), int(t.toward[r])
            out.append({"id": lead["id"], "name": lead["name"], "type": lead["type"], "root": lead["id"] == root,
                        "parent": {"id": lead["parent"], "name": names.get(lead["parent"])} if lead["parent"] else None,
                        "tile": orders.tile_name(t.tile[r]), "at": orders.tile_name(at),
                        "toward": orders.tile_name(toward), "moving": at != toward, "progress": float(t.progress[r]),
                        "destination": orders.tile_name(t.destination[r]),
                        "people": float(np.asarray(t.people[r]).sum()), "command": commands.get(lead["id"])})
        return json.dumps({"turn": t.turn, "phase": self.status()["phase"], "nation": self.nation_ref(t, nid),
                           "leaders": out}).encode()

    def moves_json(self, lid: int):
        """JSON for /api/leader/<id>/moves, or None if there is no such leader."""
        t = self.current_turn()
        r = t.row_of().get(lid) if t is not None else None
        if r is None:
            return None
        lead = t.leaders[r]
        at, toward, progress = int(t.at[r]), int(t.toward[r]), float(t.progress[r])
        options = []
        for d, i in orders.move_options(at, toward, progress):
            owner = int(t.owner[i])
            plan = orders.plan_move(at, toward, progress, i)
            cover = self.cover[:, i]
            options.append({"direction": d, "tile": orders.tile_name(i), "land": 100 - int(self.water[i]),
                            "cover": self.cover_types[int(np.argmax(cover))] if cover.any() else None,
                            "river": orders.tile_name(i) in self.rivers_at,
                            "owner": t.nations[owner - 1]["code"] if owner else None,
                            "people": float(sum(np.asarray(t.people[x]).sum() for x in t.rows_at(i))),
                            "walkable": bool(i in (at, toward) or self.walkable[i]),
                            "via": orders.tile_name(plan["via"]) if plan["via"] is not None else None,
                            "km": plan["km"]})
        return json.dumps({"turn": t.turn, "leader": {"id": lid, "name": lead["name"], "nation": lead["nation"]},
                           "from": orders.tile_name(at), "moving": at != toward,
                           "toward": orders.tile_name(toward), "progress": progress,
                           "destination": orders.tile_name(t.destination[r]), "options": options}).encode()

    def give_order(self, nid: int, body: dict):
        """Give (or take back) one leader's command for the current turn. Returns (status, JSON body)."""
        def fail(status, error):
            return status, json.dumps({"error": error}).encode()
        with self.lock:   # waits out a commit in progress; its turn is then over, which the check below catches
            ph = cs.read_phase(self.state_dir)
            if ph is None:
                return fail(409, "no game state")
            if ph["phase"] != cs.ORDERS:
                return fail(409, f"turn {ph['turn']} is resolving; commands are closed")
            if body.get("turn") != ph["turn"]:
                return fail(409, f"that command was for turn {body.get('turn')}, but it is now turn {ph['turn']}")
            t = self.current_turn()
            if not 1 <= nid <= len(t.nations):
                return fail(404, f"no nation {nid}")
            lid, command = body.get("leader"), body.get("command")
            if not isinstance(lid, int) or isinstance(lid, bool):
                return fail(400, "leader must be a leader id")
            commands = [c for c in orders.read_nation(self.state_dir, t.turn, nid) if c["leader"] != lid]
            if command is not None:
                if not isinstance(command, dict):
                    return fail(400, "command must be an object or null")
                command = {"leader": lid, "type": command.get("type"), "to": command.get("to")}
                why = orders.check(t, t.row_of(), nid, command, self.walkable)
                if why:
                    return fail(400, why)
                commands.append(command)
            orders.write_nation(self.state_dir, t.turn, nid, commands)
            return 200, json.dumps({"turn": t.turn, "commands": sorted(commands, key=lambda c: c["leader"])}).encode()

    def deaths_json(self, nid: int):
        """JSON for /api/nation/<id>/deaths, or None if there is no such nation."""
        t = self.current_turn()
        if t is None or not 1 <= nid <= len(t.nations):
            return None
        rows = np.flatnonzero(t.nation == nid)   # the nation's segments
        people = np.asarray(t.people[rows]).sum(axis=1)
        deaths = t.deaths[rows]
        order = np.argsort(-deaths.sum(axis=1), kind="stable")[:REPORT_ROWS] if len(rows) else rows
        return json.dumps({
            "turn": t.turn,
            "nation": self.nation_ref(t, nid),
            "causes": t.causes,
            "segment_count": len(rows),
            "totals": {"people": float(people.sum()), "deaths": deaths.sum(axis=0).tolist()},
            "segments": [{**ref, "people": float(people[i]), "deaths": deaths[i].tolist()}
                         for ref, i in zip(self.segment_refs(t, rows[order]), order)],
        }).encode()

    def production_json(self, nid: int):
        """JSON for /api/nation/<id>/production, or None if there is no such nation."""
        t = self.current_turn()
        if t is None or not 1 <= nid <= len(t.nations):
            return None
        rows = np.flatnonzero(t.nation == nid)   # the nation's segments
        people = np.asarray(t.people[rows]).sum(axis=1)
        made, labor, top = t.production[rows], t.labor[rows], t.production_max[rows]
        need, consumed = t.need[rows], t.consumed[rows]
        order = np.argsort(-made[:, 0], kind="stable")[:REPORT_ROWS] if len(rows) else rows
        # A tile's maximum is shared by every segment on it, so the total counts each tile once.
        _, first = np.unique(t.tile[rows], return_index=True)
        return json.dumps({
            "turn": t.turn,
            "nation": self.nation_ref(t, nid),
            "products": t.products,
            "owned_tiles": int(np.count_nonzero(t.owner == nid)),
            "segment_count": len(rows),
            "tile_count": len(first),
            "totals": {"people": float(people.sum()), "produced": made.sum(axis=0).tolist(),
                       "labor_hours": labor.sum(axis=0).tolist(), "max": top[first].sum(axis=0).tolist(),
                       "need": need.sum(axis=0).tolist(), "consumed": consumed.sum(axis=0).tolist(),
                       "short_segments": np.count_nonzero(consumed < need * (1 - 1e-9), axis=0).tolist()},
            "segments": [{**ref, "people": float(people[i]), "produced": made[i].tolist(),
                          "labor_hours": labor[i].tolist(), "max": top[i].tolist(),
                          "need": need[i].tolist(), "consumed": consumed[i].tolist()}
                         for ref, i in zip(self.segment_refs(t, rows[order]), order)],
        }).encode()

    def status(self) -> dict:
        """The current turn and phase. A commit of ours still starting the engine counts as resolving."""
        ph = cs.read_phase(self.state_dir) or {"turn": None, "phase": None, "since": None}
        if ph["turn"] is not None and self.commit_lock.locked():
            ph["phase"] = cs.RESOLVING
        return ph

    def status_json(self) -> bytes:
        return json.dumps(self.status()).encode()

    def commit(self):
        """Advance one turn. Returns (status, JSON body)."""
        if not self.commit_lock.acquire(blocking=False):
            return 409, json.dumps({"error": "the turn is already resolving"}).encode()
        try:
            self.lock.acquire()   # waits for an orders write to finish
            ph = cs.read_phase(self.state_dir)
            if ph is None:
                return 409, json.dumps({"error": "no game state; run: python engine/engine.py init"}).encode()
            if ph["phase"] == cs.RESOLVING:
                return 409, json.dumps({"error": f"turn {ph['turn']} is already resolving"}).encode()
            run = subprocess.run([sys.executable, ENGINE, "--state", self.state_dir, "step"],
                                 capture_output=True, text=True)
            output = (run.stdout + run.stderr).strip()
            print(output, flush=True)
            if run.returncode != 0:
                return 500, json.dumps({"error": "the engine step failed", "output": output}).encode()
            ph = cs.read_phase(self.state_dir)
            return 200, json.dumps({"turn": ph["turn"], "phase": ph["phase"], "output": output}).encode()
        finally:
            self.lock.release()
            self.commit_lock.release()


class Handler(BaseHTTPRequestHandler):
    control: Control = None

    def log_message(self, fmt, *args):
        if not self.path.startswith("/api/status"):   # the page polls it; keep the console readable
            super().log_message(fmt, *args)

    def send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, body: bytes, status: int = 200):
        self.send(status, body, "application/json")

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(os.path.join(STATIC, "index.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/status":
            return self.send_json(self.control.status_json())
        if path == "/api/nations":
            return self.send_json(self.control.nations_json())
        m = re.fullmatch(r"/api/nation/(\d+)/(production|deaths|leaders)", path)
        if m:
            report = {"production": self.control.production_json, "deaths": self.control.deaths_json,
                      "leaders": self.control.leaders_json}[m.group(2)]
            body = report(int(m.group(1)))
            if body is None:
                return self.send_json(json.dumps({"error": f"no nation {m.group(1)}"}).encode(), 404)
            return self.send_json(body)
        m = re.fullmatch(r"/api/leader/(\d+)/moves", path)
        if m:
            body = self.control.moves_json(int(m.group(1)))
            if body is None:
                return self.send_json(json.dumps({"error": f"no leader {m.group(1)}"}).encode(), 404)
            return self.send_json(body)
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/commit":
            status, body = self.control.commit()
            return self.send_json(body, status)
        m = re.fullmatch(r"/api/nation/(\d+)/orders", path)
        if m:
            try:
                data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")
            except ValueError:
                data = None
            if not isinstance(data, dict):
                return self.send_json(json.dumps({"error": "send a JSON object"}).encode(), 400)
            status, body = self.control.give_order(int(m.group(1)), data)
            return self.send_json(body, status)
        self.send_error(404)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--state", default=os.path.join(REPO, "state"),
                    help="game state written by engine/engine.py (default: the top-level state/ folder)")
    ap.add_argument("--countries", default=COUNTRIES, help="Natural Earth countries, for nation names (optional)")
    ap.add_argument("--map", default=MAP, help="world_map.json, for where segments can move")
    args = ap.parse_args()

    Handler.control = Control(os.path.abspath(args.state), args.countries, args.map)
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
