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

SPEED_LIMIT_MODES = ("preserve_anchor", "shorten_start", "shorten_center")
LIMIT_SOURCES = ("observed", "handy_matched", "estimate", "custom")


@dataclass
class DeviceProfile:
    key: str
    name: str
    max_speed: float = 400.0            # units/s the device can actually reach
    min_interval_ms: int = 30           # closer actions are skipped/aborted by the device
    vibration_ok: bool = True           # handles bursts of tiny, fast actions well
    thin_interpolation: bool = False    # in-between points cause micro-stutter
    speed_limit_mode: str = "shorten_start"   # one of SPEED_LIMIT_MODES
    sustained_speed: float = 300.0      # average u/s over the rolling window considered "heavy"
    sustained_window_ms: int = 30000
    min_speed: float = 0.0              # strokes slower than this get re-timed by the device (0 = never)
    firmware_limiter: bool = False      # device rewrites over-fast moves itself (shortening toward the start)
    thin_spacing_ms: int = 0            # keep simplified curve points at least this far apart (0 = no constraint)
    verified: bool = False
    # Where the limits come from: observed (forum testing), handy_matched (a self-chosen safety cap copied
    # from the well-tested Handy), estimate (placeholder) or custom (user profile file).
    limit_source: str = "estimate"
    notes: str = ""

    def __post_init__(self):
        def num(name, lo, hi):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not (lo <= v <= hi):
                raise ValueError(f"profile '{self.key}': {name} must be a number between {lo} and {hi}, got {v!r}")
        num("max_speed", 1, 2000)
        num("min_interval_ms", 0, 1000)
        num("sustained_speed", 1, 2000)
        num("sustained_window_ms", 1000, 600000)
        num("min_speed", 0, 200)
        num("thin_spacing_ms", 0, 2000)
        if self.limit_source not in LIMIT_SOURCES:
            raise ValueError(f"profile '{self.key}': limit_source must be one of {', '.join(LIMIT_SOURCES)}")
        if self.min_speed >= self.max_speed:
            raise ValueError(f"profile '{self.key}': min_speed must be below max_speed")
        if self.speed_limit_mode not in SPEED_LIMIT_MODES:
            raise ValueError(f"profile '{self.key}': speed_limit_mode must be one of {', '.join(SPEED_LIMIT_MODES)}")
        for name in ("vibration_ok", "thin_interpolation", "firmware_limiter", "verified"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"profile '{self.key}': {name} must be true or false")
        for name in ("key", "name", "notes"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"profile: {name} must be text")

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
        speed_limit_mode="preserve_anchor", min_speed=33, firmware_limiter=True, verified=True, limit_source="observed",
        notes="Observed: firmware shortens strokes over ~500 u/s toward their start (moving beat-synced "
              "bottoms) and re-times strokes under ~33 u/s. We pre-limit so the firmware never has to. "
              "Vibrations play well. min_interval is a placeholder."),
    DeviceProfile(
        "handy_ble", "The Handy (Bluetooth)",
        max_speed=550, min_interval_ms=60, vibration_ok=False, thin_interpolation=True,
        speed_limit_mode="preserve_anchor", thin_spacing_ms=120,
        notes="Observed: skips actions at high frequency, micro-stutters after every point (so curves are "
              "simplified to fewer, spaced points), moves faster than online mode. Numbers are placeholders."),
    DeviceProfile(
        "homebrew", "Homebrew device (no firmware limit)",
        max_speed=500, min_interval_ms=20, vibration_ok=False, thin_interpolation=False, limit_source="handy_matched",
        speed_limit_mode="shorten_center",
        notes="For untested and homebrew devices that play every move as fast as their motor can go. The cap is a "
              "self-chosen safety layer matched to the well-tested Handy (online); set your own in the app."),
    DeviceProfile(
        "osr", "OSR2 / SR6",
        max_speed=500, min_interval_ms=20, vibration_ok=False, thin_interpolation=False, limit_source="handy_matched",
        speed_limit_mode="shorten_center",
        notes="No firmware speed limit: plays every move at full servo speed. The cap is a self-chosen safety "
              "layer matched to the well-tested Handy (online); set your own in the app. Vibrations weaker "
              "than on other devices. Interpolation fine."),
    DeviceProfile(
        "ssr1", "SSR1",
        max_speed=500, min_interval_ms=20, vibration_ok=True, thin_interpolation=False, limit_source="handy_matched",
        speed_limit_mode="shorten_center",
        notes="No firmware speed limit: plays every move at full motor speed. The cap is a self-chosen safety "
              "layer matched to the well-tested Handy (online); set your own in the app. Vibrations and "
              "interpolation reported fine."),
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
    if not isinstance(d, dict):
        raise ValueError(f"{Path(path).name}: profile must be a JSON object")
    base_key = d.pop("base", "generic")
    if base_key not in BUILTIN:
        raise ValueError(f"{Path(path).name}: unknown base profile {base_key!r}")
    base = BUILTIN[base_key].to_dict()
    base.update(d)
    base["key"] = d.get("key", Path(path).stem)
    base["name"] = d.get("name", base["key"])
    base["verified"] = d.get("verified", False)
    base["limit_source"] = d.get("limit_source", "custom")
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
