import json
from pathlib import Path

import pytest

from script_safety import fixers, model
from script_safety.analysis import analyze, emphasis_changes, find_bursts, find_resumes, turning_points
from script_safety.cli import _output_path, main
from script_safety.model import Action, Funscript, speed
from script_safety.pipeline import FixOptions, apply
from script_safety.profiles import BUILTIN, DeviceProfile, get_profile, load_profile_file

ROOT = Path(__file__).resolve().parent.parent
STRESS = ROOT / "Stroker_Stress_Test.funscript"


def acts(*pairs):
    return [Action(t, float(p)) for t, p in pairs]


def max_speed_of(actions):
    return max(speed(a, b) for a, b in zip(actions, actions[1:]))


# --- model -----------------------------------------------------------------

def test_roundtrip_preserves_metadata(tmp_path):
    src = tmp_path / "a.funscript"
    src.write_text(json.dumps({"version": "1.0", "inverted": False, "range": 90,
                               "metadata": {"creator": "x"}, "actions": [{"at": 0, "pos": 10}]}))
    s = model.load(src)
    model.save(s, tmp_path / "b.funscript")
    out = json.loads((tmp_path / "b.funscript").read_text())
    assert out["range"] == 90 and out["metadata"]["creator"] == "x"
    assert out["actions"] == [{"at": 0, "pos": 10}]


def test_axis_detection():
    assert Funscript([], path=Path("v.roll.funscript")).axis == "roll"
    assert Funscript([], path=Path("my.video.funscript")).axis == "L0"


def test_output_path_keeps_axis_suffix():
    assert _output_path(Path("v.funscript"), None).name == "v.safe.funscript"
    assert _output_path(Path("v.twist.funscript"), None).name == "v.safe.twist.funscript"


# --- fixers ----------------------------------------------------------------

def test_sanitize():
    out, _ = fixers.sanitize(acts((100, 50), (0, 120), (100, 60), (50, -3.4)))
    assert [(a.at, a.pos) for a in out] == [(0, 100), (50, 0), (100, 60)]


def test_speed_limit_start_mode_matches_handy_behaviour():
    out, _ = fixers.speed_limit(acts((0, 0), (100, 100), (200, 0)), 500)
    # 100 ms at 500 u/s = 50 units, measured from where the previous move actually ended
    assert [a.pos for a in out] == [0, 50, 0]


@pytest.mark.parametrize("mode", ["shorten_start", "shorten_center", "preserve_anchor"])
def test_speed_limit_guarantee(mode):
    s = model.load(STRESS)
    out, _ = fixers.speed_limit(fixers.sanitize(s.actions)[0], 400, mode)
    assert max_speed_of(out) <= 400
    assert all(0 <= a.pos <= 100 and a.pos == int(a.pos) for a in out)


def test_speed_limit_center_keeps_stroke_centred():
    out, _ = fixers.speed_limit(acts((0, 50), (1000, 50), (1100, 100), (1200, 0), (1300, 100), (2300, 50)),
                                500, "shorten_center")
    mids = [a.pos for a in out[2:5]]
    assert max(mids) < 100 and min(mids) > 0
    assert max_speed_of(out) <= 500


def test_speed_limit_leaves_slow_moves_alone():
    a = acts((0, 0), (1000, 100), (5000, 0))
    out, _ = fixers.speed_limit(a, 400)
    assert [x.pos for x in out] == [0, 100, 0]


def test_min_interval_keeps_extreme():
    out, _ = fixers.min_interval(acts((0, 0), (500, 50), (505, 90), (1000, 0)), 30)
    assert [(a.at, a.pos) for a in out] == [(0, 0), (505, 90), (1000, 0)]


def test_vibration_detect_and_remove():
    a = acts((0, 50), *[(1000 + 50 * k, 50 + (5 if k % 2 else -5)) for k in range(10)], (3000, 0))
    assert find_bursts(a) == [(1, 10)]
    out, _ = fixers.vibration(a, "remove")
    assert len(out) == 4
    red, _ = fixers.vibration(a, "reduce", 0.5)
    assert max(x.pos for x in red[1:11]) - min(x.pos for x in red[1:11]) <= 6


def test_thin_keeps_turning_points():
    a = acts((0, 0), (100, 25), (200, 50), (300, 75), (400, 100), (500, 0))
    out, _ = fixers.thin_interpolation(a, 3)
    assert [(x.at, x.pos) for x in out] == [(0, 0), (400, 100), (500, 0)]


def test_soft_start_after_pause():
    a = acts((0, 50), (5000, 50), (5100, 100), (5200, 0), (9000, 100))
    assert find_resumes(a) == [1]
    out, _ = fixers.soft_start(a, ramp_ms=1000, start_factor=0.3)
    assert abs(out[2].pos - 50) < 50 and abs(out[3].pos - 50) < 50
    assert out[4].pos == 100


