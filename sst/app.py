"""Rflow, the app: the window (sst/window.py), a tray icon, and dictation without a console window.

Opening Rflow shows its window; closing the window keeps Rflow in the tray, where dictation goes on working. While
you dictate, a small "pill" near the bottom of the screen shows the recording level, then the transcribing, then
"Typed". The dictation itself is sst.dictate.Dictation, the same as the console command.

  uv run sst app                        from the source checkout
  Rflow.exe                             the installed app (the Start menu shortcut); again: shows the running window
  Rflow.exe --startup                   at sign-in: only the tray icon
  Rflow.exe --self-test                 build check: builds every window off-screen and transcribes once
"""
import ctypes
import dataclasses
import logging
import math
import os
import shutil
import sys
import threading
import time
from collections import deque
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QDesktopServices, QFont, QFontMetrics, QGuiApplication, QIcon, QPainter
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QWidget

from sst import __version__, bench, updates
from sst.audio import Recorder, input_device_names
from sst.dictate import DEFAULT_HOTKEY, Dictation, already_running, wispr_flow_running
from sst.engines import load_engine
from sst.gateway import GatewayConfig, Polisher
from sst.hotkey import HotkeyListener, parse_hotkey
from sst.settings import Profiles, Settings, Stats, add_to_history, read_history
from sst.window import APP_NAME, ICON_FILE, LOG_DIR, MainWindow, PreviewApp

UPDATE_DIR = Path(os.environ.get("TEMP", Path.home())) / "Rflow-update"  # downloaded installers
# Opening Rflow while it runs asks the running copy, through this local pipe, to show its window.
SERVER_NAME = f"Rflow-window-{os.environ.get('USERNAME', 'user')}"
ASFW_ANY = -1  # AllowSetForegroundWindow: any process

log = logging.getLogger("sst.app")


def setup_logging() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_DIR / "sst.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    return LOG_DIR


# ---------------------------------------------------------------- the recording pill

class Pill(QWidget):
    """A small capsule near the bottom of the screen. It never takes keyboard focus and ignores the mouse,
    so the dictated text still goes to the app you were typing in."""

    HEIGHT = 44
    BARS = 28

    def __init__(self, level=lambda: 0.0):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._level = level
        self.state, self.message = "hidden", ""
        self._levels: deque[float] = deque([0.0] * self.BARS, maxlen=self.BARS)
        self._phase = 0.0
        self._animation = QTimer(self, interval=33, timeout=self._animate)
        self._hide_timer = QTimer(self, singleShot=True, timeout=self.hide)
        self._font = QFont("Segoe UI", 10)

    def show_state(self, state: str, message: str = "") -> None:
        """recording, transcribing, typed, typed_raw, cancelled, ignored, warning, error, or idle/hidden to hide."""
        self._hide_timer.stop()
        if state in ("idle", "hidden"):
            self.state = "hidden"
            self.hide()
            return
        self.state, self.message = state, message
        if state == "recording":
            self._levels.extend([0.0] * self.BARS)
        self.resize(self._width(), self.HEIGHT)
        self._place()
        if not self.isVisible():
            self.show()
            _no_activate(int(self.winId()))
        self._animation.start()
        if state in ("typed", "cancelled", "ignored"):
            self._hide_timer.start(900)
        elif state == "typed_raw":
            self._hide_timer.start(1800)
        elif state in ("warning", "error"):
            self._hide_timer.start(4000)
        self.update()

    def hideEvent(self, event):
        self._animation.stop()
        super().hideEvent(event)

    def _width(self) -> int:
        if self.state == "recording":
            return 40 + self.BARS * 5 + 16
        if self.state == "transcribing":
            return 84
        text = self._text()
        return 44 + QFontMetrics(self._font).horizontalAdvance(text) + 18

    def _text(self) -> str:
        return {"typed": "Typed", "typed_raw": "Typed as heard (cleanup unavailable)", "cancelled": "Cancelled",
                "ignored": "Too short"}.get(self.state, self.message)

    def _place(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.bottom() - self.HEIGHT - 28)

    def _animate(self) -> None:
        self._phase += 0.033
        if self.state == "recording":
            rms = self._level()
            db = 20 * math.log10(rms + 1e-6)  # speech is roughly -45..-15 dBFS
            self._levels.append(min(1.0, max(0.0, (db + 55) / 40)))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QColor(255, 255, 255, 40))
        p.setBrush(QColor(22, 22, 26, 235))
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        cy = rect.center().y()
        if self.state == "recording":
            pulse = 0.55 + 0.45 * math.sin(self._phase * 6)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(239, 68, 68, int(255 * pulse)))
            p.drawEllipse(QPointF(22, cy), 5, 5)
            p.setBrush(QColor(255, 255, 255, 230))
            for i, level in enumerate(self._levels):
                h = 3 + level * 24
                p.drawRoundedRect(QRectF(40 + i * 5, cy - h / 2, 3, h), 1.5, 1.5)
        elif self.state == "transcribing":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 230))
            for i in range(3):
                lift = max(0.0, math.sin(self._phase * 7 - i * 0.9)) * 5
                p.drawEllipse(QPointF(rect.center().x() - 14 + i * 14, cy - lift), 3.5, 3.5)
        else:
            colour = {"typed": QColor(34, 197, 94), "typed_raw": QColor(245, 158, 11), "warning": QColor(245, 158, 11),
                      "error": QColor(239, 68, 68)}.get(
                self.state, QColor(160, 160, 170))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colour)
            p.drawEllipse(QPointF(22, cy), 5, 5)
            p.setPen(QColor(240, 240, 245))
            p.setFont(self._font)
            p.drawText(QRectF(36, 0, rect.width() - 40, rect.height() + 2), Qt.AlignmentFlag.AlignVCenter, self._text())
        p.end()


