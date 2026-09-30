"""Entry point of sst.exe, the command-line tool of the installed app. Opened without arguments it starts the tray
app; with arguments it works like the `sst` command (e.g. `sst.exe devices`, `sst.exe --device 2 dictate`)."""
import ctypes
import subprocess
import sys
import traceback
from pathlib import Path

from sst import __version__
from sst.cli import main

kernel32 = ctypes.windll.kernel32
kernel32.SetConsoleTitleW(f"SST Dictation {__version__}")
# While this exists, the installer asks to close the app before updating or uninstalling it.
running_mutex = kernel32.CreateMutexW(None, False, "SST-Dictation-running")


def _pause_if_own_window() -> None:
    # Opened from the Start menu the console closes on exit, so keep the reason readable.
    # Started from a terminal (which also uses the console), don't wait.
    if kernel32.GetConsoleProcessList((ctypes.c_uint * 2)(), 2) <= 1:
        input("\nPress Enter to close this window.")


if len(sys.argv) == 1:
    # Double-clicked, or opened from a shortcut or taskbar pin made before the tray app existed (sst.exe was the app
    # then): start the tray app instead of a second, console dictation that would only say "already running".
    tray_app = Path(sys.executable).with_name("SST Dictation.exe")
    if getattr(sys, "frozen", False) and tray_app.exists():
        subprocess.Popen([str(tray_app)])
        sys.exit(0)
    sys.argv.append("dictate")
try:
    main()
except SystemExit as e:
    if e.code in (None, 0):
        raise
    print(e.code, file=sys.stderr)
    _pause_if_own_window()
    sys.exit(1)
except Exception:
    traceback.print_exc()
    _pause_if_own_window()
    sys.exit(1)
