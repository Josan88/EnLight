"""User configuration: which two lights to use.

Loaded from %LOCALAPPDATA%/agent-keylight/config.json (created with
defaults on first run) so each machine picks its own keys:

  {
    "primary": "caps",      // the status light: caps | num | scroll
    "secondary": "auto"     // joins ONLY the waiting blink:
                            //   caps | num | scroll  (another indicator LED)
                            //   "fnlock"             (Fn-lock LED, if present)
                            //   "auto"               (first extra light found,
                            //                         else none)
                            //   "none"               (primary only)
  }

Only these two lights are ever touched; all other LEDs keep showing their
real lock state.
"""

import json
import os
import sys
from pathlib import Path


def _default_base():
    env = os.environ.get("AGENT_KEYLIGHT_DIR")
    if env:
        return Path(env)
    if sys.platform == "win32":
        return Path(
            os.environ.get("LOCALAPPDATA", str(Path.home()))
        ) / "agent-keylight"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/agent-keylight"
    return Path.home() / ".agent-keylight"


BASE_DIR = _default_base()
CONFIG_FILE = BASE_DIR / "config.json"

DEFAULTS = {"primary": "caps", "secondary": "auto"}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        try:
            BASE_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(
                json.dumps(DEFAULTS, indent=2) + "\n", encoding="utf-8"
            )
        except OSError:
            pass
    # env override kept for quick experiments
    env_led = os.environ.get("AGENT_KEYLIGHT_LED", "").lower()
    if env_led:
        cfg["primary"] = env_led
    return cfg
