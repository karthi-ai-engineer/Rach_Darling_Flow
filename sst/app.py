"""The tray app, Rflow: dictation without a console window.

A tray icon with a menu (status, History, Settings, Quit), a small "pill" near the bottom of the
screen while you dictate (recording with a live level, transcribing, typed), a settings window and
a history window. The dictation itself is sst.dictate.Dictation, the same as the console command.

  uv run sst app                        from the source checkout
  Rflow.exe                             the installed app (the Start menu shortcut)
  Rflow.exe --self-test                 build check: builds every window off-screen and transcribes once
"""
import ctypes
import logging
import math
import os
import re
import shutil
import sys
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from sst import RECORDINGS_DIR, __version__, bench, updates
from sst.audio import Recorder, input_device_names, save_wav
from sst.dictate import DEFAULT_HOTKEY, Dictation, already_running, wispr_flow_running
from sst.engines import load_engine
from sst.gateway import GatewayConfig, GatewayError, Polisher
from sst.hotkey import HotkeyListener, parse_hotkey
from sst.settings import (
    Settings,
    add_to_history,
    can_start_with_windows,
    read_history,
    set_start_with_windows,
    starts_with_windows,
)

APP_NAME = "Rflow"
ICON_FILE = Path(__file__).parent / "static" / "sst.ico"
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "sst" / "logs"
UPDATE_DIR = Path(os.environ.get("TEMP", Path.home())) / "Rflow-update"  # downloaded installers
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
        cleanup = QGroupBox("Text cleanup with an AI model")
        cleanup_form = QFormLayout(cleanup)
        self.cleanup_on = QCheckBox("Clean up the text before typing it (punctuation, fillers, your words)")
        self.cleanup_on.setChecked(settings.cleanup)
        self.cleanup_on.setToolTip("Works with any OpenAI-compatible endpoint. If the model fails, the backup model is "
                                   "used; if the endpoint can't help in time, the text is typed as heard.")
        cleanup_form.addRow("", self.cleanup_on)
        how = QLabel("Your voice stays on this laptop; only the finished text goes to the endpoint.")
        how.setStyleSheet("color: gray")
        cleanup_form.addRow("", how)
        self.gateway_url = QLineEdit(gateway.base_url)
        self.gateway_url.setPlaceholderText("e.g. https://api.openai.com/v1  ·  http://localhost:11434/v1 (Ollama)")
        cleanup_form.addRow("Endpoint", self.gateway_url)
        self.api_key = QLineEdit(gateway.api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Encrypted on this laptop (optional for local)")
        load = QPushButton("Load models")
        load.clicked.connect(self._load_models)
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key)
        key_row.addWidget(load)
        cleanup_form.addRow("API key", key_row)
        self.model = _model_box(settings.cleanup_model, "choose after Load models, or type a model name")
        test = QPushButton("Test")
        test.clicked.connect(self._test)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        model_row.addWidget(test)
        cleanup_form.addRow("Model", model_row)
        self.fallback = _model_box(settings.cleanup_fallback, "optional: used if the model fails")
        cleanup_form.addRow("Backup model", self.fallback)
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
                        cleanup=self.cleanup_on.isChecked(), cleanup_model=self.model.currentText().strip(),
                        cleanup_fallback=self.fallback.currentText().strip(), vocabulary=self._words())

    def result_gateway(self) -> GatewayConfig:
        return GatewayConfig(base_url=self.gateway_url.text().strip(), api_key=self.api_key.text().strip())

    def _words(self) -> list[str]:
        return [w.strip() for w in re.split(r"[\n,]", self.vocabulary.toPlainText()) if w.strip()]

    def _busy(self, message: str, work) -> None:
        self.test_result.setText(message)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            self.test_result.setText(work())
        except GatewayError as e:
            self.test_result.setText(f"Failed: {e}")
        finally:
            QApplication.restoreOverrideCursor()

    def _load_models(self) -> None:
        def load() -> str:
            models = Polisher(self.result_gateway(), "").models()
            for box in (self.model, self.fallback):
                current = box.currentText()
                box.clear()
                if box is self.fallback:
                    box.addItem("")  # no backup model
                box.addItems(models)
                box.setCurrentText(current)
            return f"Loaded {len(models)} models." if models else "The endpoint lists no models; type a model name."
        self._busy("Loading models...", load)

    def _test(self) -> None:
        model = self.model.currentText().strip()
        if not model:
            self.test_result.setText("Choose a model first (Load models).")
            return
        self._busy("Testing...", lambda: "OK: " + Polisher(self.result_gateway(), model, self._words()).check())


