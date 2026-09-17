from __future__ import annotations

from quantum_light_engine.devices.models import DeviceProfile


def validate_default_uniqueness(profiles: list[DeviceProfile]) -> None:
    defaults = [profile for profile in profiles if profile.is_default]
    if len(defaults) > 1:
        raise ValueError("only one device profile may be marked as default")
