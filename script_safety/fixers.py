"""Individual fixes. Each takes a list of actions and returns (new_actions, summary).

Fixers never mutate their input and never slow down or alter slow motion unless
the fix is explicitly about that (range, ramp, intensity cap).
"""
from __future__ import annotations

import heapq
import math
from bisect import bisect_left, bisect_right

from .analysis import PAUSE_MS, find_bursts, find_resumes, rolling_speed, turning_points
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


def _best_split(points: list[Action], i: int, j: int, spacing: int) -> tuple[float, int]:
    """Largest vertical deviation from the chord i..j among points at least `spacing` ms from both ends."""
    a, b = points[i], points[j]
    span = b.at - a.at or 1
    worst, idx = -1.0, -1
    for k in range(i + 1, j):
        p = points[k]
        if p.at - a.at < spacing or b.at - p.at < spacing:
            continue
        d = abs(p.pos - (a.pos + (b.pos - a.pos) * (p.at - a.at) / span))
        if d > worst:
            worst, idx = d, k
    return worst, idx


def _simplify_run(points: list[Action], eps: float, spacing: int) -> list[int]:
    """Greedy Ramer-Douglas-Peucker (iterative, so long runs cannot overflow the stack): keep adding the
    point that deviates most from the current polyline until every remaining point is within eps or no
    candidate respects the spacing. Returns kept indices."""
    kept = {0, len(points) - 1}
    heap = []
    worst, idx = _best_split(points, 0, len(points) - 1, spacing)
    if idx >= 0:
        heapq.heappush(heap, (-worst, idx, 0, len(points) - 1))
    while heap:
        neg, idx, i, j = heapq.heappop(heap)
        if -neg <= eps:
            break
        kept.add(idx)
        for lo, hi in ((i, idx), (idx, j)):
            w, k = _best_split(points, lo, hi, spacing)
            if k >= 0:
                heapq.heappush(heap, (-w, k, lo, hi))
    return sorted(kept)


def thin_interpolation(actions: list[Action], epsilon: float = 3.0, spacing_ms: int = 0) -> Result:
    """Simplify one-directional runs to fewer points that keep the curve's shape within epsilon.
    With spacing_ms, kept in-between points are also at least that far apart (for devices that stutter
    after every point). Turning points are always kept."""
    if len(actions) < 3:
        return [a.copy() for a in actions], "skipped"
    turns = [0] + turning_points(actions) + [len(actions) - 1]
    out: list[Action] = []
    for s, e in zip(turns, turns[1:]):
        run = actions[s:e + 1]
        keep = _simplify_run(run, epsilon, spacing_ms)
        out.extend(run[k].copy() for k in (keep if not out else keep[1:]))
    spaced = f", spaced >= {spacing_ms} ms" if spacing_ms else ""
    return out, f"{len(actions) - len(out)} interpolation point(s) removed{spaced}"


def _allowed(a: Action, b: Action, max_speed: float) -> int:
    dt = b.at - a.at
    return math.floor(max_speed * dt / 1000.0 + 1e-9) if dt > 0 else 0


def _preserve_anchor(work: list[Action], max_speed: float, anchor: str) -> bool:
    """Shrink over-fast strokes without moving anchor points (every bottom, or every top).
    Returns True when the limit holds everywhere without moving an anchor."""
    turns = set(turning_points(work))
    want = (lambda k: work[k].pos < work[k - 1].pos) if anchor == "bottom" else (lambda k: work[k].pos > work[k - 1].pos)
    anchors = {k for k in turns if want(k)}
    stuck = False
    for _ in range(200):
        moved = False
        for k in range(len(work) - 1):
            a, b = work[k], work[k + 1]
            excess = abs(b.pos - a.pos) - _allowed(a, b, max_speed)
            if excess <= 0:
                continue
            fixed_a = k in anchors or k == 0
            fixed_b = (k + 1) in anchors
            if fixed_a and fixed_b:
                stuck = True
                continue
            toward = math.copysign(1, b.pos - a.pos)
            if fixed_a:
                b.pos -= toward * excess
            elif fixed_b:
                a.pos += toward * excess
            else:
                half = excess // 2
                a.pos += toward * (excess - half)
                b.pos -= toward * half
            moved = True
        if not moved:
            return not stuck
    return False


