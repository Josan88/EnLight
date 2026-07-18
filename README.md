# Agent Keylight

Shows AI-agent status (Claude Code, Codex, any CLI agent) on your keyboard's
lights. Works on Windows and macOS (**macOS support is untested** — written
against the IOKit HID API but not yet verified on real hardware; please
report issues):

- **RGB keyboard** (any vendor, via [OpenRGB](https://openrgb.org)):
  - running → **blink white**
  - waiting for permission → **fast amber blink**
  - stopped → **blink red**
  - error → **static red**
- **Plain keyboard** (through the keyboard driver — does NOT toggle the real
  lock state). Exactly **two lights of your choice** (config.json):
  - running → **primary light: steady blink**
  - waiting for permission → **primary + secondary light: fast blink together**
  - stopped → **primary light: double-blip … pause**
  - error → **primary light: solid on**

When no agent session is active, the light is released back to normal.
With several sessions at once, priority is: error > waiting > running > stopped.

## Light flow

One cycle = 2 seconds, in 8 steps of 250 ms (`█` on, `░` off), repeating
until the state changes:

```
RUNNING  (agent working)        primary light
██░░██░░   2 blinks/cycle -- 1 per second, relaxed heartbeat

WAITING  (permission prompt)    primary + secondary, in sync
█░█░█░█░   4 blinks/cycle -- 2 per second, urgent flutter

STOPPED  (done, needs a look)   primary light
█░█░░░░░   2 quick blips, then ~1.5 s dark, repeat

ERROR    (agent crashed)        primary light
████████   no blinks -- solid on until the next prompt/clear

IDLE     (no sessions)
░░░░░░░░   released -- LED shows the real lock state again
```

Rule of thumb: slow rhythm = working, fast rhythm = needs you now,
double-knock = finished, frozen solid = something broke, dark = idle.
Urgency rises with blink speed; the secondary light only ever joins
for "needs you now".

## How it works

- `daemon.py` — tiny background loop (one wake-up every 250 ms). Reads state
  files from `%LOCALAPPDATA%\agent-keylight\sessions\` and renders through
  the best backend.
- `states.py` — all patterns/colors/priorities in one place.
- `backends/` — hardware support, tried in order until one connects:
  `rgb_openrgb` (any RGB keyboard, any vendor) → platform indicator
  (`indicator` on Windows via the keyboard class driver, `macos_hid` on
  macOS via IOKit HID) → `sendinput` (Windows-only fallback).
- `config.py` + `%LOCALAPPDATA%\agent-keylight\config.json` — each machine
  picks its own two lights (created with defaults on first run):

  ```json
  { "primary": "caps", "secondary": "auto" }
  ```

  `primary`: `caps` | `num` | `scroll`.
  `secondary` (waiting blink only): another of those, `"fnlock"` (laptop
  Fn-lock LED if present), `"auto"` (first extra light found, else none),
  or `"none"`.
- `vendors/` — plug-in folder for extra lights beyond the standard three
  indicators. Every module self-detects its hardware in `connect()` and is
  skipped on machines without it; to support new hardware, drop one file in
  this folder implementing the `Provider` interface — no other changes.
  Included: `fnlock_ec` (Fn-lock LED on laptops exposing the ATK ACPI
  embedded-controller driver).
- `lightctl.py` — writes/clears a per-session state file:
  `python lightctl.py running|waiting|stopped|error|clear [session-id]`
- Claude Code hooks (in `~/.claude/settings.json`) call
  `lightctl.py hook <state>` on UserPromptSubmit/PreToolUse (running),
  PermissionRequest/Notification (waiting), Stop (stopped),
  StopFailure (error), SessionEnd (clear).
- `codex_notify_chain.py` — Codex `notify` handler that marks turns stopped
  AND forwards to whatever notify program was configured before
  (edit `codex_notify_chain.json` if the Codex runtime path changes).
- `agent-run.cmd <any command>` — wraps any CLI agent: running while it runs,
  solid error for 15 s if it exits non-zero.

## Install

1. Copy this folder anywhere.
2. **Windows:** double-click `install.cmd` — if Python isn't on the PC, the
   installer downloads it automatically (via winget; on very old Windows
   without winget it opens python.org and asks you to re-run).
   **macOS:** run `./install.sh` in Terminal, then grant **Input
   Monitoring** to Python/Terminal if the light stays dark
   (System Settings → Privacy & Security). Auto-start uses a launchd
   agent instead of the Startup folder.

The installer does all the wiring automatically:
- Startup entry (daemon runs at every logon)
- Claude Code hooks merged into `~/.claude/settings.json`
  (existing settings untouched, backup written first; re-running the
  installer is safe and also fixes paths after moving the folder)
- Codex `notify` chained in `~/.codex/config.toml`, preserving whatever
  notify program was already configured
- optional `openrgb-python` package, then starts the daemon

Restart any open Claude Code sessions afterwards so the hooks load.
RGB keyboard? Also install [OpenRGB](https://openrgb.org) — the launcher
auto-starts its SDK server and you get the full color scheme.

**Uninstall:** `python installer.py --uninstall` (removes the startup entry,
hooks, and Codex wiring; restores the original notify program).

## Tuning

Environment variables (set for the daemon process):

- `AGENT_KEYLIGHT_BACKEND` = `openrgb` | `indicator` | `sendinput` — force a backend
- `AGENT_KEYLIGHT_LED` = `caps` | `num` | `scroll` — which indicator LED (default caps)
- `AGENT_KEYLIGHT_DIR` — state directory override

Manual test: `python lightctl.py running test` → watch the light →
`python lightctl.py clear test`.

## Notes / limitations

- Caps Lock **key still works normally**; only its LED is borrowed. If you
  toggle caps while the daemon owns the LED, the LED shows the pattern, not
  your caps state, until the agent goes idle.
- Codex only reports "turn ended" (blip pattern); it can't signal "running"
  unless launched through `agent-run.cmd`.
- The "error" state comes from Claude Code's StopFailure hook (abnormal turn
  end), `agent-run.cmd` exit codes, or a manual `lightctl.py error <id>`.
  In-conversation problems (a failing test, a tool error mid-turn) count as
  normal work and show as "running".
- Built-in indicator LEDs are fixed-color hardware — patterns, not colors.
  Colors require an RGB keyboard (works automatically via OpenRGB).
- macOS: **untested on real hardware.** The backend follows the same
  approach as proven tools (IOKit HID LED elements), but no Mac has run it
  yet. Known likely first-run issues: the light stays dark until Input
  Monitoring permission is granted, and built-in Apple keyboards usually
  expose only the caps LED (secondary stays off unless an external
  keyboard adds num/scroll).
