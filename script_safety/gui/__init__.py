"""PySide6 desktop GUI. Launch with `python -m script_safety.gui [FILE]`.

The GUI holds no safety logic: it only calls model.load/save, analysis.analyze, pipeline.apply and profiles.
"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    import pyqtgraph as pg
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    argv = sys.argv if argv is None else argv
    pg.setConfigOptions(antialias=False)
    app = QApplication.instance() or QApplication(argv)
    win = MainWindow()
    win.show()
    if len(argv) > 1:
        win.load_file(argv[1])
    return app.exec()
