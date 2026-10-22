"""Entry point for the packaged Windows app (PyInstaller runs this file)."""
from script_safety.gui import main

raise SystemExit(main())
