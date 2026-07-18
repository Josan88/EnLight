r"""Fn-lock LED provider via the ATK ACPI embedded-controller driver.

Some laptops expose their EC through a \\.\ATKACPI device with a simple
method interface (DSTS reads a device's state, DEVS sets it); device id
0x00100023 is the Fn-lock control, whose LED lives on the keyboard.

Self-detecting: connect() succeeds only if the driver device exists AND
the EC reports the Fn-lock device present -- on every other machine this
module is inert and the daemon skips it.

Hardware note: this LED indicates the real Fn-lock state, so F-key
behavior follows it while blinking; save()/restore() bracket any takeover.
"""

import ctypes
import struct

CONTROL_CODE = 0x0022240C
DSTS = 0x53545344
DEVS = 0x53564544
FNLOCK_ID = 0x00100023

GENERIC_RW = 0xC0000000
FILE_SHARE_RW = 3
OPEN_EXISTING = 3
INVALID_HANDLE = ctypes.c_void_p(-1).value


class Provider:
    name = "fnlock"

    def __init__(self):
        self.k32 = ctypes.windll.kernel32
        self.h = None
        self.active = False
        self._saved = False

    def connect(self):
        h = self.k32.CreateFileW(
            r"\\.\ATKACPI", GENERIC_RW, FILE_SHARE_RW, None, OPEN_EXISTING, 0, None
        )
        if h in (0, INVALID_HANDLE):
            return False
        self.h = h
        if self._get() is None:  # driver present but no fn-lock device
            self.close()
            return False
        return True

    def _call(self, method, args):
        buf = struct.pack("<II", method, len(args)) + args
        inbuf = ctypes.create_string_buffer(buf, len(buf))
        outbuf = ctypes.create_string_buffer(20)
        returned = ctypes.c_ulong(0)
        ok = self.k32.DeviceIoControl(
            self.h, CONTROL_CODE, inbuf, len(buf),
            outbuf, ctypes.sizeof(outbuf), ctypes.byref(returned), None,
        )
        return outbuf.raw[: returned.value] if ok else None

    def _get(self):
        out = self._call(DSTS, struct.pack("<I", FNLOCK_ID))
        if not out or len(out) < 4:
            return None
        val = struct.unpack("<I", out[:4])[0]
        if not (val & 0x00010000):  # presence flag
            return None
        return bool(val & 1)

    def save(self):
        self._saved = bool(self._get())
        self.active = True

    def set(self, on):
        self._call(DEVS, struct.pack("<II", FNLOCK_ID, 1 if on else 0))

    def restore(self):
        if self.active:
            self.set(self._saved)
            self.active = False

    def close(self):
        if self.h:
            self.k32.CloseHandle(self.h)
            self.h = None
