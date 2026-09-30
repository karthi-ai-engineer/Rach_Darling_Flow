"""Entry point of the installed tray app, "SST Dictation.exe" (no console window). See sst/app.py."""
import ctypes
import sys

from sst.app import main

# While this exists, the installer asks to close the app before updating or uninstalling it.
running_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "SST-Dictation-running")
sys.exit(main())
