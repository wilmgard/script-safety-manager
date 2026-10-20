"""Main window: load a funscript, pick a device profile, tune fixes, compare before/after, save."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
                               QLabel, QListWidget, QMainWindow, QMessageBox, QPushButton, QScrollArea, QSplitter,
                               QTableView, QTabWidget, QVBoxLayout, QWidget)

from .. import cli, model
from ..analysis import Report, analyze
from ..pipeline import FixResult, apply
from ..profiles import DeviceProfile, all_profiles
from .options_form import OptionsForm
from .plot import ScriptPlot

DEBOUNCE_MS = 200
SEVERITY_COLORS = {"danger": QColor(220, 60, 60), "warning": QColor(230, 160, 30), "info": QColor(120, 120, 120)}
SEVERITY_BRUSHES = {k: QBrush(v) for k, v in SEVERITY_COLORS.items()}
SEVERITY_ORDER = {"danger": 0, "warning": 1, "info": 2}
SAFE_SUFFIX = getattr(cli, "SAFE_SUFFIX", ".safe")
MODIFIED_SUFFIX = getattr(cli, "MODIFIED_SUFFIX", ".modified")


def _ts(ms: int) -> str:
    s, ms = divmod(int(ms), 1000)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{ms:03d}"


class FindingsModel(QAbstractTableModel):
    COLUMNS = ("severity", "kind", "time", "message")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.findings, self.display, self.keys = [], [], []

    def set_findings(self, findings) -> None:
        self.beginResetModel()
        self.findings = list(findings)
        # Precomputed so sorting (which asks for every row) doesn't re-format on each call.
        self.display = [(f.severity, f.kind,
                         _ts(f.start_ms) if f.end_ms == f.start_ms else f"{_ts(f.start_ms)} – {_ts(f.end_ms)}",
                         f.message) for f in self.findings]
        self.keys = [(SEVERITY_ORDER.get(f.severity, 9), f.kind, f.start_ms, f.message) for f in self.findings]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.findings)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        r, c = index.row(), index.column()
        if role == Qt.DisplayRole:
            return self.display[r][c]
        if role == Qt.UserRole:
            return self.keys[r][c]
        if role == Qt.ForegroundRole and c == 0:
            return SEVERITY_BRUSHES.get(self.findings[r].severity)
        return None


class FindingsTable(QTableView):
    """Model-based so repopulating hundreds of rows on every recompute stays cheap."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = FindingsModel(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.source)
        self.proxy.setSortRole(Qt.UserRole)
        self.setModel(self.proxy)
        self.setSortingEnabled(True)
        self.sortByColumn(2, Qt.AscendingOrder)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(20)
        h = self.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setStretchLastSection(True)
        for c, w in enumerate((70, 120, 170)):
            self.setColumnWidth(c, w)

    def set_report(self, report: Report | None) -> None:
        self.source.set_findings(report.findings if report else [])

    def rowCount(self) -> int:
        return self.proxy.rowCount()

    def finding_at(self, row: int):
        if not 0 <= row < self.proxy.rowCount():
            return None
        return self.source.findings[self.proxy.mapToSource(self.proxy.index(row, 0)).row()]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Script Safety Manager")
        self.resize(1400, 860)
        self.setAcceptDrops(True)

        self.script: model.Funscript | None = None
        self.result: FixResult | None = None
        self.report_before: Report | None = None
        self.report_after: Report | None = None
        self.profiles: dict[str, DeviceProfile] = {}
        self._fresh_load = False

        self._timer = QTimer(self, singleShot=True, interval=DEBOUNCE_MS)
        self._timer.timeout.connect(self.recompute)

        self._build_menu()
        self._build_ui()
        self.reload_profiles()

    # --- UI construction ---------------------------------------------------

    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("&File")
        for text, shortcut, slot in (("&Open…", QKeySequence.Open, self.open_dialog),
                                     ("&Save fixed copy", QKeySequence.Save, self.save),
                                     ("&Reload profiles", None, self.reload_profiles),
                                     ("E&xit", QKeySequence.Quit, self.close)):
            act = QAction(text, self)
            if shortcut is not None:
                act.setShortcut(shortcut)
            act.triggered.connect(slot)
            m.addAction(act)

    def _build_ui(self) -> None:
        # Left: file info, profile, options
        left = QWidget()
        lv = QVBoxLayout(left)
        self.file_label = QLabel("Open a .funscript (File › Open or drag it here)")
        self.file_label.setWordWrap(True)
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lv.addWidget(self.file_label)

        pf = QFormLayout()
        self.profile_combo = QComboBox()
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        pf.addRow("Device profile", self.profile_combo)
        lv.addLayout(pf)
        self.profile_label = QLabel()
        self.profile_label.setWordWrap(True)
        self.profile_label.setStyleSheet("color: gray")
        lv.addWidget(self.profile_label)

        self.options = OptionsForm()
        self.options.changed.connect(self.schedule_recompute)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.options)
        lv.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        reset = QPushButton("Reset options")
        reset.clicked.connect(self.options.reset)
        self.save_button = QPushButton("Save fixed copy")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(reset)
        buttons.addWidget(self.save_button)
        lv.addLayout(buttons)

        # Right: plot on top, findings / steps below
        self.plot = ScriptPlot()
        self.score_label = QLabel()
        self.score_label.setStyleSheet("font-size: 15px; font-weight: bold")
        self.counts_label = QLabel()

        self.table_before = FindingsTable()
        self.table_after = FindingsTable()
        self.steps_list = QListWidget()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.table_before, "Findings: original")
        self.tabs.addTab(self.table_after, "Findings: fixed")
        self.tabs.addTab(self.steps_list, "Fix steps")
        for t in (self.table_before, self.table_after):
            t.selectionModel().currentRowChanged.connect(lambda cur, _prev, t=t: self._zoom_to_row(t, cur.row()))
            t.clicked.connect(lambda idx, t=t: self._zoom_to_row(t, idx.row()))

        bottom = QWidget()
        bv = QVBoxLayout(bottom)
        bv.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(self.score_label)
        header.addStretch(1)
        header.addWidget(self.counts_label)
        bv.addLayout(header)
        bv.addWidget(self.tabs)

        right = QSplitter(Qt.Vertical)
        right.addWidget(self.plot)
        right.addWidget(bottom)
        right.setSizes([520, 340])

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 1060])
        self.setCentralWidget(split)

    # --- profiles ------------------------------------------------------------

    def reload_profiles(self) -> None:
        current = self.profile_combo.currentData()
        try:
            self.profiles = all_profiles()
        except (OSError, ValueError) as e:   # a broken user profile file shouldn't kill the app
            QMessageBox.warning(self, "Profiles", f"Could not load user profiles:\n{e}")
            from ..profiles import BUILTIN
            self.profiles = dict(BUILTIN)
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for key, p in self.profiles.items():
            self.profile_combo.addItem(p.name if p.verified else f"{p.name}  (unverified)", key)
            self.profile_combo.setItemData(self.profile_combo.count() - 1, p.notes, Qt.ToolTipRole)
        idx = self.profile_combo.findData(current if current in self.profiles else "generic")
        self.profile_combo.setCurrentIndex(max(0, idx))
        self.profile_combo.blockSignals(False)
        self._profile_changed()

    @property
    def profile(self) -> DeviceProfile:
        return self.profiles[self.profile_combo.currentData()]

    def set_profile(self, key: str) -> None:
        self.profile_combo.setCurrentIndex(self.profile_combo.findData(key))

    def _profile_changed(self, *_):
        if not self.profiles:
            return
        p = self.profile
        bits = [f"max {p.max_speed:.0f} u/s", f"min interval {p.min_interval_ms} ms",
                f"limiter {p.speed_limit_mode}"]
        if getattr(p, "min_speed", 0):
            bits.append(f"min speed {p.min_speed:.0f} u/s")
        text = ", ".join(bits)
        if not p.verified:
            text += "\nUnverified: numbers are placeholders."
        self.profile_label.setText(text)
        self.profile_label.setToolTip(p.notes)
        self.schedule_recompute()

    # --- loading -------------------------------------------------------------

    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open funscript", "", "Funscripts (*.funscript);;All files (*)")
        if path:
            self.load_file(path)

    def load_file(self, path: str | Path) -> bool:
        try:
            script = model.load(path)
        except (OSError, ValueError) as e:
            QMessageBox.critical(self, "Open failed", str(e))
            return False
        self.script = script
        self._fresh_load = True
        p = script.path
        self.file_label.setText(f"<b>{p.name}</b><br>{p.parent}<br>axis {script.axis} · "
                                f"{len(script.actions)} actions · {_ts(script.duration_ms)}")
        self.file_label.setToolTip(str(p))
        self.setWindowTitle(f"{p.name} – Script Safety Manager")
        self.recompute()
        return True

    def dragEnterEvent(self, e) -> None:
        if any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.load_file(u.toLocalFile())
                break

    # --- recompute -----------------------------------------------------------

    def schedule_recompute(self) -> None:
        self._timer.start()

    def recompute(self) -> None:
        self._timer.stop()
        if self.script is None or not self.profiles:
            return
        profile = self.profile
        try:
            opts = self.options.options()
            result = apply(self.script, profile, opts)
        except (ValueError, TypeError) as e:
            self.statusBar().showMessage(f"Invalid options: {e}")
            self.save_button.setEnabled(False)
            return
        self.result = result
        self.report_before = analyze(self.script, profile)
        self.report_after = analyze(result.script, profile)

        self.plot.set_data(self.script.actions, result.script.actions, profile.max_speed,
                           reset_view=self._fresh_load)
        self._fresh_load = False
        self.table_before.set_report(self.report_before)
        self.table_after.set_report(self.report_after)
        self.tabs.setTabText(0, f"Findings: original ({len(self.report_before.findings)})")
        self.tabs.setTabText(1, f"Findings: fixed ({len(self.report_after.findings)})")
        self.steps_list.clear()
        self.steps_list.addItems([f"{name}: {summary}" for name, summary in result.steps])

        self.score_label.setText(f"Safety score  {self.report_before.score} → {self.report_after.score}")
        counts = [f"{result.moved} moved", f"{result.removed} removed"]
        if getattr(result, "added", 0):
            counts.append(f"{result.added} added")
        new_turns = getattr(result, "new_turns", None)
        if new_turns:
            counts.append(f"stroke turns off-script at {len(new_turns)} point(s) (first {_ts(new_turns[0])})")
        self.counts_label.setText(" · ".join(counts))
        self.save_button.setEnabled(True)
        self.save_button.setText("Save fixed copy" if opts.is_safe else "Save modified copy")
        self.statusBar().showMessage(f"Output: {self.output_path().name}" if self.output_path() else "")

    def _zoom_to_row(self, table: FindingsTable, row: int) -> None:
        f = table.finding_at(row) if row >= 0 else None
        if f is not None:
            self.plot.zoom_to(f.start_ms, f.end_ms)

    # --- saving ----------------------------------------------------------------

    def output_path(self) -> Path | None:
        if self.script is None or self.script.path is None:
            return None
        safe = getattr(self.options.options(), "is_safe", True)
        return cli._output_path(self.script.path, None, SAFE_SUFFIX if safe else MODIFIED_SUFFIX)

    def confirm_overwrite(self, dest: Path) -> bool:
        return QMessageBox.question(self, "Overwrite?", f"{dest.name} already exists. Overwrite it?",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def save(self) -> Path | None:
        if self._timer.isActive():   # flush a pending option change so we save what's on screen
            self.recompute()
        if self.result is None:
            return None
        dest = self.output_path()
        src = self.script.path
        if dest is None or dest.resolve() == src.resolve():
            QMessageBox.critical(self, "Save", "Refusing to overwrite the source script.")
            return None
        if dest.exists() and not self.confirm_overwrite(dest):
            return None
        try:
            model.save(self.result.script, dest)
        except OSError as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return None
        self.statusBar().showMessage(f"Saved {dest}", 8000)
        return dest