def _model_box(current: str, hint: str) -> QComboBox:
    box = QComboBox()
    box.setEditable(True)  # pick from the loaded list, or type any model name
    box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    box.lineEdit().setPlaceholderText(hint)
    if current:
        box.addItem(current)
    box.setCurrentText(current)
    return box


class HistoryWindow(QWidget):
    """What a click on the tray icon opens: the recent dictations, plus the way to Settings (users look for it here)."""

    def __init__(self, open_settings=None):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle(f"{APP_NAME} history")
        self.setWindowIcon(QIcon(str(ICON_FILE)))
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.itemDoubleClicked.connect(lambda item: self._copy(item))
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: self._copy(self.list.currentItem()))
        self.status = QLabel("Double-click a line to copy it. Hover to see what was heard.")
        self.status.setStyleSheet("color: gray")
        row = QHBoxLayout()
        if open_settings:
            settings = QPushButton("Settings...")
            settings.clicked.connect(open_settings)
            row.addWidget(settings)
        row.addWidget(self.status)
        row.addStretch()
        row.addWidget(copy)
        # The "a new version is available" banner, shown by the app when an update exists.
        self.banner = QFrame()
        self.banner.setObjectName("updateBanner")
        self.banner.setStyleSheet("#updateBanner { background: #2563eb; border-radius: 8px; } "
                                  "#updateBanner QLabel { color: white; font-weight: 600; }")
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        self.notes_button = QPushButton("What's new")
        self.update_button = QPushButton("Update now")
        banner_row = QHBoxLayout(self.banner)
        banner_row.addWidget(self.banner_text, 1)
        banner_row.addWidget(self.notes_button)
        banner_row.addWidget(self.update_button)
        self.banner.hide()
        layout = QVBoxLayout(self)
        layout.addWidget(self.banner)
        layout.addWidget(self.list)
        layout.addLayout(row)
        self.resize(600, 440)

    def show_update(self, text: str, busy: bool = False) -> None:
        self.banner_text.setText(text)
        self.update_button.setEnabled(not busy)
        self.banner.show()

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


