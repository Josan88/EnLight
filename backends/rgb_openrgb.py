"""OpenRGB backend: full-color status on any RGB keyboard, any vendor.

Requires OpenRGB running with its SDK server (localhost:6742) and the
openrgb-python package. Falls back to all RGB devices if no keyboard is
exposed; remembers each device's mode and restores it on release.
"""

from states import RGB_PATTERNS


class OpenRGBBackend:
    name = "openrgb"

    def __init__(self):
        self.client = None
        self.devices = []
        self.saved_modes = {}
        self.owned = False

    def connect(self):
        try:
            from openrgb import OpenRGBClient
            from openrgb.utils import DeviceType
        except ImportError:
            return False
        try:
            self.client = OpenRGBClient(name="agent-keylight")
            devs = self.client.get_devices_by_type(DeviceType.KEYBOARD)
            self.devices = devs or list(self.client.devices)
            return bool(self.devices)
        except Exception:
            self.drop()
            return False

    def drop(self):
        try:
            if self.client:
                self.client.disconnect()
        except Exception:
            pass
        self.client = None
        self.devices = []
        self.saved_modes = {}
        self.owned = False

    def show(self, state, tick):
        from openrgb.utils import RGBColor
        if not self.owned:
            for d in self.devices:
                try:
                    self.saved_modes[d.id] = d.active_mode
                except Exception:
                    pass
                for mode in ("Direct", "Static"):
                    try:
                        d.set_mode(mode)
                        break
                    except Exception:
                        continue
            self.owned = True
        (r, g, b), pattern = RGB_PATTERNS[state]
        color = RGBColor(r, g, b) if pattern[tick % 8] else RGBColor(0, 0, 0)
        for d in self.devices:
            d.set_color(color, fast=True)

    def release(self):
        if not self.owned:
            return
        for d in self.devices:
            try:
                m = self.saved_modes.get(d.id)
                if m is not None:
                    d.set_mode(m)
            except Exception:
                pass
        self.owned = False
