"""One-shot installer for Agent Keylight (Windows + macOS).

    python installer.py                  install / repair / update paths
    python installer.py --no-autostart   install without the login autostart
    python installer.py --uninstall      remove everything it added

Does all the wiring a user would otherwise do by hand:
  1. default config.json (choose your two lights there afterwards)
  2. optional openrgb-python package (RGB keyboards)
  3. Claude Code hooks merged into ~/.claude/settings.json (backed up first;
     re-running is safe -- old keylight entries are replaced, nothing else
     is touched)
  4. Codex notify wiring in ~/.codex/config.toml, preserving any existing
     notify program by chaining to it (backed up first)
  5. Cursor IDE hooks merged into ~/.cursor/hooks.json (if Cursor exists)
  6. Hermes shell hooks in every profile's config.yaml under HERMES_HOME
     (default ~/.hermes), pre-approved in each shell-hooks-allowlist.json
  7. a periodic trigger task (every 2 min, invisible) that starts the
     daemon only while an agent app is actually open, so hook-less agents
     (the Codex/ChatGPT app, Gemini, opencode, ...) get detected without
     anything running at boot or while no agent is around -- skip it with
     --no-autostart, then the daemon only starts on demand (first agent
     hook) and exits when idle

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
CURSOR_DIR = Path.home() / ".cursor"
CURSOR_HOOKS = CURSOR_DIR / "hooks.json"

# Windows: legacy login-autostart location (older versions; now removed)
STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / (
    "Microsoft/Windows/Start Menu/Programs/Startup"
)
STARTUP_CMD = STARTUP_DIR / "agent-keylight.cmd"
# Windows: periodic trigger task (starts the daemon only while agents run)
TASK_NAME = "AgentKeylightTrigger"

# macOS trigger / legacy autostart
LAUNCHD_LABEL = "com.agent-keylight.daemon"
LAUNCHD_PLIST = Path.home() / "Library/LaunchAgents" / f"{LAUNCHD_LABEL}.plist"

HOOK_EVENTS = {
    "UserPromptSubmit": "running",
    "PreToolUse": "running",
    "PermissionRequest": "waiting",
    "Notification": "waiting",
    "Stop": "clear",  # go dark when a turn ends instead of double-blipping
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


def install_trigger():
    """Periodic trigger (every 2 min, invisible): starts the daemon only
    while an agent app is actually open -- nothing runs at boot and the
    daemon exits again once every agent is gone. Covers agents that never
    fire a hook (Codex/ChatGPT app, Gemini, opencode, ...)."""
    if IS_WIN:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        exe = str(pythonw) if pythonw.exists() else sys.executable
        r = subprocess.run(
            ["schtasks", "/Create", "/F", "/TN", TASK_NAME,
             "/SC", "MINUTE", "/MO", "2",
             "/TR", f'"{exe}" "{HERE}\\trigger.py"'],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            log("trigger task installed (daemon starts when an agent opens)")
        else:
            log(f"SKIPPED trigger task: {r.stderr.strip() or r.stdout.strip()}")
    elif IS_MAC:
        LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
        LAUNCHD_PLIST.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{sys.executable}</string><string>{HERE}/trigger.py</string></array>
  <key>StartInterval</key><integer>120</integer>
</dict></plist>
""", encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(LAUNCHD_PLIST)],
                       capture_output=True)
        subprocess.run(["launchctl", "load", str(LAUNCHD_PLIST)],
                       capture_output=True)
        log("trigger installed (daemon starts when an agent opens)")


def remove_trigger():
    """Also cleans up the login autostart older versions installed.
    Without the trigger the daemon is started on demand by lightctl.py
    hooks only (hook-less agents then go undetected while it is down)."""
    if IS_WIN:
        r = subprocess.run(
            ["schtasks", "/Delete", "/F", "/TN", TASK_NAME],
            capture_output=True,
        )
        if r.returncode == 0:
            log("trigger task removed")
        if STARTUP_CMD.exists():
            STARTUP_CMD.unlink()
            log("startup entry removed")
    if IS_MAC and LAUNCHD_PLIST.exists():
        subprocess.run(["launchctl", "unload", str(LAUNCHD_PLIST)],
                       capture_output=True)
        LAUNCHD_PLIST.unlink()
        log("launchd trigger removed")


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


