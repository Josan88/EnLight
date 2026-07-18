"""Codex notify forwarder: updates the keylight AND calls the original
notify program, since Codex supports only one notify command.

The original command lives in codex_notify_chain.json ("forward" list);
edit that file if the Codex runtime path changes.
"""

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import lightctl  # noqa: E402


def main(argv):
    payload = argv[1] if len(argv) > 1 else "{}"

    # forward to the original notify program (fire and forget)
    try:
        cfg = json.loads((HERE / "codex_notify_chain.json").read_text(encoding="utf-8"))
        fwd = cfg.get("forward") or []
        if fwd and Path(fwd[0]).exists():
            subprocess.Popen(fwd + [payload], close_fds=True)
    except Exception:
        pass

    # keylight: a completed turn means the agent stopped
    try:
        data = json.loads(payload)
        if data.get("type") not in (None, "agent-turn-complete"):
            return 0
    except Exception:
        pass
    return lightctl.main(["lightctl", "stopped", "codex"])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
