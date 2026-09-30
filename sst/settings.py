"""User settings and dictation history, kept in %APPDATA%\\sst (shared by the installed app and the source checkout)."""
import json
import logging
import os
import sys
import time
import winreg
from dataclasses import asdict, dataclass, fields
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "sst"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
HISTORY_FILE = CONFIG_DIR / "history.jsonl"
HISTORY_KEEP = 200  # entries shown and kept

log = logging.getLogger(__name__)


@dataclass
class Settings:
    hotkey: str = "ctrl+win"
    microphone: str = ""  # a device name from input_device_names(); "" = the Windows default
    sounds: bool = True
    save_recordings: bool = True

    @classmethod
    def load(cls, path: Path = SETTINGS_FILE) -> "Settings":
        """Settings from disk. A missing or damaged file gives the defaults, so the app always starts."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as e:
            log.warning("Ignoring unreadable settings file %s: %s", path, e)
            return cls()
        defaults = cls()
        values = {}
        for f in fields(cls):  # keep only known keys with the right type
            value = data.get(f.name, getattr(defaults, f.name)) if isinstance(data, dict) else getattr(defaults, f.name)
            values[f.name] = value if isinstance(value, type(getattr(defaults, f.name))) else getattr(defaults, f.name)
        return cls(**values)

    def save(self, path: Path = SETTINGS_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)  # atomic: a crash mid-write never leaves a half-written file


def add_to_history(text: str, path: Path = HISTORY_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "text": text}, ensure_ascii=False) + "\n")


def read_history(path: Path = HISTORY_FILE) -> list[dict]:
    """Newest first. Damaged lines are skipped; the file is trimmed to HISTORY_KEEP entries."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
            if isinstance(entry, dict) and isinstance(entry.get("text"), str):
                entries.append(entry)
        except ValueError:
            continue
    if len(lines) > HISTORY_KEEP * 2:  # trim now and then, not on every write
        entries = entries[-HISTORY_KEEP:]
        path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8")
    return entries[::-1][:HISTORY_KEEP]


# ---- start with Windows: a value under HKCU\...\Run (the installer's "start when I sign in" option uses the same one)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "SST Dictation"


def can_start_with_windows() -> bool:
    return bool(getattr(sys, "frozen", False))  # only the installed app has a fixed .exe to start


def starts_with_windows() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except OSError:
        return False


def set_start_with_windows(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, f'"{sys.executable}" --startup')  # starts quietly
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
