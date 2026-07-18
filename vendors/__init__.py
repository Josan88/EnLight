"""Vendor LED providers: optional extra lights beyond the standard keyboard.

Each module in this package defines a `Provider` class:

    class Provider:
        name = "some-vendor-led"
        def connect(self) -> bool   # True only if this hardware is present
        def save(self)              # remember real state before blinking
        def set(self, on: bool)     # drive the LED
        def restore(self)           # put the real state back
        def close(self)

Providers must self-detect in connect() and return False on machines
without their hardware — the daemon loads every module and simply keeps
the ones that connect. To support a new laptop brand, drop a new file in
this folder; no other code changes needed.
"""

import importlib
import pkgutil


def load_providers():
    providers = []
    for mod_info in pkgutil.iter_modules(__path__):
        try:
            mod = importlib.import_module(f"{__name__}.{mod_info.name}")
            cls = getattr(mod, "Provider", None)
            if cls is None:
                continue
            p = cls()
            if p.connect():
                providers.append(p)
        except Exception:
            continue
    return providers
