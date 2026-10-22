"""Batch mode: core (script_safety.batch) and the app's batch window."""
import json
import os
import shutil
from pathlib import Path

import pytest

from script_safety import batch, model
from script_safety.pipeline import FixOptions
from script_safety.profiles import BUILTIN

STRESS = Path(__file__).resolve().parent.parent / "Stroker_Stress_Test.funscript"
HOMEBREW = BUILTIN["homebrew_1"]


def _script(actions):
    return json.dumps({"actions": [{"at": t, "pos": p} for t, p in actions]})


@pytest.fixture
def folder(tmp_path):
    """One of everything batch mode has to cope with."""
    root = tmp_path / "scripts"
    (root / "sub").mkdir(parents=True)
    (root / "gentle.funscript").write_text(_script([(0, 50), (3000, 50)] + [(3000 + 1000 * k, 30 + 40 * (k % 2))
                                                                         for k in range(1, 20)]))
    shutil.copy(STRESS, root / "wild.funscript")
    shutil.copy(STRESS, root / "sub" / "wild.funscript")             # same name in a subfolder
    (root / "broken.funscript").write_text('{"actions":[{"at":0,"pos":0},{"at":500,"pos":NaN},'
                                           '{"at":1000,"pos":100},{"at":2000,"pos":0}]}')
    (root / "damaged.funscript").write_text('{"actions":[{"at":0,"pos":0},{"at":10')
    shutil.copy(STRESS, root / "video.roll.funscript")
    shutil.copy(STRESS, root / "wild.safe.funscript")                # our own output: never re-checked
    (root / "notes.txt").write_text("not a script")
    return root


def _check(root, repair=False, recursive=True):
    files = batch.find_scripts(root, recursive)
    return {str(i.path.relative_to(root)): i
            for i in batch.check_folder(files, HOMEBREW, FixOptions(), repair=repair)}


def test_find_scripts_skips_outputs_and_other_files(folder):
    names = [str(p.relative_to(folder)) for p in batch.find_scripts(folder)]
    assert names == ["broken.funscript", "damaged.funscript", "gentle.funscript",
                     os.path.join("sub", "wild.funscript"), "video.roll.funscript", "wild.funscript"]
    assert os.path.join("sub", "wild.funscript") not in [str(p.relative_to(folder))
                                                          for p in batch.find_scripts(folder, recursive=False)]


def test_statuses(folder):
    r = _check(folder)
    assert r["gentle.funscript"].status == "ok" and r["gentle.funscript"].score_before == 100
    wild = r["wild.funscript"]
    assert wild.status == "problems" and wild.score_before == 0 and wild.main_problem == "over_speed"
    assert wild.problems_after < wild.problems_before
    assert r["broken.funscript"].status == "broken" and r["broken.funscript"].result is None
    assert r["damaged.funscript"].status == "error" and "not valid JSON" in r["damaged.funscript"].message
    assert r["video.roll.funscript"].status == "skipped"


def test_repair_option_fixes_broken_scripts(folder):
    item = _check(folder, repair=True)["broken.funscript"]
    assert item.status in ("ok", "problems") and item.repaired == 1 and item.result is not None
    assert "1 broken action(s) removed" in item.message


def test_worst_first(folder):
    order = [i.path.name for i in sorted(_check(folder).values(), key=lambda i: i.sort_key)]
    assert order[0] == "damaged.funscript" and order[1] == "broken.funscript"
    assert order.index("wild.funscript") < order.index("gentle.funscript")
    assert order[-1] == "video.roll.funscript"


def test_save_beside_originals_never_overwrites(folder):
    items = _check(folder)
    before = (folder / "wild.funscript").read_bytes()
    ok, msg = batch.save_item(items[os.path.join("sub", "wild.funscript")], FixOptions())
    assert ok and msg == "wild.safe.funscript" and (folder / "sub" / "wild.safe.funscript").exists()
    # wild.safe.funscript already exists next to the top-level script: refused unless replacing is asked for
    ok, msg = batch.save_item(items["wild.funscript"], FixOptions())
    assert not ok and "already exists" in msg
    ok, _ = batch.save_item(items["wild.funscript"], FixOptions(), replace=True)
    assert ok
    assert (folder / "wild.funscript").read_bytes() == before
    assert model.load(folder / "wild.safe.funscript").raw["metadata"]["safety_manager"]["profile"] == "homebrew_1"


def test_save_to_other_folder_keeps_subfolders(folder, tmp_path):
    items = _check(folder)
    out = tmp_path / "out"
    for key in ("wild.funscript", os.path.join("sub", "wild.funscript")):
        ok, _ = batch.save_item(items[key], FixOptions(), out, root=folder)
        assert ok
    assert (out / "wild.safe.funscript").exists() and (out / "sub" / "wild.safe.funscript").exists()


def test_nothing_to_save_for_unopenable(folder):
    assert batch.save_item(_check(folder)["damaged.funscript"], FixOptions()) == (False, "nothing to save")


def test_not_a_folder(tmp_path):
    with pytest.raises(ValueError):
        batch.find_scripts(tmp_path / "missing")


# --- the app's batch window --------------------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PySide6")
    pytest.importorskip("pyqtgraph")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_batch_window_end_to_end(app, folder, tmp_path):
    from script_safety.gui.main_window import MainWindow
    win = MainWindow()
    win.set_profile("homebrew_1")
    bw = win.show_batch()
    assert bw.start(folder)
    assert bw.running
    bw.wait()
    assert not bw.running and bw.table.rowCount() == 6
    assert bw.table.item(0, 0).text() == "damaged.funscript"          # worst first
    assert "2 with problems" in bw.summary.text() and "1 with broken actions" in bw.summary.text()

    # Only scripts with problems are saved by default, into another folder with subfolders kept
    out = tmp_path / "safe_copies"
    saved, skipped = bw.save_all(out)
    assert saved == 2 and not skipped
    assert (out / "wild.safe.funscript").exists() and (out / "sub" / "wild.safe.funscript").exists()
    assert not (out / "gentle.safe.funscript").exists()

    # Double-clicking a row opens that script in the main window
    row = [bw.table.item(r, 0).text() for r in range(bw.table.rowCount())].index("wild.funscript")
    bw._open_row(row, 0)
    assert win.script is not None and win.script.path.name == "wild.funscript"

    # Changing the device in the main window marks the results as out of date
    win.set_profile("handy_online")
    assert bw.profile.key == "handy_online" and "Check again" in bw.summary.text()
    assert not bw.save_button.isEnabled()
    bw.start(folder)
    bw.wait()
    assert "handy" in bw.settings_label.text().lower()
    bw.close()
    win.close()


def test_batch_window_stop(app, folder):
    from script_safety.gui.batch_window import BatchWindow
    bw = BatchWindow(HOMEBREW, FixOptions(), {})
    bw.start(folder)
    bw.stop()
    bw.wait()
    assert not bw.running
    assert "checked" in bw.summary.text()


def test_batch_window_shows_folder_name_not_full_path(app, folder):
    from script_safety.gui.batch_window import BatchWindow
    bw = BatchWindow(HOMEBREW, FixOptions(), {})
    bw.start(folder)
    bw.wait()
    assert bw.folder_label.text() == "Folder: scripts"
    assert str(folder.parent) not in bw.folder_label.text()
    bw.close()