class ReadingTest(QWidget):
    """The reading test: read the sentences aloud, then see how many words each setup gets wrong (sst/bench.py)."""

    scored = Signal(object)  # from the scoring thread
    progressed = Signal(str)
    failed = Signal(str)

    def __init__(self, recorder, score, add_words, microphone: str, folder: Path | None = None):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle(f"{APP_NAME} reading test")
        self.setWindowIcon(QIcon(str(ICON_FILE)))
        self.recorder, self._score, self._add_words = recorder, score, add_words
        self.folder = folder or bench.BENCH_DIR / time.strftime("%Y-%m-%d_%H%M%S")
        self.index, self.recording = 0, False
        self.scored.connect(self._show_results)
        self.progressed.connect(lambda text: self.status.setText(text))
        self.failed.connect(self._score_failed)

        # page 1: reading
        intro = QLabel(f"Read each sentence aloud the way you normally dictate. Microphone: {microphone}. "
                       "Rflow then counts the words it gets wrong, with and without text cleanup. About 10 minutes.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: gray")
        self.counter = QLabel()
        self.sentence = QLabel()
        self.sentence.setWordWrap(True)
        self.sentence.setMinimumHeight(110)
        self.sentence.setFont(QFont("Segoe UI", 17))
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        self.level.setFixedHeight(8)
        self.status = QLabel("Press Record (or Space), read the sentence, then Stop.")
        self.record_button = QPushButton("Record")
        self.record_button.clicked.connect(self.toggle_recording)
        self.redo_button = QPushButton("Redo")
        self.redo_button.clicked.connect(self.toggle_recording)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(lambda: self.go(self.index - 1))
        self.next_button = QPushButton("Next")
        self.next_button.clicked.connect(lambda: self.go(self.index + 1))
        self.score_button = QPushButton("Score")
        self.score_button.clicked.connect(self.start_scoring)
        buttons = QHBoxLayout()
        for button in (self.record_button, self.redo_button, self.back_button, self.next_button):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.score_button)
        reading = QWidget()
        reading_layout = QVBoxLayout(reading)
        for widget in (intro, self.counter, self.sentence, self.level, self.status):
            reading_layout.addWidget(widget)
        reading_layout.addStretch()
        reading_layout.addLayout(buttons)

        # page 2: results
        self.report = QTextBrowser()
        self.report.setOpenExternalLinks(False)
        self.suggestions = QListWidget()
        self.suggestions.setMaximumHeight(120)
        add = QPushButton("Add to Your words")
        add.clicked.connect(self._add_selected)
        again = QPushButton("Score again")
        again.clicked.connect(self.start_scoring)
        open_folder = QPushButton("Open folder")
        open_folder.clicked.connect(lambda: _open_folder(self.folder))
        self.results_status = QLabel("")
        self.results_status.setWordWrap(True)
        self.results_status.setStyleSheet("color: gray")
        results_buttons = QHBoxLayout()
        for button in (add, again, open_folder):
            results_buttons.addWidget(button)
        results_buttons.addStretch()
        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.addWidget(self.report, 1)
        worth = QLabel("Worth adding to Your words (untick any you don't want):")
        worth.setWordWrap(True)
        results_layout.addWidget(worth)
        results_layout.addWidget(self.suggestions)
        results_layout.addWidget(self.results_status)
        results_layout.addLayout(results_buttons)

        self.pages = QStackedWidget()
        self.pages.addWidget(reading)
        self.pages.addWidget(results)
        layout = QVBoxLayout(self)
        layout.addWidget(self.pages)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self._space)
        self.meter = QTimer(self)
        self.meter.setInterval(50)
        self.meter.timeout.connect(lambda: self.level.setValue(min(100, int(self.recorder.level * 400))))
        self.resize(640, 420)
        self.go(next(iter(self._to_read()), 0))  # a resumed test opens at the first sentence not read yet
        if self.recorded():
            self.status.setText(f"Continuing your unfinished test ({self.recorded()} of {len(bench.SENTENCES)} read).")

    def recorded(self) -> int:
        return len(bench.recordings(self.folder)) if self.folder.exists() else 0

    def _to_read(self) -> list[int]:
        return [i for i in range(len(bench.SENTENCES)) if not (self.folder / f"{i + 1:02}.wav").exists()]

    def go(self, index: int) -> None:
        if self.recording:
            return
        self.index = max(0, min(index, len(bench.SENTENCES) - 1))
        done = (self.folder / f"{self.index + 1:02}.wav").exists()
        self.counter.setText(f"Sentence {self.index + 1} of {len(bench.SENTENCES)}" + ("  ·  recorded" if done else ""))
        self.sentence.setText(bench.SENTENCES[self.index])
        self.record_button.setEnabled(not done)
        self.redo_button.setEnabled(done)
        self.back_button.setEnabled(self.index > 0)
        self.next_button.setEnabled(self.index < len(bench.SENTENCES) - 1)
        count = self.recorded()
        self.score_button.setEnabled(count > 0)
        self.score_button.setText(f"Score ({count} recorded)" if count else "Score")

    def _space(self) -> None:
        if self.pages.currentIndex() == 0:
            self.toggle_recording()

    def toggle_recording(self) -> None:
        if not self.recording:
            try:
                self.recorder.start()
            except Exception as e:
                self.status.setText(f"Could not open the microphone: {e}")
                return
            self.recording = True
            self.meter.start()
            for button in (self.redo_button, self.back_button, self.next_button, self.score_button):
                button.setEnabled(False)
            self.record_button.setEnabled(True)
            self.record_button.setText("Stop")
            self.status.setText("Recording... read the sentence, then press Stop (or Space).")
            return
        self.recording = False
        self.meter.stop()
        self.level.setValue(0)
        self.record_button.setText("Record")
        audio = self.recorder.stop()
        if len(audio) < self.recorder.rate * 0.5:
            self.status.setText("That was too short; press Record and read the sentence again.")
            self.go(self.index)
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        stem = self.folder / f"{self.index + 1:02}"
        save_wav(stem.with_suffix(".wav"), audio, self.recorder.rate)
        stem.with_suffix(".txt").write_text(bench.SENTENCES[self.index], encoding="utf-8")
        remaining = self._to_read()
        if remaining:
            self.status.setText("Saved. Next sentence:")
            later = [i for i in remaining if i > self.index]
            self.go(later[0] if later else remaining[0])
        else:
            self.status.setText("All sentences recorded. Press Score.")
            self.go(self.index)

    def start_scoring(self) -> None:
        if self.recording or not self.recorded():
            return
        self.score_button.setEnabled(False)
        self.results_status.setText("Scoring...")
        self.status.setText("Scoring...")

        def work() -> None:
            try:
                self.scored.emit(self._score(self.folder, self.progressed.emit))
            except Exception as e:
                log.exception("Scoring the reading test failed")
                self.failed.emit(str(e))
        threading.Thread(target=work, name="reading-test-score", daemon=True).start()

    def _show_results(self, results) -> None:
        best = min(results.setups, key=lambda s: s.error_rate)
        rows = "".join(
            f"<tr><td>{'<b>' if s is best else ''}{s.name}{'</b>' if s is best else ''}</td>"
            f"<td align=right>{s.error_rate:.1%}</td><td align=right>{s.wrong} / {s.total}</td>"
            f"<td align=right>{s.seconds_per_sentence:.2f} s</td></tr>" for s in results.setups)
        misheard = "".join(f"<li>{said or '<i>(extra word)</i>'} → {heard or '<i>(missed)</i>'} ({times}×)</li>"
                           for said, heard, times in results.misheard[:10])
        self.report.setHtml(
            f"<h3>{len(results.sentences)} sentences scored</h3>"
            f"<table cellpadding=6><tr><th align=left>Setup</th><th>Word errors</th><th>Wrong / total</th>"
            f"<th>Time per sentence</th></tr>{rows}</table>"
            f"<p>Lowest error rate: <b>{best.name}</b>.</p>"
            + (f"<h4>Most misheard (by speech recognition)</h4><ul>{misheard}</ul>" if misheard else ""))
        self.suggestions.clear()
        for word in results.suggestions:
            item = QListWidgetItem(word)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.suggestions.addItem(item)
        # The folder name only: a full path would make the window wider than the screen allows for it.
        self.results_status.setText(f"Saved as report.md in the {self.folder.name} test folder.")
        self.results_status.setToolTip(str(self.folder))
        self.pages.setCurrentIndex(1)
        self.go(self.index)

    def _score_failed(self, message: str) -> None:
        self.status.setText(f"Scoring failed: {message}")
        self.results_status.setText(f"Scoring failed: {message}")
        self.go(self.index)

    def _add_selected(self) -> None:
        chosen = [self.suggestions.item(i).text() for i in range(self.suggestions.count())
                  if self.suggestions.item(i).checkState() == Qt.CheckState.Checked]
        added = self._add_words(chosen)
        self.results_status.setText(f"Added {added} word(s) to Your words. Press Score again to see the difference."
                                    if added else "Those words are already in Your words.")


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


