"""Batch window: check every script in a folder for the chosen device, worst first, and save safe copies."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import batch
from ..pipeline import FixOptions
from ..profiles import DeviceProfile

STATUS_TEXT = {"error": "Can't open", "broken": "Broken actions", "problems": "Problems", "ok": "OK",
               "skipped": "Skipped"}
STATUS_COLOR = {"error": "#d64545", "broken": "#d64545", "problems": "#d08a00", "ok": "#2e9d57", "skipped": "#888888"}


class _Worker(QObject):
    """Runs the checks off the UI thread so the window stays responsive on big folders."""
    item_done = Signal(object)
    finished = Signal(bool)          # True when stopped early

    def __init__(self, files, profile, opts, repair):
        super().__init__()
        self.files, self.profile, self.opts, self.repair = files, profile, opts, repair
        self.stop = False

    def run(self):
        for item in batch.check_folder(self.files, self.profile, self.opts, self.repair, lambda: self.stop):
            self.item_done.emit(item)
        self.finished.emit(self.stop)


class BatchWindow(QDialog):
    open_requested = Signal(str)     # double-clicked a script: open it in the main window

    COLUMNS = ("Script", "Status", "Score", "Main problem", "Details")

    def __init__(self, profile: DeviceProfile, opts: FixOptions, problem_names: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Check a folder – Script Safety Manager")
        self.resize(1100, 640)
        self.profile, self.opts, self.names = profile, opts, problem_names
        self.items: list[batch.BatchItem] = []
        self.folder: Path | None = None
        self._thread: QThread | None = None
        self._worker: _Worker | None = None
        self._total = 0

        top = QHBoxLayout()
        choose = QPushButton("Choose folder…")
        choose.clicked.connect(self.choose_folder)
        self.folder_label = QLabel("No folder chosen")
        self.subfolders = QCheckBox("Include subfolders")
        self.subfolders.setChecked(True)
        self.repair = QCheckBox("Remove broken actions")
        self.repair.setToolTip("Off: scripts with broken actions are listed so you can open and review them.\n"
                               "On: broken actions are removed, and the safe copy records what was removed.")
        self.again = QPushButton("Check again")
        self.again.setEnabled(False)
        self.again.clicked.connect(lambda: self.folder and self.start(self.folder))
        top.addWidget(choose)
        top.addWidget(self.again)
        top.addWidget(self.folder_label, 1)
        top.addWidget(self.subfolders)
        top.addWidget(self.repair)

        self.settings_label = QLabel()
        self.settings_label.setStyleSheet("color: #888")
        self._show_settings()

        self.summary = QLabel()
        self.summary.setStyleSheet("font-size: 15px; font-weight: 600")
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self.stop)
        prog = QHBoxLayout()
        prog.addWidget(self.summary, 1)
        prog.addWidget(self.progress)
        prog.addWidget(self.stop_button)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Interactive)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        for c, w in enumerate((300, 110, 90, 230)):
            self.table.setColumnWidth(c, w)
        self.table.cellDoubleClicked.connect(self._open_row)

        bottom = QHBoxLayout()
        hint = QLabel("Double-click a script to open it in the main window.")
        hint.setStyleSheet("color: #888")
        self.only_problems = QCheckBox("Only scripts with problems")
        self.only_problems.setChecked(True)
        self.save_button = QPushButton("Save safe copies…")
        self.save_button.setEnabled(False)
        self.save_button.setMinimumHeight(34)
        f = self.save_button.font()
        f.setBold(True)
        self.save_button.setFont(f)
        self.save_button.clicked.connect(self.save_dialog)
        bottom.addWidget(hint, 1)
        bottom.addWidget(self.only_problems)
        bottom.addWidget(self.save_button)

        v = QVBoxLayout(self)
        v.addLayout(top)
        v.addWidget(self.settings_label)
        v.addLayout(prog)
        v.addWidget(self.table, 1)
        v.addLayout(bottom)

    # --- settings ---------------------------------------------------------------------

    def set_settings(self, profile: DeviceProfile, opts: FixOptions) -> None:
        """Main window changed device or fixes. Results on screen are for the old settings, so say so and
        offer a re-check rather than silently re-running a possibly large folder."""
        if (profile.key, opts) == (self.profile.key, self.opts):
            return
        self.profile, self.opts = profile, opts
        self._show_settings()
        if self.items:
            self.summary.setText("Device or fixes changed. Click Check again to update the results.")
            self.save_button.setEnabled(False)

    def _show_settings(self) -> None:
        cap = self.opts.max_speed or self.profile.max_speed
        limit = f"speed cap {cap:.0f} units/s" if self.opts.speed_limit else "no speed cap"
        self.settings_label.setText(f"Checking for: {self.profile.name} · {limit} · uses the fixes chosen in "
                                    f"the main window")

    # --- running ----------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder with scripts")
        if folder:
            self.start(Path(folder))

    def start(self, folder: Path) -> bool:
        if self.running:
            return False
        try:
            files = batch.find_scripts(folder, self.subfolders.isChecked())
        except ValueError as e:
            self._plain_message(QMessageBox.Warning, "Check a folder", str(e))
            return False
        self.folder = folder
        self.again.setEnabled(True)
        self.folder_label.setText(f"Folder: {folder.name}")   # just the name: screenshots stay free of user paths
        self.folder_label.setToolTip(str(folder))
        self.items = []
        self.table.setRowCount(0)
        self.save_button.setEnabled(False)
        self._total = len(files)
        if not files:
            self.summary.setText("No .funscript files in this folder.")
            return True
        self.progress.setRange(0, len(files))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.stop_button.setVisible(True)
        self.summary.setText(f"Checking {len(files)} script(s)…")

        self._thread = QThread(self)
        self._worker = _Worker(files, self.profile, self.opts, self.repair.isChecked())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.item_done.connect(self._add_item)
        self._worker.finished.connect(self._finished)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.stop = True

    def wait(self, timeout_ms: int = 120000) -> None:
        """Block until the current check is done (used by tests and the self-test)."""
        from PySide6.QtCore import QCoreApplication, QElapsedTimer
        timer = QElapsedTimer()
        timer.start()
        while self.running and timer.elapsed() < timeout_ms:
            QCoreApplication.processEvents()
            QThread.msleep(5)

    def _add_item(self, item: batch.BatchItem) -> None:
        self.items.append(item)
        self.progress.setValue(len(self.items))

    def _finished(self, stopped: bool) -> None:
        self._thread.quit()
        self._thread.wait()
        self._thread.deleteLater()
        self._thread = self._worker = None
        self.progress.setVisible(False)
        self.stop_button.setVisible(False)
        self.items.sort(key=lambda i: i.sort_key)
        self._fill_table()
        self._summarize(stopped)

    def _fill_table(self) -> None:
        self.table.setRowCount(len(self.items))
        for r, it in enumerate(self.items):
            score = "" if it.score_before is None else f"{it.score_before} → {it.score_after}"
            cells = (str(it.path.relative_to(self.folder)) if self.folder else it.path.name,
                     STATUS_TEXT[it.status], score, self.names.get(it.main_problem, it.main_problem), it.message)
            for c, text in enumerate(cells):
                cell = QTableWidgetItem(text)   # plain text: file names and messages are never rich text
                if c == 1:
                    cell.setForeground(QBrush(QColor(STATUS_COLOR[it.status])))
                if c == 2:
                    cell.setTextAlignment(Qt.AlignCenter)
                if c in (0, 4):
                    cell.setToolTip(f"{it.path}" if c == 0 else text)
                self.table.setItem(r, c, cell)

    def _summarize(self, stopped: bool) -> None:
        count = lambda s: sum(1 for i in self.items if i.status == s)
        bad, broken, err = count("problems"), count("broken"), count("error")
        parts = [f"{len(self.items)} checked" + (f" (stopped, {self._total - len(self.items)} not checked)"
                                                 if stopped else "")]
        if bad:
            parts.append(f"{bad} with problems")
        if broken:
            parts.append(f"{broken} with broken actions")
        if err:
            parts.append(f"{err} can't be opened")
        if count("ok"):
            parts.append(f"{count('ok')} OK")
        if count("skipped"):
            parts.append(f"{count('skipped')} skipped")
        self.summary.setText(" · ".join(parts))
        self.save_button.setEnabled(any(self._saveable()))

    # --- saving -----------------------------------------------------------------------

    def _saveable(self) -> list[batch.BatchItem]:
        wanted = ("problems",) if self.only_problems.isChecked() else ("problems", "ok")
        return [i for i in self.items if i.result is not None and i.status in wanted]

    def save_dialog(self) -> None:
        items = self._saveable()
        if not items:
            return
        box = QMessageBox(QMessageBox.Question, "Save safe copies",
                          f"Save {len(items)} safe cop{'y' if len(items) == 1 else 'ies'}?\n\n"
                          "Next to the originals: each script gets a NAME.safe.funscript beside it.\n"
                          "Another folder: copies go into a folder you choose (subfolders are kept).\n\n"
                          "Originals are never changed.", QMessageBox.NoButton, self)
        box.setTextFormat(Qt.PlainText)
        replace = QCheckBox("Replace safe copies that already exist")
        box.setCheckBox(replace)
        beside = box.addButton("Next to the originals", QMessageBox.AcceptRole)
        other = box.addButton("Another folder…", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() is beside:
            out_dir = None
        elif box.clickedButton() is other:
            chosen = QFileDialog.getExistingDirectory(self, "Save safe copies to")
            if not chosen:
                return
            out_dir = Path(chosen)
        else:
            return
        saved, skipped = self.save_all(out_dir, replace.isChecked())
        where = "next to the originals" if out_dir is None else f"in {out_dir}"
        text = f"Saved {saved} safe cop{'y' if saved == 1 else 'ies'} {where}."
        if skipped:
            text += f"\n\nNot saved ({len(skipped)}):\n" + "\n".join(skipped[:15])
            if len(skipped) > 15:
                text += f"\n… and {len(skipped) - 15} more"
        self._plain_message(QMessageBox.Information, "Save safe copies", text)

    def save_all(self, out_dir: Path | None, replace: bool = False) -> tuple[int, list[str]]:
        """Write safe copies without asking anything (save_dialog does the asking)."""
        saved, skipped = 0, []
        for item in self._saveable():
            ok, msg = batch.save_item(item, self.opts, out_dir, replace=replace, root=self.folder)
            if ok:
                saved += 1
            else:
                skipped.append(f"{item.path.name}: {msg}")
        self.summary.setText(f"Saved {saved} safe cop{'y' if saved == 1 else 'ies'}"
                             + (f", {len(skipped)} not saved" if skipped else ""))
        return saved, skipped

    def _plain_message(self, icon, title, text) -> None:
        box = QMessageBox(icon, title, text, QMessageBox.Ok, self)
        box.setTextFormat(Qt.PlainText)
        box.exec()

    def _open_row(self, row: int, _col: int) -> None:
        if 0 <= row < len(self.items):
            self.open_requested.emit(str(self.items[row].path))

    def closeEvent(self, e) -> None:
        self.stop()
        self.wait(5000)
        super().closeEvent(e)
