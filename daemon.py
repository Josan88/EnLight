"""Agent Keylight daemon.

Shows AI-agent status (Claude Code, Codex, any CLI agent) on whatever
keyboard light the PC has. Watches a small state directory (one file per
agent session, written by lightctl.py) and renders the highest-priority
state through the best available backend -- see the `backends` package for
hardware support and `states.py` for the patterns.

When no agent session is active the lights are released back to normal.

Env overrides:
  AGENT_KEYLIGHT_DIR      state directory (default %LOCALAPPDATA%/agent-keylight)
  AGENT_KEYLIGHT_BACKEND  openrgb | indicator | sendinput  (default: auto)
  AGENT_KEYLIGHT_LED      caps | num | scroll  (default: caps)
"""

import socket
import sys
import time

from backends import pick_backend
from config import BASE_DIR
from states import PRIORITY, TICK

STATE_DIR = BASE_DIR / "sessions"

STALE_SECS = 6 * 3600  # forget sessions that crashed without cleaning up
SINGLETON_PORT = 51765


def aggregate_state():
    """Highest-priority state across all live session files, or None."""
    best = None
    now = time.time()
    try:
        files = list(STATE_DIR.glob("*.state"))
    except OSError:
        return None
    for f in files:
        try:
            if now - f.stat().st_mtime > STALE_SECS:
                f.unlink(missing_ok=True)
                continue
            s = f.read_text(encoding="utf-8", errors="ignore").strip().lower()
        except OSError:
            continue
        if s in PRIORITY and (best is None or PRIORITY[s] > PRIORITY[best]):
            best = s
    return best


def main():
    # single-instance guard: hold a localhost port for the process lifetime
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        guard.bind(("127.0.0.1", SINGLETON_PORT))
        guard.listen(1)  # makes the daemon visible to `Get-NetTCPConnection`
    except OSError:
        return 0  # another daemon already running

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    backend = None
    tick = 0

    while True:
        state = aggregate_state()

        if state is None:
            if backend is not None:
                try:
                    backend.release()
                except Exception:
                    backend.drop()
            time.sleep(TICK * 2)
            continue

        if backend is None:
            backend = pick_backend()
            if backend is None:
                time.sleep(5)
                continue

        try:
            backend.show(state, tick)
        except Exception:
            backend.drop()
            backend = None
            time.sleep(3)

        tick += 1
        time.sleep(TICK)


if __name__ == "__main__":
    sys.exit(main())