# Cursor IDE hooks (~/.cursor/hooks.json): its native agent has no other
# external status signal. Events picked so an absent reply never blocks
# the agent (observability events default to allow/continue).
CURSOR_EVENTS = {
    "beforeSubmitPrompt": "running",
    "afterFileEdit": "running",
    "stop": "stopped",
}


def _strip_cursor_hooks(hooks):
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        hooks[event] = [
            e for e in entries
            if "lightctl.py" not in str(e.get("command", ""))
        ]
        if not hooks[event]:
            del hooks[event]


def install_cursor_hooks():
    if not CURSOR_DIR.exists():
        log("Cursor not found -- skipped (optional)")
        return
    data = {}
    if CURSOR_HOOKS.exists():
        try:
            data = json.loads(CURSOR_HOOKS.read_text(encoding="utf-8"))
        except ValueError:
            log(f"SKIPPED Cursor hooks: {CURSOR_HOOKS} is not valid JSON")
            return
        backup(CURSOR_HOOKS)
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    _strip_cursor_hooks(hooks)  # replace any previous install (moved folder)
    for event, state in CURSOR_EVENTS.items():
        hooks.setdefault(event, []).append({
            "command": f"{PY_CMD} {HERE_FWD}/lightctl.py hook {state}"
        })
    CURSOR_HOOKS.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )
    log("Cursor hooks installed (~/.cursor/hooks.json)")


def remove_cursor_hooks():
    if not CURSOR_HOOKS.exists():
        return
    try:
        data = json.loads(CURSOR_HOOKS.read_text(encoding="utf-8"))
    except ValueError:
        return
    if "hooks" in data:
        _strip_cursor_hooks(data["hooks"])
        CURSOR_HOOKS.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )
        log("Cursor hooks removed")


# Hermes shell hooks (`hooks:` in each profile's config.yaml). Hermes is
# always busy in the background (gateway, cron, heartbeats), so watching
# its CPU or logs can't tell a real turn apart -- hooks can. Each profile
# is its own Hermes home with its own config and consent allowlist; the
# desktop app and gateway can't show the first-use consent prompt, so the
# allowlist entries are written here too.
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
HERMES_EVENTS = {
    "pre_llm_call": "running",
    "on_human_input_request": "waiting",   # approval / clarify / sudo
    "on_human_input_resolved": "running",
    "on_session_end": "clear",             # fires at every turn end
}
HERMES_BEGIN = "# >>> agent-keylight hooks (managed by EnLight installer.py)"
HERMES_END = "# <<< agent-keylight hooks"
HERMES_ALLOWLIST = "shell-hooks-allowlist.json"


def _hermes_homes():
    if not (HERMES_HOME / "config.yaml").exists():
        return []
    return [HERMES_HOME] + sorted(
        p.parent for p in HERMES_HOME.glob("profiles/*/config.yaml"))


def _hermes_cmd(state):
    return f"{PY_CMD} {HERE_FWD}/lightctl.py hook {state}"


def _strip_hermes_block(text):
    start = text.find(HERMES_BEGIN)
    if start < 0:
        return text
    end = text.find(HERMES_END, start)
    end = len(text) if end < 0 else end + len(HERMES_END)
    return (text[:start].rstrip("\n") + "\n" + text[end:].lstrip("\n")).lstrip("\n")


def _update_hermes_allowlist(home, add):
    path = home / HERMES_ALLOWLIST
    data = {"approvals": []}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except ValueError:
            return False
    approvals = [e for e in data.get("approvals", [])
                 if "lightctl.py" not in str(e.get("command", ""))]
    if add:
        approvals += [{"event": ev, "command": _hermes_cmd(st),
                       "approved_at": None, "script_mtime_at_approval": None}
                      for ev, st in HERMES_EVENTS.items()]
    data["approvals"] = approvals
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return True


def install_hermes_hooks():
    homes = _hermes_homes()
    if not homes:
        log("Hermes not found -- skipped (optional)")
        return
    block = HERMES_BEGIN + "\nhooks:\n" + "".join(
        f"  {ev}:\n    - command: \"{_hermes_cmd(st)}\"\n      timeout: 10\n"
        for ev, st in HERMES_EVENTS.items()) + HERMES_END + "\n"
    done = 0
    for home in homes:
        cfg = home / "config.yaml"
        text = _strip_hermes_block(cfg.read_text(encoding="utf-8"))
        # no YAML parser in the stdlib: only add our block when the file
        # has no hooks of its own, rather than risk mangling them
        if re.search(r"^hooks\s*:", text, re.MULTILINE):
            log(f"SKIPPED Hermes hooks: {cfg} already has a hooks: section")
            continue
        backup(cfg)
        cfg.write_text(text.rstrip("\n") + "\n\n" + block, encoding="utf-8")
        _update_hermes_allowlist(home, add=True)
        done += 1
    log(f"Hermes hooks installed ({done} profile(s) -- restart Hermes)")


