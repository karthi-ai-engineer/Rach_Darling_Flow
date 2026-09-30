"""User settings and dictation history, kept in %APPDATA%\\sst (shared by the installed app and the source checkout)."""
import json
import logging
import os
import sys
import time
import winreg
from dataclasses import asdict, dataclass, field, fields
from datetime import date, timedelta
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "sst"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
HISTORY_FILE = CONFIG_DIR / "history.jsonl"
HISTORY_KEEP = 200  # entries shown and kept
STATS_FILE = CONFIG_DIR / "stats.json"
STATS_DAYS = 400  # days of per-day word counts kept (for "this week" and the streak)

log = logging.getLogger(__name__)


@dataclass
class Settings:
    hotkey: str = "ctrl+win"
    microphone: str = ""  # a device name from input_device_names(); "" = the Windows default
    sounds: bool = True
    save_recordings: bool = True
    cleanup: bool = False  # clean up the text with an AI model before typing it
    cleanup_model: str = ""  # a model id on the user's endpoint (sst.gateway)
    cleanup_fallback: str = ""  # optional backup model, tried when the first one fails
    vocabulary: list[str] = field(default_factory=list)  # the user's names and terms, for the cleanup
    welcomed: bool = False  # the first-run welcome was completed (or skipped)
    told_about_tray: bool = False  # closing the window keeps Rflow in the tray; said once

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
            default = getattr(defaults, f.name)
            value = data.get(f.name, default) if isinstance(data, dict) else default
            ok = isinstance(value, type(default)) and (not isinstance(value, list) or all(isinstance(v, str) for v in value))
            values[f.name] = value if ok else default
        if isinstance(data, dict) and "cleanup" not in data and values["cleanup_model"]:
            values["cleanup"] = True  # settings from before the on/off switch: a chosen model meant "on"
        return cls(**values)

    def save(self, path: Path = SETTINGS_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)  # atomic: a crash mid-write never leaves a half-written file


def add_to_history(text: str, heard: str | None = None, path: Path = HISTORY_FILE) -> None:
    """`text` as typed; `heard` is the recognizer's text when the cleanup changed it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "text": text}
    if heard is not None and heard != text:
        entry["heard"] = heard
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


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


@dataclass
class Stats:
    """Totals for the Home page. Kept apart from the history, which only keeps the last HISTORY_KEEP dictations."""
    words: int = 0
    dictations: int = 0
    timed_words: int = 0  # words of the dictations whose length is known, for the speaking speed
    seconds: float = 0.0
    days: dict[str, int] = field(default_factory=dict)  # "2026-09-30" -> words dictated that day

    def add(self, text: str, seconds: float | None, day: date) -> None:
        words = len(text.split())
        self.words += words
        self.dictations += 1
        if seconds:
            self.timed_words += words
            self.seconds += seconds
        key = day.isoformat()
        self.days[key] = self.days.get(key, 0) + words
        if len(self.days) > STATS_DAYS:
            self.days = dict(sorted(self.days.items())[-STATS_DAYS:])

    @property
    def words_per_minute(self) -> int | None:
        """Speaking speed, once there is enough to say (half a minute of speech)."""
        return round(self.timed_words / self.seconds * 60) if self.seconds >= 30 else None

    def words_this_week(self, today: date) -> int:
        return sum(self.days.get((today - timedelta(days=n)).isoformat(), 0) for n in range(7))

    def streak(self, today: date) -> int:
        """Days in a row with dictation, up to today (or up to yesterday: today isn't over yet)."""
        day = today if today.isoformat() in self.days else today - timedelta(days=1)
        count = 0
        while day.isoformat() in self.days:
            count, day = count + 1, day - timedelta(days=1)
        return count

    @classmethod
    def load(cls, path: Path = STATS_FILE, history: Path = HISTORY_FILE) -> "Stats":
        """The saved totals; the first time, a start from the history (its dictations have no length yet)."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(words=int(data["words"]), dictations=int(data["dictations"]), timed_words=int(data["timed_words"]),
                       seconds=float(data["seconds"]), days={str(k): int(v) for k, v in dict(data["days"]).items()})
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("Starting the stats again; unreadable %s: %s", path, e)
        stats = cls()
        for entry in reversed(read_history(history)):
            try:
                stats.add(entry["text"], None, date.fromisoformat(entry.get("time", "")[:10]))
            except ValueError:
                continue
        return stats

    def save(self, path: Path = STATS_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)


# ---- start with Windows: a value under HKCU\...\Run (the installer's "start when I sign in" option uses the same one)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Rflow"


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
