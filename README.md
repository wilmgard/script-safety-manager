# Script Safety Manager

A local, offline tool that checks a `.funscript` against the device you will actually play it on, and writes a fixed copy that the device can play **as the scripter wrote it**: on the beat, within its speed limits, and without surprises.

> Status: desktop app and command-line tool both work and are tested. Device profiles are still mostly estimates.

## Why

Devices handle "impossible" moves very differently (from community testing on EroScripts, e.g. [FUNSR1 vs the Handy](https://discuss.eroscripts.com/t/review-funsr1-in-action-beats-the-handy/157363) and [Handy firmware blends slow actions](https://discuss.eroscripts.com/t/handys-firmware-blends-slow-actions/107231)):

| Device | Too fast | Too slow | Many tiny / in-between points |
|---|---|---|---|
| The Handy (online) | Firmware shortens the stroke toward its start (~500 u/s), which can move a beat-synced bottom so the stroke turns off the beat | Firmware shifts turning-point timing (~33 u/s) | Fine |
| The Handy (Bluetooth) | Plays at top speed, aborts when the next command arrives | Moves at its slowest speed, then waits | Skips actions; micro-stutters after every point |
| OSR2 / SR6 / SSR1 | Plays at top speed, aborts when the next command arrives | Plays as written | Fine (vibrations weaker on servo devices) |

So there is no single "safe" script. This tool works **per device profile**, prefers fixing a script *before* the device's firmware rewrites it, and leaves slow motion alone on devices that play it correctly.

## Install

Requires Python 3.10 or newer (developed on 3.14). The command line has no dependencies; the desktop app needs PySide6, pyqtgraph and numpy (`requirements.txt`).

```bash
git clone https://github.com/wilmgard/script-safety-manager.git
cd script-safety-manager
pip install pytest   # only needed to run the tests
```

## Desktop app

```bash
pip install -r requirements.txt
python -m script_safety.gui            # or: python -m script_safety.gui video.funscript
```

1. **Open** a script (button, or drag it onto the window) and pick your **device**.
2. The **score** shows how well the original plays on that device and how well the fixed copy will (0-100).
3. The **graph** shows the original (grey), the fixed version (blue) and moves that are too fast (red).
4. **Problems** lists what is wrong in plain words with before/after counts. Click one to jump to it.
5. **What to fix** has the few switches most people need, including how too-fast strokes are shortened
   (keep bottoms or tops on the beat, shorten evenly, or the Handy firmware's way).
6. **Save safe copy** writes `NAME.safe.funscript` next to the original. The original is never changed.

Every option, the full problem list and a log of what was changed are under **Advanced settings…**.

## Command line

```bash
python -m script_safety profiles                                  # list device profiles
python -m script_safety analyze video.funscript -p handy_online   # report problems, change nothing
python -m script_safety fix video.funscript -p handy_online --dry-run
python -m script_safety fix video.funscript -p handy_online       # writes video.safe.funscript
python -m script_safety fix "D:/Scripts" -p osr -o "D:/Scripts/safe"   # a whole folder
```

- The source file is never overwritten. Output is `NAME.safe.funscript` (multi-axis files keep their axis suffix: `NAME.safe.roll.funscript`). With `--no-speed-limit` the output is called `NAME.modified.funscript`, because it is no longer speed-guaranteed.
- Existing outputs are only replaced with `--force`.
- `analyze --json` gives machine-readable output.
- Every fixed file records what was done in `metadata.safety_manager` (tool version, date, profile, options, steps, counts). No paths or personal information are written.

Run `python -m script_safety fix -h` for all options.

## What it checks

| Check | Meaning |
|---|---|
| `over_speed` | Moves faster than the profile's `max_speed` |
| `emphasis_lost` | (Handy online) the firmware's own limiter would make the stroke turn at an in-between point, off the beat |
| `slow_retime` | (Handy online) strokes slow enough that the firmware may shift their timing |
| `short_interval` | Actions closer together than the device can follow |
| `hard_start` / `hard_resume` | Starting, or resuming after a pause, at high speed with no ramp-in |
| `sustained` | High average speed over a 30 s window (motor heat, fatigue) |
| `vibration` | Bursts of small, rapid actions (informational) |
| `hard_reversal` | Fast-in, fast-out direction changes (informational) |
| `out_of_range`, `unsorted`, `duplicate_time`, `non_integer` | File problems |

Each report ends with a 0–100 safety score for that device.

## What it fixes

Applied in this order. Each step can be switched off or tuned.

1. **Sanitize**: sort, merge duplicate timestamps, clamp to 0–100, round.
2. **Vibration** (`--vibration keep|reduce|remove`, default keep).
3. **Minimum interval**: merge actions closer than the device can follow, keeping the more extreme point.
4. **Thin** (`--thin auto|on|off`): simplify curves to fewer points that keep their shape. For Handy BLE, points are also spaced apart to avoid micro-stutter. Turning points are always kept.
5. **Range / invert** (`--range MIN MAX`, `--range-mode scale|clip`, `--invert`).
6. **Soft start** (`--ramp-ms`): ramp stroke depth in at the start and after every pause.
7. **Intensity cap** (`--intensity-cap`, off by default): reduce depth where the 30 s average speed is too high.
8. **Speed limit**, which always runs last among position changes, so its guarantee holds:
   - `preserve_anchor` (Handy default): every bottom (or top, with `--anchor top`) keeps its exact time and depth; strokes are shortened from the other end.
   - `shorten_start`: what the Handy firmware does. Simple, but can move beat-synced points.
   - `shorten_center`: strokes shrink evenly toward their middle.
9. **Slow fix** (devices with a minimum speed only): a too-slow stroke becomes a hold plus a move at the minimum speed, keeping both endpoints' timing (`--slow-mode hold_then_move|move_then_hold`).

## Device profiles

| Key | Device | Max speed | Notes |
|---|---|---|---|
| `handy_online` | The Handy (WiFi) | 500 u/s | Speed and slow limits observed; other values placeholders |
| `handy_ble` | The Handy (Bluetooth) | 550 u/s | Unverified |
| `osr` | OSR2 / SR6 | 450 u/s | Unverified |
| `ssr1` | SSR1 | 700 u/s | Unverified |
| `launch`, `keon`, `a10` | Kiiroo Launch / Keon, Vorze A10 Piston | 350–400 u/s | Unverified |
| `generic` | Unknown hardware | 400 u/s | Conservative |

**Most numbers are estimates.** Help measure them: play `Stroker_Stress_Test.funscript` on your device, note or record what it does, and open an issue.

### Custom profiles

Put a JSON file in `~/.script_safety_manager/profiles/`, or pass its path with `-p`. Fields you leave out come from `base`:

```json
{
  "base": "osr",
  "name": "My SR6 (fast servos)",
  "max_speed": 600,
  "min_interval_ms": 15
}
```

Values are range-checked when loaded, so a bad or malicious profile can't switch off the limits. Custom profiles are always shown as unverified.

## Development

```bash
python -m pytest -q
```

| Path | Contents |
|---|---|
| `script_safety/model.py` | Funscript loading/saving, validation |
| `script_safety/profiles.py` | Device profiles |
| `script_safety/analysis.py` | Checks and report |
| `script_safety/fixers.py` | Individual fixes |
| `script_safety/pipeline.py` | Fix order and options |
| `script_safety/cli.py` | Command line |
| `script_safety/gui/` | Desktop app (PySide6 + pyqtgraph); holds no safety logic of its own |
| `tests/` | pytest suite, including hostile-input tests |
| `Stroker_Stress_Test.funscript` | Stress-test script from the community thread, used as a test fixture |

## Safety note

This tool reduces common mechanical and comfort risks, but it can't know your device's condition or your limits. Start with low speed and stroke-length settings on the device, stay within reach of its stop control, and stop if anything feels wrong.