def speed_limit(actions: list[Action], max_speed: float, mode: str = "shorten_start",
                anchor: str = "bottom") -> Result:
    """Guarantee no move exceeds max_speed (u/s).

    preserve_anchor - bottoms (or tops) keep their exact time and depth; over-fast strokes are shortened
                      from the other end, so beat-synced thrusts stay on the beat and no in-between
                      point turns into a new bottom.
    shorten_start   - each over-fast move stops short, measured from where the previous move ended
                      (what the Handy firmware does; can move beat-synced bottoms).
    shorten_center  - over-fast strokes shrink symmetrically toward their middle.
    Every mode finishes with a shorten_start pass, so the limit holds even in cases the gentler
    modes cannot solve (e.g. two anchors too close together).
    """
    work = [a.copy() for a in actions]
    if len(work) < 2:
        return work, "skipped"
    note = ""
    if mode == "preserve_anchor":
        if anchor not in ("bottom", "top"):
            raise ValueError(f"unknown anchor '{anchor}'")
        if not _preserve_anchor(work, max_speed, anchor):
            note = "; some anchors had to move"
        mode_txt = f"{mode}, {anchor}s kept"
    elif mode == "shorten_center":
        mode_txt = mode
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
    elif mode == "shorten_start":
        mode_txt = mode
    else:
        raise ValueError(f"unknown speed limit mode '{mode}'")

    out = [work[0]]
    for a in work[1:]:
        prev = out[-1]
        allowed = _allowed(prev, a, max_speed)
        d = a.pos - prev.pos
        if abs(d) > allowed:
            a = Action(a.at, prev.pos + math.copysign(allowed, d))
        out.append(a)
    changed = sum(1 for x, y in zip(actions, out) if x.pos != y.pos)
    return out, f"{changed} position(s) shortened to stay under {max_speed:.0f} u/s ({mode_txt}{note})"


def slow_fix(actions: list[Action], min_speed: float, mode: str = "hold_then_move",
             min_move: float = 3, min_gap_ms: int = 0) -> Result:
    """Rewrite strokes slower than min_speed as a hold plus a move at min_speed, for devices whose
    firmware would otherwise re-time slow strokes. Both endpoints keep their exact time and position.

    hold_then_move - wait at the start, then arrive at the end point exactly on time (default: keeps
                     the timing of the point the stroke is heading to, usually the beat).
    move_then_hold - leave on time and wait at the end point.
    Moves smaller than min_move units are treated as holds and left alone.
    """
    if min_speed <= 0 or len(actions) < 2:
        return [a.copy() for a in actions], "skipped"
    if mode not in ("hold_then_move", "move_then_hold"):
        raise ValueError(f"unknown slow fix mode '{mode}'")
    out = [actions[0].copy()]
    added = 0
    for a, b in zip(actions, actions[1:]):
        d = abs(b.pos - a.pos)
        dt = b.at - a.at
        if d >= min_move and dt > 0 and d / dt * 1000 < min_speed:
            move_ms = math.floor(d * 1000 / min_speed)   # floor: the move ends up at or just above min_speed
            t = b.at - move_ms if mode == "hold_then_move" else a.at + move_ms
            # Barely-slow strokes would put the new point too close to an endpoint for the device to
            # follow; nudge it inward (the move gets slightly faster, which is the point anyway).
            t = min(max(t, a.at + min_gap_ms + 1), b.at - min_gap_ms - 1)
            if t - a.at > min_gap_ms and b.at - t > min_gap_ms:
                out.append(Action(t, a.pos if mode == "hold_then_move" else b.pos))
                added += 1
        out.append(b.copy())
    return out, f"{added} slow stroke(s) re-timed to move at >= {min_speed:.0f} u/s ({mode})"


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
    """Scale stroke depth down wherever the average speed over a window exceeds max_avg_speed.
    Repeats (a few passes at most) until the trailing-window average, the one the analysis checks,
    is under the cap too."""
    if len(actions) < 3:
        return [a.copy() for a in actions], "skipped"
    out = actions
    changed: set[int] = set()
    for _ in range(6):
        out, touched = _intensity_pass(out, max_avg_speed, window_ms)
        changed |= touched
        if not touched or max((s for _, s in rolling_speed(out, window_ms)), default=0) <= max_avg_speed:
            break
    return out, f"{len(changed)} action(s) reduced to keep {window_ms // 1000}s average under {max_avg_speed:.0f} u/s"


def _intensity_pass(actions: list[Action], max_avg_speed: float, window_ms: int) -> tuple[list[Action], set[int]]:
    n = len(actions)
    times = [a.at for a in actions]
    dist = [0.0]
    psum = [0.0]
    for k in range(n):
        if k:
            dist.append(dist[-1] + abs(actions[k].pos - actions[k - 1].pos))
        psum.append(psum[-1] + actions[k].pos)
    out, touched = [], set()
    for k, a in enumerate(actions):
        lo = bisect_left(times, a.at - window_ms // 2)
        hi = bisect_right(times, a.at + window_ms // 2) - 1
        span = times[hi] - times[lo]
        avg = (dist[hi] - dist[lo]) / span * 1000 if span > 0 else 0.0
        if avg > max_avg_speed:
            centre = (psum[hi + 1] - psum[lo]) / (hi - lo + 1)
            f = max_avg_speed / avg
            out.append(Action(a.at, float(round(centre + (a.pos - centre) * f))))
            if out[-1].pos != a.pos:
                touched.add(a.at)
        else:
            out.append(a.copy())
    return out, touched
