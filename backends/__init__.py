"""Light backends, tried in order of capability until one connects:

  1. rgb_openrgb       - any RGB keyboard, any vendor (OpenRGB SDK server)
  2. platform indicator - any standard keyboard:
       Windows: indicator (keyboard class driver; no lock-state change)
       macOS:   mac-hid   (IOKit HID LED elements; no lock-state change)
  3. sendinput         - Windows-only fallback (really toggles the lock key)

Backend interface:
  connect() -> bool     claim the hardware (False if unavailable)
  show(state, tick)     render one pattern tick
  release()             give the lights back to the system
  drop()                tear down (connection lost / shutdown)

Which lights are used comes from config.json -- see config.py.
"""

import os
import sys

from config import load_config
from .rgb_openrgb import OpenRGBBackend

VALID_LEDS = ("caps", "num", "scroll")


def _candidates(primary, secondary):
    if sys.platform == "win32":
        from .indicator import IndicatorBackend
        from .sendinput import SendInputBackend
        return {
            "openrgb": [OpenRGBBackend()],
            "indicator": [IndicatorBackend(primary, secondary)],
            "sendinput": [SendInputBackend(primary)],
            "": [
                OpenRGBBackend(),
                IndicatorBackend(primary, secondary),
                SendInputBackend(primary),
            ],
        }
    if sys.platform == "darwin":
        from .macos_hid import MacHidBackend
        return {
            "openrgb": [OpenRGBBackend()],
            "indicator": [MacHidBackend(primary, secondary)],
            "": [OpenRGBBackend(), MacHidBackend(primary, secondary)],
        }
    return {"": [OpenRGBBackend()]}  # other platforms: RGB only


def pick_backend():
    cfg = load_config()
    primary = cfg.get("primary", "caps")
    if primary not in VALID_LEDS:
        primary = "caps"
    secondary = cfg.get("secondary", "auto")
    forced = os.environ.get("AGENT_KEYLIGHT_BACKEND", "").lower()
    table = _candidates(primary, secondary)
    for b in table.get(forced, table[""]):
        try:
            if b.connect():
                return b
        except Exception:
            pass
    return None
