"""Command line interface.

    python -m script_safety profiles
    python -m script_safety analyze FILE_OR_DIR... [-p PROFILE] [--json]
    python -m script_safety fix FILE_OR_DIR... [-p PROFILE] [-o OUT_DIR] [options]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, model
from .analysis import analyze
from .pipeline import FixOptions, apply
from .profiles import all_profiles, get_profile

SAFE_SUFFIX = ".safe"


def _collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files.extend(sorted(f for f in p.rglob("*.funscript") if SAFE_SUFFIX + "." not in f.name))
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(p)
    return files


def _output_path(src: Path, out_dir: Path | None) -> Path:
    """video.funscript -> video.safe.funscript; video.roll.funscript -> video.safe.roll.funscript,
    so multi-axis players still pick up the axis suffix."""
    parts = src.name.split(".")
    if len(parts) >= 3 and parts[-2] in model.AXIS_SUFFIXES:
        name = ".".join(parts[:-2]) + SAFE_SUFFIX + "." + ".".join(parts[-2:])
    else:
        name = src.stem + SAFE_SUFFIX + src.suffix
    return (out_dir or src.parent) / name


def _print_report(rep, path: Path) -> None:
    print(f"\n== {path.name}  [axis {rep.axis}, profile {rep.profile}]  safety score {rep.score}/100")
    print("   " + ", ".join(f"{k}={v}" for k, v in rep.stats.items()))
    if not rep.findings:
        print("   no issues found")
        return
    for kind, n in sorted(rep.counts().items()):
        print(f"   {kind:15} {n}")
    shown = [f for f in rep.findings if f.severity != "info"][:15]
    for f in shown:
        print(f"   [{f.severity:7}] {_ts(f.start_ms)}-{_ts(f.end_ms)}  {f.message}")
    hidden = len(rep.findings) - len(shown)
    if hidden:
        print(f"   ... {hidden} more (use --json for all)")


def _ts(ms: int) -> str:
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{ms:03d}"


def cmd_profiles(args) -> int:
    for p in all_profiles().values():
        flag = "" if p.verified else "  (unverified)"
        print(f"{p.key:14} {p.name:26} max {p.max_speed:>4.0f} u/s, min interval {p.min_interval_ms:>3} ms, "
              f"limit mode {p.speed_limit_mode}{flag}")
    return 0


def cmd_analyze(args) -> int:
    profile = get_profile(args.profile)
    reports = []
    for path in _collect(args.paths):
        rep = analyze(model.load(path), profile)
        if args.json:
            reports.append({"file": str(path), **rep.to_dict()})
        else:
            _print_report(rep, path)
    if args.json:
        json.dump(reports, sys.stdout, indent=2)
        print()
    return 0


def cmd_fix(args) -> int:
    profile = get_profile(args.profile)
    opts = FixOptions(
        min_interval=not args.no_min_interval,
        vibration=args.vibration,
        thin=args.thin,
        range_min=args.range[0], range_max=args.range[1], range_mode=args.range_mode,
        invert=args.invert,
        soft_start=not args.no_soft_start, ramp_ms=args.ramp_ms,
        intensity_cap=args.intensity_cap,
        speed_limit=not args.no_speed_limit, max_speed=args.max_speed, speed_mode=args.speed_mode,
    )
    out_dir = Path(args.out_dir) if args.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    for path in _collect(args.paths):
        script = model.load(path)
        result = apply(script, profile, opts)
        dest = _output_path(path, out_dir)
        if dest.resolve() == path.resolve():
            print(f"refusing to overwrite source {path}", file=sys.stderr)
            return 2
        if dest.exists() and not args.force:
            print(f"{dest} exists, use --force to overwrite", file=sys.stderr)
            return 2
        before = analyze(script, profile).score
        after = analyze(result.script, profile).score
        if not args.dry_run:
            model.save(result.script, dest)
        print(f"{path.name} -> {dest.name}{' (dry run)' if args.dry_run else ''}: "
              f"score {before} -> {after}, {result.moved} moved, {result.removed} removed")
        for name, summary in result.steps:
            print(f"   {name:13} {summary}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="script_safety", description="Funscript safety analyzer and fixer")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("profiles", help="list device profiles").set_defaults(func=cmd_profiles)

    def common(p):
        p.add_argument("paths", nargs="+", help="funscript files or folders")
        p.add_argument("-p", "--profile", default="generic", help="profile key or path to a profile .json")

    a = sub.add_parser("analyze", help="report problems without changing anything")
    common(a)
    a.add_argument("--json", action="store_true", help="machine-readable output")
    a.set_defaults(func=cmd_analyze)

    f = sub.add_parser("fix", help="write a safe copy (NAME.safe.funscript)")
    common(f)
    f.add_argument("-o", "--out-dir", help="output folder (default: next to the source)")
    f.add_argument("--force", action="store_true", help="overwrite existing .safe files")
    f.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    f.add_argument("--max-speed", type=float, help="override the profile's speed limit (u/s)")
    f.add_argument("--speed-mode", choices=["shorten_start", "shorten_center"])
    f.add_argument("--no-speed-limit", action="store_true")
    f.add_argument("--no-min-interval", action="store_true")
    f.add_argument("--vibration", choices=["keep", "reduce", "remove"], default="keep")
    f.add_argument("--thin", choices=["auto", "on", "off"], default="auto",
                   help="remove interpolation points (auto follows the profile)")
    f.add_argument("--range", nargs=2, type=float, default=[0, 100], metavar=("MIN", "MAX"))
    f.add_argument("--range-mode", choices=["scale", "clip"], default="scale")
    f.add_argument("--invert", action="store_true")
    f.add_argument("--no-soft-start", action="store_true")
    f.add_argument("--ramp-ms", type=int, default=2000)
    f.add_argument("--intensity-cap", action="store_true",
                   help="limit sustained average speed to the profile's sustained_speed")
    f.set_defaults(func=cmd_fix)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
