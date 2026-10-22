"""Build dist/ScriptSafetyManager.exe: one file, no Python needed on the user's PC.

    pip install -r requirements.txt -r requirements-build.txt
    python packaging/build_exe.py

Then the exe checks itself with the bundled stress test (`--self-test`) before the build counts as done.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "ScriptSafetyManager"
ICON = ROOT / "script_safety" / "gui" / "icon.ico"


def build() -> Path:
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", NAME,
        "--icon", str(ICON),
        "--add-data", f"{ICON}{';' if sys.platform == 'win32' else ':'}script_safety/gui",
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
        # Not used by the app; keeps the exe smaller.
        "--exclude-module", "tkinter", "--exclude-module", "pytest", "--exclude-module", "PyInstaller",
        "--exclude-module", "OpenGL", "--exclude-module", "matplotlib", "--exclude-module", "scipy",
        str(ROOT / "packaging" / "launch.py"),
    ]
    subprocess.run(cmd, check=True, cwd=ROOT)
    exe = ROOT / "dist" / f"{NAME}.exe"
    if not exe.exists():
        raise SystemExit(f"build finished but {exe} is missing")
    return exe


def self_test(exe: Path) -> None:
    """Run the built exe headless against the stress test and check it saved a valid safe copy."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "Stroker_Stress_Test.funscript"
        shutil.copy(ROOT / "Stroker_Stress_Test.funscript", src)
        result = subprocess.run([str(exe), "--self-test", str(src)], timeout=180)
        out = Path(tmp) / "Stroker_Stress_Test.safe.funscript"
        if result.returncode != 0 or not out.exists():
            raise SystemExit(f"self-test FAILED (exit code {result.returncode}, output exists: {out.exists()})")
        report = (Path(tmp) / "self-test.txt").read_text(encoding="utf-8")
        print(report)


if __name__ == "__main__":
    exe = build()
    self_test(exe)
    print(f"OK: {exe} ({exe.stat().st_size / 1e6:.0f} MB)")
