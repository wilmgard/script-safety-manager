"""Offscreen smoke test for the PySide6 GUI."""
import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import QApplication  # noqa: E402

from script_safety import model  # noqa: E402
from script_safety.gui.main_window import MainWindow  # noqa: E402

STRESS = Path(__file__).resolve().parent.parent / "Stroker_Stress_Test.funscript"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_window_loads_recomputes_and_saves(app, tmp_path):
    src = tmp_path / STRESS.name
    shutil.copy(STRESS, src)
    win = MainWindow()
    win.set_profile("handy_online")
    assert win.load_file(src)
    assert win.result is not None and win.report_before is not None
    assert len(win.result.script.actions) > 0
    assert win.table_before.rowCount() == len(win.report_before.findings)

    # Toggling an option goes through the debounced path; flush it and check the fix changed.
    win.options.set_options(win.options.options().__class__(speed_limit=False, soft_start=False))
    assert win._timer.isActive()
    win.recompute()
    assert win.report_after is not None
    assert "speed_limit" not in [name for name, _ in win.result.steps]

    win.options.reset()
    win.recompute()
    win._zoom_to_row(win.table_before, 0)
    dest = win.save()
    assert dest is not None and dest.exists() and dest != src
    assert dest.name == "Stroker_Stress_Test.safe.funscript"
    assert len(model.load(dest).actions) == len(win.result.script.actions)
    win.close()


def test_simple_flow(app, tmp_path):
    src = tmp_path / STRESS.name
    shutil.copy(STRESS, src)
    win = MainWindow()
    assert win.pages.currentIndex() == 0          # empty state invites a drop
    win.set_profile("handy_online")
    assert win.load_file(src)
    assert win.pages.currentIndex() == 1
    assert win.problems.rowCount() > 0 and win.problems.kinds[0] == "over_speed"
    assert win.badge_after.text() and "fixed" in win.verdict_label.text()

    # Plain checkboxes write through to the full options
    win.fix_panel.calm.setChecked(True)
    assert win.options.options().intensity_cap
    win.recompute()
    assert "intensity_cap" in [n for n, _ in win.result.steps]

    # The slow-stroke fix only shows for devices that need it
    win.set_profile("osr")
    assert win.fix_panel.slow.isHidden()
    # OSR's own limiter is shown as its default, and choosing "keep bottoms" really switches the limiter
    assert win.fix_panel.anchor.currentData() == ("shorten_center", None)
    win.fix_panel.anchor.setCurrentIndex(0)
    assert win.options.options().speed_mode == "preserve_anchor"
    win.recompute()
    assert any("preserve_anchor" in s for _, s in win.result.steps)

    # Clicking a problem zooms to it and cycles through occurrences
    win.jump_to("over_speed")
    first = win.plot.main.viewRange()[0]
    win.jump_to("over_speed")
    assert win.plot.main.viewRange()[0] != first

    # Turning the speed limit off renames the output honestly
    win.fix_panel.speed.setChecked(False)
    win.recompute()
    assert win.save_button.text() == "Save modified copy"
    assert win.output_path().name == "Stroker_Stress_Test.modified.funscript"
    win.close()


def test_speed_cap_is_user_defined(app, tmp_path):
    src = tmp_path / STRESS.name
    shutil.copy(STRESS, src)
    win = MainWindow()
    win.set_profile("osr")
    assert win.load_file(src)
    assert win.fix_panel.cap.value() == 500            # matched to the Handy by default
    assert "matched to the Handy" in win.profile_label.text()
    win.fix_panel.cap.setValue(300)
    assert win.options.options().max_speed == 300
    win.recompute()
    assert any("under 300 u/s" in s for _, s in win.result.steps)
    win.fix_panel.cap.setValue(500)                    # back to the device default
    assert win.options.options().max_speed is None
    win.close()


def test_multi_axis_file_warns(app, tmp_path):
    src = tmp_path / "video.roll.funscript"
    shutil.copy(STRESS, src)
    win = MainWindow()
    assert win.load_file(src)
    assert "single-axis" in win.file_label.text()
    win.close()
