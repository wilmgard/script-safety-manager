"""Read-only safety analysis of a funscript against a device profile."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .model import Action, Funscript, speed
from .profiles import DeviceProfile

# Detection thresholds shared by analysis and fixers.
BURST_INTERVAL_MS = 100     # intervals shorter than this may belong to a vibration burst
BURST_AMPLITUDE = 30        # ...if each move is no bigger than this
BURST_MIN_ACTIONS = 5
PAUSE_MS = 3000             # a hold at least this long counts as a pause
HOLD_TOLERANCE = 2          # pos jitter still considered "holding"
REVERSAL_FRACTION = 0.8     # reversal is "hard" when both sides exceed this share of max speed
RESUME_FRACTION = 0.7       # resuming above this share of max speed is flagged

SEVERITIES = ("info", "warning", "danger")


@dataclass
class Finding:
    kind: str
    severity: str
    start_ms: int
    end_ms: int
    message: str
    value: float | None = None


@dataclass
class Report:
    profile: str
    axis: str
    stats: dict
    findings: list[Finding] = field(default_factory=list)

    @property
    def score(self) -> int:
        """0..100, 100 = nothing to worry about on this device."""
        penalty = {"info": 0, "warning": 1, "danger": 4}
        total = sum(penalty[f.severity] for f in self.findings)
        return max(0, 100 - total)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f.kind] = out.get(f.kind, 0) + 1
        return out

    def to_dict(self) -> dict:
        return {"profile": self.profile, "axis": self.axis, "score": self.score,
                "stats": self.stats, "counts": self.counts(),
                "findings": [asdict(f) for f in self.findings]}


# --- shared detectors -------------------------------------------------------

def find_bursts(actions: list[Action], interval_ms: int = BURST_INTERVAL_MS,
                amplitude: float = BURST_AMPLITUDE, min_actions: int = BURST_MIN_ACTIONS) -> list[tuple[int, int]]:
    """Runs of small, rapid actions. Returns inclusive (first, last) action index pairs."""
    runs = []
    start = None
    for i in range(1, len(actions) + 1):
        ok = (i < len(actions)
              and actions[i].at - actions[i - 1].at < interval_ms
              and abs(actions[i].pos - actions[i - 1].pos) <= amplitude)
        if ok and start is None:
            start = i - 1
        elif not ok and start is not None:
            if i - start >= min_actions:
                runs.append((start, i - 1))
            start = None
    return runs


def turning_points(actions: list[Action]) -> list[int]:
    """Indices (excluding first and last) where the stroke reverses direction. Flat stretches are
    skipped over, so a hold inside a one-way run is not a turn; a flat top/bottom turns at its first point."""
    out = []
    last_dir = 0
    flat_start = None
    for k in range(1, len(actions)):
        d = actions[k].pos - actions[k - 1].pos
        if d == 0:
            if flat_start is None:
                flat_start = k - 1
            continue
        direction = 1 if d > 0 else -1
        if last_dir and direction != last_dir:
            out.append(flat_start if flat_start is not None else k - 1)
        flat_start = None
        last_dir = direction
    return out


def emphasis_changes(before: list[Action], after: list[Action]) -> tuple[list[int], list[int]]:
    """Compare stroke structure. Returns (new_turns, lost_turns) as timestamps:
    new_turns  - points that were in-between points but became a top/bottom (the stroke now turns
                 somewhere the scripter did not put a turn, e.g. off the beat);
    lost_turns - tops/bottoms that are no longer turns (flattened or removed)."""
    t_before = {before[k].at for k in turning_points(before)}
    t_after = {after[k].at for k in turning_points(after)}
    present = {a.at for a in after}
    first_last = {after[0].at, after[-1].at} if after else set()
    new = sorted(t for t in t_after - t_before if t not in first_last)
    lost = sorted(t for t in t_before - t_after if t in present and t not in first_last)
    return new, lost


def _cluster(times: list[int], gap_ms: int = 2000) -> list[list[int]]:
    groups: list[list[int]] = []
    for t in times:
        if groups and t - groups[-1][-1] <= gap_ms:
            groups[-1].append(t)
        else:
            groups.append([t])
    return groups


def emphasis_findings(before: list[Action], after: list[Action], who: str) -> list[Finding]:
    new, _lost = emphasis_changes(before, after)
    return [Finding("emphasis_lost", "warning", g[0], g[-1],
                    f"{who} turns the stroke at {len(g)} in-between point(s): strokes no longer turn where "
                    f"the script does (off the beat)", len(g))
            for g in _cluster(new)]


def find_resumes(actions: list[Action], pause_ms: int = PAUSE_MS) -> list[int]:
    """Indices of the action where motion restarts after a hold of at least pause_ms."""
    resumes = []
    hold_start = 0
    for i in range(1, len(actions)):
        if abs(actions[i].pos - actions[hold_start].pos) <= HOLD_TOLERANCE:
            continue
        if actions[i - 1].at - actions[hold_start].at >= pause_ms:
            resumes.append(i - 1)
        hold_start = i
    return resumes


def rolling_speed(actions: list[Action], window_ms: int) -> list[tuple[int, float]]:
    """Average speed (distance/time) over the window ending at each action."""
    out = []
    j = 0
    dist = 0.0
    for i in range(1, len(actions)):
        dist += abs(actions[i].pos - actions[i - 1].pos)
        while actions[i].at - actions[j].at > window_ms and j < i - 1:
            dist -= abs(actions[j + 1].pos - actions[j].pos)
            j += 1
        span = actions[i].at - actions[j].at
        if span >= window_ms * 0.9:
            out.append((i, dist / span * 1000.0))
    return out


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(p * (len(s) - 1)))]


def _ranges(flags: list[bool]) -> list[tuple[int, int]]:
    """Group consecutive True interval indices into (first, last) pairs."""
    out, start = [], None
    for i, f in enumerate(flags + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            out.append((start, i - 1))
            start = None
    return out


# --- analysis ---------------------------------------------------------------

def analyze(script: Funscript, profile: DeviceProfile) -> Report:
    acts = script.actions
    findings: list[Finding] = []
    add = findings.append

    # Structure
    bad_pos = [a for a in acts if not 0 <= a.pos <= 100]
    if bad_pos:
        add(Finding("out_of_range", "danger", bad_pos[0].at, bad_pos[-1].at,
                    f"{len(bad_pos)} action(s) outside 0-100", len(bad_pos)))
    if any(b.at < a.at for a, b in zip(acts, acts[1:])):
        add(Finding("unsorted", "danger", 0, script.duration_ms, "actions are not in time order"))
    dupes = [b for a, b in zip(acts, acts[1:]) if b.at == a.at]
    if dupes:
        add(Finding("duplicate_time", "warning", dupes[0].at, dupes[-1].at,
                    f"{len(dupes)} action(s) share a timestamp with the previous one", len(dupes)))
    if any(a.pos != int(a.pos) for a in acts):
        add(Finding("non_integer", "info", 0, script.duration_ms, "positions are not whole numbers"))

    if len(acts) < 2:
        stats = {"actions": len(acts), "duration_s": script.duration_ms / 1000}
        return Report(profile.key, script.axis, stats, findings)

    pairs = list(zip(acts, acts[1:]))
    speeds = [speed(a, b) for a, b in pairs]
    finite = [s for s in speeds if s != float("inf")]

    # Over-speed, grouped into contiguous stretches
    for i, j in _ranges([s > profile.max_speed for s in speeds]):
        peak = max(speeds[i:j + 1])
        sev = "danger" if peak > profile.max_speed * 1.5 else "warning"
        peak_txt = "instant" if peak == float("inf") else f"{peak:.0f} u/s"
        add(Finding("over_speed", sev, acts[i].at, acts[j + 1].at,
                    f"{j - i + 1} move(s) above {profile.max_speed:.0f} u/s (peak {peak_txt})",
                    None if peak == float("inf") else round(peak, 1)))

    # Devices whose firmware limits speed itself: predict what it will do to the stroke structure
    if profile.firmware_limiter and any(s > profile.max_speed for s in speeds):
        from .fixers import sanitize, speed_limit
        clean = sanitize(acts)[0]
        emulated = speed_limit(clean, profile.max_speed, "shorten_start")[0]
        findings.extend(emphasis_findings(clean, emulated, f"{profile.name} firmware"))

    # Strokes slow enough that the firmware re-times them
    if profile.min_speed > 0:
        slow = [abs(b.pos - a.pos) >= 3 and b.at > a.at and s < profile.min_speed for (a, b), s in zip(pairs, speeds)]
        for i, j in _ranges(slow):
            add(Finding("slow_retime", "warning", acts[i].at, acts[j + 1].at,
                        f"{j - i + 1} stroke(s) under {profile.min_speed:.0f} u/s: device firmware may shift "
                        f"their timing", round(min(speeds[i:j + 1]), 1)))

    # Actions closer together than the device can follow
    for i, j in _ranges([0 < b.at - a.at < profile.min_interval_ms for a, b in pairs]):
        shortest = min(acts[k + 1].at - acts[k].at for k in range(i, j + 1))
        add(Finding("short_interval", "warning", acts[i].at, acts[j + 1].at,
                    f"{j - i + 1} interval(s) under {profile.min_interval_ms} ms (shortest {shortest} ms)", shortest))

    # Vibration bursts
    for i, j in find_bursts(acts):
        rate = (j - i) / max(1, acts[j].at - acts[i].at) * 1000
        # A fidelity note rather than a hazard: devices that can't follow simply skip or dampen these,
        # and actions too close to follow are already reported as short_interval.
        add(Finding("vibration", "info", acts[i].at, acts[j].at,
                    f"vibration burst: {j - i + 1} actions at {rate:.0f}/s"
                    + ("" if profile.vibration_ok else " (device may skip or dampen these)"), round(rate, 1)))

    # Hard reversals (fast in, fast out, opposite direction) stress motors and gearing
    hard = REVERSAL_FRACTION * profile.max_speed
    for k in range(1, len(acts) - 1):
        d_in = acts[k].pos - acts[k - 1].pos
        d_out = acts[k + 1].pos - acts[k].pos
        if d_in * d_out < 0 and speeds[k - 1] > hard and speeds[k] > hard:
            if not (findings and findings[-1].kind == "hard_reversal" and acts[k].at - findings[-1].end_ms < 1000):
                add(Finding("hard_reversal", "info", acts[k].at, acts[k].at,
                            "fast direction change at both sides of the turn"))
            else:
                findings[-1].end_ms = acts[k].at

    # Starting / resuming at high speed
    first_fast = speeds[0] > RESUME_FRACTION * profile.max_speed
    if first_fast:
        add(Finding("hard_start", "warning", acts[0].at, acts[1].at,
                    f"script starts at {speeds[0]:.0f} u/s with no ramp-in"))
    for k in find_resumes(acts):
        window = [speeds[m] for m in range(k, len(speeds)) if acts[m].at - acts[k].at < 1000]
        if window and max(window) > RESUME_FRACTION * profile.max_speed:
            add(Finding("hard_resume", "warning", acts[k].at, acts[k].at + 1000,
                        f"resumes at {max(window):.0f} u/s right after a pause"))

    # Sustained intensity (heat / fatigue)
    heavy = [(i, s) for i, s in rolling_speed(acts, profile.sustained_window_ms) if s > profile.sustained_speed]
    if heavy:
        groups, cur = [], [heavy[0]]
        for h in heavy[1:]:
            if h[0] - cur[-1][0] <= 1:
                cur.append(h)
            else:
                groups.append(cur)
                cur = [h]
        groups.append(cur)
        for g in groups:
            peak = max(s for _, s in g)
            start = max(0, acts[g[0][0]].at - profile.sustained_window_ms)
            add(Finding("sustained", "warning", start, acts[g[-1][0]].at,
                        f"average {peak:.0f} u/s over {profile.sustained_window_ms // 1000}s "
                        f"(limit {profile.sustained_speed:.0f})", round(peak, 1)))

    findings.sort(key=lambda f: (f.start_ms, f.kind))
    dts = [b.at - a.at for a, b in pairs]
    stats = {
        "actions": len(acts),
        "duration_s": round(script.duration_ms / 1000, 1),
        "avg_speed": round(sum(abs(b.pos - a.pos) for a, b in pairs) / max(1, acts[-1].at - acts[0].at) * 1000, 1),
        "p95_speed": round(_percentile(finite, 0.95), 1),
        "max_speed": "instant" if len(finite) < len(speeds) else round(max(finite), 1),
        "min_interval_ms": min(dts),
        "actions_per_s": round(len(acts) / max(1, script.duration_ms) * 1000, 2),
        "over_speed_moves": sum(s > profile.max_speed for s in speeds),
    }
    return Report(profile.key, script.axis, stats, findings)
