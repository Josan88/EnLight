"""macOS indicator backend: keyboard LEDs via IOKit HID.

Drives the LED usage page (0x08) of every keyboard the HID manager
exposes -- caps lock on built-in Apple keyboards, plus num/scroll on
external keyboards that have them. Setting the LED element does not
change the real lock state; macOS re-asserts LEDs on lock changes, and
the daemon re-asserts its pattern every tick, so they coexist.

Permissions: the process (or its terminal) needs Input Monitoring rights
(System Settings > Privacy & Security > Input Monitoring). Without them
IOHIDManagerOpen fails and connect() returns False.

Same two-light contract as the Windows backend: a primary LED for every
state, an optional secondary that joins only during SECONDARY_STATES.
"""

import ctypes

from states import PATTERNS, SECONDARY_STATES

LED_USAGE = {"num": 0x01, "caps": 0x02, "scroll": 0x03}
LED_PAGE = 0x08
ALPHA_SHIFT = 0x00010000  # caps lock bit in CGEventFlags

_iokit = None
_cf = None
_cg = None


def _load_frameworks():
    global _iokit, _cf, _cg
    if _iokit:
        return True
    try:
        _iokit = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/IOKit.framework/IOKit"
        )
        _cf = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        _cg = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
    except OSError:
        return False
    _iokit.IOHIDManagerCreate.restype = ctypes.c_void_p
    _iokit.IOHIDManagerCreate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    _iokit.IOHIDManagerSetDeviceMatching.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _iokit.IOHIDManagerOpen.restype = ctypes.c_int
    _iokit.IOHIDManagerOpen.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    _iokit.IOHIDManagerCopyDevices.restype = ctypes.c_void_p
    _iokit.IOHIDManagerCopyDevices.argtypes = [ctypes.c_void_p]
    _iokit.IOHIDDeviceCopyMatchingElements.restype = ctypes.c_void_p
    _iokit.IOHIDDeviceCopyMatchingElements.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
    ]
    _iokit.IOHIDElementGetUsagePage.restype = ctypes.c_uint32
    _iokit.IOHIDElementGetUsagePage.argtypes = [ctypes.c_void_p]
    _iokit.IOHIDElementGetUsage.restype = ctypes.c_uint32
    _iokit.IOHIDElementGetUsage.argtypes = [ctypes.c_void_p]
    _iokit.IOHIDValueCreateWithIntegerValue.restype = ctypes.c_void_p
    _iokit.IOHIDValueCreateWithIntegerValue.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_long,
    ]
    _iokit.IOHIDDeviceSetValue.restype = ctypes.c_int
    _iokit.IOHIDDeviceSetValue.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    _cf.CFSetGetCount.restype = ctypes.c_long
    _cf.CFSetGetCount.argtypes = [ctypes.c_void_p]
    _cf.CFSetGetValues.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _cf.CFArrayGetCount.restype = ctypes.c_long
    _cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    _cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    _cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
    _cf.CFRelease.argtypes = [ctypes.c_void_p]
    _cg.CGEventSourceFlagsState.restype = ctypes.c_uint64
    _cg.CGEventSourceFlagsState.argtypes = [ctypes.c_int]
    return True


class MacHidBackend:
    name = "mac-hid"

    def __init__(self, primary="caps", secondary="auto"):
        self.primary = primary if primary in LED_USAGE else "caps"
        self.secondary = (secondary or "none").lower()
        self.manager = None
        self.leds = {}       # led name -> [(device, element)]
        self.second_led = None
        self.owned = False

    def connect(self):
        if not _load_frameworks():
            return False
        self.manager = _iokit.IOHIDManagerCreate(None, 0)
        if not self.manager:
            return False
        _iokit.IOHIDManagerSetDeviceMatching(self.manager, None)  # all devices
        if _iokit.IOHIDManagerOpen(self.manager, 0) != 0:
            return False  # likely missing Input Monitoring permission
        dev_set = _iokit.IOHIDManagerCopyDevices(self.manager)
        if not dev_set:
            return False
        count = _cf.CFSetGetCount(dev_set)
        devices = (ctypes.c_void_p * count)()
        _cf.CFSetGetValues(dev_set, devices)
        usage_to_name = {v: k for k, v in LED_USAGE.items()}
        for dev in devices:
            if not dev:
                continue
            elements = _iokit.IOHIDDeviceCopyMatchingElements(dev, None, 0)
            if not elements:
                continue
            for i in range(_cf.CFArrayGetCount(elements)):
                el = _cf.CFArrayGetValueAtIndex(elements, i)
                if not el or _iokit.IOHIDElementGetUsagePage(el) != LED_PAGE:
                    continue
                name = usage_to_name.get(_iokit.IOHIDElementGetUsage(el))
                if name:
                    self.leds.setdefault(name, []).append((dev, el))
        _cf.CFRelease(dev_set)
        if self.primary not in self.leds:
            return False
        self._resolve_secondary()
        return True

    def _resolve_secondary(self):
        sec = self.secondary
        if sec in LED_USAGE and sec != self.primary and sec in self.leds:
            self.second_led = sec
        elif sec == "auto":
            for name in self.leds:
                if name != self.primary:
                    self.second_led = name
                    break

    def _set_led(self, name, on):
        for dev, el in self.leds.get(name, []):
            value = _iokit.IOHIDValueCreateWithIntegerValue(
                None, el, 0, 1 if on else 0
            )
            if value:
                _iokit.IOHIDDeviceSetValue(dev, el, value)
                _cf.CFRelease(value)

    def _true_state(self, name):
        """The real lock state a released LED should show."""
        if name == "caps":
            return bool(_cg.CGEventSourceFlagsState(1) & ALPHA_SHIFT)
        return False  # num/scroll lock have no meaningful macOS state

    def show(self, state, tick):
        self.owned = True
        on = bool(PATTERNS[state][tick % 8])
        self._set_led(self.primary, on)
        if self.second_led:
            if state in SECONDARY_STATES:
                self._set_led(self.second_led, on)
            else:
                self._set_led(self.second_led, self._true_state(self.second_led))

    def release(self):
        if not self.owned:
            return
        self._set_led(self.primary, self._true_state(self.primary))
        if self.second_led:
            self._set_led(self.second_led, self._true_state(self.second_led))
        self.owned = False

    def drop(self):
        try:
            self.release()
        except Exception:
            pass
        if self.manager:
            _cf.CFRelease(self.manager)
            self.manager = None
        self.leds = {}
        self.second_led = None