def _no_activate(hwnd: int) -> None:
    # Belt and braces on top of Qt's flags: a window that got focus would receive the paste instead of the user's app.
    user32 = ctypes.windll.user32
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x20, 0x80
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = ctypes.c_void_p
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_running_mutex = None


def hold_running_mutex() -> None:
    # While it exists, the installer asks to close Rflow before updating or uninstalling it. The name is from the
    # first versions; kept so that new installers still recognise a running old version.
    global _running_mutex
    _running_mutex = _kernel32.CreateMutexW(None, False, "SST-Dictation-running")


def release_running_mutex() -> None:
    global _running_mutex
    if _running_mutex:
        _kernel32.CloseHandle(_running_mutex)
        _running_mutex = None


def show_running_window() -> bool:
    """Ask a running Rflow to show its window; False if none answers (e.g. the console `sst dictate` or 1.1 and older)."""
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(1500):
        return False
    # Windows only lets the process the user just started take the foreground. Pass that on to the running copy,
    # or its window would only flash in the taskbar.
    ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
    socket.write(b"show\n")
    socket.waitForBytesWritten(1500)
    socket.disconnectFromServer()
    return True


# ---------------------------------------------------------------- the app

class _Signals(QObject):
    state = Signal(str, str)  # from Dictation, possibly on its worker thread
    result = Signal(str, str, float)  # (heard, typed, seconds of audio), from Dictation's worker thread
    loaded = Signal(object)
    failed = Signal(str)
    update_found = Signal(object)  # the rest come from the update threads
    update_none = Signal()
    update_progress = Signal(str)
    update_ready = Signal(str)
    update_failed = Signal(str, bool)  # (message, tell the user)


