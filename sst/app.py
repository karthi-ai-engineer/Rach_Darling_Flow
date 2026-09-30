"""The tray app: SST Dictation without a console window.

A tray icon with a menu (status, History, Settings, Quit), a small "pill" near the bottom of the
screen while you dictate (recording with a live level, transcribing, typed), a settings window and
a history window. The dictation itself is sst.dictate.Dictation, the same as the console command.

  uv run sst app                        from the source checkout
  SST Dictation.exe                     the installed app (the Start menu shortcut)
  SST Dictation.exe --self-test         build check: builds every window off-screen and transcribes once
"""
import ctypes
import logging
import math
import os
import re
import sys
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QDesktopServices, QFont, QFontMetrics, QGuiApplication, QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from sst import RECORDINGS_DIR, __version__
from sst.audio import Recorder, input_device_names
from sst.dictate import DEFAULT_HOTKEY, Dictation, already_running, wispr_flow_running
from sst.engines import load_engine
from sst.gateway import MODELS, GatewayConfig, GatewayError, Polisher, other_model
from sst.hotkey import HotkeyListener, parse_hotkey
from sst.settings import (
    Settings,
    add_to_history,
    can_start_with_windows,
    read_history,
    set_start_with_windows,
    starts_with_windows,
)

APP_NAME = "SST Dictation"
ICON_FILE = Path(__file__).parent / "static" / "sst.ico"
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "sst" / "logs"
HOTKEY_CHOICES = [("Ctrl+Win (like Wispr Flow)", "ctrl+win"), ("Menu key", "menu"), ("Ctrl+Alt+D", "ctrl+alt+d")]

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


# ---------------------------------------------------------------- settings and history windows

