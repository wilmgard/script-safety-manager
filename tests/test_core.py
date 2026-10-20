import json
from pathlib import Path

import pytest

from script_safety import fixers, model
from script_safety.analysis import analyze, find_bursts, find_resumes
from script_safety.cli import _output_path, main
from script_safety.model import Action, Funscript, speed
from script_safety.pipeline import FixOptions, apply
from script_safety.profiles import BUILTIN, get_profile, load_profile_file

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


@pytest.mark.parametrize("mode", ["shorten_start", "shorten_center"])
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
        assert not [f for f in rep.findings if f.kind in ("over_speed", "short_interval", "out_of_range")], p.key
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