class TrayApp:
    """Owns the settings and the dictation; the window (sst/window.py) shows them and asks this class for changes."""

    def __init__(self, quiet_start: bool = False):
        self.profiles = Profiles.load()
        self._load_profile()
        self.recorder = Recorder(self.settings.microphone or None)
        self.dictation: Dictation | None = None
        self.listener: HotkeyListener | None = None
        self.quiet_start = quiet_start
        self._cleanup_notice = -1e9  # when the user was last told that the cleanup couldn't help
        self.update: updates.Update | None = None
        self._update_told = ""  # the version the user was last notified about

        self.signals = _Signals()
        self.signals.state.connect(self._on_state)
        self.signals.result.connect(self._on_result)
        self.signals.loaded.connect(self._on_loaded)
        self.signals.failed.connect(self._on_failed)
        self.signals.update_found.connect(self._on_update_found)
        self.signals.update_none.connect(self._on_update_none)
        self.signals.update_progress.connect(lambda message: self.window.show_update(message, busy=True))
        self.signals.update_ready.connect(self._on_update_ready)
        self.signals.update_failed.connect(self._on_update_failed)

        self.icon = QIcon(str(ICON_FILE))
        self.recording_icon = _with_red_dot(self.icon)
        self.pill = Pill(level=lambda: self.recorder.level)
        self.window = MainWindow(self)

        self.tray = QSystemTrayIcon(self.icon)
        menu = QMenu()
        self.status_action = QAction("Loading the speech model...", menu)
        self.status_action.setEnabled(False)
        menu.addAction(self.status_action)
        self.update_action = QAction("", menu)
        self.update_action.triggered.connect(lambda: self.window.open("home"))
        self.update_action.setVisible(False)
        menu.addAction(self.update_action)
        menu.addSeparator()
        menu.addAction(f"Open {APP_NAME}", lambda: self.window.open("home"))
        menu.addAction("Dictionary", lambda: self.window.open("dictionary"))
        menu.addAction("Reading test", lambda: self.window.open("reading"))
        menu.addAction("AI cleanup", lambda: self.window.open("cleanup"))
        menu.addAction("Settings", lambda: self.window.open("settings"))
        menu.addAction("Check for updates", lambda: self.check_for_updates(manual=True))
        menu.addSeparator()
        menu.addAction(f"Quit {APP_NAME}", self.quit)
        self.menu = menu  # keep a reference; the tray only borrows it
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.window.open("home")
                                    if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.messageClicked.connect(lambda: self.window.open())  # e.g. "Rflow 1.2.0 is available"
        self.tray.setToolTip(f"{APP_NAME}: loading...")
        self.tray.show()
        self._set_status("Loading the speech model...")

        self.server = QLocalServer()
        self.server.newConnection.connect(self._on_connection)
        if not self.server.listen(SERVER_NAME):
            log.warning("No window server (%s); opening Rflow again won't show this window", self.server.errorString())

        self.pump = QTimer()
        self.pump.setInterval(15)
        self.pump.timeout.connect(self._pump)
        threading.Thread(target=self._load, name="model-loader", daemon=True).start()
        if getattr(sys, "frozen", False):  # the source checkout is updated with git, not by the app
            # The installer of an update that has finished (it started this version) isn't needed any more.
            QTimer.singleShot(60_000, lambda: shutil.rmtree(UPDATE_DIR, ignore_errors=True))
            QTimer.singleShot(20_000, self.check_for_updates)
            self.update_timer = QTimer()
            self.update_timer.setInterval(updates.CHECK_EVERY_HOURS * 3600 * 1000)
            self.update_timer.timeout.connect(self.check_for_updates)
            self.update_timer.start()
        if not quiet_start or not self.settings.welcomed:
            self.window.open()

    # -- start-up

    def _load(self) -> None:
        try:
            t0 = time.perf_counter()
            engine = load_engine("parakeet")
            log.info("Model loaded in %.1fs", time.perf_counter() - t0)
            self.signals.loaded.emit(engine)
        except Exception as e:
            log.exception("Could not load the speech model")
            self.signals.failed.emit(str(e))

    def _on_loaded(self, engine) -> None:
        self.dictation = Dictation(engine, self.recorder, sounds=self.settings.sounds, save=self.settings.save_recordings)
        self.dictation.on_state = self.signals.state.emit
        self.dictation.on_result = self.signals.result.emit
        self._apply_cleanup()
        self._start_listener()
        self.pump.start()
        if self.listener and wispr_flow_running() and self.listener.hotkey.modifiers == {"ctrl", "win"}:
            self._notify("Wispr Flow is running", "It also listens to Ctrl+Win, so both would type. Quit Wispr Flow.",
                         QSystemTrayIcon.MessageIcon.Warning)
        elif self.listener and not self.quiet_start and not self.window.isVisible():
            self._notify(f"{APP_NAME} is ready", f"Hold {self.listener.hotkey.label} in any app and speak.")

    def _on_failed(self, message: str) -> None:
        self._set_status("Could not load the speech model")
        QMessageBox.critical(None, APP_NAME, f"Could not load the speech model:\n\n{message}\n\n"
                             f"Details are in the log: {LOG_DIR}")

    def _start_listener(self) -> None:
        if self.listener:
            self.listener.stop()
        try:
            key = parse_hotkey(self.settings.hotkey)
        except ValueError as e:
            log.warning("Bad hotkey %r in the settings (%s); using %s", self.settings.hotkey, e, DEFAULT_HOTKEY)
            key = parse_hotkey(DEFAULT_HOTKEY)
        self.listener = HotkeyListener(key)
        self.listener.start()
        self.dictation.listener = self.listener
        self._update_status()
        log.info("Listening for %s", key.text)

    def _apply_cleanup(self) -> None:
        """Use the model chosen in AI cleanup from the next dictation on (or none)."""
        s = self.settings
        model = s.cleanup_model if s.cleanup else ""
        polisher = None
        if model and self.gateway.address:
            polisher = Polisher(self.gateway, model, s.vocabulary, fallback=s.cleanup_fallback or None)
            polisher.prepare()  # connect now, so the first dictation doesn't wait for it
        elif s.cleanup:
            self._notify(APP_NAME, "AI cleanup needs an endpoint and a model: set them in AI cleanup.",
                         QSystemTrayIcon.MessageIcon.Warning)
        self.dictation.cleanup = polisher
        self._update_status()
        log.info("Text cleanup: %s, backup %s (%s, %d words)", model or "off", s.cleanup_fallback or "none",
                 self.gateway, len(s.vocabulary))

    def _update_status(self) -> None:
        if not self.listener:
            return
        model = self.dictation.cleanup.model.rsplit("/", 1)[-1] if self.dictation and self.dictation.cleanup else None
        self._set_status(f"Ready: hold {self.listener.hotkey.label}" + (f" · cleanup: {model}" if model else ""), True)

    # -- running

    def _pump(self) -> None:
        events = self.listener.events
        while not events.empty():
            event, at = events.get_nowait()
            self.dictation.handle(event, at)
        self.dictation.tick(time.monotonic())

    def _on_result(self, heard: str, typed: str, seconds: float) -> None:
        add_to_history(typed, heard, path=self.profile.history_file)
        self.stats.add(typed, seconds, date.today())
        try:
            self.stats.save(self.profile.stats_file)
        except OSError as e:
            log.warning("Could not save the stats: %s", e)
        if self.window.isVisible():
            self.window.refresh()

    def _on_state(self, state: str, message: str) -> None:
        if state in ("warning", "error"):
            log.warning(message)
            self._notify(APP_NAME, message, QSystemTrayIcon.MessageIcon.Warning)
        if state == "typed_raw":
            log.warning("Typed as heard: %s", message)
            if time.monotonic() - self._cleanup_notice > 600:  # at most one notification per 10 minutes
                self._cleanup_notice = time.monotonic()
                self._notify("Text cleanup unavailable", f"Typed what was heard ({message}).",
                             QSystemTrayIcon.MessageIcon.Warning)
        recording = bool(self.dictation and self.dictation.recording)
        self.tray.setIcon(self.recording_icon if recording else self.icon)
        if recording and state not in ("recording",):
            return  # a previous dictation finished while a new one is being recorded: keep showing the recording
        self.pill.show_state(state, message)

    def _on_connection(self) -> None:
        while (socket := self.server.nextPendingConnection()) is not None:
            socket.disconnected.connect(socket.deleteLater)
            self.window.open()  # someone opened Rflow again (Start menu, desktop): show this one

    # -- what the window asks for

    def hotkey_label(self) -> str:
        if self.listener:
            return self.listener.hotkey.label
        try:
            return parse_hotkey(self.settings.hotkey).label
        except ValueError:
            return parse_hotkey(DEFAULT_HOTKEY).label

    def history_entries(self) -> list[dict]:
        return read_history(self.profile.history_file)

    def microphones(self) -> list[str]:
        return input_device_names()

    def new_recorder(self) -> Recorder:
        return Recorder(self.settings.microphone or None)

    def apply_settings(self, new: Settings) -> None:
        """Save the settings and use them at once (the Settings page and the welcome change them one by one)."""
        old, self.settings = self.settings, new
        new.save(self.profile.settings_file)
        self.recorder.device = new.microphone or None
        if self.dictation:
            self.dictation.sounds, self.dictation.save = new.sounds, new.save_recordings
            if (new.cleanup, new.cleanup_model, new.cleanup_fallback, new.vocabulary) != (
                    old.cleanup, old.cleanup_model, old.cleanup_fallback, old.vocabulary):
                self._apply_cleanup()  # the chosen model is active from the next dictation
            if new.hotkey != old.hotkey:
                self._start_listener()
        self.window.refresh()
        log.info("Settings saved: %s", new)

    def save_cleanup(self, on: bool, model: str, fallback: str, gateway: GatewayConfig) -> None:
        if gateway != self.gateway:
            gateway.save(self.profile.gateway_file)
            self.gateway = gateway
        self.apply_settings(dataclasses.replace(self.settings, cleanup=on, cleanup_model=model, cleanup_fallback=fallback))
        if self.dictation:
            self._apply_cleanup()  # also when only the endpoint or key changed

    def add_words(self, new_words: list[str]) -> int:
        known = {w.lower() for w in self.settings.vocabulary}
        added = [w for w in dict.fromkeys(new_words) if w.lower() not in known]
        if added:
            # The cleanup uses the new words from the next dictation (and the next scoring).
            self.apply_settings(dataclasses.replace(self.settings, vocabulary=self.settings.vocabulary + added))
        return len(added)

    def remove_word(self, word: str) -> None:
        self.apply_settings(dataclasses.replace(self.settings, vocabulary=[w for w in self.settings.vocabulary if w != word]))

    def score_reading(self, folder: Path, progress) -> bench.Results:
        """Parakeet alone, then with the cleanup model and the backup model, one at a time (the endpoint may be small)."""
        if not self.dictation:
            raise RuntimeError("The speech model is still loading; try again in a moment.")
        s = self.settings
        models = [m for m in dict.fromkeys((s.cleanup_model, s.cleanup_fallback)) if m] if self.gateway.address else []
        polishers = {f"Parakeet + {m.rsplit('/', 1)[-1]}": Polisher(self.gateway, m, s.vocabulary) for m in models}
        return bench.score(folder, self.dictation.engine, polishers, progress)

    def finish_welcome(self, name: str = "") -> None:
        if name.strip():
            self.rename_profile(self.profile.id, name)
        self.apply_settings(dataclasses.replace(self.settings, welcomed=True))

    def window_closed(self) -> None:
        if not self.settings.told_about_tray:
            self._notify(f"{APP_NAME} is still running", f"Dictate with {self.hotkey_label()} as usual. Click the tray "
                         f"icon to open {APP_NAME}; right-click it to quit.")
            self.apply_settings(dataclasses.replace(self.settings, told_about_tray=True))

    # -- profiles: each person's own settings, words, AI provider and keys, history, stats and reading tests

    def _load_profile(self) -> None:
        self.profile = self.profiles.current
        self.settings = Settings.load(self.profile.settings_file)
        self.gateway = GatewayConfig.load(self.profile.gateway_file)
        self.stats = Stats.load(self.profile.stats_file, history=self.profile.history_file)

    def bench_dir(self) -> Path:
        return self.profile.folder(bench.BENCH_DIR)

    def switch_profile(self, profile_id: str) -> None:
        if profile_id == self.profile.id or not self.profiles.get(profile_id):
            return
        self.profiles.active = profile_id
        self.profiles.save()
        self._activate_profile()

    def create_profile(self, name: str) -> None:
        profile = self.profiles.add(name)
        self.profiles.save()
        self.switch_profile(profile.id)  # a new profile starts with the welcome

    def rename_profile(self, profile_id: str, name: str) -> None:
        profile = self.profiles.get(profile_id)
        if profile and name.strip() and name.strip() != profile.name:
            profile.name = name.strip()
            self.profiles.save()
            self.window.refresh()

    def delete_profile(self, profile_id: str) -> None:
        """Delete another profile and its files (settings, keys, history, stats, reading tests)."""
        profile = self.profiles.get(profile_id)
        if not profile or profile.id == self.profile.id:
            return  # the one in use can't go; switch first (and the first profile can't go at all)
        self.profiles.remove(profile_id)
        self.profiles.save()
        shutil.rmtree(profile.folder(), ignore_errors=True)
        shutil.rmtree(profile.folder(bench.BENCH_DIR), ignore_errors=True)
        self.window.refresh()
        log.info("Deleted profile %s", profile.id)

    def _activate_profile(self) -> None:
        if self.dictation and self.dictation.recording:
            self.dictation.close()  # a recording started for the other profile is dropped
        old_hotkey = self.settings.hotkey
        self._load_profile()
        self.recorder.device = self.settings.microphone or None
        if self.dictation:
            self.dictation.sounds, self.dictation.save = self.settings.sounds, self.settings.save_recordings
            self._apply_cleanup()
            if self.settings.hotkey != old_hotkey:
                self._start_listener()
        # Every page shows the profile's own data: build the window again rather than update each field.
        old, self.window = self.window, MainWindow(self)
        self.window.set_status(*self._status)
        if self.update:
            self.window.show_update(f"Rflow {self.update.version} is available (you have {__version__}).",
                                    version=self.update.version)
        if old.isVisible():
            self.window.setGeometry(old.geometry())
            self.window.open()
        old.hide()
        old.deleteLater()
        log.info("Profile: %s (%s)", self.profile.label, self.profile.id)

    # -- updates

    def check_for_updates(self, manual: bool = False) -> None:
        if manual:
            self.window.set_update_status("Checking for updates...")

        def work() -> None:
            try:
                update = updates.check()
            except Exception as e:  # offline, rate-limited...: try again at the next check
                self.signals.update_failed.emit(f"Could not check for updates: {e}", manual)
                return
            if update:
                self.signals.update_found.emit(update)
            elif manual:
                self.signals.update_none.emit()
        threading.Thread(target=work, name="update-check", daemon=True).start()

    def _on_update_none(self) -> None:
        self.window.set_update_status(f"You have the latest version ({__version__}).")
        if not self.window.isVisible():
            self._notify(APP_NAME, f"You have the latest version ({__version__}).")

    def _on_update_found(self, update) -> None:
        self.update = update
        message = f"Rflow {update.version} is available (you have {__version__})."
        self.window.show_update(message, version=update.version)
        self.window.set_update_status(message)
        self.update_action.setText(f"Update to Rflow {update.version}...")
        self.update_action.setVisible(True)
        log.info("Update available: %s", update.version)
        if self._update_told != update.version:
            self._update_told = update.version
            self._notify(f"Rflow {update.version} is available", "Click here, then Update now.")

    def start_update(self) -> None:
        update = self.update
        if not update:
            return
        if not getattr(sys, "frozen", False):
            QDesktopServices.openUrl(QUrl(update.page))  # a source checkout: just show the release
            return
        self.window.show_update(f"Downloading Rflow {update.version}...", busy=True)
        last = [-1]

        def progress(done: int, total: int) -> None:
            percent = int(done * 100 / total) if total else 0
            if percent != last[0]:
                last[0] = percent
                self.signals.update_progress.emit(f"Downloading Rflow {update.version}... {percent}%")

        def work() -> None:
            try:
                path = updates.download(update, UPDATE_DIR, progress)
            except Exception as e:
                log.exception("Update download failed")
                self.signals.update_failed.emit(f"Update failed: {e}", True)
                return
            self.signals.update_ready.emit(str(path))
        threading.Thread(target=work, name="update-download", daemon=True).start()

    def _on_update_ready(self, path: str) -> None:
        log.info("Installing %s; Rflow restarts when it's done", path)
        self.window.show_update("Installing... Rflow restarts by itself when it's done.", busy=True)
        release_running_mutex()  # otherwise the installer stops to ask for Rflow to be closed
        self.server.close()  # the new version opens its own
        try:
            updates.install(Path(path))
        except OSError as e:
            hold_running_mutex()
            self.server.listen(SERVER_NAME)
            self._on_update_failed(f"Could not start the installer: {e}", True)
            return
        self.quit()

    def _on_update_failed(self, message: str, tell: bool) -> None:
        log.warning(message)
        if tell:
            self._notify(APP_NAME, message, QSystemTrayIcon.MessageIcon.Warning)
        self.window.set_update_status(message)
        if self.update:
            self.window.show_update(f"{message} You can try again.")

    def open_release_notes(self) -> None:
        if self.update and self.update.page:
            QDesktopServices.openUrl(QUrl(self.update.page))

    def quit(self) -> None:
        if self.listener:
            self.listener.stop()
        if self.dictation:
            self.dictation.close()
        self.server.close()
        self.tray.hide()
        QApplication.quit()

    def _set_status(self, message: str, ready: bool = False) -> None:
        self._status = (message, ready)  # a window built later (another profile) shows it too
        self.status_action.setText(message)
        self.tray.setToolTip(f"{APP_NAME}: {message}")
        self.window.set_status(message, ready)

    def _notify(self, title: str, message: str, icon=QSystemTrayIcon.MessageIcon.Information) -> None:
        self.tray.showMessage(title, message, icon, 5000)


