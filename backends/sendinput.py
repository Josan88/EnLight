"""SendInput fallback: blinks by genuinely toggling a lock key.

Universal but intrusive -- the lock state really changes while active
(e.g. caps lock turns on/off), so this is only used when the driver
interface is blocked, or when forced via AGENT_KEYLIGHT_BACKEND.
"""

import ctypes

from states import PATTERNS

VK = {"caps": 0x14, "num": 0x90, "scroll": 0x91}
KEYEVENTF_KEYUP = 0x0002


class SendInputBackend:
    name = "sendinput"

    def __init__(self, led="caps"):
        self.vk = VK[led]
        self.u32 = ctypes.windll.user32
        self.owned = False
        self.original = None

    def connect(self):
        return True

    def drop(self):
        self.owned = False

    def _toggled(self):
        return bool(self.u32.GetKeyState(self.vk) & 1)

    def _tap(self):
        self.u32.keybd_event(self.vk, 0, 0, 0)
        self.u32.keybd_event(self.vk, 0, KEYEVENTF_KEYUP, 0)

    def show(self, state, tick):
        if not self.owned:
            self.original = self._toggled()
            self.owned = True
        want = bool(PATTERNS[state][tick % 8])
        if self._toggled() != want:
            self._tap()

    def release(self):
        if not self.owned:
            return
        if self.original is not None and self._toggled() != self.original:
            self._tap()
        self.owned = False
