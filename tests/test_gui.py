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