def _open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


# ---------------------------------------------------------------- the app

class _Signals(QObject):
    state = Signal(str, str)  # from Dictation, possibly on its worker thread
    result = Signal(str, str)  # (heard, typed), from Dictation's worker thread
    loaded = Signal(object)
    failed = Signal(str)
    update_found = Signal(object)  # the rest come from the update threads
    update_none = Signal()
    update_progress = Signal(str)
    update_ready = Signal(str)
    update_failed = Signal(str, bool)  # (message, tell the user)


class TrayApp:
    def __init__(self, quiet_start: bool = False):
        self.settings = Settings.load()
        self.gateway = GatewayConfig.load()
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
        self.signals.update_none.connect(lambda: self._notify(APP_NAME, f"You have the latest version ({__version__})."))
        self.signals.update_progress.connect(lambda text: self.history.show_update(text, busy=True))
        self.signals.update_ready.connect(self._on_update_ready)
        self.signals.update_failed.connect(self._on_update_failed)

        self.icon = QIcon(str(ICON_FILE))
        self.recording_icon = _with_red_dot(self.icon)
        self.pill = Pill(level=lambda: self.recorder.level)
        self.history = HistoryWindow(open_settings=self.show_settings)
        self.history.notes_button.clicked.connect(self._open_release_notes)
        self.history.update_button.clicked.connect(self.start_update)

        self.tray = QSystemTrayIcon(self.icon)
        menu = QMenu()
        self.status_action = QAction("Loading the speech model...", menu)
        self.status_action.setEnabled(False)
        menu.addAction(self.status_action)
        self.update_action = QAction("", menu)
        self.update_action.triggered.connect(self.show_history)
        self.update_action.setVisible(False)
        menu.addAction(self.update_action)
        menu.addSeparator()
        menu.addAction("History...", self.show_history)
        menu.addAction("Settings...", self.show_settings)
        menu.addAction("Reading test...", self.show_reading_test)
        menu.addAction("Check for updates", lambda: self.check_for_updates(manual=True))
        menu.addAction("Open logs folder", lambda: _open_folder(LOG_DIR))
        menu.addSeparator()
        menu.addAction(f"Quit {APP_NAME}", self.quit)
        self.menu = menu  # keep a reference; the tray only borrows it
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_history()
                                    if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.messageClicked.connect(self.show_history)  # e.g. "Rflow 1.1.0 is available"
        self.tray.setToolTip(f"{APP_NAME}: loading...")
        self.tray.show()

        self.pump = QTimer()
        self.pump.setInterval(15)
        self.pump.timeout.connect(self._pump)
        threading.Thread(target=self._load, name="model-loader", daemon=True).start()
        self.reading: ReadingTest | None = None
        if getattr(sys, "frozen", False):  # the source checkout is updated with git, not by the app
            # The installer of an update that has finished (it started this version) isn't needed any more.
            QTimer.singleShot(60_000, lambda: shutil.rmtree(UPDATE_DIR, ignore_errors=True))
            QTimer.singleShot(20_000, self.check_for_updates)
            self.update_timer = QTimer()
            self.update_timer.setInterval(updates.CHECK_EVERY_HOURS * 3600 * 1000)
            self.update_timer.timeout.connect(self.check_for_updates)
            self.update_timer.start()

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
        s = self.settings
        model = s.cleanup_model if s.cleanup else ""
        polisher = None
        if model and self.gateway.base_url:
            polisher = Polisher(self.gateway, model, s.vocabulary, fallback=s.cleanup_fallback or None)
            polisher.prepare()  # connect now, so the first dictation doesn't wait for it
        elif s.cleanup:
            self._notify(APP_NAME, "Text cleanup needs an endpoint and a model: set them in Settings.",
                         QSystemTrayIcon.MessageIcon.Warning)
        self.dictation.cleanup = polisher
        self._update_status()
        log.info("Text cleanup: %s, backup %s (%s, %d words)", model or "off", s.cleanup_fallback or "none",
                 self.gateway, len(s.vocabulary))

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

    # -- reading test

    def show_reading_test(self) -> None:
        if not self.dictation:
            self._notify(APP_NAME, "The speech model is still loading; try again in a moment.")
            return
        if self.reading is None or not self.reading.isVisible():
            self.reading = ReadingTest(Recorder(self.settings.microphone or None), self._score_reading, self._add_words,
                                       self.settings.microphone or "Windows default", folder=bench.unfinished())
        self.reading.show()
        self.reading.raise_()
        self.reading.activateWindow()

    def _score_reading(self, folder: Path, progress) -> bench.Results:
        """Parakeet alone, then with the cleanup model and the backup model, one at a time (the endpoint may be small)."""
        s = self.settings
        models = [m for m in dict.fromkeys((s.cleanup_model, s.cleanup_fallback)) if m] if self.gateway.base_url else []
        polishers = {f"Parakeet + {m.rsplit('/', 1)[-1]}": Polisher(self.gateway, m, s.vocabulary) for m in models}
        return bench.score(folder, self.dictation.engine, polishers, progress)

    def _add_words(self, new_words: list[str]) -> int:
        known = {w.lower() for w in self.settings.vocabulary}
        added = [w for w in new_words if w.lower() not in known]
        if added:
            self.settings.vocabulary = self.settings.vocabulary + added
            self.settings.save()
            self._apply_cleanup()  # the cleanup uses the new words from the next dictation (and the next scoring)
        return len(added)

    # -- updates

    def check_for_updates(self, manual: bool = False) -> None:
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

    def _on_update_found(self, update) -> None:
        self.update = update
        self.history.show_update(f"Rflow {update.version} is available (you have {__version__}).")
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
        self.history.show_update(f"Downloading Rflow {update.version}...", busy=True)
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
        self.history.show_update("Installing... Rflow restarts by itself when it's done.", busy=True)
        release_running_mutex()  # otherwise the installer stops to ask for Rflow to be closed
        try:
            updates.install(Path(path))
        except OSError as e:
            hold_running_mutex()
            self._on_update_failed(f"Could not start the installer: {e}", True)
            return
        self.quit()

    def _on_update_failed(self, message: str, tell: bool) -> None:
        log.warning(message)
        if tell:
            self._notify(APP_NAME, message, QSystemTrayIcon.MessageIcon.Warning)
        if self.update:
            self.history.show_update(f"{message} You can try again.")

    def _open_release_notes(self) -> None:
        if self.update and self.update.page:
            QDesktopServices.openUrl(QUrl(self.update.page))

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
    history = HistoryWindow(open_settings=lambda: None)
    history.refresh()
    history.show_update("Rflow 9.9.9 is available (you have 1.0.0).")
    history.grab()
    ReadingTest(Recorder(), lambda folder, progress: None, lambda words: 0, "Test microphone").grab()
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
