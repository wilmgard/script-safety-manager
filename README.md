# Script Safety Manager

A local, offline tool that checks a `.funscript` **before you play it**, especially a new, untested one, and writes a copy your device can play safely.

> Status: desktop app and command-line tool both work and are tested. Device profiles are still mostly estimates.

## Why this exists

More and more scripts are being released by new authors without being tested on real hardware. A script is just a list of positions and times. Nothing in the file stops it from asking for a full-length stroke in 5 ms, or hundreds of actions a second. What happens then depends entirely on your device.

**The Handy protects you by itself.** Its firmware caps speed (around 500 units/s in online mode) and rewrites anything faster before playing it. That behaviour has been tested thoroughly by forum members. For the Handy this tool is mostly about *playing the script as intended*: the firmware's limiter can move beat-synced strokes off the beat, and it re-times very slow strokes.

**OSR2, SR6 and SSR1 do not.** They play every command as fast as their servos or motor can move. There is no firmware cap. A script with a 1 ms gap or a 17,000 units/s jump is executed at the device's top speed, so a careless or broken script becomes a full-power slam. For these devices this tool is the safety layer: it finds those moves and caps them at a speed *you* choose before the device ever sees them.

| | The Handy | OSR2 / SR6 / SSR1 |
|---|---|---|
| Built-in speed limit | Yes, firmware (well tested) | **No**, moves at top speed |
| What an untested script can do | Play off the beat, or with shifted timing | Slam end to end at full power, rapid-fire jolts |
| What this tool does | Fixes it so the firmware has nothing to rewrite | **Caps speed and removes jolts before playing** |

### How devices react to extreme scripts

