"""Start the daemon only when an agent app is actually open.

Run periodically by the scheduled task the installer creates (Windows
Task Scheduler / macOS launchd StartInterval) -- NOT at login. Checks
for watched agent processes or fresh agent file activity and brings the
daemon up via lightctl.ensure_daemon(); exits immediately otherwise, so
nothing stays resident while no agent is around.

Hooked agents (Claude Code, Cursor, Codex notify) still start the daemon
instantly through their hooks; this trigger only covers hook-less apps
(the Codex/ChatGPT app, Gemini, opencode, ...), with a worst-case delay
of one trigger interval.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import lightctl  # noqa: E402
import watchers  # noqa: E402


def main():
    if watchers.poll() or watchers.procs_alive():
        lightctl.ensure_daemon()
    return 0


if __name__ == "__main__":
    sys.exit(main())