class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, microphones: list[str], gateway: GatewayConfig | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} settings")
        self.setWindowIcon(QIcon(str(ICON_FILE)))
        form = QFormLayout()

        self.hotkey = QComboBox()
        for label, value in HOTKEY_CHOICES:
            self.hotkey.addItem(label, value)
        if self.hotkey.findData(settings.hotkey) < 0:
            self.hotkey.addItem(settings.hotkey, settings.hotkey)  # a custom one set with --hotkey or by hand
        self.hotkey.setCurrentIndex(self.hotkey.findData(settings.hotkey))
        form.addRow("Dictation key", self.hotkey)
        hint = QLabel("Hold to talk, tap for hands-free. With Ctrl+Win, Ctrl+Win+Space is hands-free as well.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")
        form.addRow("", hint)

        self.microphone = QComboBox()
        self.microphone.addItem("Windows default", "")
        for name in microphones:
            self.microphone.addItem(name, name)
        if settings.microphone and self.microphone.findData(settings.microphone) < 0:
            self.microphone.addItem(f"{settings.microphone} (not connected)", settings.microphone)
        self.microphone.setCurrentIndex(max(0, self.microphone.findData(settings.microphone)))
        form.addRow("Microphone", self.microphone)

        self.sounds = QCheckBox("Beep when recording starts and stops")
        self.sounds.setChecked(settings.sounds)
        form.addRow("", self.sounds)
        self.save_recordings = QCheckBox("Keep recordings (audio + text) on this laptop")
        self.save_recordings.setChecked(settings.save_recordings)
        open_folder = QPushButton("Open folder")
        open_folder.clicked.connect(lambda: _open_folder(RECORDINGS_DIR))
        row = QHBoxLayout()
        row.addWidget(self.save_recordings)
        row.addStretch()
        row.addWidget(open_folder)
        form.addRow("", row)
        self.start_with_windows = QCheckBox("Start when I sign in to Windows")
        self.start_with_windows.setChecked(starts_with_windows())
        self.start_with_windows.setEnabled(can_start_with_windows())
        if not can_start_with_windows():
            self.start_with_windows.setToolTip("Available in the installed app")
        form.addRow("", self.start_with_windows)

        gateway = gateway or GatewayConfig()
        cleanup = QGroupBox("Text cleanup (company AI gateway)")
        cleanup_form = QFormLayout(cleanup)
        self.cleanup = QComboBox()
        self.cleanup.addItem("Off: type exactly what was heard", "")
        for model, label in MODELS:
            self.cleanup.addItem(label, model)
        if settings.cleanup_model and self.cleanup.findData(settings.cleanup_model) < 0:
            self.cleanup.addItem(settings.cleanup_model, settings.cleanup_model)
        self.cleanup.setCurrentIndex(max(0, self.cleanup.findData(settings.cleanup_model)))
        cleanup_form.addRow("Model", self.cleanup)
        how = QLabel("Speech is still recognised on this laptop; only the finished text goes to the gateway. If the "
                     "model fails the other one is tried, and if the gateway can't help in time the text is typed as heard.")
        how.setWordWrap(True)
        how.setStyleSheet("color: gray")
        cleanup_form.addRow("", how)
        self.gateway_url = QLineEdit(gateway.base_url)
        cleanup_form.addRow("Gateway", self.gateway_url)
        self.api_key = QLineEdit(gateway.api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Paste your gateway API key (kept on this laptop only)")
        test = QPushButton("Test")
        test.clicked.connect(self._test)
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key)
        key_row.addWidget(test)
        cleanup_form.addRow("API key", key_row)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        cleanup_form.addRow("", self.test_result)
        self.vocabulary = QPlainTextEdit("\n".join(settings.vocabulary))
        self.vocabulary.setPlaceholderText("Your names and terms, one per line (or separated by commas), e.g.\n"
                                           "Claude Code\nGitHub\nTamil")
        self.vocabulary.setFixedHeight(96)
        cleanup_form.addRow("Your words", self.vocabulary)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(cleanup)
        layout.addWidget(buttons)
        self.setMinimumWidth(540)

    def result_settings(self) -> Settings:
        return Settings(hotkey=self.hotkey.currentData(), microphone=self.microphone.currentData(),
                        sounds=self.sounds.isChecked(), save_recordings=self.save_recordings.isChecked(),
                        cleanup_model=self.cleanup.currentData(), vocabulary=self._words())

    def result_gateway(self) -> GatewayConfig:
        return GatewayConfig(base_url=self.gateway_url.text().strip() or GatewayConfig().base_url,
                             api_key=self.api_key.text().strip())

    def _words(self) -> list[str]:
        return [w.strip() for w in re.split(r"[\n,]", self.vocabulary.toPlainText()) if w.strip()]

    def _test(self) -> None:
        polisher = Polisher(self.result_gateway(), self.cleanup.currentData() or MODELS[0][0], self._words())
        self.test_result.setText("Testing...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            self.test_result.setText("OK: " + polisher.check())
        except GatewayError as e:
            self.test_result.setText(f"Failed: {e}")
        finally:
            QApplication.restoreOverrideCursor()


class HistoryWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle(f"{APP_NAME} history")
        self.setWindowIcon(QIcon(str(ICON_FILE)))
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.itemDoubleClicked.connect(lambda item: self._copy(item))
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: self._copy(self.list.currentItem()))
        self.status = QLabel("Double-click a line to copy it.")
        self.status.setStyleSheet("color: gray")
        row = QHBoxLayout()
        row.addWidget(self.status)
        row.addStretch()
        row.addWidget(copy)
        layout = QVBoxLayout(self)
        layout.addWidget(self.list)
        layout.addLayout(row)
        self.resize(560, 420)

    def refresh(self) -> None:
        self.list.clear()
        for entry in read_history():
            item = QListWidgetItem(f"{entry.get('time', '')[5:16]}   {entry['text']}")
            item.setData(Qt.ItemDataRole.UserRole, entry["text"])
            item.setToolTip(entry["text"] + (f"\n\nHeard: {entry['heard']}" if entry.get("heard") else ""))
            self.list.addItem(item)
        if not self.list.count():
            self.list.addItem("Nothing dictated yet. Hold Ctrl+Win in any app and speak.")

    def _copy(self, item) -> None:
        text = item.data(Qt.ItemDataRole.UserRole) if item else None
        if text:
            QGuiApplication.clipboard().setText(text)
            self.status.setText("Copied.")


def _open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


# ---------------------------------------------------------------- the app

class _Signals(QObject):
    state = Signal(str, str)  # from Dictation, possibly on its worker thread
    result = Signal(str, str)  # (heard, typed), from Dictation's worker thread
    loaded = Signal(object)
    failed = Signal(str)


