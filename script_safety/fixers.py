"""Individual fixes. Each takes a list of actions and returns (new_actions, summary).

Fixers never mutate their input and never slow down or alter slow motion unless
the fix is explicitly about that (range, ramp, intensity cap).
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right

from .analysis import PAUSE_MS, find_bursts, find_resumes
from .model import Action

Result = tuple[list[Action], str]


def sanitize(actions: list[Action]) -> Result:
    """Sort by time, collapse duplicate timestamps (last wins), clamp to 0-100, round to integers."""
    by_time: dict[int, float] = {}
    for a in sorted(actions, key=lambda a: a.at):
        by_time[a.at] = a.pos
    out = [Action(t, float(min(100, max(0, round(p))))) for t, p in by_time.items()]
    changed = sum(1 for a in actions if not (0 <= a.pos <= 100) or a.pos != round(a.pos))
    dropped = len(actions) - len(out)
    return out, f"{dropped} duplicate(s) removed, {changed} position(s) clamped/rounded"


def min_interval(actions: list[Action], min_ms: int) -> Result:
    """Merge actions closer than min_ms, keeping whichever of the pair is the more extreme turn."""
    if len(actions) < 2 or min_ms <= 0:
        return [a.copy() for a in actions], "skipped"
    out = [actions[0].copy()]
    dropped = 0
    for a in actions[1:]:
        if a.at - out[-1].at >= min_ms:
            out.append(a.copy())
            continue
        dropped += 1
        if len(out) >= 2:
            ref = out[-2].pos
            if abs(a.pos - ref) > abs(out[-1].pos - ref):
                out[-1] = a.copy()
    return out, f"{dropped} action(s) merged (min interval {min_ms} ms)"


def vibration(actions: list[Action], mode: str = "reduce", factor: float = 0.5) -> Result:
    """Handle vibration bursts: 'keep', 'reduce' amplitude by factor, or 'remove' the burst interior."""
    out = [a.copy() for a in actions]
    if mode == "keep":
        return out, "kept"
    bursts = find_bursts(actions)
    drop: set[int] = set()
    for i, j in bursts:
        if mode == "remove":
            drop.update(range(i + 1, j))
        elif mode == "reduce":
            mean = sum(a.pos for a in actions[i:j + 1]) / (j - i + 1)
            for k in range(i, j + 1):
                out[k].pos = round(mean + (actions[k].pos - mean) * factor)
        else:
            raise ValueError(f"unknown vibration mode '{mode}'")
    if drop:
        out = [a for k, a in enumerate(out) if k not in drop]
    return out, f"{len(bursts)} burst(s) {mode}d"


def _rdp(points: list[Action], eps: float) -> list[Action]:
    """Ramer-Douglas-Peucker using vertical (position) distance from the chord."""
    if len(points) < 3:
        return points
    a, b = points[0], points[-1]
    span = b.at - a.at or 1
    worst, idx = -1.0, 0
    for k in range(1, len(points) - 1):
        p = points[k]
        expected = a.pos + (b.pos - a.pos) * (p.at - a.at) / span
        d = abs(p.pos - expected)
        if d > worst:
            worst, idx = d, k
    if worst <= eps:
        return [a, b]
    return _rdp(points[:idx + 1], eps)[:-1] + _rdp(points[idx:], eps)


def thin_interpolation(actions: list[Action], epsilon: float = 3.0) -> Result:
    """Drop in-between points of one-directional runs when the stroke stays within epsilon of a straight line.
    Turning points are always kept."""
    if len(actions) < 3:
        return [a.copy() for a in actions], "skipped"
    turns = [0]
    for k in range(1, len(actions) - 1):
        d_in = actions[k].pos - actions[k - 1].pos
        d_out = actions[k + 1].pos - actions[k].pos
        if d_in * d_out <= 0:
            turns.append(k)
    turns.append(len(actions) - 1)
    out: list[Action] = []
    for s, e in zip(turns, turns[1:]):
        seg = _rdp(actions[s:e + 1], epsilon)
        out.extend(a.copy() for a in (seg if not out else seg[1:]))
    return out, f"{len(actions) - len(out)} interpolation point(s) removed"


def speed_limit(actions: list[Action], max_speed: float, mode: str = "shorten_start") -> Result:
    """Guarantee no move exceeds max_speed (u/s).

    shorten_start  - each over-fast move stops short, measured from where the previous move ended
                     (what the Handy does internally).
    shorten_center - over-fast strokes shrink symmetrically toward their middle, keeping the
                     stroke centred; a final shorten_start pass guarantees the limit after rounding.
    """
    work = [a.copy() for a in actions]
    if len(work) < 2:
        return work, "skipped"
    if mode == "shorten_center":
        for _ in range(30):
            moved = False
            for k in range(len(work) - 1):
                a, b = work[k], work[k + 1]
                dt = b.at - a.at
                d = b.pos - a.pos
                excess = abs(d) - max_speed * dt / 1000.0
                if dt > 0 and excess > 0.01:
                    half = math.copysign(excess / 2, d)
                    a.pos += half if k > 0 else 0
                    b.pos -= half if k > 0 else excess * math.copysign(1, d)
                    moved = True
            if not moved:
                break
        for a in work:
            a.pos = float(round(a.pos))
    elif mode != "shorten_start":
        raise ValueError(f"unknown speed limit mode '{mode}'")

    out = [work[0]]
    for a in work[1:]:
        prev = out[-1]
        dt = a.at - prev.at
        allowed = math.floor(max_speed * dt / 1000.0 + 1e-9) if dt > 0 else 0
        d = a.pos - prev.pos
        if abs(d) > allowed:
            a = Action(a.at, prev.pos + math.copysign(allowed, d))
        out.append(a)
    changed = sum(1 for x, y in zip(actions, out) if x.pos != y.pos)
    return out, f"{changed} position(s) shortened to stay under {max_speed:.0f} u/s ({mode})"


def remap_range(actions: list[Action], lo: float = 0, hi: float = 100, mode: str = "scale",
                invert: bool = False) -> Result:
    """Fit positions into [lo, hi] by scaling (keeps the shape) or clipping (keeps the speed of small moves)."""
    out = []
    for a in actions:
        p = 100 - a.pos if invert else a.pos
        if mode == "scale":
            p = lo + p * (hi - lo) / 100
        elif mode == "clip":
            p = min(hi, max(lo, p))
        else:
            raise ValueError(f"unknown range mode '{mode}'")
        out.append(Action(a.at, float(round(p))))
    return out, f"range {lo:g}-{hi:g} ({mode}{', inverted' if invert else ''})"


def soft_start(actions: list[Action], ramp_ms: int = 2000, start_factor: float = 0.3,
               pause_ms: int = PAUSE_MS) -> Result:
    """Ramp stroke amplitude up from start_factor to full at the script start and after every pause."""
    out = [a.copy() for a in actions]
    if len(actions) < 2:
        return out, "skipped"
    anchors = [0] + find_resumes(actions, pause_ms)
    for k in anchors:
        t0, anchor = actions[k].at, actions[k].pos
        for m in range(k + 1, len(actions)):
            t = actions[m].at - t0
            if t >= ramp_ms:
                break
            f = start_factor + (1 - start_factor) * t / ramp_ms
            out[m].pos = float(round(anchor + (actions[m].pos - anchor) * f))
    return out, f"{len(anchors)} ramp(s) of {ramp_ms} ms applied"


def intensity_cap(actions: list[Action], max_avg_speed: float, window_ms: int = 30000) -> Result:
    """Scale stroke depth down wherever the average speed over a centred window exceeds max_avg_speed."""
    n = len(actions)
    if n < 3:
        return [a.copy() for a in actions], "skipped"
    times = [a.at for a in actions]
    dist = [0.0]
    psum = [0.0]
    for k in range(n):
        if k:
            dist.append(dist[-1] + abs(actions[k].pos - actions[k - 1].pos))
        psum.append(psum[-1] + actions[k].pos)
    out, touched = [], 0
    for k, a in enumerate(actions):
        lo = bisect_left(times, a.at - window_ms // 2)
        hi = bisect_right(times, a.at + window_ms // 2) - 1
        span = times[hi] - times[lo]
        avg = (dist[hi] - dist[lo]) / span * 1000 if span > 0 else 0.0
        if avg > max_avg_speed:
            centre = (psum[hi + 1] - psum[lo]) / (hi - lo + 1)
            f = max_avg_speed / avg
            out.append(Action(a.at, float(round(centre + (a.pos - centre) * f))))
            touched += 1
        else:
            out.append(a.copy())
    return out, f"{touched} action(s) reduced to keep {window_ms // 1000}s average under {max_avg_speed:.0f} u/s"
