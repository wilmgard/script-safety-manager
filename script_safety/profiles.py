"""Device profiles: the hardware limits each check and fix is measured against.

Only the Handy (online) speed limit (~500 u/s) is backed by an observation
(eroscripts "Studied my toys' behavior" thread). Every other number is a
conservative placeholder marked verified=False; measure your device with the
stress test script and override via a profile JSON file.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class DeviceProfile:
    key: str
    name: str
    max_speed: float = 400.0            # units/s the device can actually reach
    min_interval_ms: int = 30           # closer actions are skipped/aborted by the device
    vibration_ok: bool = True           # handles bursts of tiny, fast actions well
    thin_interpolation: bool = False    # in-between points cause micro-stutter
    speed_limit_mode: str = "shorten_start"   # shorten_start | shorten_center
    sustained_speed: float = 300.0      # average u/s over the rolling window considered "heavy"
    sustained_window_ms: int = 30000
    verified: bool = False
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceProfile":
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown profile field(s): {', '.join(sorted(unknown))}")
        return cls(**d)

    def to_dict(self) -> dict:
        return asdict(self)


BUILTIN: dict[str, DeviceProfile] = {p.key: p for p in [
    DeviceProfile(
        "handy_online", "The Handy (online/WiFi)",
        max_speed=500, min_interval_ms=20, vibration_ok=True, thin_interpolation=False,
        speed_limit_mode="shorten_start", verified=True,
        notes="Speed limit ~500 u/s observed; device itself shortens strokes toward their start. "
              "Vibrations play well. min_interval is a placeholder."),
    DeviceProfile(
        "handy_ble", "The Handy (Bluetooth)",
        max_speed=550, min_interval_ms=60, vibration_ok=False, thin_interpolation=True,
        speed_limit_mode="shorten_start",
        notes="Observed: skips actions at high frequency, micro-stutters on interpolation points, "
              "moves faster than online mode. Numbers are placeholders."),
    DeviceProfile(
        "osr", "OSR2 / SR6",
        max_speed=450, min_interval_ms=20, vibration_ok=False, thin_interpolation=False,
        speed_limit_mode="shorten_center",
        notes="Servo-dependent. Vibrations weaker than on other devices. Interpolation fine."),
    DeviceProfile(
        "ssr1", "SSR1",
        max_speed=700, min_interval_ms=15, vibration_ok=True, thin_interpolation=False,
        speed_limit_mode="shorten_center",
        notes="Brushless gimbal motor; vibrations and interpolation reported fine."),
    DeviceProfile(
        "launch", "Kiiroo Launch",
        max_speed=400, min_interval_ms=100, vibration_ok=False, thin_interpolation=True,
        notes="Unmeasured placeholder."),
    DeviceProfile(
        "keon", "Kiiroo Keon",
        max_speed=400, min_interval_ms=100, vibration_ok=False, thin_interpolation=True,
        notes="Unmeasured placeholder."),
    DeviceProfile(
        "a10", "Vorze A10 Piston",
        max_speed=350, min_interval_ms=100, vibration_ok=False, thin_interpolation=True,
        notes="Unmeasured placeholder."),
    DeviceProfile(
        "generic", "Generic / conservative",
        max_speed=400, min_interval_ms=50, vibration_ok=False, thin_interpolation=False,
        notes="Conservative defaults for unknown hardware."),
]}


def user_profile_dir() -> Path:
    return Path.home() / ".script_safety_manager" / "profiles"


def load_profile_file(path: str | Path) -> DeviceProfile:
    """Load a profile JSON. Fields not given fall back to the profile named in 'base' (default: generic)."""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    base = BUILTIN.get(d.pop("base", "generic"), BUILTIN["generic"]).to_dict()
    base.update(d)
    base["key"] = d.get("key", Path(path).stem)
    base["name"] = d.get("name", base["key"])
    base["verified"] = d.get("verified", False)
    return DeviceProfile.from_dict(base)


def all_profiles() -> dict[str, DeviceProfile]:
    profiles = dict(BUILTIN)
    d = user_profile_dir()
    if d.is_dir():
        for p in sorted(d.glob("*.json")):
            prof = load_profile_file(p)
            profiles[prof.key] = prof
    return profiles


def get_profile(key_or_path: str) -> DeviceProfile:
    if key_or_path.lower().endswith(".json"):
        return load_profile_file(key_or_path)
    profiles = all_profiles()
    if key_or_path not in profiles:
        raise KeyError(f"unknown profile '{key_or_path}'. Available: {', '.join(profiles)}")
    return profiles[key_or_path]
