"""One-shot installer for Agent Keylight (Windows + macOS).

    python installer.py               install / repair / update paths
    python installer.py --uninstall   remove everything it added

Does all the wiring a user would otherwise do by hand:
  1. default config.json (choose your two lights there afterwards)
  2. optional openrgb-python package (RGB keyboards)
  3. auto-start at login (Windows: Startup folder / macOS: launchd agent)
  4. Claude Code hooks merged into ~/.claude/settings.json (backed up first;
     re-running is safe -- old keylight entries are replaced, nothing else
     is touched)
  5. Codex notify wiring in ~/.codex/config.toml, preserving any existing
     notify program by chaining to it (backed up first)
  6. (re)starts the daemon

Everything uses this folder's current location, so users can copy the
folder anywhere before running.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

HERE = Path(__file__).resolve().parent
HERE_FWD = str(HERE).replace("\\", "/")
PY_CMD = "python" if IS_WIN else "python3"

CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
CODEX_CONFIG = Path.home() / ".codex" / "config.toml"

# Windows autostart
STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / (
    "Microsoft/Windows/Start Menu/Programs/Startup"
)
STARTUP_CMD = STARTUP_DIR / "agent-keylight.cmd"

# macOS autostart
LAUNCHD_LABEL = "com.agent-keylight.daemon"
LAUNCHD_PLIST = Path.home() / "Library/LaunchAgents" / f"{LAUNCHD_LABEL}.plist"

HOOK_EVENTS = {
    "UserPromptSubmit": "running",
    "PreToolUse": "running",
    "PermissionRequest": "waiting",
    "Notification": "waiting",
    "Stop": "stopped",
    "StopFailure": "error",
    "SessionEnd": "clear",
}

DAEMON_PORT = 51765


def log(msg):
    print(f"  * {msg}")


def backup(path, suffix=".bak-keylight"):
    bak = path.with_name(path.name + suffix)
    if path.exists() and not bak.exists():
        bak.write_bytes(path.read_bytes())


# ------------------------------------------------------------------ pieces
def ensure_config():
    sys.path.insert(0, str(HERE))
    from config import load_config
    load_config()
    log("config.json ready (edit it to choose your two lights)")


def pip_openrgb():
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--user", "--quiet",
         "openrgb-python"],
        capture_output=True,
    )
    log("openrgb-python installed (RGB keyboards)" if r.returncode == 0
        else "openrgb-python skipped (optional, RGB keyboards only)")


def install_autostart():
    if IS_WIN:
        STARTUP_DIR.mkdir(parents=True, exist_ok=True)
        STARTUP_CMD.write_text(
            f'@echo off\r\ncall "{HERE}\\launcher.cmd"\r\n', encoding="ascii"
        )
        log(f"startup entry: {STARTUP_CMD}")
    elif IS_MAC:
        LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
        LAUNCHD_PLIST.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{sys.executable}</string>
    <string>{HERE}/daemon.py</string>
  </array>
  <key>WorkingDirectory</key><string>{HERE}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
</dict>
</plist>
""", encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(LAUNCHD_PLIST)],
                       capture_output=True)
        subprocess.run(["launchctl", "load", str(LAUNCHD_PLIST)],
                       capture_output=True)
        log(f"launchd agent: {LAUNCHD_PLIST}")


def remove_autostart():
    if IS_WIN and STARTUP_CMD.exists():
        STARTUP_CMD.unlink()
        log("startup entry removed")
    if IS_MAC and LAUNCHD_PLIST.exists():
        subprocess.run(["launchctl", "unload", str(LAUNCHD_PLIST)],
                       capture_output=True)
        LAUNCHD_PLIST.unlink()
        log("launchd agent removed")


def _strip_keylight_hooks(hooks):
    """Remove our commands from a settings hooks dict, dropping empty shells."""
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        for g in groups:
            g["hooks"] = [
                h for h in g.get("hooks", [])
                if "lightctl.py" not in str(h.get("command", ""))
            ]
        hooks[event] = [g for g in groups if g.get("hooks")]
        if not hooks[event]:
            del hooks[event]


def install_claude_hooks():
    settings = {}
    if CLAUDE_SETTINGS.exists():
        try:
            settings = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))
        except ValueError:
            log(f"SKIPPED Claude hooks: {CLAUDE_SETTINGS} is not valid JSON")
            return
        backup(CLAUDE_SETTINGS)
    hooks = settings.setdefault("hooks", {})
    _strip_keylight_hooks(hooks)  # replace any previous install (moved folder)
    for event, state in HOOK_EVENTS.items():
        hooks.setdefault(event, []).append({
            "hooks": [{
                "type": "command",
                "command": f"{PY_CMD} {HERE_FWD}/lightctl.py hook {state}",
                "timeout": 10,
                "async": True,
            }]
        })
    CLAUDE_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    CLAUDE_SETTINGS.write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )
    log("Claude Code hooks installed (~/.claude/settings.json)")


