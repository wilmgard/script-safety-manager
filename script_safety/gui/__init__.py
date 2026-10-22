"""PySide6 desktop GUI. Launch with `python -m script_safety.gui [FILE]`.

The GUI holds no safety logic: it only calls model.load/save, analysis.analyze, pipeline.apply and profiles.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

ICON = Path(__file__).resolve().parent / "icon.ico"


def _self_test(win, path: Path) -> int:
    """Headless end-to-end check used to verify a packaged build: open, fix, save, re-read.
    Writes self-test.txt next to the script; exit code 0 means everything worked."""
    from .. import model
    from ..analysis import analyze

    report = path.parent / "self-test.txt"
    try:
        win.set_profile("homebrew_1")
        assert win.load_file(path), "could not open the script"
        win.fix_panel.calm.setChecked(True)
        win.recompute()
        dest = win.save()
        assert dest is not None and dest.exists(), "save failed"
        fixed = model.load(dest)
        score = analyze(fixed, win.profile).score
        before = win.report_before.score
        assert score == 100, f"fixed copy scored {score}, expected 100"

        from .batch_window import BatchWindow   # batch mode: the folder holds the script (+ its safe copy)
        bw = BatchWindow(win.profile, win.options.options(), {})
        assert bw.start(path.parent), "batch check did not start"
        bw.wait()
        assert [i.path.name for i in bw.items] == [path.name], f"batch saw {[i.path.name for i in bw.items]}"
        assert bw.items[0].status == "problems", f"batch status {bw.items[0].status}"
        report.write_text(f"self-test OK: {path.name} score {before} -> {score}, saved {dest.name} "
                          f"({len(fixed.actions)} actions); batch: {bw.summary.text()}\n", encoding="utf-8")
        return 0
    except Exception:
        report.write_text("self-test FAILED\n" + traceback.format_exc(), encoding="utf-8")
        return 1


def main(argv: list[str] | None = None) -> int:
    import pyqtgraph as pg
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    argv = sys.argv if argv is None else argv
    pg.setConfigOptions(antialias=True)
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("Script Safety Manager")
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))
    win = MainWindow()
    if len(argv) > 2 and argv[1] == "--self-test":
        return _self_test(win, Path(argv[2]).resolve())
    win.show()
    if len(argv) > 1:
        win.load_file(argv[1])
    return app.exec()
