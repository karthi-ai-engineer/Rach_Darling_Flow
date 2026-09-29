"""Entry point of the installed app, sst.exe. Opening it starts dictation; with arguments it
works like the `sst` command (e.g. `sst.exe devices`, `sst.exe --device 2 dictate`)."""
import ctypes
import sys
import traceback

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
