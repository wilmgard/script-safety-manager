"""Main window: open a script, pick the device, see the verdict, choose fixes, save.

Layout, top to bottom, in the order a user thinks:
  1. Open a script + choose the device
  2. Verdict: score before -> after and one plain sentence
  3. The graph (original grey, fixed blue, too-fast red)
  4. Problems (plain names, before -> after counts; click to jump)  |  What to fix (a few checkboxes)
  5. Save
Every core option is still reachable under "Advanced settings".
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QListWidget,
                               QMainWindow, QMessageBox, QPushButton, QScrollArea, QSpinBox, QSplitter,
                               QStackedWidget, QTableView, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from .. import cli, model
from ..analysis import Report, analyze
from ..pipeline import FixResult, apply
from ..profiles import BUILTIN, DeviceProfile, all_profiles
from .options_form import OptionsForm
from .plot import ScriptPlot

DEBOUNCE_MS = 200
SEVERITY_COLORS = {"danger": QColor(220, 60, 60), "warning": QColor(230, 150, 20), "info": QColor(130, 130, 130)}
SEVERITY_BRUSHES = {k: QBrush(v) for k, v in SEVERITY_COLORS.items()}
SEVERITY_ORDER = {"danger": 0, "warning": 1, "info": 2}
GOOD, OK, BAD = "#2e9d57", "#d08a00", "#d64545"

# Plain-language names, in the order they are listed.
PROBLEM_NAMES = {
    "over_speed": "Too fast for this device",
    "emphasis_lost": "Device would play it off the beat",
    "slow_retime": "Too slow: device would shift the timing",
    "short_interval": "Actions too close together",
    "hard_start": "Starts at full speed",
    "hard_resume": "Restarts at full speed after a pause",
    "sustained": "Long, very intense section",
    "out_of_range": "File problem: positions outside 0-100",
    "unsorted": "File problem: actions out of order",
    "duplicate_time": "File problem: duplicate timestamps",
    "non_integer": "File note: fractional positions",
    "vibration": "Vibration bursts (note)",
    "hard_reversal": "Sharp direction changes (note)",
}


def _ts(ms: int) -> str:
    s, ms = divmod(int(ms), 1000)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{ms:03d}"


def _score_color(score: int) -> str:
    return GOOD if score >= 90 else OK if score >= 60 else BAD


def _problems(report: Report | None) -> int:
    return sum(1 for f in report.findings if f.severity != "info") if report else 0


# --- detailed findings table (Advanced > Details) --------------------------------

class FindingsModel(QAbstractTableModel):
    COLUMNS = ("severity", "problem", "time", "details")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.findings, self.display, self.keys = [], [], []

    def set_findings(self, findings) -> None:
        self.beginResetModel()
        self.findings = list(findings)
        self.display = [(f.severity, PROBLEM_NAMES.get(f.kind, f.kind),
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
        h.setStretchLastSection(True)
        for c, w in enumerate((70, 230, 170)):
            self.setColumnWidth(c, w)

    def set_report(self, report: Report | None) -> None:
        self.source.set_findings(report.findings if report else [])

    def rowCount(self) -> int:
        return self.proxy.rowCount()

    def finding_at(self, row: int):
        if not 0 <= row < self.proxy.rowCount():
            return None
        return self.source.findings[self.proxy.mapToSource(self.proxy.index(row, 0)).row()]


# --- simple widgets -------------------------------------------------------------

class ScoreBadge(QLabel):
    def __init__(self, caption: str):
        super().__init__()
        self.caption = caption
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumWidth(110)
        self.set_score(None)

    def set_score(self, score: int | None) -> None:
        color = "#999" if score is None else _score_color(score)
        value = "–" if score is None else str(score)
        self.setText(f"<div style='font-size:11px;color:#888'>{self.caption}</div>"
                     f"<div style='font-size:30px;font-weight:600;color:{color}'>{value}</div>")


class ProblemTable(QTableWidget):
    """One row per kind of problem: plain name, count in the original, count after fixing."""

    def __init__(self, parent=None):
        super().__init__(0, 3, parent)
        self.setHorizontalHeaderLabels(["Problem", "Before", "After"])
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setShowGrid(False)
        h = self.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.kinds: list[str] = []

    def set_reports(self, before: Report, after: Report) -> None:
        cb, ca = before.counts(), after.counts()
        sev = {f.kind: f.severity for f in before.findings + after.findings}
        self.kinds = [k for k in PROBLEM_NAMES if k in cb or k in ca]
        self.kinds += sorted(k for k in set(cb) | set(ca) if k not in PROBLEM_NAMES)
        self.setRowCount(len(self.kinds))
        for r, kind in enumerate(self.kinds):
            note = sev.get(kind) == "info"
            name = QTableWidgetItem(("●  " if not note else "○  ") + PROBLEM_NAMES.get(kind, kind))
            name.setForeground(QBrush(SEVERITY_COLORS["info" if note else sev.get(kind, "warning")]))
            name.setToolTip("Click to jump to it in the graph")
            b = QTableWidgetItem(str(cb.get(kind, 0)))
            a = QTableWidgetItem("–" if note else str(ca.get(kind, 0)))
            for item in (b, a):
                item.setTextAlignment(Qt.AlignCenter)
            a.setForeground(QBrush(QColor(GOOD if not ca.get(kind) else (
                "#888" if note else SEVERITY_COLORS[sev.get(kind, "warning")].name()))))
            for c, item in enumerate((name, b, a)):
                self.setItem(r, c, item)


class FixPanel(QGroupBox):
    """The handful of fixes most people need, in plain words. Writes into the advanced OptionsForm,
    which stays the single source of truth."""

    def __init__(self, options: OptionsForm, parent=None):
        super().__init__("What to fix", parent)
        self.options = options
        g = QGridLayout(self)
        g.setVerticalSpacing(6)

        def check(field, text, tip):
            c = QCheckBox(text)
            c.setToolTip(tip)
            c.toggled.connect(lambda v, f=field: self._set(f, v))
            return c

        self.speed = check("speed_limit", "Keep within the device's speed limit",
                           "Shortens strokes that are faster than the device can move.")
        self.profile_mode = "preserve_anchor"
        self.anchor = QComboBox()
        for text, data in (("by keeping bottoms on the beat", ("preserve_anchor", "bottom")),
                           ("by keeping tops on the beat", ("preserve_anchor", "top")),
                           ("by shortening evenly around the middle", ("shorten_center", None)),
                           ("by shortening toward the start (like the Handy firmware)", ("shorten_start", None))):
            self.anchor.addItem(text, data)
        self.anchor.setToolTip("How a stroke that is too fast gets shortened. Keeping bottoms (or tops) in place "
                               "keeps beat-synced thrusts on the beat.")
        self.anchor.currentIndexChanged.connect(self._limiter_chosen)
        self.slow = check("slow_fix", "Fix strokes too slow for this device",
                          "This device's firmware shifts the timing of very slow strokes. A short hold is added "
                          "instead so every point still lands on time.")
        self.interval = check("min_interval", "Merge actions too close together",
                              "Removes actions closer together than the device can follow.")
        self.ease = check("soft_start", "Ease in at the start and after pauses",
                          "Ramps the stroke length up over two seconds instead of starting at full speed.")
        self.calm = check("intensity_cap", "Tone down long, intense sections",
                          "Shortens strokes where the 30-second average speed is very high.")
        self.lo, self.hi = QSpinBox(), QSpinBox()
        for s, f in ((self.lo, "range_min"), (self.hi, "range_max")):
            s.setRange(0, 100)
            s.setKeyboardTracking(False)
            s.valueChanged.connect(lambda v, f=f: self._set(f, v))
        rng = QHBoxLayout()
        rng.addWidget(QLabel("Stroke range"))
        rng.addWidget(self.lo)
        rng.addWidget(QLabel("to"))
        rng.addWidget(self.hi)
        rng.addStretch(1)

        indent = QHBoxLayout()
        indent.addSpacing(24)
        indent.addWidget(self.anchor)
        indent.addStretch(1)
        g.addWidget(self.speed, 0, 0)
        g.addLayout(indent, 1, 0)
        g.setContentsMargins(10, 14, 10, 10)
        for row, w in enumerate((self.slow, self.interval, self.ease, self.calm), start=2):
            g.addWidget(w, row, 0)
        g.addLayout(rng, 6, 0)
        g.setRowStretch(7, 1)
        options.changed.connect(self.sync)
        self.sync()

    def _set(self, field, value):
        self._set_many(**{field: value})

    def _set_many(self, **changes):
        opts = self.options.options()
        if any(getattr(opts, k) != v for k, v in changes.items()):
            self.options.set_options(dataclasses.replace(opts, **changes))

    def _limiter_chosen(self, *_):
        mode, anchor = self.anchor.currentData()
        # Store "profile default" when the choice matches the device's own default, so switching
        # devices keeps following each device's default.
        changes = {"speed_mode": None if mode == self.profile_mode else mode}
        if anchor:
            changes["anchor"] = anchor
        self._set_many(**changes)

    def sync(self) -> None:
        o = self.options.options()
        widgets = (self.speed, self.slow, self.interval, self.ease, self.calm, self.anchor, self.lo, self.hi)
        for w in widgets:
            w.blockSignals(True)
        self.speed.setChecked(o.speed_limit)
        self.slow.setChecked(o.slow_fix)
        self.interval.setChecked(o.min_interval)
        self.ease.setChecked(o.soft_start)
        self.calm.setChecked(o.intensity_cap)
        mode = o.speed_mode or self.profile_mode
        want = (mode, o.anchor if mode == "preserve_anchor" else None)
        self.anchor.setCurrentIndex(max(0, next((i for i in range(self.anchor.count())
                                                 if self.anchor.itemData(i) == want), 0)))
        self.anchor.setEnabled(o.speed_limit)
        self.lo.setMaximum(int(o.range_max) - 1)
        self.hi.setMinimum(int(o.range_min) + 1)
        self.lo.setValue(int(o.range_min))
        self.hi.setValue(int(o.range_max))
        for w in widgets:
            w.blockSignals(False)

    def set_profile(self, p: DeviceProfile) -> None:
        self.slow.setVisible(p.min_speed > 0)
        self.profile_mode = p.speed_limit_mode
        self.sync()


class AdvancedDialog(QDialog):
    def __init__(self, options: OptionsForm, before: FindingsTable, after: FindingsTable, steps: QListWidget,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Advanced")
        self.resize(900, 600)
        tabs = QTabWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(options)
        settings = QWidget()
        sv = QVBoxLayout(settings)
        sv.addWidget(QLabel("All fix settings. Changes apply immediately."))
        sv.addWidget(scroll, 1)
        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(options.reset)
        sv.addWidget(reset, 0, Qt.AlignLeft)
        tabs.addTab(settings, "Settings")
        tabs.addTab(before, "All problems: original")
        tabs.addTab(after, "All problems: fixed")
        tabs.addTab(steps, "What was changed")
        self.tabs = tabs
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.hide)
        v = QVBoxLayout(self)
        v.addWidget(tabs)
        v.addWidget(box)


# --- main window ---------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Script Safety Manager")
        self.resize(1280, 820)
        self.setAcceptDrops(True)

        self.script: model.Funscript | None = None
        self.result: FixResult | None = None
        self.report_before: Report | None = None
        self.report_after: Report | None = None
        self.profiles: dict[str, DeviceProfile] = {}
        self._fresh_load = False
        self._jump_index: dict[str, int] = {}

        self._timer = QTimer(self, singleShot=True, interval=DEBOUNCE_MS)
        self._timer.timeout.connect(self.recompute)

        # Advanced pieces exist up front (the simple panel writes into the options form).
        self.options = OptionsForm()
        self.options.changed.connect(self.schedule_recompute)
        self.table_before = FindingsTable()
        self.table_after = FindingsTable()
        self.steps_list = QListWidget()
        for t in (self.table_before, self.table_after):
            t.selectionModel().currentRowChanged.connect(lambda cur, _prev, t=t: self._zoom_to_row(t, cur.row()))
        self.advanced = AdvancedDialog(self.options, self.table_before, self.table_after, self.steps_list, self)

        self._build_menu()
        self._build_ui()
        self.reload_profiles()

    # --- UI construction ---------------------------------------------------

    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("&File")
        for text, shortcut, slot in (("&Open…", QKeySequence.Open, self.open_dialog),
                                     ("&Save safe copy", QKeySequence.Save, self.save),
                                     ("&Advanced…", None, self.show_advanced),
                                     ("&Reload profiles", None, self.reload_profiles),
                                     ("E&xit", QKeySequence.Quit, self.close)):
            act = QAction(text, self)
            if shortcut is not None:
                act.setShortcut(shortcut)
            act.triggered.connect(slot)
            m.addAction(act)

    def _build_ui(self) -> None:
        root = QWidget()
        rv = QVBoxLayout(root)
        rv.setContentsMargins(16, 12, 16, 12)
        rv.setSpacing(10)

        # 1. Open + device
        top = QHBoxLayout()
        open_btn = QPushButton("Open script…")
        open_btn.clicked.connect(self.open_dialog)
        self.file_label = QLabel("No script open")
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(260)
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        top.addWidget(open_btn)
        top.addWidget(self.file_label, 1)
        top.addWidget(QLabel("Device:"))
        top.addWidget(self.profile_combo)
        rv.addLayout(top)
        self.profile_label = QLabel()
        self.profile_label.setStyleSheet("color: #888")
        self.profile_label.setAlignment(Qt.AlignRight)
        rv.addWidget(self.profile_label)

        self.pages = QStackedWidget()
        rv.addWidget(self.pages, 1)

        # Empty state
        empty = QLabel("<div style='font-size:22px'>Drop a <b>.funscript</b> here</div>"
                       "<div style='font-size:13px;color:#888;margin-top:8px'>or click <b>Open script…</b> above. "
                       "Then choose your device to see what it will struggle with.</div>")
        empty.setAlignment(Qt.AlignCenter)
        empty.setFrameShape(QFrame.StyledPanel)
        empty.setStyleSheet("QLabel { border: 2px dashed #999; border-radius: 12px; }")
        self.pages.addWidget(empty)

        # Loaded state
        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(10)

        # 2. Verdict
        verdict = QFrame()
        verdict.setFrameShape(QFrame.StyledPanel)
        vh = QHBoxLayout(verdict)
        self.badge_before = ScoreBadge("ORIGINAL")
        arrow = QLabel("→")
        arrow.setStyleSheet("font-size: 26px; color: #888")
        self.badge_after = ScoreBadge("AFTER FIX")
        self.verdict_label = QLabel()
        self.verdict_label.setWordWrap(True)
        self.counts_label = QLabel()
        self.counts_label.setStyleSheet("color: #888")
        self.counts_label.setWordWrap(True)
        text = QVBoxLayout()
        text.addStretch(1)
        text.addWidget(self.verdict_label)
        text.addWidget(self.counts_label)
        text.addStretch(1)
        vh.addWidget(self.badge_before)
        vh.addWidget(arrow)
        vh.addWidget(self.badge_after)
        vh.addSpacing(12)
        vh.addLayout(text, 1)
        self.score_label = self.verdict_label   # kept for callers/tests that read the headline
        pv.addWidget(verdict)

        # 3. Graph
        self.plot = ScriptPlot(show_heat=False)
        legend = QLabel("<span style='color:#8a8a8a'>━ original</span> &nbsp; "
                        "<span style='color:#1f8fd6'>━ fixed</span> &nbsp; "
                        "<span style='color:#ff3c3c'>━ too fast</span> &nbsp; "
                        "<span style='color:#888'>scroll to zoom, drag to move</span>")
        if self.plot.main.legend is not None:   # replaced by the plain legend line below the graph
            self.plot.main.legend.setVisible(False)
        graph = QWidget()
        gv = QVBoxLayout(graph)
        gv.setContentsMargins(0, 0, 0, 0)
        gv.addWidget(self.plot, 1)
        gv.addWidget(legend)

        # 4. Problems | What to fix
        self.problems = ProblemTable()
        self.problems.cellClicked.connect(lambda r, _c: self.jump_to(self.problems.kinds[r]))
        probs = QGroupBox("Problems for this device (click one to see where)")
        pl = QVBoxLayout(probs)
        pl.setContentsMargins(10, 14, 10, 10)
        pl.addWidget(self.problems)
        self.fix_panel = FixPanel(self.options)

        lower = QWidget()
        lh = QHBoxLayout(lower)
        lh.setContentsMargins(0, 0, 0, 0)
        lh.addWidget(probs, 3)
        lh.addWidget(self.fix_panel, 2)

        split = QSplitter(Qt.Vertical)
        split.addWidget(graph)
        split.addWidget(lower)
        split.setSizes([380, 300])
        pv.addWidget(split, 1)

        # 5. Save
        bottom = QHBoxLayout()
        adv = QPushButton("Advanced settings…")
        adv.setFlat(True)
        adv.clicked.connect(self.show_advanced)
        self.output_label = QLabel()
        self.output_label.setStyleSheet("color: #888")
        self.save_button = QPushButton("Save safe copy")
        self.save_button.setEnabled(False)
        self.save_button.setMinimumHeight(36)
        self.save_button.setMinimumWidth(180)
        f = QFont(self.save_button.font())
        f.setBold(True)
        self.save_button.setFont(f)
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self.save)
        bottom.addWidget(adv)
        bottom.addStretch(1)
        bottom.addWidget(self.output_label)
        bottom.addWidget(self.save_button)
        pv.addLayout(bottom)

        self.pages.addWidget(page)
        self.setCentralWidget(root)

    def show_advanced(self) -> None:
        self.advanced.show()
        self.advanced.raise_()

    # --- profiles ------------------------------------------------------------

    def reload_profiles(self) -> None:
        current = self.profile_combo.currentData()
        try:
            self.profiles = all_profiles()
        except (OSError, ValueError) as e:   # a broken user profile file shouldn't kill the app
            QMessageBox.warning(self, "Profiles", f"Could not load your custom profiles:\n{e}")
            self.profiles = dict(BUILTIN)
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for key, p in self.profiles.items():
            self.profile_combo.addItem(p.name, key)
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
        bits = [f"top speed {p.max_speed:.0f} units/s"]
        if p.firmware_limiter:
            bits.append("firmware rewrites strokes that are too fast")
        if p.min_speed:
            bits.append(f"and slower than {p.min_speed:.0f} units/s")
        if not p.verified:
            bits.append("these numbers are estimates")
        self.profile_label.setText(" · ".join(bits))
        self.profile_label.setToolTip(p.notes)
        self.fix_panel.set_profile(p)
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
            QMessageBox.critical(self, "Can't open this file", str(e))
            return False
        self.script = script
        self._fresh_load = True
        self._jump_index.clear()
        p = script.path
        axis = "" if script.axis == "L0" else f" · {script.axis} axis"
        self.file_label.setText(f"<b>{p.name}</b> &nbsp;<span style='color:#888'>{_ts(script.duration_ms)[:5]} · "
                                f"{len(script.actions):,} actions{axis}</span>")
        self.file_label.setToolTip(str(p))
        self.setWindowTitle(f"{p.name} – Script Safety Manager")
        self.pages.setCurrentIndex(1)
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
            self.verdict_label.setText(f"<span style='color:{BAD}'>These settings don't work: {e}</span>")
            self.save_button.setEnabled(False)
            return
        self.result = result
        self.statusBar().clearMessage()
        self.report_before = analyze(self.script, profile)
        self.report_after = analyze(result.script, profile)

        self.plot.set_data(self.script.actions, result.script.actions, profile.max_speed,
                           reset_view=self._fresh_load)
        self._fresh_load = False
        self.problems.set_reports(self.report_before, self.report_after)
        self.table_before.set_report(self.report_before)
        self.table_after.set_report(self.report_after)
        self.steps_list.clear()
        self.steps_list.addItems([f"{name}: {summary}" for name, summary in result.steps])

        before, after = _problems(self.report_before), _problems(self.report_after)
        self.badge_before.set_score(self.report_before.score)
        self.badge_after.set_score(self.report_after.score)
        if before == 0:
            head = f"<span style='color:{GOOD}'>✓ This script already plays well on {profile.name}.</span>"
        elif after == 0:
            head = (f"<span style='color:{GOOD}'>✓ Fixed: all {before} problem(s) solved for "
                    f"{profile.name}.</span>")
        else:
            head = (f"<span style='color:{OK}'>{before - after} of {before} problem(s) fixed, {after} left.</span> "
                    f"<span style='color:#888'>Turn on more fixes on the right, or check the list.</span>")
        self.verdict_label.setText(f"<span style='font-size:16px;font-weight:600'>{head}</span>")
        changes = [f"{result.moved:,} points adjusted", f"{result.removed:,} removed"]
        if result.added:
            changes.append(f"{result.added:,} added")
        line = "Changes: " + ", ".join(changes) + "."
        if result.new_turns:
            line += f" Note: {len(result.new_turns)} stroke(s) now turn at a different point than the script."
        self.counts_label.setText(line)

        out = self.output_path()
        self.save_button.setEnabled(True)
        self.save_button.setText("Save safe copy" if opts.is_safe else "Save modified copy")
        self.output_label.setText(f"Saves as {out.name}" if out else "")

    def jump_to(self, kind: str) -> None:
        """Zoom to the next occurrence of this problem (original first, else what's left after fixing)."""
        pool = [f for f in self.report_before.findings if f.kind == kind] if self.report_before else []
        if not pool and self.report_after:
            pool = [f for f in self.report_after.findings if f.kind == kind]
        if not pool:
            return
        i = self._jump_index.get(kind, 0) % len(pool)
        self._jump_index[kind] = i + 1
        f = pool[i]
        self.plot.zoom_to(f.start_ms, f.end_ms)
        self.statusBar().showMessage(f"{PROBLEM_NAMES.get(kind, kind)} {i + 1}/{len(pool)} at {_ts(f.start_ms)}: "
                                     f"{f.message}  (click again for the next one)")

    def _zoom_to_row(self, table: FindingsTable, row: int) -> None:
        f = table.finding_at(row) if row >= 0 else None
        if f is not None:
            self.plot.zoom_to(f.start_ms, f.end_ms)

    # --- saving ----------------------------------------------------------------

    def output_path(self) -> Path | None:
        if self.script is None or self.script.path is None:
            return None
        suffix = cli.SAFE_SUFFIX if self.options.options().is_safe else cli.MODIFIED_SUFFIX
        return cli._output_path(self.script.path, None, suffix)

    def confirm_overwrite(self, dest: Path) -> bool:
        return QMessageBox.question(self, "Replace file?", f"{dest.name} already exists. Replace it?",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def save(self) -> Path | None:
        if self._timer.isActive():   # flush a pending option change so we save what's on screen
            self.recompute()
        if self.result is None:
            return None
        dest = self.output_path()
        src = self.script.path
        if dest is None or dest.resolve() == src.resolve():
            QMessageBox.critical(self, "Save", "Refusing to overwrite the original script.")
            return None
        if dest.exists() and not self.confirm_overwrite(dest):
            return None
        try:
            model.save(self.result.script, dest)
        except OSError as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return None
        self.output_label.setText(f"<span style='color:{GOOD}'>✓ Saved {dest.name}</span>")
        self.statusBar().showMessage(f"Saved to {dest}", 10000)
        return dest
