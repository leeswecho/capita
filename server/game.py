"""What every server that lets players act shares: the current turn, its phase, orders and commits.

The control server (control/control.py) and the player server (player/player.py) each make one Game.
Orders are written under the state's lock (cohort_state.state_lock()), which the engine holds for a
whole step, so an order can't land in the middle of one, from either server. A commit runs
`engine/engine.py step` as its own process, so the engine stays the only writer of the turns.
"""
import json
import os
import subprocess
import sys
import threading

import cohort_state as cs
import names
import orders
from convert_map import read_map

SERVER = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(SERVER)
ENGINE = os.path.join(REPO, "engine", "engine.py")
MAP = os.path.join(SERVER, "world_map.json")
COUNTRIES = os.path.join(SERVER, "ne_10m_admin_0_countries.geojson")
ENGINE_BUSY = 3   # engine.py step's exit status when another step is running or the turn has moved on


def load_country_names(path: str = COUNTRIES) -> dict:
    """ISO alpha-3 code -> country name, from the Natural Earth file (the same lookup as server.py's)."""
    out = {"XKX": "Kosovo"}
    try:
        with open(path, encoding="utf-8") as f:
            features = json.load(f)["features"]
    except (OSError, ValueError, KeyError):
        print(f"  (no country names: couldn't read {os.path.basename(path)})", flush=True)
        return out
    for feat in features:
        p = feat["properties"]
        code = p.get("ISO_A3_EH")
        if code and code != "-99" and code not in out:
            out[code] = p.get("NAME") or p.get("ADMIN")
    return out


def load_flag_codes(path: str = COUNTRIES) -> dict:
    """ISO alpha-3 code -> lower-case ISO alpha-2 code, which names a country's flag (the player page
    shows flags from the flag-icons package, which are named by alpha-2 code)."""
    out = {"XKX": "xk"}
    try:
        with open(path, encoding="utf-8") as f:
            features = json.load(f)["features"]
    except (OSError, ValueError, KeyError):
        return out
    for feat in features:
        p = feat["properties"]
        a3, a2 = p.get("ISO_A3_EH"), p.get("ISO_A2_EH")
        if a3 and a3 != "-99" and a2 and a2 != "-99" and a3 not in out:
            out[a3] = a2.lower()
    return out


class Game:
    def __init__(self, state_dir: str, map_path: str = MAP, countries_path: str = COUNTRIES):
        self.state_dir = state_dir
        self.commit_lock = threading.Lock()   # held while a commit of ours runs
        self.country_names = load_country_names(countries_path)
        self.flag_codes = load_flag_codes(countries_path)
        self.turn_cache = None                # the last Turn opened, reused until current.json names another
        self.world = read_map(map_path)
        self.walkable = orders.walkable(self.world)

    def current_turn(self):
        """The current turn, memory-mapped, or None if there is no state."""
        cur = cs.read_current(self.state_dir)
        if cur is None:
            return None
        t = self.turn_cache
        if t is None or os.path.basename(t.path) != cur["dir"]:
            t = self.turn_cache = cs.Turn(self.state_dir, cur["dir"], mmap=True)
        return t

    def nation_name(self, t, nid: int) -> str:
        """The name the nation's player gave it, or its country's name."""
        code = t.nations[nid - 1]["code"]
        return names.read(self.state_dir).get(nid) or self.country_names.get(code) or code

    def nation_ref(self, t, nid: int) -> dict:
        """{"id", "name", "flag"}: the flag is the nation's country's alpha-2 code (None if unknown)."""
        return {"id": nid, "name": self.nation_name(t, nid), "flag": self.flag_codes.get(t.nations[nid - 1]["code"])}

    def status(self) -> dict:
        """The current turn and phase. A commit of ours still starting the engine counts as resolving."""
        ph = cs.read_phase(self.state_dir) or {"turn": None, "phase": None, "since": None}
        if ph["turn"] is not None and self.commit_lock.locked():
            ph["phase"] = cs.RESOLVING
        return ph

    def give_order(self, nid: int, turn, lid, command):
        """Give leader `lid` of nation `nid` a command for turn `turn`, replacing any it had; command None
        takes it back. Returns (HTTP status, dict): the nation's commands, or {"error": ...}."""
        def fail(status, error):
            return status, {"error": error}
        with cs.state_lock(self.state_dir):   # waits out a step; its turn is then over, which is caught below
            ph = cs.read_phase(self.state_dir)
            if ph is None:
                return fail(409, "no game state")
            if ph["phase"] != cs.ORDERS:
                return fail(409, f"turn {ph['turn']} is resolving; commands are closed")
            if turn != ph["turn"]:
                return fail(409, f"that command was for turn {turn}, but it is now turn {ph['turn']}")
            t = self.current_turn()
            if not 1 <= nid <= len(t.nations):
                return fail(404, f"no nation {nid}")
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
            return 200, {"turn": t.turn, "commands": sorted(commands, key=lambda c: c["leader"])}

    def commit(self, turn=None):
        """Commit the turn: run one engine step. With `turn`, only if that is still the current turn.
        Returns (HTTP status, dict)."""
        if not self.commit_lock.acquire(blocking=False):
            return 409, {"error": "the turn is already resolving"}
        try:
            ph = cs.read_phase(self.state_dir)
            if ph is None:
                return 409, {"error": "no game state; run: python engine/engine.py init"}
            if ph["phase"] == cs.RESOLVING:
                return 409, {"error": f"turn {ph['turn']} is already resolving"}
            if turn is not None and turn != ph["turn"]:
                return 409, {"error": f"that commit was for turn {turn}, but it is now turn {ph['turn']}"}
            cmd = [sys.executable, ENGINE, "--state", self.state_dir, "step"]
            if turn is not None:
                cmd += ["--expect-turn", str(turn)]   # checked again under the engine's lock
            run = subprocess.run(cmd, capture_output=True, text=True)
            output = (run.stdout + run.stderr).strip()
            print(output, flush=True)
            if run.returncode == ENGINE_BUSY:
                return 409, {"error": output or "the turn is already resolving"}
            if run.returncode != 0:
                return 500, {"error": "the engine step failed", "output": output}
            ph = cs.read_phase(self.state_dir)
            return 200, {"turn": ph["turn"], "phase": ph["phase"], "output": output}
        finally:
            self.commit_lock.release()
