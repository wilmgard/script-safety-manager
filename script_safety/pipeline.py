"""Chains the fixers in a safe order and records what changed."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from . import __version__, fixers
from .model import Funscript
from .profiles import DeviceProfile


@dataclass
class FixOptions:
    sanitize: bool = True
    min_interval: bool = True
    vibration: str = "keep"            # keep | reduce | remove
    vibration_factor: float = 0.5
    thin: str = "auto"                 # auto (follow profile) | on | off
    thin_epsilon: float = 3.0
    range_min: float = 0
    range_max: float = 100
    range_mode: str = "scale"          # scale | clip
    invert: bool = False
    soft_start: bool = True
    ramp_ms: int = 2000
    ramp_start_factor: float = 0.3
    intensity_cap: bool = False
    speed_limit: bool = True
    max_speed: float | None = None     # None = profile value
    speed_mode: str | None = None      # None = profile value


@dataclass
class FixResult:
    script: Funscript
    steps: list[tuple[str, str]] = field(default_factory=list)
    removed: int = 0
    moved: int = 0


def apply(script: Funscript, profile: DeviceProfile, opts: FixOptions | None = None) -> FixResult:
    opts = opts or FixOptions()
    acts = [a.copy() for a in script.actions]
    steps: list[tuple[str, str]] = []

    def run(name, fn, *args, **kw):
        nonlocal acts
        acts, summary = fn(acts, *args, **kw)
        steps.append((name, summary))

    # Structural cleanup first; every later step assumes sorted, unique, integer data.
    run("sanitize", fixers.sanitize)
    if opts.vibration != "keep":
        run("vibration", fixers.vibration, opts.vibration, opts.vibration_factor)
    if opts.min_interval:
        run("min_interval", fixers.min_interval, profile.min_interval_ms)
    if opts.thin == "on" or (opts.thin == "auto" and profile.thin_interpolation):
        run("thin", fixers.thin_interpolation, opts.thin_epsilon)
    if opts.invert or opts.range_min > 0 or opts.range_max < 100:
        run("range", fixers.remap_range, opts.range_min, opts.range_max, opts.range_mode, opts.invert)
    if opts.soft_start:
        run("soft_start", fixers.soft_start, opts.ramp_ms, opts.ramp_start_factor)
    if opts.intensity_cap:
        run("intensity_cap", fixers.intensity_cap, profile.sustained_speed, profile.sustained_window_ms)
    # Speed limit runs last so nothing after it can push a move back over the limit.
    if opts.speed_limit:
        run("speed_limit", fixers.speed_limit, opts.max_speed or profile.max_speed,
            opts.speed_mode or profile.speed_limit_mode)

    before = {a.at: a.pos for a in script.actions}
    after = {a.at: a.pos for a in acts}
    removed = sum(1 for t in before if t not in after)
    moved = sum(1 for t, p in after.items() if t in before and round(before[t]) != p)

    out = script.copy()
    out.actions = acts
    meta = out.raw.setdefault("metadata", {})
    if not isinstance(meta, dict):
        meta = out.raw["metadata"] = {}
    meta["safety_manager"] = {
        "version": __version__,
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": profile.key,
        "options": asdict(opts),
        "steps": [f"{n}: {s}" for n, s in steps],
        "actions_removed": removed,
        "actions_moved": moved,
    }
    return FixResult(out, steps, removed, moved)
