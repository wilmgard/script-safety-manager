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
        return Funscript([a.copy() for a in self.actions], json.loads(json.dumps(self.raw)), self.path)


def speed(a: Action, b: Action) -> float:
    """Speed between two actions in units/second (OFS convention). Zero-length gaps count as infinite."""
    dt = b.at - a.at
    dp = abs(b.pos - a.pos)
    if dt <= 0:
        return float("inf") if dp else 0.0
    return dp / dt * 1000.0


def load(path: str | Path) -> Funscript:
    path = Path(path)
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except RecursionError as e:
        raise ValueError(f"{path.name}: JSON is nested too deeply") from e
    except UnicodeDecodeError as e:
        raise ValueError(f"{path.name}: not a UTF-8 text file") from e
    if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
        raise ValueError(f"{path.name}: not a funscript (missing 'actions' list)")
    actions = []
    for i, a in enumerate(data["actions"]):
        try:
            at, pos = float(a["at"]), float(a["pos"])
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"{path.name}: bad action #{i}: {str(a)[:80]}") from e
        if not (math.isfinite(at) and math.isfinite(pos)) or at < 0:
            raise ValueError(f"{path.name}: action #{i} has an invalid time or position: {str(a)[:80]}")
        actions.append(Action(int(at), pos))
    raw = {k: v for k, v in data.items() if k != "actions"}
    return Funscript(actions, raw, path)


def save(script: Funscript, path: str | Path) -> None:
    data = dict(script.raw)
    data.setdefault("version", "1.0")
    data.setdefault("inverted", False)
    data.setdefault("range", 100)
    # Final guarantee: whatever happened upstream, never write a position outside 0-100.
    data["actions"] = [{"at": int(a.at), "pos": min(100, max(0, int(round(a.pos))))} for a in script.actions]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
