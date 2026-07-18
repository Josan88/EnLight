"""Shared state definitions for Agent Keylight.

States (highest priority first):
  error   - an agent crashed
  waiting - an agent is blocked on user permission/input
  running - an agent is working
  stopped - an agent finished and is idle
"""

TICK = 0.25  # pattern resolution in seconds; patterns are 8 ticks = 2s cycle

PRIORITY = {"error": 4, "waiting": 3, "running": 2, "stopped": 1}

# on/off patterns per state (8 ticks @ 250ms) for single-color lights
PATTERNS = {
    "running": [1, 1, 0, 0, 1, 1, 0, 0],   # steady blink
    "waiting": [1, 0, 1, 0, 1, 0, 1, 0],   # fast blink (needs the user)
    "stopped": [1, 0, 1, 0, 0, 0, 0, 0],   # double-blip, pause
    "error":   [1, 1, 1, 1, 1, 1, 1, 1],   # solid
}

# states where the configured secondary light blinks alongside the primary
SECONDARY_STATES = {"waiting"}

# color + pattern per state for RGB keyboards
RGB_PATTERNS = {
    "running": ((255, 255, 255), [1, 1, 0, 0, 1, 1, 0, 0]),  # blink white
    "waiting": ((255, 160, 0),   [1, 0, 1, 0, 1, 0, 1, 0]),  # fast amber
    "stopped": ((255, 0, 0),     [1, 1, 0, 0, 1, 1, 0, 0]),  # blink red
    "error":   ((255, 0, 0),     [1, 1, 1, 1, 1, 1, 1, 1]),  # static red
}