def test_remap_range():
    out, _ = fixers.remap_range(acts((0, 0), (1, 100)), 20, 80)
    assert [a.pos for a in out] == [20, 80]
    out, _ = fixers.remap_range(acts((0, 0)), invert=True)
    assert out[0].pos == 100


def test_intensity_cap_reduces_heavy_section():
    a = acts(*[(k * 100, 0 if k % 2 else 100) for k in range(600)])  # 1000 u/s for 60 s
    out, _ = fixers.intensity_cap(a, 300, 10000)
    mid = out[300:310]
    assert max(x.pos for x in mid) - min(x.pos for x in mid) <= 32


# --- analysis & pipeline -----------------------------------------------------

def test_stress_test_analysis_flags_known_problems():
    rep = analyze(model.load(STRESS), BUILTIN["handy_online"])
    kinds = rep.counts()
    assert kinds.get("over_speed") and kinds.get("short_interval") and kinds.get("vibration")
    assert rep.score < 100


def test_pipeline_makes_stress_test_safe_for_every_profile():
    s = model.load(STRESS)
    for p in BUILTIN.values():
        res = apply(s, p, FixOptions(vibration="reduce"))
        rep = analyze(res.script, p)
        assert not [f for f in rep.findings if f.kind in ("over_speed", "short_interval", "out_of_range",
                                                                  "slow_retime", "emphasis_lost")], p.key
        assert res.script.raw["metadata"]["safety_manager"]["profile"] == p.key
    assert s.actions[0].pos == model.load(STRESS).actions[0].pos  # input untouched


def test_custom_profile_file(tmp_path):
    f = tmp_path / "mydevice.json"
    f.write_text(json.dumps({"base": "osr", "max_speed": 333}))
    p = load_profile_file(f)
    assert p.key == "mydevice" and p.max_speed == 333 and p.speed_limit_mode == "shorten_center"
    assert not p.verified
    assert get_profile(str(f)).max_speed == 333


def test_cli_fix_writes_safe_copy(tmp_path, capsys):
    src = tmp_path / "v.funscript"
    src.write_bytes(STRESS.read_bytes())
    assert main(["fix", str(src), "-p", "handy_online"]) == 0
    out = tmp_path / "v.safe.funscript"
    assert out.exists() and src.read_bytes() == STRESS.read_bytes()
    assert main(["fix", str(src)]) == 2  # refuses to overwrite without --force
    assert main(["analyze", str(tmp_path), "--json"]) == 0


# --- validation (security review) ---------------------------------------------

@pytest.mark.parametrize("body", [
    '{"actions":[{"at":0,"pos":NaN}]}',
    '{"actions":[{"at":0,"pos":Infinity}]}',
    '{"actions":[{"at":-5,"pos":10}]}',
    '{"actions":[{"at":"x","pos":10}]}',
    "[" * 100000 + "]" * 100000,
], ids=["nan", "inf", "negative_time", "text_time", "deep_json"])
def test_load_rejects_hostile_input(tmp_path, body):
    f = tmp_path / "bad.funscript"
    f.write_text(body)
    with pytest.raises(ValueError):
        model.load(f)


def test_save_always_clamps(tmp_path):
    model.save(Funscript(acts((0, -50), (100, 150))), tmp_path / "o.funscript")
    out = json.loads((tmp_path / "o.funscript").read_text())["actions"]
    assert [a["pos"] for a in out] == [0, 100]


@pytest.mark.parametrize("field,value", [("max_speed", -100), ("max_speed", 1e9), ("max_speed", "fast"),
                                         ("min_interval_ms", -1), ("speed_limit_mode", "yolo"),
                                         ("vibration_ok", "yes"), ("min_speed", 999)])
def test_profile_rejects_bad_values(field, value):
    with pytest.raises(ValueError):
        DeviceProfile("x", "x", **{field: value})


def test_bad_profile_file_rejected(tmp_path):
    f = tmp_path / "evil.json"
    f.write_text(json.dumps({"max_speed": -100}))
    with pytest.raises(ValueError):
        load_profile_file(f)


@pytest.mark.parametrize("kw", [dict(range_min=-50, range_max=80), dict(range_min=60, range_max=40),
                                dict(max_speed=0), dict(vibration="loud"), dict(anchor="middle")])
def test_fix_options_rejected(kw):
    with pytest.raises(ValueError):
        apply(Funscript(acts((0, 0), (100, 100))), BUILTIN["generic"], FixOptions(**kw))


def test_output_named_modified_without_speed_limit(tmp_path):
    src = tmp_path / "v.funscript"
    src.write_bytes(STRESS.read_bytes())
    assert main(["fix", str(src), "--no-speed-limit"]) == 0
    assert (tmp_path / "v.modified.funscript").exists() and not (tmp_path / "v.safe.funscript").exists()
    assert main(["fix", str(src), "--range", "-50", "80"]) == 1


