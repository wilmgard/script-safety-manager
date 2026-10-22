"""Check and fix every script in a folder. Used by the app's batch window; holds no GUI code."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from . import model
from .analysis import Report, analyze
from .pipeline import FixOptions, FixResult, apply
from .profiles import DeviceProfile

OUTPUT_MARKERS = (".safe.", ".modified.")   # our own outputs are never checked again
MAX_FILES = 5000


@dataclass
class BatchItem:
    path: Path
    status: str                         # "ok", "problems", "broken", "error", "skipped"
    message: str = ""                   # plain-language status for the table
    score_before: int | None = None
    score_after: int | None = None
    problems_before: int = 0
    problems_after: int = 0
    main_problem: str = ""              # finding kind with the most occurrences in the original
    repaired: int = 0
    result: FixResult | None = None     # kept so the fixed copy can be saved without recomputing

    @property
    def sort_key(self) -> tuple:
        """Worst first: unreadable files, then lowest original score."""
        rank = {"error": 0, "broken": 1, "problems": 2, "ok": 3, "skipped": 4}[self.status]
        return rank, self.score_before if self.score_before is not None else -1, self.path.name.lower()


def find_scripts(folder: str | Path, recursive: bool = True) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a folder")
    pattern = folder.rglob("*.funscript") if recursive else folder.glob("*.funscript")
    files = sorted((p for p in pattern if p.is_file() and not any(m in p.name for m in OUTPUT_MARKERS)),
                   key=lambda p: str(p).lower())
    if len(files) > MAX_FILES:
        raise ValueError(f"{len(files)} scripts found; batch mode handles up to {MAX_FILES} at a time")
    return files


def _problems(report: Report) -> int:
    return sum(1 for f in report.findings if f.severity != "info")


def check_file(path: Path, profile: DeviceProfile, opts: FixOptions, repair: bool = False) -> BatchItem:
    try:
        try:
            script = model.load(path)
        except model.BrokenActionsError as e:
            if not repair:
                return BatchItem(path, "broken", f"{len(e.broken)} broken action(s): open it to review, "
                                                 f"or tick 'Remove broken actions'")
            script = model.load(path, repair=True)
    except (OSError, ValueError) as e:
        return BatchItem(path, "error", f"Can't open: {e}")
    if script.axis != "L0":
        return BatchItem(path, "skipped", f"{script.axis} axis: only single-axis scripts are supported")
    try:
        result = apply(script, profile, opts)
    except ValueError as e:
        return BatchItem(path, "error", f"Can't fix: {e}")
    before, after = analyze(script, profile), analyze(result.script, profile)
    counts = {k: v for k, v in before.counts().items()
              if any(f.kind == k and f.severity != "info" for f in before.findings)}
    item = BatchItem(path, "problems" if _problems(before) else "ok", score_before=before.score,
                     score_after=after.score, problems_before=_problems(before), problems_after=_problems(after),
                     main_problem=max(counts, key=counts.get) if counts else "", repaired=len(script.repairs),
                     result=result)
    if item.status == "ok":
        item.message = "Plays well as it is"
    elif item.problems_after == 0:
        item.message = f"{item.problems_before} problem(s), all fixed in the safe copy"
    else:
        item.message = f"{item.problems_before} problem(s), {item.problems_after} left after fixing"
    if item.repaired:
        item.message = f"{item.repaired} broken action(s) removed. " + item.message
    return item


def check_folder(files: list[Path], profile: DeviceProfile, opts: FixOptions, repair: bool = False,
                 cancelled: Callable[[], bool] = lambda: False) -> Iterator[BatchItem]:
    opts.validate()
    for path in files:
        if cancelled():
            return
        yield check_file(path, profile, opts, repair)


def output_path(src: Path, out_dir: Path | None, safe: bool) -> Path:
    from .cli import MODIFIED_SUFFIX, SAFE_SUFFIX, _output_path
    return _output_path(src, out_dir, SAFE_SUFFIX if safe else MODIFIED_SUFFIX)


def save_item(item: BatchItem, opts: FixOptions, out_dir: Path | None = None, replace: bool = False,
              root: Path | None = None) -> tuple[bool, str]:
    """Write the fixed copy. Never touches the original, and only replaces an existing copy when asked.
    With out_dir and root, subfolders under root are recreated under out_dir, so same-named scripts in
    different subfolders don't collide."""
    if item.result is None:
        return False, "nothing to save"
    target = out_dir
    if out_dir is not None and root is not None:
        try:
            target = out_dir / item.path.parent.relative_to(root)
        except ValueError:
            target = out_dir
    dest = output_path(item.path, target, opts.is_safe)
    if dest.resolve() == item.path.resolve():
        return False, "would overwrite the original"
    if dest.exists() and not replace:
        return False, f"{dest.name} already exists"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        model.save(item.result.script, dest)
    except OSError as e:
        return False, str(e)
    return True, dest.name