def remove_claude_hooks():
    if not CLAUDE_SETTINGS.exists():
        return
    try:
        settings = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))
    except ValueError:
        return
    if "hooks" in settings:
        _strip_keylight_hooks(settings["hooks"])
        if not settings["hooks"]:
            del settings["hooks"]
        CLAUDE_SETTINGS.write_text(
            json.dumps(settings, indent=2) + "\n", encoding="utf-8"
        )
        log("Claude Code hooks removed")


NOTIFY_RE = re.compile(r"^notify\s*=\s*(\[.*\])\s*$", re.MULTILINE)


def install_codex_notify():
    if not CODEX_CONFIG.exists():
        log("Codex not found -- skipped (optional)")
        return
    text = CODEX_CONFIG.read_text(encoding="utf-8")
    ours = (f'notify = [{json.dumps(PY_CMD)}, '
            f'{json.dumps(str(HERE / "codex_notify_chain.py"))}]')
    if "codex_notify_chain.py" in text:
        CODEX_CONFIG.write_text(NOTIFY_RE.sub(ours, text), encoding="utf-8")
        log("Codex notify already chained (path refreshed)")
        return
    backup(CODEX_CONFIG)
    m = NOTIFY_RE.search(text)
    if m:
        # preserve the existing notify program so the chain keeps calling it
        try:
            forward = json.loads(m.group(1))
        except ValueError:
            forward = []
        (HERE / "codex_notify_chain.json").write_text(
            json.dumps({"forward": forward}, indent=2) + "\n", encoding="utf-8"
        )
        text = NOTIFY_RE.sub(ours, text, count=1)
    else:
        (HERE / "codex_notify_chain.json").write_text(
            json.dumps({"forward": []}, indent=2) + "\n", encoding="utf-8"
        )
        text = ours + "\n" + text  # top-level keys must precede any [table]
    CODEX_CONFIG.write_text(text, encoding="utf-8")
    log("Codex notify chained (~/.codex/config.toml)")


def remove_codex_notify():
    if not CODEX_CONFIG.exists():
        return
    text = CODEX_CONFIG.read_text(encoding="utf-8")
    if "codex_notify_chain.py" not in text:
        return
    try:
        forward = json.loads(
            (HERE / "codex_notify_chain.json").read_text(encoding="utf-8")
        ).get("forward") or []
    except (OSError, ValueError):
        forward = []
    if forward:
        arr = "[ " + ", ".join(json.dumps(x) for x in forward) + " ]"
        text = NOTIFY_RE.sub(f"notify = {arr}", text, count=1)
    else:
        text = NOTIFY_RE.sub("", text, count=1)
    CODEX_CONFIG.write_text(text, encoding="utf-8")
    log("Codex notify restored")


def stop_daemon():
    if IS_WIN:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            if f":{DAEMON_PORT}" in line and "LISTENING" in line:
                pid = line.split()[-1]
                subprocess.run(["taskkill", "/PID", pid, "/F"],
                               capture_output=True)
                log(f"stopped running daemon (pid {pid})")
                return
    else:
        r = subprocess.run(["lsof", "-ti", f"tcp:{DAEMON_PORT}"],
                           capture_output=True, text=True)
        for pid in r.stdout.split():
            subprocess.run(["kill", pid], capture_output=True)
            log(f"stopped running daemon (pid {pid})")


def start_daemon():
    if IS_WIN:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        exe = str(pythonw) if pythonw.exists() else sys.executable
        DETACHED = 0x00000008 | 0x00000200
        subprocess.Popen(
            [exe, str(HERE / "daemon.py")],
            creationflags=DETACHED, close_fds=True, cwd=str(HERE),
        )
    else:
        subprocess.Popen(
            [sys.executable, str(HERE / "daemon.py")],
            start_new_session=True, close_fds=True, cwd=str(HERE),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    log("daemon started")


# ------------------------------------------------------------------ main
def main():
    if not (IS_WIN or IS_MAC):
        print("This installer supports Windows and macOS.")
        return 1
    if "--uninstall" in sys.argv:
        print("Uninstalling Agent Keylight...")
        stop_daemon()
        remove_autostart()
        remove_claude_hooks()
        remove_codex_notify()
        print("Done. (State folder left in place.)")
        return 0

    print("Installing Agent Keylight...")
    ensure_config()
    pip_openrgb()
    install_autostart()
    install_claude_hooks()
    install_codex_notify()
    stop_daemon()   # restart so a re-install picks up new code
    start_daemon()
    print("Done! Restart any open Claude Code sessions to activate the hooks.")
    if IS_MAC:
        print("macOS: grant Input Monitoring to Python/Terminal if the light")
        print("stays dark (System Settings > Privacy & Security).")
    print("Choose your two lights in the agent-keylight config.json "
          "(see README).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
