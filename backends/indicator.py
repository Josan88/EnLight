"""Indicator backend: works on any standard keyboard.

Writes LED indicator flags (Caps/Num/Scroll) straight to the Windows
keyboard class driver -- the LED lights without changing the real lock
state. Windows rewrites indicators on every keypress, so the pattern is
simply re-asserted each tick.

Exactly two lights are used, per the user's config:
  primary   - shows every status pattern
  secondary - blinks alongside the primary during SECONDARY_STATES only;
              may be another indicator LED or an extra light discovered
              from the `vendors` package ("auto" picks the first one found)
All other LEDs are left showing their real lock state.
"""

import ctypes

from states import PATTERNS, SECONDARY_STATES
from vendors import load_providers

IOCTL_KEYBOARD_SET_INDICATORS = 0x000B0008
LED_FLAG = {"scroll": 0x01, "num": 0x02, "caps": 0x04}
VK = {"caps": 0x14, "num": 0x90, "scroll": 0x91}
DDD_RAW_TARGET_PATH = 0x00000001
DDD_REMOVE_DEFINITION = 0x00000002
DDD_EXACT_MATCH_ON_REMOVE = 0x00000004
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE = ctypes.c_void_p(-1).value


class _KbdIndicatorParams(ctypes.Structure):
    _fields_ = [("UnitId", ctypes.c_ushort), ("LedFlags", ctypes.c_ushort)]


class IndicatorBackend:
    name = "indicator"

    def __init__(self, primary="caps", secondary="auto"):
        self.primary = primary if primary in LED_FLAG else "caps"
        self.secondary = secondary
        self.k32 = ctypes.windll.kernel32
        self.u32 = ctypes.windll.user32
        self.handles = []        # (dos_name, handle)
        self.owned = False
        self.second_led = None   # secondary as an indicator LED name
        self.provider = None     # secondary as a vendor extra light

    def _lock_flags(self, exclude=()):
        """LED flags matching the real current lock states."""
        flags = 0
        for name, vk in VK.items():
            if name not in exclude and (self.u32.GetKeyState(vk) & 1):
                flags |= LED_FLAG[name]
        return flags

    def _resolve_secondary(self):
        sec = (self.secondary or "none").lower()
        if sec == "none":
            return
        if sec in LED_FLAG:
            if sec != self.primary:
                self.second_led = sec
            return
        # vendor extra light: match by provider name, or "auto" = first found
        providers = load_providers()
        for p in providers:
            if self.provider is None and (sec == "auto" or p.name == sec):
                self.provider = p
            else:
                try:
                    p.close()
                except Exception:
                    pass

    def connect(self):
        self.handles = []
        for i in range(4):
            dos = f"AgentKeylightKbd{i}"
            if not self.k32.DefineDosDeviceW(
                DDD_RAW_TARGET_PATH, dos, f"\\Device\\KeyboardClass{i}"
            ):
                continue
            h = self.k32.CreateFileW(
                f"\\\\.\\{dos}", GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None
            )
            if h in (0, INVALID_HANDLE):
                self.k32.DefineDosDeviceW(
                    DDD_REMOVE_DEFINITION | DDD_RAW_TARGET_PATH
                    | DDD_EXACT_MATCH_ON_REMOVE,
                    dos, f"\\Device\\KeyboardClass{i}",
                )
                continue
            self.handles.append((dos, h))
        if not self.handles:
            return False
        # verify the ioctl is accepted before claiming this backend works
        if not self._set_flags(self._lock_flags()):
            return False
        self._resolve_secondary()
        return True

    def _set_flags(self, flags):
        ok_any = False
        params = _KbdIndicatorParams(0, flags)
        returned = ctypes.c_ulong(0)
        for _, h in self.handles:
            if self.k32.DeviceIoControl(
                h, IOCTL_KEYBOARD_SET_INDICATORS,
                ctypes.byref(params), ctypes.sizeof(params),
                None, 0, ctypes.byref(returned), None,
            ):
                ok_any = True
        return ok_any

    def _provider_restore(self):
        if self.provider:
            try:
                self.provider.restore()
            except Exception:
                pass

    def show(self, state, tick):
        self.owned = True
        on = PATTERNS[state][tick % 8]
        use_secondary = state in SECONDARY_STATES
        leds = (self.primary,)
        if use_secondary and self.second_led:
            leds = (self.primary, self.second_led)
        flags = self._lock_flags(exclude=leds)  # keep other LEDs truthful
        if on:
            for led in leds:
                flags |= LED_FLAG[led]
        if not self._set_flags(flags):
            raise OSError("indicator ioctl failed")
        if self.provider:
            try:
                if use_secondary:
                    if not self.provider.active:
                        self.provider.save()
                    self.provider.set(bool(on))
                else:
                    self._provider_restore()
            except Exception:
                pass

    def release(self):
        if not self.owned:
            return
        self._provider_restore()
        self._set_flags(self._lock_flags())  # resync LEDs to real lock state
        self.owned = False

    def drop(self):
        self._provider_restore()
        if self.provider:
            try:
                self.provider.close()
            except Exception:
                pass
            self.provider = None
        self.second_led = None
        for dos, h in self.handles:
            try:
                self.k32.CloseHandle(h)
            except Exception:
                pass
            self.k32.DefineDosDeviceW(
                DDD_REMOVE_DEFINITION | DDD_RAW_TARGET_PATH, dos, None
            )
        self.handles = []
        self.owned = False
