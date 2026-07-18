"""Report agent state to the keylight daemon (writes a per-session state file).

Usage:
  python lightctl.py running|stopped|error|clear [session_id]
  python lightctl.py hook running|stopped|error|clear

The `hook` form is for Claude Code hooks: it reads the hook JSON from stdin
and uses its session_id, so each Claude session gets its own state file.
Without a session id, the parent process id is used.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import BASE_DIR  # noqa: E402

STATE_DIR = BASE_DIR / "sessions"

VALID = {"running", "waiting", "stopped", "error", "clear"}


def main(argv):
    args = list(argv[1:])
    sid = None

    if args and args[0] == "hook":
        args.pop(0)
        try:
            sid = json.load(sys.stdin).get("session_id")
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
        f.write_text(state, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