# --- beat-preserving limiter (feedback: firmware moves beat-synced bottoms) ----

def beat_script(cycles=6):
    """Hold, then: top -> bottom at 600 u/s on the beat -> low in-between point -> back to top."""
    out = [(0, 100)]
    t = 1000
    for _ in range(cycles):
        out += [(t, 100), (t + 150, 10), (t + 300, 20), (t + 600, 100)]
        t += 750
    return acts(*out)


def test_firmware_emulation_moves_turn_off_the_beat():
    a = beat_script()
    emulated, _ = fixers.speed_limit(a, 500, "shorten_start")
    new, _ = emphasis_changes(a, emulated)
    assert 1300 in new          # the in-between point became the bottom
    rep = analyze(Funscript(a), BUILTIN["handy_online"])
    assert rep.counts().get("emphasis_lost")


def test_preserve_anchor_keeps_bottoms_on_the_beat():
    a = beat_script()
    out, summary = fixers.speed_limit(a, 500, "preserve_anchor", "bottom")
    bottoms_before = {(x.at, x.pos) for x in a if x.pos == 10}
    assert bottoms_before <= {(x.at, x.pos) for x in out}
    assert max_speed_of(out) <= 500
    assert emphasis_changes(a, out) == ([], [])
    assert "anchors had to move" not in summary


def test_preserve_anchor_tops():
    a = [Action(x.at, 100 - x.pos) for x in beat_script()]
    out, _ = fixers.speed_limit(a, 500, "preserve_anchor", "top")
    assert {(x.at, x.pos) for x in a if x.pos == 90} <= {(x.at, x.pos) for x in out}
    assert max_speed_of(out) <= 500


def test_pipeline_handy_online_default_keeps_beat():
    res = apply(Funscript(beat_script()), BUILTIN["handy_online"], FixOptions(soft_start=False))
    assert res.new_turns == []
    assert not analyze(res.script, BUILTIN["handy_online"]).counts().get("emphasis_lost")


# --- slow strokes (feedback: firmware re-times strokes under ~33 u/s) ----------

def test_slow_fix_keeps_endpoint_timing():
    out, _ = fixers.slow_fix(acts((0, 0), (5000, 100), (5200, 50)), 33)
    assert [(x.at, x.pos) for x in out] == [(0, 0), (1970, 0), (5000, 100), (5200, 50)]
    assert speed(out[1], out[2]) >= 33


def test_slow_fix_move_then_hold_and_small_moves():
    out, _ = fixers.slow_fix(acts((0, 0), (5000, 100)), 33, "move_then_hold")
    assert [(x.at, x.pos) for x in out] == [(0, 0), (3030, 100), (5000, 100)]
    out, _ = fixers.slow_fix(acts((0, 50), (5000, 52)), 33)
    assert len(out) == 2


def test_slow_strokes_flagged_only_for_retiming_devices():
    s = Funscript(acts((0, 0), (5000, 100)))
    assert analyze(s, BUILTIN["handy_online"]).counts().get("slow_retime")
    assert not analyze(s, BUILTIN["osr"]).counts().get("slow_retime")
    res = apply(s, BUILTIN["handy_online"], FixOptions(soft_start=False))
    assert not analyze(res.script, BUILTIN["handy_online"]).counts().get("slow_retime")
    assert [a.pos for a in apply(s, BUILTIN["osr"], FixOptions(soft_start=False)).script.actions] == [0, 100]


# --- spaced simplification for Handy BLE ---------------------------------------

def test_thin_spacing_keeps_shape_with_fewer_points():
    curve = acts(*[(k * 10, round(100 * (k / 300) ** 2)) for k in range(301)], (3200, 0))
    out, _ = fixers.thin_interpolation(curve, 2, spacing_ms=120)
    assert out[0].at == 0 and (3000, 100) in {(x.at, x.pos) for x in out}
    gaps = [b.at - a.at for a, b in zip(out, out[1:])]
    assert min(gaps) >= 120 and len(out) < 40
    assert turning_points(out) == [len(out) - 2]


def test_thin_handles_very_long_runs():
    run = acts(*[(k, k * 100 // 20000) for k in range(20001)])
    out, _ = fixers.thin_interpolation(run, 0.5)
    assert out[0].at == 0 and out[-1].at == 20000


def test_all_fixes_on_leave_no_problems_on_stress_test():
    s = model.load(STRESS)
    for p in BUILTIN.values():
        res = apply(s, p, FixOptions(intensity_cap=True))
        left = [f.kind for f in analyze(res.script, p).findings if f.severity != "info"]
        assert left == [], (p.key, left)