From community testing on EroScripts ([FUNSR1 vs the Handy](https://discuss.eroscripts.com/t/review-funsr1-in-action-beats-the-handy/157363), [Handy firmware blends slow actions](https://discuss.eroscripts.com/t/handys-firmware-blends-slow-actions/107231)) using the stress-test script included here:

| Device | Too fast | Too slow | Many tiny / in-between points |
|---|---|---|---|
| The Handy (online) | Firmware shortens the stroke toward its start (~500 u/s), which can move a beat-synced bottom so the stroke turns off the beat | Firmware shifts turning-point timing (~33 u/s) | Fine |
| The Handy (Bluetooth) | Plays at top speed, aborts when the next command arrives | Moves at its slowest speed, then waits | Skips actions; micro-stutters after every point |
| OSR2 / SR6 / SSR1 | **Plays at top speed**, aborts when the next command arrives | Plays as written | Fine (vibrations weaker on servo devices) |

So there is no single "safe" script: it depends on the device. The tool works **per device profile**, and leaves slow motion alone on devices that play it correctly.

## How the checks catch the problems from the threads

Each issue reported in the threads maps to a check (what the tool reports) and a fix (what it changes). The examples are from the included `Stroker_Stress_Test.funscript`, which contains moves up to 17,000 u/s and actions 1 ms apart.

| Issue from the threads | Who it affects | Check | Fix |
|---|---|---|---|
| Moves faster than the device can or should go. OSR-type devices play them at full power; the Handy rewrites them | All devices; **dangerous on OSR2 / SR6 / SSR1** | **Too fast for this device** (`over_speed`): every move above the profile's speed limit, with the peak speed. On the stress test with the OSR profile: 490 places | **Speed limit**: no move in the output exceeds the limit. For OSR-type devices, the limit is a cap you choose; lower it with `--max-speed` if you want more margin |
| Actions crammed closer together than a device can follow. The Handy BLE skips them; servo devices jerk back and forth | All devices; jolts on servo devices | **Actions too close together** (`short_interval`): gaps under the profile's minimum, with the shortest gap (1 ms in the stress test) | **Minimum interval**: merges them, keeping the more extreme point so the stroke shape survives |
| Script starts, or restarts after a pause, at full speed, so the device jumps from rest | Mostly devices without a firmware cap | **Starts / restarts at full speed** (`hard_start`, `hard_resume`) | **Soft start**: ramps stroke length in over the first 2 seconds and after every pause |
| Handy firmware shortens fast strokes toward their start, moving the bottom so an in-between point becomes the new bottom and strokes fall off the beat | The Handy (online) | **Device would play it off the beat** (`emphasis_lost`): the tool simulates the firmware and shows where the stroke would turn somewhere the script doesn't | **Keep bottoms (or tops) on the beat** (`preserve_anchor`): fast strokes are shortened from the other end, so beat-synced points keep their exact time and depth |
| Handy firmware "cheeses" strokes slower than ~33 u/s by shifting their timing, so downstrokes no longer land when they should | The Handy (online) | **Too slow: device would shift the timing** (`slow_retime`) | **Slow fix**: a short hold, then a move at the minimum speed, so every point still lands exactly on time |
| Handy BLE micro-stutters after every point, so detailed scripts with many in-between points play badly | The Handy (Bluetooth) | Shown as dense sections in the graph | **Thin**: simplifies curves to fewer, evenly spaced points that keep their shape |
| Vibrations (tiny fast moves) work on the Handy but are weaker on servo devices | Servo devices (fidelity, not safety) | **Vibration bursts** (`vibration`, a note) | Optional: keep, reduce or remove |
| Long stretches at very high average speed (motor heat, user fatigue). Not from the threads, but it matters most on devices that never slow themselves down | All devices | **Long, very intense section** (`sustained`): average speed over 30 s | Optional **Tone down**: shortens strokes in those sections |
| Broken files: positions outside 0–100, unsorted or duplicate times, `NaN` values | All devices | **File problems** (`out_of_range`, `unsorted`, `duplicate_time`); unreadable values are rejected when loading | **Sanitize**: sort, de-duplicate, clamp; positions are always clamped to 0–100 on save |

The checks never change anything. Run them first to see whether a script is safe as it is, then decide what to fix.

## Recommended use for untested scripts

1. Open the script and pick **your** device (OSR2 / SR6, SSR1, …).
2. Look at the score and the **Problems** list. Red "Too fast for this device" or "Actions too close together" on a servo device means the script would make it move at full power.
3. Leave the default fixes on and save the **safe copy**. The original stays untouched.
4. Play the safe copy with the device's own stroke-length and speed settings turned down the first time.

Because OSR-type devices don't limit themselves, it's worth checking every untested script, even ones that "look fine" in an editor.

## Install

Requires Python 3.10 or newer (developed on 3.14). The command line has no dependencies; the desktop app needs PySide6, pyqtgraph and numpy (`requirements.txt`).

```bash
git clone https://github.com/wilmgard/script-safety-manager.git
cd script-safety-manager
pip install -r requirements.txt   # for the desktop app
pip install pytest                # only needed to run the tests
```

## Desktop app

```bash
python -m script_safety.gui            # or: python -m script_safety.gui video.funscript
```

1. **Open** a script (button, or drag it onto the window) and pick your **device**.
2. The **score** shows how well the original plays on that device and how well the fixed copy will (0–100).
3. The **graph** shows the original (grey), the fixed version (blue) and moves that are too fast (red).
4. **Problems** lists what is wrong in plain words with before/after counts. Click one to jump to it.
5. **What to fix** has the few switches most people need, including how too-fast strokes are shortened
   (keep bottoms or tops on the beat, shorten evenly, or the Handy firmware's way).
6. **Save safe copy** writes `NAME.safe.funscript` next to the original. The original is never changed.

Every option, the full problem list and a log of what was changed are under **Advanced settings…**.

## Command line

```bash
python -m script_safety profiles                                  # list device profiles
python -m script_safety analyze video.funscript -p osr            # report problems, change nothing
python -m script_safety fix video.funscript -p osr --dry-run      # show what would change
python -m script_safety fix video.funscript -p osr                # writes video.safe.funscript
python -m script_safety fix video.funscript -p osr --max-speed 300   # stricter cap than the profile
python -m script_safety fix "D:/Scripts" -p ssr1 -o "D:/Scripts/safe"   # a whole folder
```

- The source file is never overwritten. Output is `NAME.safe.funscript` (multi-axis files keep their axis suffix: `NAME.safe.roll.funscript`). With `--no-speed-limit` the output is called `NAME.modified.funscript`, because it is no longer speed-guaranteed.
- Existing outputs are only replaced with `--force`.
- `analyze --json` gives machine-readable output.
- Every fixed file records what was done in `metadata.safety_manager` (tool version, date, profile, options, steps, counts). No paths or personal information are written.

Run `python -m script_safety fix -h` for all options.

## What it fixes, in order

Each step can be switched off or tuned.

1. **Sanitize**: sort, merge duplicate timestamps, clamp to 0–100, round.
2. **Vibration** (`--vibration keep|reduce|remove`, default keep).
3. **Minimum interval**: merge actions closer than the device can follow, keeping the more extreme point.
4. **Thin** (`--thin auto|on|off`): simplify curves to fewer points that keep their shape. For Handy BLE, points are also spaced apart to avoid micro-stutter. Turning points are always kept.
5. **Range / invert** (`--range MIN MAX`, `--range-mode scale|clip`, `--invert`).
6. **Soft start** (`--ramp-ms`): ramp stroke length in at the start and after every pause.
7. **Intensity cap** (`--intensity-cap`, off by default): reduce stroke length where the 30 s average speed is too high.
8. **Speed limit**, which runs after every step that moves positions, so its guarantee holds in the saved file:
   - `preserve_anchor` (Handy default): every bottom (or top, with `--anchor top`) keeps its exact time and depth; strokes are shortened from the other end.
   - `shorten_center` (OSR / SSR1 default): strokes shrink evenly toward their middle.
   - `shorten_start`: what the Handy firmware does. Simple, but can move beat-synced points.
9. **Slow fix** (devices with a minimum speed only): a too-slow stroke becomes a hold plus a move at the minimum speed, keeping both endpoints' timing (`--slow-mode hold_then_move|move_then_hold`).

## Device profiles

| Key | Device | Speed limit | Notes |
|---|---|---|---|
| `handy_online` | The Handy (WiFi) | 500 u/s | Speed and slow limits observed; other values placeholders |
| `handy_ble` | The Handy (Bluetooth) | 550 u/s | Unverified |
| `osr` | OSR2 / SR6 | 450 u/s | Unverified. A safety cap, not the servo's top speed |
| `ssr1` | SSR1 | 700 u/s | Unverified. A safety cap, not the motor's top speed |
| `launch`, `keon`, `a10` | Kiiroo Launch / Keon, Vorze A10 Piston | 350–400 u/s | Unverified |
| `generic` | Unknown hardware | 400 u/s | Conservative |

**Most numbers are estimates.** For the Handy, the speed limit reflects what the firmware enforces anyway. For OSR2 / SR6 / SSR1 nothing enforces it except this tool, so treat it as *your* comfort and safety cap: lower it with `--max-speed` or a custom profile if you want more margin.

Help measure the real numbers: play `Stroker_Stress_Test.funscript` on your device with its stroke length turned down, note or record what it does, and open an issue.

### Custom profiles

Put a JSON file in `~/.script_safety_manager/profiles/`, or pass its path with `-p`. Fields you leave out come from `base`:

```json
{
  "base": "osr",
  "name": "My SR6 (gentle)",
  "max_speed": 300,
  "min_interval_ms": 30
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

This tool lowers the risk from badly made scripts, but it can't know your device's condition, its settings, or your limits, and most profile numbers are estimates. Especially on OSR2 / SR6 / SSR1: start with the device's own stroke length and speed turned down, keep its stop control or power within reach, and stop at once if anything feels wrong.