def _with_red_dot(icon: QIcon) -> QIcon:
    pixmap = icon.pixmap(64, 64)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor(255, 255, 255))
    p.setBrush(QColor(239, 68, 68))
    p.drawEllipse(QPointF(48, 48), 13, 13)
    p.end()
    return QIcon(pixmap)


# ---------------------------------------------------------------- entry points

def self_test() -> int:
    """Build every window off-screen and transcribe once; exit code 0 if it all works (used by the installer build)."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication(sys.argv)
    pill = Pill(level=lambda: 0.05)
    for state in ("recording", "transcribing", "typed", "error"):
        pill.show_state(state, "test")
        pill.grab()
    preview = PreviewApp(history=[{"time": "2026-09-30 10:15:00", "text": "Self-test."}])
    window = MainWindow(preview)
    window.show_update("Rflow 9.9.9 is available (you have 1.0.0).", version="9.9.9")
    for page in ("home", "dictionary", "reading", "cleanup", "settings", "welcome"):
        window.show_page(page)
        window.grab()
    window.pages["reading"].ensure_test().grab()
    _with_red_dot(QIcon(str(ICON_FILE)))
    from sst.engines.parakeet import MODEL_DIR
    wav = MODEL_DIR / "test_wavs" / "0.wav"
    engine = load_engine("parakeet")
    if wav.exists():
        from sst.audio import load_wav
        if not engine.transcribe(*load_wav(wav)):
            return 1
    app.quit()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--self-test" in argv:
        return self_test()
    log_dir = setup_logging()
    log.info("%s %s starting", APP_NAME, __version__)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)  # closing the window keeps Rflow in the tray
    app.setWindowIcon(QIcon(str(ICON_FILE)))
    if already_running():
        if "--startup" not in argv and not show_running_window():
            QMessageBox.information(None, APP_NAME, f"{APP_NAME} is already running. Look for its icon in the tray.")
        return 0
    hold_running_mutex()
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, APP_NAME, "Windows has no notification area (system tray) available.")
        return 1
    tray_app = TrayApp(quiet_start="--startup" in argv)  # noqa: F841 (kept alive for the app's lifetime)
    try:
        return app.exec()
    except Exception:
        log.exception("Crashed; logs in %s", log_dir)
        raise
