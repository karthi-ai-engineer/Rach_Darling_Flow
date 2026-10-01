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

from sst import __version__, bench, downloads, evaluate, scan, updates
from sst.audio import TAIL_SECONDS, Recorder, input_device_names
from sst.dictate import DEFAULT_HOTKEY, Dictation, already_running, wispr_flow_running
from sst.engines import DEFAULT_MODEL, SPEECH_MODELS, load_engine, usable
from sst.engines.cloud import CLOUD
from sst.gateway import GatewayConfig, Polisher
from sst.hotkey import HotkeyListener, parse_hotkey
from sst.settings import Profiles, Settings, Stats, add_to_history, read_history
from sst.window import APP_NAME, ICON_FILE, LOG_DIR, MainWindow, PreviewApp

UPDATE_DIR = Path(os.environ.get("TEMP", Path.home())) / "Rflow-update"  # downloaded installers
# Opening Rflow while it runs asks the running copy, through this local pipe, to show its window.
SERVER_NAME = f"Rflow-window-{os.environ.get('USERNAME', 'user')}"
ASFW_ANY = -1  # AllowSetForegroundWindow: any process
WARM_SECONDS = 300  # the microphone stays open this long after a dictation (the owner chose 5 minutes)

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
        """recording, transcribing, typed, typed_raw, typed_local, cancelled, ignored, warning, error, or idle/hidden to
        hide."""
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
        elif state in ("typed_raw", "typed_local"):
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
        return {"typed": "Typed", "typed_raw": "Typed as heard (cleanup unavailable)",
                "typed_local": "Typed with Parakeet (cloud unavailable)", "cancelled": "Cancelled",
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
            colour = {"typed": QColor(34, 197, 94), "typed_raw": QColor(245, 158, 11),
                      "typed_local": QColor(245, 158, 11), "warning": QColor(245, 158, 11),
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
    failed = Signal(str, str)  # (speech model, why it couldn't load)
    update_found = Signal(object)  # the rest come from the update threads
    update_none = Signal()
    update_progress = Signal(str)
    update_ready = Signal(str)
    update_failed = Signal(str, bool)  # (message, tell the user)
    download_progress = Signal(str, int, int)  # (speech model, bytes done, bytes in all)
    download_done = Signal(str, str)  # (speech model, "" or "cancelled" or why it failed)
    scan_progress = Signal(str)
    scan_done = Signal(object)  # the scan's result (sst.scan.save), or {"error": why}


class TrayApp:
    """Owns the settings and the dictation; the window (sst/window.py) shows them and asks this class for changes."""

    def __init__(self, quiet_start: bool = False):
        self.profiles = Profiles.load()
        self._load_profile()
        self.recorder = self.new_recorder()
        self.dictation: Dictation | None = None
        self.loading_speech = ""  # the speech model being loaded in the background, if any
        self.downloading: tuple[str, int, int] | None = None  # (speech model, bytes done, bytes in all)
        self.scanning = ""  # what the scan is doing now, while it runs
        self.last_scan = scan.load()
        self._cancel_download = threading.Event()
        self.listener: HotkeyListener | None = None
        self.quiet_start = quiet_start
        self._cleanup_notice = -1e9  # when the user was last told that the cleanup couldn't help
        self._speech_notice = -1e9  # when the user was last told that a cloud speech model couldn't help
        self._local = None  # Parakeet, once loaded: a cloud speech model falls back on it
        self._local_lock = threading.Lock()
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
        self.signals.download_progress.connect(self._on_download_progress)
        self.signals.download_done.connect(self._on_download_done)
        self.signals.scan_progress.connect(self._on_scan_progress)
        self.signals.scan_done.connect(self._on_scan_done)

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
        menu.addAction("Speech recognition", lambda: self.window.open("speech"))
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
        self._load_speech()
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

    def _load_speech(self) -> None:
        """Load the profile's speech model on a thread. A dictation already running keeps its model until the new one
        is ready, so switching never leaves the user without dictation."""
        key = usable(self.settings.speech_model, self.gateway)
        if key == self.loading_speech or (self.dictation and self.dictation.engine.name == key and not self.loading_speech):
            return
        self.loading_speech = key
        if self.dictation:
            self._set_status(f"Loading {SPEECH_MODELS[key].name}...", True)
        self.window.refresh()

        def work() -> None:
            try:
                t0 = time.perf_counter()
                local = self._local
                engine = local if key == DEFAULT_MODEL and local else self._new_engine(key)
                log.info("Speech model %s loaded in %.1fs", key, time.perf_counter() - t0)
                self.signals.loaded.emit(engine)
            except Exception as e:
                log.exception("Could not load the speech model %s", key)
                self.signals.failed.emit(key, str(e))
        threading.Thread(target=work, name="model-loader", daemon=True).start()

    def _new_engine(self, key: str, fallback: bool = True):
        """A speech model as the settings say. A cloud model gets its key and model, and Parakeet to fall back on."""
        s = self.settings
        if key not in CLOUD:
            return load_engine(key, s.speech_language)
        return load_engine(key, s.speech_language, self.gateway.key_for(key), s.speech_cloud_models.get(key, ""),
                           self._fallback_engine if fallback else None)

    def _fallback_engine(self):
        """Parakeet, for a cloud model that couldn't help: the one loaded already, or loaded now (once). Using only
        a cloud model keeps Rflow light until then."""
        with self._local_lock:
            if self._local is None:
                self._local = load_engine(DEFAULT_MODEL)
            return self._local

    def _on_loaded(self, engine) -> None:
        if self.loading_speech == engine.name:
            self.loading_speech = ""
        first = self.dictation is None
        if not first and engine.name != usable(self.settings.speech_model, self.gateway):
            self._load_speech()  # another model was chosen while this one loaded: drop it, load (or await) that one
            return
        if engine.name == DEFAULT_MODEL:
            self._local = engine  # a cloud model chosen later falls back on it without loading it again
        elif engine.name not in CLOUD:
            self._local = None  # another model runs on this computer: Parakeet's memory can go
        if first:
            self.dictation = Dictation(engine, self.recorder, sounds=self.settings.sounds,
                                       save=self.settings.save_recordings)
            self.dictation.on_state = self.signals.state.emit
            self.dictation.on_result = self.signals.result.emit
        else:
            self.dictation.engine = engine  # the next dictation uses it; one being transcribed finishes with the old
        self._apply_cleanup()  # also gives the new model Your words
        if first:
            self._start_listener()
            self.pump.start()
        self.window.refresh()
        if not first:
            return
        if engine.name != usable(self.settings.speech_model, self.gateway):
            self._load_speech()  # dictation works now; the chosen model follows
            return
        if self.listener and wispr_flow_running() and self.listener.hotkey.modifiers == {"ctrl", "win"}:
            self._notify("Wispr Flow is running", "It also listens to Ctrl+Win, so both would type. Quit Wispr Flow.",
                         QSystemTrayIcon.MessageIcon.Warning)
        elif self.listener and not self.quiet_start and not self.window.isVisible():
            self._notify(f"{APP_NAME} is ready", f"Hold {self.listener.hotkey.label} in any app and speak.")

    def _on_failed(self, key: str, message: str) -> None:
        if self.loading_speech == key:
            self.loading_speech = ""
        if self.dictation:  # a switch failed: dictation goes on with the model it had
            self._update_status()
            self.window.refresh()
            self._notify(APP_NAME, f"Could not load {SPEECH_MODELS[key].name}; still using "
                         f"{SPEECH_MODELS[self.dictation.engine.name].name}. {message}",
                         QSystemTrayIcon.MessageIcon.Warning)
            return
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

    def _apply_speech(self) -> None:
        """The speech model's options that need no reload, from the next dictation on: the language, Your words, and a
        cloud model's model and key."""
        s, engine = self.settings, self.dictation.engine
        if hasattr(engine, "language"):
            engine.language = s.speech_language
        if hasattr(engine, "words"):
            engine.words = list(s.vocabulary)  # the recogniser listens for them (hotwords)
        if engine.name in CLOUD:
            engine.model = s.speech_cloud_models.get(engine.name) or CLOUD[engine.name].models[0]
            engine.api_key = self.gateway.key_for(engine.name) or engine.api_key  # no key: Parakeet is on its way

    def _apply_cleanup(self) -> None:
        """Use Your words and the model chosen in AI cleanup from the next dictation on."""
        s = self.settings
        self._apply_speech()
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
        if not self.listener or self.loading_speech:
            return
        model = self.dictation.cleanup.model.rsplit("/", 1)[-1] if self.dictation and self.dictation.cleanup else None
        engine = self.dictation.engine if self.dictation else None
        speech = f" · speech: {engine.title}" if engine and engine.name != DEFAULT_MODEL else ""
        self._set_status(f"Ready: hold {self.listener.hotkey.label}" + speech + (f" · cleanup: {model}" if model else ""),
                         True)

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
        if state == "typed_local":
            log.warning("Typed with Parakeet: %s", message)
            if time.monotonic() - self._speech_notice > 600:  # at most one notification per 10 minutes
                self._speech_notice = time.monotonic()
                self._notify("Cloud speech unavailable", f"Parakeet typed it on this computer ({message}).",
                             QSystemTrayIcon.MessageIcon.Warning)
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
        """A recorder set up as the settings say (dictation's, and the reading test's, so a test hears what dictation
        hears)."""
        recorder = Recorder()
        self._configure(recorder)
        return recorder

    def _configure(self, recorder: Recorder) -> None:
        s = self.settings
        recorder.device, recorder.raw, recorder.tail = s.microphone or None, s.raw_audio, TAIL_SECONDS
        recorder.warm_seconds = WARM_SECONDS if s.warm_mic else 0.0

    def apply_settings(self, new: Settings) -> None:
        """Save the settings and use them at once (the Settings page and the welcome change them one by one)."""
        old, self.settings = self.settings, new
        new.save(self.profile.settings_file)
        self._configure(self.recorder)
        if self.dictation:
            self.dictation.sounds, self.dictation.save = new.sounds, new.save_recordings
            if (new.cleanup, new.cleanup_model, new.cleanup_fallback, new.vocabulary) != (
                    old.cleanup, old.cleanup_model, old.cleanup_fallback, old.vocabulary):
                self._apply_cleanup()  # the chosen model is active from the next dictation
            elif (new.speech_language, new.speech_cloud_models) != (old.speech_language, old.speech_cloud_models):
                self._apply_speech()  # from the next dictation; no reload needed
            if new.hotkey != old.hotkey:
                self._start_listener()
        if new.speech_model != old.speech_model:
            self._load_speech()
        self.window.refresh()
        log.info("Settings saved: %s", new)

    def save_cleanup(self, on: bool, model: str, fallback: str, gateway: GatewayConfig) -> None:
        if gateway != self.gateway:
            gateway.save(self.profile.gateway_file)
            self.gateway = gateway
        self.apply_settings(dataclasses.replace(self.settings, cleanup=on, cleanup_model=model, cleanup_fallback=fallback))
        if self.dictation:
            self._apply_cleanup()  # also when only the endpoint or key changed (a cloud speech model's key too)
        self._load_speech()  # a key added or removed can change which speech model is usable

    def add_words(self, new_words: list[str]) -> int:
        known = {w.lower() for w in self.settings.vocabulary}
        added = [w for w in dict.fromkeys(new_words) if w.lower() not in known]
        if added:
            # The cleanup uses the new words from the next dictation (and the next scoring).
            self.apply_settings(dataclasses.replace(self.settings, vocabulary=self.settings.vocabulary + added))
        return len(added)

    def remove_word(self, word: str) -> None:
        self.apply_settings(dataclasses.replace(self.settings, vocabulary=[w for w in self.settings.vocabulary if w != word]))

    def score_reading(self, folders: list[Path], progress) -> evaluate.Results:
        """The speech model alone, then with the cleanup model and the backup model, one at a time (the endpoint may be
        small).
        The report goes into the test's folder, or into 'summary' when several tests are scored together."""
        if not self.dictation:
            raise RuntimeError("The speech model is still loading; try again in a moment.")
        s = self.settings
        models = [m for m in dict.fromkeys((s.cleanup_model, s.cleanup_fallback)) if m] if self.gateway.address else []
        polishers = {m.rsplit("/", 1)[-1]: Polisher(self.gateway, m, s.vocabulary) for m in models}
        engine = self.dictation.engine
        if engine.name in CLOUD:  # scored without Parakeet to fall back on, so that a provider's failure shows
            engine = self._new_engine(engine.name, fallback=False)
            engine.words = list(s.vocabulary)
        results = evaluate.run(folders, engine, evaluate.pipelines_for(polishers, title=engine.title), progress)
        results.save(evaluate.output_folder(folders))
        return results

    def choose_speech_model(self, key: str) -> None:
        """The Speech recognition page: use this model from now on (it loads in the background)."""
        if key in SPEECH_MODELS and usable(key, self.gateway) == key and key != self.settings.speech_model:
            self.apply_settings(dataclasses.replace(self.settings, speech_model=key))

    def use_cloud_speech(self, provider: str, api_key: str, model: str) -> None:
        """A cloud card's "Use this model", or "Save" while it is in use: keep its key (shared with AI cleanup) and its
        model, and use it from the next dictation. The window has asked first: the voice goes to the provider."""
        if provider not in CLOUD or not api_key:
            return
        if api_key != self.gateway.key_for(provider):
            self.gateway = self.gateway.with_key(provider, api_key)
            self.gateway.save(self.profile.gateway_file)
            if self.dictation:
                self._apply_cleanup()  # AI cleanup may use the same provider, and so the same key
        models = {**self.settings.speech_cloud_models, provider: model or CLOUD[provider].models[0]}
        self.apply_settings(dataclasses.replace(self.settings, speech_model=provider, speech_cloud_models=models))

    def test_cloud_speech(self, provider: str, api_key: str, model: str) -> str:
        """A cloud card's Test button (the window runs it on a thread): the sample sentence through the provider.
        Raises with a readable reason."""
        sample = _sample_sentence()
        if sample is None:
            raise RuntimeError("the sample sentence is missing (it comes with Parakeet)")
        engine = load_engine(provider, "", api_key, model)  # the sample is English, whatever language is chosen
        engine.words = list(self.settings.vocabulary)
        return engine.check(*sample)

    def set_speech_language(self, code: str) -> None:
        if code != self.settings.speech_language:
            self.apply_settings(dataclasses.replace(self.settings, speech_language=code))

    def download_speech_model(self, key: str) -> None:
        """Download a speech model in the background (one at a time), then use it."""
        model = SPEECH_MODELS.get(key)
        if not model or not model.download or self.downloading:
            return
        self.downloading = (key, 0, model.download.size)
        self._cancel_download.clear()
        self.window.refresh()
        last = [-1]

        def progress(done: int, total: int) -> None:
            percent = done * 100 // total if total else 0
            if percent != last[0]:  # the window needn't redraw for every megabyte
                last[0] = percent
                self.signals.download_progress.emit(key, done, total)

        def work() -> None:
            try:
                downloads.download(model.download, progress, self._cancel_download.is_set)
                self.signals.download_done.emit(key, "")
            except downloads.Cancelled:
                self.signals.download_done.emit(key, "cancelled")
            except Exception as e:
                log.exception("Downloading %s failed", key)
                self.signals.download_done.emit(key, str(e) or type(e).__name__)
        threading.Thread(target=work, name="model-download", daemon=True).start()

    def cancel_download(self) -> None:
        self._cancel_download.set()  # what is downloaded so far stays, and the next try goes on from there

    def remove_speech_model(self, key: str) -> None:
        """Free the disk space of a downloaded model that isn't in use."""
        model = SPEECH_MODELS.get(key)
        if model and model.download and key not in (self.settings.speech_model, self.speech_in_use()) and \
                not (self.downloading and self.downloading[0] == key):
            downloads.remove(model.download)
            log.info("Removed the download of %s", key)
            self.window.refresh()

    def scan_computer(self) -> None:
        """Scan my computer, in the background: the hardware, a benchmark, and each downloaded model timed on the
        speech model's sample sentence (the model in use is timed as it is; others are loaded for it if memory allows)."""
        if self.scanning:
            return
        self.scanning = "Reading this computer..."
        self.window.refresh()
        in_use = self.dictation.engine if self.dictation else None
        local = [m for m in SPEECH_MODELS.values() if m.where == "local" and m.ready]

        def work() -> None:
            try:
                pc = scan.computer()
                measured = {}
                sample = _sample_sentence()
                for model in local if sample is not None else []:
                    if not model.installed():
                        continue
                    if in_use is not None and in_use.name == model.key:
                        engine = in_use
                    elif pc.free_memory_gb >= scan.MEMORY_GB.get(model.key, 1.0) + 1:
                        self.signals.scan_progress.emit(f"Loading {model.name} to try it...")
                        engine = load_engine(model.key)
                    else:
                        continue  # not enough free memory to load it next to the one in use: estimated instead
                    self.signals.scan_progress.emit(f"Trying {model.name} on a short sentence...")
                    t0 = time.perf_counter()
                    engine.transcribe(*sample)
                    measured[model.key] = time.perf_counter() - t0
                    del engine
                self.signals.scan_done.emit(scan.save(pc, scan.judge(pc, local, measured)))
            except Exception as e:
                log.exception("Scanning the computer failed")
                self.signals.scan_done.emit({"error": str(e) or type(e).__name__})
        threading.Thread(target=work, name="scan", daemon=True).start()

    def _on_scan_progress(self, message: str) -> None:
        self.scanning = message
        self.window.refresh()

    def _on_scan_done(self, data: dict) -> None:
        self.scanning = ""
        if "error" in data:
            self._notify(APP_NAME, f"The scan didn't finish: {data['error']}", QSystemTrayIcon.MessageIcon.Warning)
        else:
            self.last_scan = data
            log.info("Scan: %s", data["computer"])
        self.window.refresh()

    def _on_download_progress(self, key: str, done: int, total: int) -> None:
        self.downloading = (key, done, total)
        self.window.refresh()

    def _on_download_done(self, key: str, error: str) -> None:
        self.downloading = None
        name = SPEECH_MODELS[key].name
        if not error:
            log.info("%s downloaded", name)
            self.choose_speech_model(key)  # what the user asked for: download it and use it
        elif error != "cancelled":
            self._notify(APP_NAME, f"Could not download {name}: {error}", QSystemTrayIcon.MessageIcon.Warning)
        self.window.refresh()

    def speech_in_use(self) -> str:
        """The speech model dictation uses right now ("" while the first one loads)."""
        return self.dictation.engine.name if self.dictation else ""

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
        self._configure(self.recorder)
        if self.dictation:
            self.dictation.sounds, self.dictation.save = self.settings.sounds, self.settings.save_recordings
            self._apply_cleanup()
            if self.settings.hotkey != old_hotkey:
                self._start_listener()
            self._load_speech()  # the other profile may use another speech model
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


def _sample_sentence():
    """The sentence the scan times every model on: the Parakeet model's own test recording (7.4 s of speech)."""
    from sst.audio import load_wav
    from sst.engines.parakeet import MODEL_DIR
    try:
        return load_wav(MODEL_DIR / "test_wavs" / "0.wav")
    except OSError:
        return None


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
    import ctranslate2  # noqa: F401 (Whisper's runtime: bundled and its DLLs load, without needing the downloaded model)
    import faster_whisper  # noqa: F401

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
