"""Chains the fixers in a safe order and records what changed."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from . import __version__, fixers
from .analysis import emphasis_changes
from .model import Funscript
from .profiles import SPEED_LIMIT_MODES, DeviceProfile

CHOICES = {
    "vibration": ("keep", "reduce", "remove"),
    "thin": ("auto", "on", "off"),
    "range_mode": ("scale", "clip"),
    "speed_mode": (None,) + SPEED_LIMIT_MODES,
    "anchor": ("bottom", "top"),
    "slow_mode": ("hold_then_move", "move_then_hold"),
}


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
    anchor: str = "bottom"             # preserve_anchor keeps every bottom (or top) exactly in place
    slow_fix: bool = True              # only acts on profiles with min_speed > 0
    slow_mode: str = "hold_then_move"

    def validate(self) -> None:
        for name, allowed in CHOICES.items():
            if getattr(self, name) not in allowed:
                raise ValueError(f"{name} must be one of {', '.join(map(str, allowed))}")
        if not 0 <= self.range_min < self.range_max <= 100:
            raise ValueError("range must satisfy 0 <= min < max <= 100")
        if self.max_speed is not None and not 1 <= self.max_speed <= 2000:
            raise ValueError("max_speed must be between 1 and 2000 u/s")
        if not 0 <= self.vibration_factor <= 1:
            raise ValueError("vibration_factor must be between 0 and 1")
        if not 0 <= self.ramp_start_factor <= 1:
            raise ValueError("ramp_start_factor must be between 0 and 1")
        if not 0 <= self.ramp_ms <= 60000:
            raise ValueError("ramp_ms must be between 0 and 60000")
        if not 0 <= self.thin_epsilon <= 50:
            raise ValueError("thin_epsilon must be between 0 and 50")

    @property
    def is_safe(self) -> bool:
        """Output deserves the '.safe' name only when the speed guarantee actually ran."""
        return self.speed_limit


@dataclass
class FixResult:
    script: Funscript
    steps: list[tuple[str, str]] = field(default_factory=list)
    removed: int = 0
    moved: int = 0
    added: int = 0
    new_turns: list[int] = field(default_factory=list)   # timestamps where the stroke now turns off-script


def apply(script: Funscript, profile: DeviceProfile, opts: FixOptions | None = None) -> FixResult:
    opts = opts or FixOptions()
    opts.validate()
    acts = [a.copy() for a in script.actions]
    steps: list[tuple[str, str]] = []

    def run(name, fn, *args, **kw):
        nonlocal acts
        acts, summary = fn(acts, *args, **kw)
        steps.append((name, summary))

    # Structural cleanup first; every later step assumes sorted, unique, integer data.
    run("sanitize", fixers.sanitize)
    reference = [a.copy() for a in acts]
    if opts.vibration != "keep":
        run("vibration", fixers.vibration, opts.vibration, opts.vibration_factor)
    if opts.min_interval:
        run("min_interval", fixers.min_interval, profile.min_interval_ms)
    if opts.thin == "on" or (opts.thin == "auto" and profile.thin_interpolation):
        run("thin", fixers.thin_interpolation, opts.thin_epsilon, profile.thin_spacing_ms)
    if opts.invert or opts.range_min > 0 or opts.range_max < 100:
        run("range", fixers.remap_range, opts.range_min, opts.range_max, opts.range_mode, opts.invert)
    if opts.soft_start:
        run("soft_start", fixers.soft_start, opts.ramp_ms, opts.ramp_start_factor)
    if opts.intensity_cap:
        # Aim 10% under the limit: the cap uses a centred window, the check a trailing one, and later
        # steps (speed limit, slow fix) shift positions slightly.
        run("intensity_cap", fixers.intensity_cap, profile.sustained_speed * 0.9, profile.sustained_window_ms)
    # Speed limit runs after everything that moves positions, so nothing can push a move back over it.
    if opts.speed_limit:
        run("speed_limit", fixers.speed_limit, opts.max_speed or profile.max_speed,
            opts.speed_mode or profile.speed_limit_mode, opts.anchor)
    # Slow fix only inserts holds and moves at min_speed (< max_speed), so it cannot break the limit.
    if opts.slow_fix and profile.min_speed > 0:
        run("slow_fix", fixers.slow_fix, profile.min_speed, opts.slow_mode, min_gap_ms=profile.min_interval_ms)
    for a in acts:   # positions can only leave 0-100 through a bug; never let that reach a file
        a.pos = min(100.0, max(0.0, a.pos))

    before = {a.at: a.pos for a in script.actions}
    after = {a.at: a.pos for a in acts}
    removed = sum(1 for t in before if t not in after)
    added = sum(1 for t in after if t not in before)
    moved = sum(1 for t, p in after.items() if t in before and round(before[t]) != p)
    new_turns, _ = emphasis_changes(reference, acts) if acts else ([], [])

    out = script.copy()
    out.actions = acts
    meta = out.raw.setdefault("metadata", {})
    if not isinstance(meta, dict):
        meta = out.raw["metadata"] = {}
    meta["safety_manager"] = {
        "version": __version__,
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": profile.key,
        "speed_limited": opts.is_safe,
        "options": asdict(opts),
        "steps": [f"{n}: {s}" for n, s in steps],
        "actions_removed": removed,
        "actions_moved": moved,
        "actions_added": added,
        "new_turns": len(new_turns),
    }
    return FixResult(out, steps, removed, moved, added, new_turns)