def remove_hermes_hooks():
    for home in _hermes_homes():
        cfg = home / "config.yaml"
        text = cfg.read_text(encoding="utf-8")
        if HERMES_BEGIN in text:
            cfg.write_text(_strip_hermes_block(text), encoding="utf-8")
            _update_hermes_allowlist(home, add=False)
            log(f"Hermes hooks removed ({home.name})")


NOTIFY_RE = re.compile(r"^notify\s*=\s*(\[.*\])\s*$", re.MULTILINE)


def install_codex_notify():
    if not CODEX_CONFIG.exists():
        log("Codex not found -- skipped (optional)")
        return
    text = CODEX_CONFIG.read_text(encoding="utf-8")
    ours = (f'notify = [{json.dumps(PY_CMD)}, '
            f'{json.dumps(str(HERE / "codex_notify_chain.py"))}]')
    # NOTE: replacements go through a lambda so re.sub does not treat the
    # backslashes in Windows paths as escape sequences (halving them would
    # write invalid TOML, which Codex then drops on its next config rewrite)
    if "codex_notify_chain.py" in text:
        new = NOTIFY_RE.sub(lambda _: ours, text)
        if new != text:  # only touch the file when the path actually moved
            CODEX_CONFIG.write_text(new, encoding="utf-8")
        log("Codex notify already chained")
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
        text = NOTIFY_RE.sub(lambda _: ours, text, count=1)
    else:
        (HERE / "codex_notify_chain.json").write_text(
            json.dumps({"forward": []}, indent=2) + "\n", encoding="utf-8"
        )
        text = ours + "\n" + text  # top-level keys must precede any [table]
    CODEX_CONFIG.write_text(text, encoding="utf-8")
    log("Codex notify chained (~/.codex/config.toml)")


def remove_codex_notify():
    chain_json = HERE / "codex_notify_chain.json"
    text = (CODEX_CONFIG.read_text(encoding="utf-8")
            if CODEX_CONFIG.exists() else "")
    if "codex_notify_chain.py" in text:
        try:
            forward = json.loads(
                chain_json.read_text(encoding="utf-8")
            ).get("forward") or []
        except (OSError, ValueError):
            forward = []
        if forward:
            arr = "[ " + ", ".join(json.dumps(x) for x in forward) + " ]"
            text = NOTIFY_RE.sub(lambda _: f"notify = {arr}", text, count=1)
        else:
            text = NOTIFY_RE.sub("", text, count=1)
        CODEX_CONFIG.write_text(text, encoding="utf-8")
        log("Codex notify restored")
    # without this file the daemon knows not to re-chain (see daemon.py)
    chain_json.unlink(missing_ok=True)


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
        remove_trigger()
        remove_claude_hooks()
        remove_codex_notify()
        remove_cursor_hooks()
        remove_hermes_hooks()
        print("Done. (State folder left in place.)")
        return 0

    print("Installing Agent Keylight...")
    ensure_config()
    pip_openrgb()
    install_claude_hooks()
    install_codex_notify()
    install_cursor_hooks()
    install_hermes_hooks()
    stop_daemon()   # so a re-install picks up new code
    from config import BASE_DIR
    if IS_WIN and STARTUP_CMD.exists():
        STARTUP_CMD.unlink()  # login autostart from older versions
        log("old login autostart removed")
    if "--no-autostart" in sys.argv:
        remove_trigger()
    else:
        install_trigger()
    # bring the daemon up now if an agent is already active
    import watchers
    if (any((BASE_DIR / "sessions").glob("*.state"))
            or watchers.poll() or watchers.procs_alive()):
        start_daemon()
    else:
        log("daemon starts when the next agent runs")
    print("Done! Restart any open Claude Code sessions to activate the hooks.")
    if IS_MAC:
        print("macOS: grant Input Monitoring to Python/Terminal if the light")
        print("stays dark (System Settings > Privacy & Security).")
    print("Choose your two lights in the agent-keylight config.json "
          "(see README).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
