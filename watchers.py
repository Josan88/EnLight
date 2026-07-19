"""Hook-less agent detection (Codex/ChatGPT app, Gemini, opencode, ...).

Claude Code reports precise states through hooks, and Codex reports turn
ends through notify, but most agentic tools expose no integration at all.
This module lets the daemon detect them anyway, using two generic signals:

  file activity   agents stream their transcript/log to disk while they
                  work (e.g. ~/.codex/sessions rollouts); a fresh write
                  means "running". This also covers the Codex/ChatGPT
                  desktop app, which shares ~/.codex with the CLI.
  process CPU     for native-binary CLIs, a busy process means "running".
                  Skipped for Electron apps (always busy rendering) and
                  node/python-hosted CLIs (their process name is just
                  node/python, so they can't be told apart).

After activity ends a tool shows "stopped" while its process is still
alive; when the process can't be identified (node-hosted CLIs), stopped
is held for STOP_HOLD_SECS instead. Then the light is released.

Watcher states merge with hook states at normal priority, so hook-driven
"waiting"/"error" still win and a notify "stopped" coexists with a
watcher "running" (running is higher priority while the turn lasts).

Extra tools: add  "watch": {"mytool": ["proc-name", ...]}  to config.json
to detect those process names by CPU activity.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lightctl  # noqa: E402  (_proc_table / _norm_name)
from config import load_config  # noqa: E402
from states import PRIORITY  # noqa: E402

HOME = Path.home()

POLL_SECS = 2.0          # min interval between scans (daemon ticks faster)
ACTIVE_SECS = 30         # file write within this window => running
CPU_BUSY = 0.03          # fraction of one core that counts as "working"
STOP_HOLD_SECS = 15 * 60  # stopped-light hold when no process is identifiable


def _codex_home():
    return Path(os.environ.get("CODEX_HOME") or HOME / ".codex")


class Tool:
    """One watched agent: transcript globs and/or process names."""

    def __init__(self, name, base=None, patterns=(), cpu=(), alive=()):
        self.name = name
        self.base = base              # dir (or callable) the patterns are under
        self.patterns = patterns      # glob patterns, strftime-expanded
        self.cpu = set(cpu)           # process names safe to CPU-sample
        self.alive = set(alive) | self.cpu  # names that count as "still open"
        self.seen_active = False
        self.proc_seen = False
        self.last_active = 0.0
        self._cpu_prev = {}           # pid -> (wall time, cpu seconds)

    def _newest_mtime(self, now):
        if self.base is None or not self.patterns:
            return 0.0
        base = self.base() if callable(self.base) else Path(self.base)
        newest = 0.0
        for pat in self.patterns:
            # date-stamped patterns: check today and yesterday, so a turn
            # crossing midnight keeps its rollout file in view
            days = (now, now - 86400) if "%" in pat else (now,)
            for day in days:
                try:
                    hits = base.glob(time.strftime(pat, time.localtime(day)))
                except (OSError, ValueError):
                    continue
                for f in hits:
                    try:
                        m = f.stat().st_mtime
                    except OSError:
                        continue
                    if m > newest:
                        newest = m
        return min(newest, now)  # clamp files stamped in the future

    def _cpu_busy(self, now, pids, cpu_seconds):
        prev, self._cpu_prev = self._cpu_prev, {}
        busy = False
        for pid in pids:
            sec = cpu_seconds(pid)
            if sec is None:
                continue
            self._cpu_prev[pid] = (now, sec)
            before = prev.get(pid)
            if (before and now - before[0] > 0.5
                    and (sec - before[1]) / (now - before[0]) >= CPU_BUSY):
                busy = True
        return busy

    def poll(self, now, names, cpu_seconds):
        """names: {pid: normalized exe name} for every process."""
        alive_pids = [p for p, n in names.items() if n in self.alive]
        if alive_pids:
            self.proc_seen = True
        active = now - self._newest_mtime(now) <= ACTIVE_SECS
        if not active and self.cpu:
            cpu_pids = [p for p, n in names.items() if n in self.cpu]
            active = self._cpu_busy(now, cpu_pids, cpu_seconds)
        if active:
            self.seen_active = True
            self.last_active = now
            return "running"
        if not self.seen_active:
            return None
        if self.proc_seen:
            if alive_pids:
                return "stopped"       # done, tool still open: needs a look
        elif now - self.last_active <= STOP_HOLD_SECS:
            return "stopped"           # process unidentifiable: timed hold
        self.seen_active = False       # tool closed / hold expired: release
        return None


# Codex/ChatGPT desktop-app tool calls that pop up a blocking approval
# dialog with no notify/hook signal (verified against a live rollout: the
# dialog appears as an unresolved function_call, Codex's `notify` fires
# only on turn-complete so it never reports this moment on its own).
APPROVAL_FUNCS = {"request_permissions"}
APPROVAL_TAIL_BYTES = 32768
APPROVAL_WINDOW_SECS = 30 * 60  # how far back to look for a live prompt


class CodexTool(Tool):
    """Adds approval-prompt detection on top of file-activity/CPU checks.

    A pending approval leaves the rollout's last function_call unresolved
    (no later function_call_output with a matching call_id) -- the file
    goes quiet while the dialog sits there, which the base class would
    otherwise read as "stopped" once ACTIVE_SECS elapses.
    """

    def _recent_session_files(self, now):
        """Every rollout touched recently -- NOT just the single newest one.

        Several Codex sessions can be live at once (e.g. the ChatGPT app
        plus an unrelated Codex-companion process backing a coding-agent
        session), each writing its own file; picking only the newest by
        mtime can miss the file that actually holds a pending approval.
        """
        base = self.base() if callable(self.base) else Path(self.base)
        files = []
        for pat in self.patterns:
            if "sessions" not in pat:
                continue
            days = (now, now - 86400) if "%" in pat else (now,)
            for day in days:
                try:
                    hits = base.glob(time.strftime(pat, time.localtime(day)))
                except (OSError, ValueError):
                    continue
                for f in hits:
                    try:
                        m = f.stat().st_mtime
                    except OSError:
                        continue
                    if now - m <= APPROVAL_WINDOW_SECS:
                        files.append(f)
        return files

    def _file_has_pending_approval(self, f):
        try:
            with open(f, "rb") as fh:
                fh.seek(0, 2)
                fh.seek(max(0, fh.tell() - APPROVAL_TAIL_BYTES))
                tail = fh.read().decode("utf-8", "ignore")
        except OSError:
            return False
        pending = None
        resolved = set()
        for line in tail.splitlines():
            if '"function_call"' not in line and '"function_call_output"' not in line:
                continue
            try:
                payload = json.loads(line)["payload"]
            except Exception:
                continue
            ptype = payload.get("type")
            if ptype == "function_call_output":
                resolved.add(payload.get("call_id"))
            elif ptype == "function_call" and payload.get("name") in APPROVAL_FUNCS:
                pending = payload.get("call_id")
        return pending is not None and pending not in resolved

    def _pending_approval(self, now):
        return any(self._file_has_pending_approval(f)
                   for f in self._recent_session_files(now))

    def poll(self, now, names, cpu_seconds):
        state = super().poll(now, names, cpu_seconds)
        alive_pids = [p for p, n in names.items() if n in self.alive]
        if alive_pids and self._pending_approval(now):
            self.seen_active = True
            self.last_active = now
            return "waiting"
        return state


def _build():
    tools = [
        # Codex CLI and the Codex/ChatGPT desktop app both stream turn
        # rollouts under CODEX_HOME/sessions; CPU-sampling is unsafe here
        # (the app's Electron shell can also be named codex.exe)
        CodexTool("codex", _codex_home,
                  ("sessions/%Y/%m/%d/*.jsonl", "history.jsonl"),
                  alive=("codex", "codex-tui", "codex-exec", "chatgpt")),
        # node-hosted (process is just node.exe): file activity only
        Tool("gemini", lambda: HOME / ".gemini", ("tmp/*/logs.json",)),
        Tool("copilot", lambda: HOME / ".copilot",
             ("history-session-state/*.json", "logs/*.log")),
        # native binaries: CPU activity works
        Tool("opencode", lambda: HOME / ".local/share/opencode",
             ("log/*.log",), cpu=("opencode",)),
        Tool("cursor-agent", lambda: HOME / ".cursor",
             ("chats/*/*",), cpu=("cursor-agent",)),
        Tool("hermes", cpu=("hermes",)),
    ]
    extra = load_config().get("watch") or {}
    for name, procs in extra.items():
        if isinstance(procs, list):
            procs = [lightctl._norm_name(str(p)) for p in procs if p]
            if procs:
                tools.append(Tool(str(name), cpu=procs))
    return tools


if sys.platform == "win32":
    def _make_cpu_fn():
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        def cpu_seconds(pid):
            # PROCESS_QUERY_LIMITED_INFORMATION
            h = k32.OpenProcess(0x1000, False, pid)
            if not h:
                return None
            try:
                t = (ctypes.c_ulonglong * 4)()  # creation/exit/kernel/user
                if not k32.GetProcessTimes(
                        h, ctypes.byref(t, 0), ctypes.byref(t, 8),
                        ctypes.byref(t, 16), ctypes.byref(t, 24)):
                    return None
                return (t[2] + t[3]) / 1e7  # FILETIME is 100 ns units
            finally:
                k32.CloseHandle(h)
        return cpu_seconds
else:
    def _parse_ps_time(s):
        """[[dd-]hh:]mm:ss cumulative CPU time -> seconds."""
        days = 0
        if "-" in s:
            d, _, s = s.partition("-")
            days = int(d)
        total = 0.0
        for part in s.split(":"):
            total = total * 60 + float(part)
        return total + days * 86400

    def _make_cpu_fn():
        cache = {}
        filled = []

        def cpu_seconds(pid):
            if not filled:  # one ps call per poll, only when needed
                filled.append(True)
                try:
                    out = subprocess.run(
                        ["ps", "-Ao", "pid=,time="],
                        capture_output=True, text=True, timeout=5).stdout
                    for line in out.splitlines():
                        parts = line.split()
                        if len(parts) == 2 and parts[0].isdigit():
                            try:
                                cache[int(parts[0])] = _parse_ps_time(parts[1])
                            except ValueError:
                                pass
                except Exception:
                    pass
            return cache.get(pid)
        return cpu_seconds


_tools = None
_last_poll = 0.0
_state = None
_alive = False


def poll():
    """Best watcher state across all tools, or None. Throttled internally."""
    global _tools, _last_poll, _state, _alive
    now = time.time()
    if now - _last_poll < POLL_SECS:
        return _state
    _last_poll = now
    if _tools is None:
        _tools = _build()

    try:
        table = lightctl._proc_table()
    except Exception:
        table = {}
    names = {}
    for pid, (_, name) in table.items():
        names[pid] = lightctl._norm_name(name)

    cpu_fn = _make_cpu_fn()
    best = None
    alive = False
    watched = set().union(*(t.alive for t in _tools)) if _tools else set()
    for n in names.values():
        if n in watched:
            alive = True
            break
    for t in _tools:
        try:
            s = t.poll(now, names, cpu_fn)
        except Exception:
            s = None
        if s and (best is None or PRIORITY[s] > PRIORITY.get(best, 0)):
            best = s
    _state, _alive = best, alive
    return best


def procs_alive():
    """True if any watched agent process was seen on the last poll."""
    return _alive
