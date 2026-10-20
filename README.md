# EnLight

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

## Install

**Windows** — open PowerShell and run:

```powershell
git clone https://github.com/mrfandu1/EnLight.git
cd EnLight
install.cmd
```

**macOS** — open Terminal and run:

```bash
git clone https://github.com/mrfandu1/EnLight.git
cd EnLight
./install.sh
```

That's it — Claude Code, Codex, Cursor, and Hermes get wired up automatically.
Restart any open Claude Code sessions afterward so the hooks load.
RGB keyboard? Also install [OpenRGB](https://openrgb.org) for full color
support. macOS: grant **Input Monitoring** to Python/Terminal if the light
stays dark (System Settings → Privacy & Security).

To remove everything later: `python installer.py --uninstall`

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
  PermissionRequest/Notification (waiting), Stop (clear),
  StopFailure (error), SessionEnd (clear).
- Cursor IDE hooks (in `~/.cursor/hooks.json`) do the same for Cursor's
  native agent: beforeSubmitPrompt/afterFileEdit (running), stop (stopped).
- Hermes shell hooks (a managed `hooks:` block in every profile's
  `config.yaml` under `HERMES_HOME`, pre-approved in each
  `shell-hooks-allowlist.json`): pre_llm_call (running),
  on_human_input_request (waiting), on_human_input_resolved (running),
  on_session_end (clear). Restart Hermes after installing. Profiles that
  already have their own `hooks:` section are skipped.
- `watchers.py` — hook-less detection for everything else. Most agents
  stream their transcript/log to disk while they work, so a fresh write
  means running; for native-binary CLIs a busy process (CPU) also counts.
  When activity ends the tool shows stopped while its process is alive,
  then the light is released. Built-in: **Codex CLI and the Codex/ChatGPT
  desktop app** (both write `~/.codex/sessions` rollouts), **Gemini CLI**
  (`~/.gemini/tmp`), **Copilot CLI**, **opencode**, **cursor-agent**.
  Add your own tools in config.json:

  ```json
  { "watch": { "mytool": ["mytool-process-name"] } }
  ```

  (CPU-based detection of those process names.) Watcher states generally
  can't distinguish "waiting for permission" — that still needs hooks.
  Exception: the Codex/ChatGPT desktop app's `request_permissions` dialog
  is detected directly from the rollout JSONL (an unresolved function-call
  of that name), since Codex's `notify` hook never fires for it.
- Each state file also records the agent's process id, so closing the
  IDE/terminal without a clean exit (no SessionEnd) still turns the light
  off: the daemon drops any session whose owner process is gone.
- `codex_notify_chain.py` — Codex `notify` handler that marks turns stopped
  AND forwards to whatever notify program was configured before
  (edit `codex_notify_chain.json` if the Codex runtime path changes).
- `agent-run.cmd <any command>` — wraps any CLI agent: running while it runs,
  solid error for 15 s if it exits non-zero.

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
- Watcher-detected agents (Gemini, opencode, ...) show running/stopped
  only; "waiting for permission" needs hooks (Claude Code, Cursor) or,
  for the Codex/ChatGPT desktop app, its `request_permissions` dialog
  (detected from the rollout JSONL). For node/python-hosted CLIs (Gemini,
  Copilot) the process can't be identified, so their stopped light times
  out after 15 minutes instead of clearing when the tool closes.
- VS Code's built-in Copilot agent exposes no external signal and isn't
  detectable; agents used *inside* VS Code that have their own wiring
  (Claude Code, Codex, Gemini CLI in a terminal) still work.
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