class TrayApp:
    def __init__(self, quiet_start: bool = False):
        self.settings = Settings.load()
        self.gateway = GatewayConfig.load()
        self.recorder = Recorder(self.settings.microphone or None)
        self.dictation: Dictation | None = None
        self.listener: HotkeyListener | None = None
        self.quiet_start = quiet_start
        self._cleanup_notice = -1e9  # when the user was last told that the cleanup couldn't help

        self.signals = _Signals()
        self.signals.state.connect(self._on_state)
        self.signals.result.connect(self._on_result)
        self.signals.loaded.connect(self._on_loaded)
        self.signals.failed.connect(self._on_failed)

        self.icon = QIcon(str(ICON_FILE))
        self.recording_icon = _with_red_dot(self.icon)
        self.pill = Pill(level=lambda: self.recorder.level)
        self.history = HistoryWindow()

        self.tray = QSystemTrayIcon(self.icon)
        menu = QMenu()
        self.status_action = QAction("Loading the speech model...", menu)
        self.status_action.setEnabled(False)
        menu.addAction(self.status_action)
        menu.addSeparator()
        menu.addAction("History...", self.show_history)
        menu.addAction("Settings...", self.show_settings)
        menu.addAction("Open logs folder", lambda: _open_folder(LOG_DIR))
        menu.addSeparator()
        menu.addAction(f"Quit {APP_NAME}", self.quit)
        self.menu = menu  # keep a reference; the tray only borrows it
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_history()
                                    if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.setToolTip(f"{APP_NAME}: loading...")
        self.tray.show()

        self.pump = QTimer()
        self.pump.setInterval(15)
        self.pump.timeout.connect(self._pump)
        threading.Thread(target=self._load, name="model-loader", daemon=True).start()

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
        elif self.listener and not self.quiet_start:
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
        """Use the model chosen in Settings from the next dictation on (or none)."""
        model = self.settings.cleanup_model
        polisher = None
        if model and self.gateway.api_key:
            polisher = Polisher(self.gateway, model, self.settings.vocabulary, fallback=other_model(model))
            polisher.prepare()  # connect now, so the first dictation doesn't wait for it
        elif model:
            self._notify(APP_NAME, "Text cleanup needs the gateway API key: add it in Settings.",
                         QSystemTrayIcon.MessageIcon.Warning)
        self.dictation.cleanup = polisher
        self._update_status()
        log.info("Text cleanup: %s (%s, %d words)", model or "off", self.gateway, len(self.settings.vocabulary))

    def _update_status(self) -> None:
        if not self.listener:
            return
        model = self.dictation.cleanup.model.rsplit("/", 1)[-1] if self.dictation and self.dictation.cleanup else None
        self._set_status(f"Ready: hold {self.listener.hotkey.label}" + (f" · cleanup: {model}" if model else ""))

    # -- running

    def _pump(self) -> None:
        events = self.listener.events
        while not events.empty():
            event, at = events.get_nowait()
            self.dictation.handle(event, at)
        self.dictation.tick(time.monotonic())

    def _on_result(self, heard: str, typed: str) -> None:
        add_to_history(typed, heard)
        if self.history.isVisible():
            self.history.refresh()

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

    # -- menu actions

    def show_history(self) -> None:
        self.history.refresh()
        self.history.show()
        self.history.raise_()
        self.history.activateWindow()

    def show_settings(self) -> None:
        # Re-reading the device list resets the audio system, so not while a recording is running.
        dialog = SettingsDialog(self.settings, input_device_names(refresh=not (self.dictation and self.dictation.recording)),
                                self.gateway)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new = dialog.result_settings()
        gateway = dialog.result_gateway()
        if gateway != self.gateway:
            gateway.save()
            self.gateway = gateway
        if can_start_with_windows() and dialog.start_with_windows.isChecked() != starts_with_windows():
            set_start_with_windows(dialog.start_with_windows.isChecked())
        hotkey_changed = new.hotkey != self.settings.hotkey
        self.settings = new
        new.save()
        self.recorder.device = new.microphone or None
        if self.dictation:
            self.dictation.sounds, self.dictation.save = new.sounds, new.save_recordings
            self._apply_cleanup()  # the chosen model is active from the next dictation
            if hotkey_changed:
                self._start_listener()
        log.info("Settings saved: %s", new)

    def quit(self) -> None:
        if self.listener:
            self.listener.stop()
        if self.dictation:
            self.dictation.close()
        self.tray.hide()
        QApplication.quit()

    def _set_status(self, text: str) -> None:
        self.status_action.setText(text)
        self.tray.setToolTip(f"{APP_NAME}: {text}")

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
    SettingsDialog(Settings(), ["Test microphone"], GatewayConfig()).grab()
    history = HistoryWindow()
    history.refresh()
    history.grab()
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
    app.setQuitOnLastWindowClosed(False)  # closing History or Settings must not end the app
    app.setWindowIcon(QIcon(str(ICON_FILE)))
    if already_running():
        QMessageBox.information(None, APP_NAME, f"{APP_NAME} is already running. Look for its icon in the tray.")
        return 0
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, APP_NAME, "Windows has no notification area (system tray) available.")
        return 1
    tray_app = TrayApp(quiet_start="--startup" in argv)  # noqa: F841 (kept alive for the app's lifetime)
    try:
        return app.exec()
    except Exception:
        log.exception("Crashed; logs in %s", log_dir)
        raise
