"""Agent Keylight daemon.

Shows AI-agent status (Claude Code, Codex, any CLI agent) on whatever
keyboard light the PC has. Two detection sources, merged by priority:

  1. state files (one per agent session, written by lightctl.py from
     Claude Code hooks / Codex notify / manual calls)
  2. watchers.py -- hook-less detection of everything else (the
     Codex/ChatGPT app, Gemini, opencode, Copilot, Cursor, ...) via
     transcript-file activity and process CPU

and renders the highest-priority state through the best available backend
-- see the `backends` package for hardware support and `states.py` for
the patterns.

When no agent is active the lights are released back to normal, and once
no agent process is around either, the daemon exits entirely -- nothing
runs at boot or while you're not using an agent. It comes back via
lightctl.py (any hook) or trigger.py (the installer's periodic task,
which starts it when a hook-less agent app opens).

Env overrides:
  AGENT_KEYLIGHT_DIR      state directory (default %LOCALAPPDATA%/agent-keylight)
  AGENT_KEYLIGHT_BACKEND  openrgb | indicator | sendinput  (default: auto)
  AGENT_KEYLIGHT_LED      caps | num | scroll  (default: caps)
"""

import os
import socket
import sys
import time
from pathlib import Path

import watchers
from backends import pick_backend
from config import BASE_DIR
from states import PRIORITY, TICK

HERE = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "sessions"

STALE_SECS = 6 * 3600  # forget sessions that crashed without cleaning up
SINGLETON_PORT = 51765
IDLE_EXIT_SECS = 10 * 60  # exit when no session has been active this long
CODEX_HEAL_SECS = 5 * 60  # how often to re-check the Codex notify chain


def pid_alive(pid):
    """Best-effort liveness check; False only when the pid is surely gone."""
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # PROCESS_QUERY_LIMITED_INFORMATION
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: alive
        try:
            code = ctypes.c_ulong()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        pass
    return True


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
            parts = f.read_text(encoding="utf-8", errors="ignore").split()
        except OSError:
            continue
        if not parts:
            continue
        s = parts[0].lower()
        # lightctl records the owning agent's pid; a dead owner means the
        # IDE/CLI was closed without a SessionEnd hook -- drop its light
        if len(parts) > 1 and parts[1].isdigit() and not pid_alive(int(parts[1])):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        if s in PRIORITY and (best is None or PRIORITY[s] > PRIORITY[best]):
            best = s
    return best


def heal_codex_chain():
    """Re-chain Codex notify if the Codex app rewrote config.toml over it.

    The Codex desktop app periodically rewrites ~/.codex/config.toml (e.g.
    when its computer-use runtime updates) and restores its own notify
    command, silently unhooking the keylight. codex_notify_chain.json only
    exists while our wiring is installed, so uninstalled setups are left
    alone; install_codex_notify is a no-op when the chain is intact.
    """
    if not (HERE / "codex_notify_chain.json").exists():
        return
    from installer import install_codex_notify
    install_codex_notify()


def main():
    # single-instance guard: hold a localhost port for the process lifetime
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        guard.bind(("127.0.0.1", SINGLETON_PORT))
        guard.listen(5)  # lightctl probes this port to see if we're alive
        guard.setblocking(False)
    except OSError:
        return 0  # another daemon already running

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    backend = None
    tick = 0
    idle_since = None
    next_heal = 0

    while True:
        # drain liveness probes so the accept backlog never fills up
        try:
            while True:
                conn, _ = guard.accept()
                conn.close()
        except OSError:
            pass

        if time.time() >= next_heal:
            next_heal = time.time() + CODEX_HEAL_SECS
            try:
                heal_codex_chain()
            except Exception:
                pass

        state = aggregate_state()
        wstate = watchers.poll()  # hook-less agents (Codex app, Gemini, ...)
        if wstate and (state is None or PRIORITY[wstate] > PRIORITY[state]):
            state = wstate

        if state is None:
            if backend is not None:
                try:
                    backend.release()
                except Exception:
                    backend.drop()
            if idle_since is None:
                idle_since = time.time()
            elif (not watchers.procs_alive()
                    and time.time() - idle_since > IDLE_EXIT_SECS):
                # no sessions AND no agent apps open: nothing left to show;
                # lightctl (hooks) or trigger.py bring us back on demand
                return 0
            time.sleep(TICK * 2)
            continue
        idle_since = None

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
