"""Funscript data model and file I/O."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

# Known multi-axis suffixes, e.g. "video.roll.funscript" -> "roll".
AXIS_SUFFIXES = {
    "surge", "sway", "twist", "roll", "pitch", "vib", "pump", "valve", "suck", "lube",
    "L0", "L1", "L2", "R0", "R1", "R2", "V0", "V1", "A0", "A1", "A2",
}


@dataclass
class Action:
    at: int      # milliseconds
    pos: float   # 0..100

    def copy(self) -> "Action":
        return Action(self.at, self.pos)


@dataclass
class Funscript:
    actions: list[Action]
    raw: dict = field(default_factory=dict)   # every other top-level key, preserved on save
    path: Path | None = None
    repairs: list[BrokenAction] = field(default_factory=list)   # broken actions removed when loading

    @property
    def axis(self) -> str:
        """Axis name derived from the filename; 'L0' (stroke) when there is no suffix."""
        if self.path is None:
            return "L0"
        parts = self.path.name.split(".")
        if len(parts) >= 3 and parts[-2] in AXIS_SUFFIXES:
            return parts[-2]
        return "L0"

    @property
    def duration_ms(self) -> int:
        return self.actions[-1].at if self.actions else 0

    def copy(self) -> "Funscript":
        return Funscript([a.copy() for a in self.actions], json.loads(json.dumps(self.raw)), self.path,
                         list(self.repairs))


@dataclass
class BrokenAction:
    index: int       # position in the file's actions list (0-based)
    reason: str
    snippet: str     # the raw entry, shortened, for showing to the user


class BrokenActionsError(ValueError):
    """Some actions are unusable. The file itself is valid JSON, so it can be opened with repair=True,
    which drops exactly these actions."""

    def __init__(self, path: Path, broken: list[BrokenAction], total: int):
        self.path, self.broken, self.total = path, broken, total
        first = ", ".join(f"#{b.index + 1}" for b in broken[:5]) + (" ..." if len(broken) > 5 else "")
        super().__init__(f"{path.name}: {len(broken)} of {total} actions are broken ({first}); "
                         f"first problem: {broken[0].reason}")


def speed(a: Action, b: Action) -> float:
    """Speed between two actions in units/second (OFS convention). Zero-length gaps count as infinite."""
    dt = b.at - a.at
    dp = abs(b.pos - a.pos)
    if dt <= 0:
        return float("inf") if dp else 0.0
    return dp / dt * 1000.0


def _number(value) -> float | None:
    """Accept numbers and numeric text ("50"); reject booleans, blanks and anything non-finite."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        v = float(value.strip() if isinstance(value, str) else value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _check_action(a) -> tuple[Action | None, str]:
    if not isinstance(a, dict):
        return None, "not an action object"
    if "at" not in a or "pos" not in a:
        return None, "missing " + ("time ('at')" if "at" not in a else "position ('pos')")
    at, pos = _number(a["at"]), _number(a["pos"])
    if at is None:
        return None, f"time is not a usable number: {a['at']!r}"[:80]
    if pos is None:
        return None, f"position is not a usable number: {a['pos']!r}"[:80]
    if at < 0:
        return None, f"negative time: {at:g} ms"
    return Action(int(at), pos), ""


def load(path: str | Path, repair: bool = False) -> Funscript:
    """Load a funscript.

    Files that aren't valid JSON or have no actions list are always refused: guessing at a damaged file
    could invent moves the author never wrote. Individual broken actions (missing or non-numeric values,
    NaN/Infinity, negative times) raise BrokenActionsError, unless repair=True, which drops exactly those
    actions and records them in Funscript.repairs. Out-of-range positions and ordering are not "broken";
    the sanitize step fixes them.
    """
    path = Path(path)
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except RecursionError as e:
        raise ValueError(f"{path.name}: JSON is nested too deeply") from e
    except UnicodeDecodeError as e:
        raise ValueError(f"{path.name}: not a UTF-8 text file") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"{path.name}: damaged file, not valid JSON (line {e.lineno}, column {e.colno}). "
                         f"It can't be repaired safely.") from e
    if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
        raise ValueError(f"{path.name}: not a funscript (missing 'actions' list)")
    actions, broken = [], []
    for i, a in enumerate(data["actions"]):
        action, reason = _check_action(a)
        if action is None:
            broken.append(BrokenAction(i, reason, json.dumps(a, default=str)[:80]))
        else:
            actions.append(action)
    if broken and (not repair or not actions):
        if not actions:
            raise ValueError(f"{path.name}: none of the {len(broken)} actions are usable")
        raise BrokenActionsError(path, broken, len(data["actions"]))
    raw = {k: v for k, v in data.items() if k != "actions"}
    return Funscript(actions, raw, path, broken)


def save(script: Funscript, path: str | Path) -> None:
    data = dict(script.raw)
    data.setdefault("version", "1.0")
    data.setdefault("inverted", False)
    data.setdefault("range", 100)
    # Final guarantee: whatever happened upstream, never write a position outside 0-100.
    data["actions"] = [{"at": int(a.at), "pos": min(100, max(0, int(round(a.pos))))} for a in script.actions]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
