"""Report agent state to the keylight daemon (writes a per-session state file).

Usage:
  python lightctl.py running|stopped|error|clear [session_id]
  python lightctl.py hook running|stopped|error|clear

The `hook` form is for Claude Code / Cursor hooks: it reads the hook JSON
from stdin and uses its session id (Claude: session_id, Cursor:
conversation_id), so each session gets its own state file. Without a
session id, the parent process id is used.

Each state file also records the pid of the agent process that owns the
session (the nearest non-shell ancestor), so the daemon can clear the light
when the IDE/CLI is closed without a clean SessionEnd.
"""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from config import BASE_DIR  # noqa: E402

STATE_DIR = BASE_DIR / "sessions"
DAEMON_PORT = 51765  # daemon's single-instance guard port

VALID = {"running", "waiting", "stopped", "error", "clear"}

# wrappers the agent may run hooks through; the real owner is above these
_SHELLS = {"cmd", "conhost", "powershell", "pwsh", "sh", "bash", "zsh",
           "dash", "fish"}


def _win_proc_table():
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if snap in (0, -1):
        return {}
    table = {}
    entry = PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
    try:
        if k32.Process32First(snap, ctypes.byref(entry)):
            while True:
                table[int(entry.th32ProcessID)] = (
                    int(entry.th32ParentProcessID),
                    entry.szExeFile.decode(errors="ignore"),
                )
                if not k32.Process32Next(snap, ctypes.byref(entry)):
                    break
    finally:
        k32.CloseHandle(snap)
    return table


def _proc_table():
    """{pid: (ppid, exe name)} for all processes, {} if unavailable."""
    if sys.platform == "win32":
        try:
            return _win_proc_table()
        except Exception:
            return {}
    try:
        out = subprocess.run(["ps", "-Ao", "pid=,ppid=,comm="],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return {}
    table = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2])
    return table


def _norm_name(name):
    name = Path(name.strip()).name.lower().lstrip("-")
    return name[:-4] if name.endswith(".exe") else name


def owner_pid():
    """Pid of the long-lived agent process this hook belongs to, or None.

    Hooks are usually spawned through a shell wrapper that exits with us,
    so walk up past shells to the agent (claude/codex/terminal) itself.
    None (no pid recorded) when the tree can't be resolved -- better no
    liveness check than tying the light to a process that dies instantly.
    """
    table = _proc_table()
    pid = os.getppid()
    for _ in range(6):
        if pid not in table:
            return None
        ppid, name = table[pid]
        if _norm_name(name) not in _SHELLS:
            return pid
        if ppid == pid:
            return None
        pid = ppid
    return None


def ensure_daemon():
    """Start the daemon on demand if it is not already running.

    There is no login autostart: the first hook of an agent session brings
    the daemon up, and it exits again once every session is gone.
    """
    try:
        socket.create_connection(("127.0.0.1", DAEMON_PORT), timeout=0.3).close()
        return  # already running
    except OSError:
        pass
    if sys.platform == "win32":
        # launcher.cmd also brings up the OpenRGB SDK server when installed
        NO_WINDOW = 0x08000000
        subprocess.Popen(
            ["cmd", "/c", str(HERE / "launcher.cmd")],
            creationflags=NO_WINDOW, close_fds=True, cwd=str(HERE),
        )
    else:
        subprocess.Popen(
            [sys.executable or "python3", str(HERE / "daemon.py")],
            start_new_session=True, close_fds=True, cwd=str(HERE),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def main(argv):
    args = list(argv[1:])
    sid = None

    if args and args[0] == "hook":
        args.pop(0)
        try:
            data = json.load(sys.stdin)
            sid = (data.get("session_id")          # Claude Code
                   or data.get("conversation_id")  # Cursor
                   or data.get("thread_id"))
        except Exception:
            pass

    if not args or args[0] not in VALID:
        print(__doc__, file=sys.stderr)
        return 2
    state = args[0]
    if len(args) > 1:
        sid = args[1]
    if not sid:
        sid = f"pid{os.getppid()}"

    # keep the filename filesystem-safe
    sid = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(sid))

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    f = STATE_DIR / f"{sid}.state"
    if state == "clear":
        f.unlink(missing_ok=True)
    else:
        owner = None
        try:
            owner = owner_pid()
        except Exception:
            pass
        f.write_text(state if owner is None else f"{state} {owner}",
                     encoding="utf-8")
        try:
            ensure_daemon()
        except Exception:
            pass  # never fail the hook over the daemon
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
