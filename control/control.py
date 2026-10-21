"""Local web server for the player's control page, kept separate from the globe viewer (server.py).

    python control/control.py [--port 8001] [--host 127.0.0.1] [--state state]

Then open http://localhost:8001/ in a browser. (Paths shown from the repository root; the script
works from any directory.)

The page is where a player enters their commands for the turn. For now it has only "Commit Turn":
the player has entered everything for this turn, so the engine advances to the next one. Committing
runs `engine/engine.py step` as its own process, so the engine stays the only writer of the state;
the viewer picks up the new turn by itself within about two seconds.

Each turn has a phase (cohort_state.read_phase()), which the engine records: "orders" while the turn
accepts commands, and "resolving" while the engine applies them and a year's passage of time to
compute the next turn. Commands, and commits, are refused while the turn is resolving, however the
step was started (from this page or from a terminal).

Endpoints:
    /                      the control page (static/index.html)
    /api/status            {"turn": t, "phase": p, "since": s}: the state's current turn and its
                           phase, "orders" or "resolving" (both null if there is no state), and when
                           that phase began (JSON)
    POST /api/commit       commit the turn: advance the engine one step and return
                           {"turn": t, "phase": p, "output": "..."}; 409 if there is no state or the
                           turn is already resolving, 500 with the engine's output if the step fails (JSON)
"""
import argparse
import json
import os
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

import cohort_state as cs  # noqa: E402


class Control:
    """Runs the engine for commits, one at a time."""

    def __init__(self, state_dir: str):
        self.state_dir = state_dir
        self.lock = threading.Lock()   # held while a commit's engine step runs

    def status(self) -> dict:
        """The current turn and phase. A commit of ours still starting the engine counts as resolving."""
        ph = cs.read_phase(self.state_dir) or {"turn": None, "phase": None, "since": None}
        if ph["turn"] is not None and self.lock.locked():
            ph["phase"] = cs.RESOLVING
        return ph

    def status_json(self) -> bytes:
        return json.dumps(self.status()).encode()

    def commit(self):
        """Advance one turn. Returns (status, JSON body)."""
        if not self.lock.acquire(blocking=False):
            return 409, json.dumps({"error": "the turn is already resolving"}).encode()
        try:
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
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/commit":
            status, body = self.control.commit()
            return self.send_json(body, status)
        self.send_error(404)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--state", default=os.path.join(REPO, "state"),
                    help="game state written by engine/engine.py (default: the top-level state/ folder)")
    args = ap.parse_args()

    Handler.control = Control(os.path.abspath(args.state))
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
