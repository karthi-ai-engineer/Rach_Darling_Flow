"""The Rflow window: a small app like Wispr Flow's, next to the tray icon.

A sidebar leads to Home (stats and the recent dictations), Dictionary ("Your words"), Reading test, AI cleanup and
Settings; a first-run welcome sets up the microphone and a first dictation. It is native Qt, styled by one stylesheet
that follows Windows' light or dark mode (an embedded browser would add ~150 MB for the same look).

The window keeps no state of its own. It reads and changes everything through `app`: the TrayApp (sst/app.py), which
applies a change at once (hotkey, microphone, cleanup model...), or PreviewApp below for the self-test, the tests and
the website's screenshots.
"""
import dataclasses
import logging
import math
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sst import RECORDINGS_DIR, __version__, bench
from sst.audio import LevelMeter, Take, call_quality, save_wav
from sst.gateway import PROVIDERS, GatewayConfig, Polisher
from sst.hotkey import parse_hotkey
from sst.settings import (
    Profiles,
    Settings,
    Stats,
    can_start_with_windows,
    set_start_with_windows,
    starts_with_windows,
)

APP_NAME = "Rflow"
ICON_FILE = Path(__file__).parent / "static" / "sst.ico"
UI_IMAGES = Path(__file__).parent / "static" / "ui"  # drawn by scripts/make_ui_images.py
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "sst" / "logs"
WEBSITE = "https://rachdarlingflow-site.vercel.app"
REPO = "https://github.com/karthi-ai-engineer/Rach_Darling_Flow"
HOTKEY_CHOICES = [("Ctrl+Win (like Wispr Flow)", "ctrl+win"), ("Menu key", "menu"), ("Ctrl+Alt+D", "ctrl+alt+d")]

log = logging.getLogger("sst.window")

# Windows' own icon font (Segoe Fluent Icons on Windows 11, MDL2 Assets on 10): crisp icons without image files.
ICON_FONTS = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]
GLYPHS = {"home": "\ue80f", "dictionary": "\ue82d", "reading": "\ue9d9", "cleanup": "\ue99a", "settings": "\ue713",
          "copy": "\ue8c8", "check": "\ue73e", "delete": "\ue74d", "words": "\ue8d2", "speed": "\ue916",
          "streak": "\uecad", "week": "\ue787", "mic": "\ue720", "update": "\ue895", "profiles": "\ue716",
          "profile": "\ue77b"}

# The website's colours (site/index.html), so the app and the site look like one product.
THEMES = {
    "light": {"bg": "#f7f8fb", "side": "#eef1f6", "surface": "#ffffff", "text": "#111827", "muted": "#5b6475",
              "line": "#e3e7ef", "hover": "#e4e8f0", "selected": "#dbe4f8", "accent": "#2563eb", "accent2": "#4f46e5",
              "ok": "#16a34a", "warn": "#d97706", "bad": "#dc2626"},
    "dark": {"bg": "#0d1117", "side": "#11161e", "surface": "#151b24", "text": "#e8ecf3", "muted": "#9aa4b5",
             "line": "#263041", "hover": "#1c2430", "selected": "#1e2a3d", "accent": "#3b82f6", "accent2": "#6366f1",
             "ok": "#4ade80", "warn": "#fbbf24", "bad": "#f87171"},
}


def dark_mode() -> bool:
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def stylesheet(theme: str) -> str:
    t = THEMES[theme]
    check, arrow = (UI_IMAGES / "check.png").as_posix(), (UI_IMAGES / f"arrow-{theme}.png").as_posix()
    return f"""
    #root, #page {{ background: {t['bg']}; }}
    #sidebar {{ background: {t['side']}; border-right: 1px solid {t['line']}; }}
    QLabel {{ color: {t['text']}; background: transparent; }}
    QLabel[muted="true"] {{ color: {t['muted']}; }}
    QLabel#h1 {{ font-size: 19pt; font-weight: 600; }}
    QLabel#h2 {{ font-size: 11pt; font-weight: 600; }}
    QLabel#brand {{ font-size: 14pt; font-weight: 700; }}
    QLabel#section {{ color: {t['muted']}; font-size: 8pt; font-weight: 700; }}
    QLabel#stat {{ font-size: 17pt; font-weight: 600; }}
    QLabel#glyph {{ color: {t['accent']}; }}
    QLabel#sentence {{ font-size: 17pt; }}
    QLabel#warning {{ color: {t['warn']}; }}
    QPushButton#nav {{ text-align: left; padding: 9px 12px; border: none; border-radius: 8px; color: {t['muted']};
                       background: transparent; }}
    QPushButton#nav:hover {{ background: {t['hover']}; color: {t['text']}; }}
    QPushButton#nav:checked {{ background: {t['selected']}; color: {t['text']}; font-weight: 600; }}
    QPushButton#profile {{ text-align: left; padding: 8px 12px; border: 1px solid {t['line']}; border-radius: 8px;
                           background: {t['surface']}; color: {t['text']}; }}
    QPushButton#profile:hover {{ border-color: {t['accent']}; }}
    QMenu {{ background: {t['surface']}; color: {t['text']}; border: 1px solid {t['line']}; padding: 4px; }}
    QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {t['selected']}; }}
    QMenu::separator {{ height: 1px; background: {t['line']}; margin: 4px 8px; }}
    QFrame#card {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 12px; }}
    QFrame#hero {{ border-radius: 14px; border: none;
                   background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {t['accent']}, stop:1 {t['accent2']}); }}
    QFrame#hero QLabel {{ color: white; }}
    QFrame#divider {{ background: {t['line']}; border: none; max-height: 1px; min-height: 1px; }}
    QPushButton {{ background: {t['surface']}; color: {t['text']}; border: 1px solid {t['line']}; border-radius: 8px;
                   padding: 6px 14px; }}
    QPushButton:hover {{ background: {t['hover']}; }}
    QPushButton:disabled {{ color: {t['muted']}; }}
    QPushButton#primary {{ background: {t['accent']}; color: white; border: 1px solid {t['accent']}; font-weight: 600; }}
    QPushButton#primary:hover {{ background: {t['accent2']}; border-color: {t['accent2']}; }}
    QPushButton#primary:disabled {{ background: {t['line']}; border-color: {t['line']}; color: {t['muted']}; }}
    QPushButton#link {{ border: none; background: transparent; color: {t['accent']}; padding: 2px 0; text-align: left; }}
    QToolButton#icon {{ border: none; border-radius: 6px; padding: 4px; color: {t['muted']}; background: transparent; }}
    QToolButton#icon:hover {{ background: {t['hover']}; color: {t['text']}; }}
    QLineEdit, QPlainTextEdit, QComboBox, QTextBrowser, QListWidget {{
        background: {t['surface']}; color: {t['text']}; border: 1px solid {t['line']}; border-radius: 8px;
        padding: 5px 8px; selection-background-color: {t['accent']}; selection-color: white; }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border: 1px solid {t['accent']}; }}
    QComboBox {{ padding-right: 30px; }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 30px; border: none;
                            background: transparent; }}
    QComboBox::down-arrow {{ image: url({arrow}); width: 12px; height: 12px; }}
    QComboBox QAbstractItemView {{ background: {t['surface']}; color: {t['text']}; border: 1px solid {t['line']};
                                   selection-background-color: {t['selected']}; selection-color: {t['text']}; }}
    QListWidget::item {{ padding: 4px 2px; }}
    QCheckBox {{ color: {t['text']}; spacing: 10px; background: transparent; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {t['muted']}; border-radius: 4px;
                            background: {t['surface']}; }}
    QCheckBox::indicator:hover {{ border-color: {t['accent']}; }}
    QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; image: url({check}); }}
    QCheckBox::indicator:disabled {{ background: {t['line']}; border-color: {t['line']}; }}
    QListWidget::indicator {{ width: 16px; height: 16px; border: 1px solid {t['muted']}; border-radius: 4px;
                              background: {t['surface']}; }}
    QListWidget::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; image: url({check}); }}
    QProgressBar {{ background: {t['line']}; border: none; border-radius: 3px; }}
    QProgressBar::chunk {{ background: {t['accent']}; border-radius: 3px; }}
    QScrollArea {{ background: transparent; border: none; }}
    #banner {{ background: {t['accent']}; border-radius: 10px; }}
    #banner QLabel {{ color: white; font-weight: 600; }}
    #banner QPushButton {{ background: white; color: {t['accent']}; border: none; font-weight: 600; }}
    """


def glyph(name: str, size: int = 13) -> QLabel:
    label = QLabel(GLYPHS[name])
    font = QFont()
    font.setFamilies(ICON_FONTS)
    font.setPointSize(size)
    label.setFont(font)
    label.setObjectName("glyph")
    return label


def text(value: str = "", name: str | None = None, muted: bool = False, wrap: bool = True) -> QLabel:
    label = QLabel(value)
    label.setWordWrap(wrap)
    if name:
        label.setObjectName(name)
    if muted:
        label.setProperty("muted", True)
    return label


def card(spacing: int = 10) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(spacing)
    return frame, layout


def button(label: str, on_click=None, primary: bool = False, link: bool = False) -> QPushButton:
    b = QPushButton(label)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary or link:
        b.setObjectName("primary" if primary else "link")
    if on_click:
        b.clicked.connect(on_click)
    return b


def icon_button(name: str, tip: str, on_click=None) -> QToolButton:
    b = QToolButton()
    b.setObjectName("icon")
    b.setText(GLYPHS[name])
    font = QFont()
    font.setFamilies(ICON_FONTS)
    font.setPointSize(11)
    b.setFont(font)
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click:
        b.clicked.connect(on_click)
    return b


def row(*items, stretch_at: int | None = None) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(8)
    for i, item in enumerate(items):
        if i == stretch_at:
            layout.addStretch()
        if isinstance(item, QHBoxLayout):
            layout.addLayout(item)
        else:
            layout.addWidget(item)
    if stretch_at is not None and stretch_at >= len(items):
        layout.addStretch()
    return layout


def clear(layout: QLayout) -> None:
    """Empty a layout that is filled again (history, words)."""
    while layout.count():
        widget = layout.takeAt(0).widget()
        if widget:
            widget.hide()  # at once: deleteLater waits for the event loop, and until then it would still be drawn
            widget.deleteLater()


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class _Relay(QObject):
    done = Signal(object, object)  # (result, error)


def run_in_background(owner: QObject, work, done) -> None:
    """work() on a thread (network calls), then done(result, error) back on the UI thread."""
    relay = _Relay(owner)  # parented, so it lives until it has delivered
    relay.done.connect(lambda result, error: (done(result, error), relay.deleteLater()),
                       Qt.ConnectionType.QueuedConnection)

    def run() -> None:
        try:
            relay.done.emit(work(), None)
        except Exception as e:
            relay.done.emit(None, e)
    threading.Thread(target=run, name="window-task", daemon=True).start()


class Page(QScrollArea):
    """A page: a title, an optional subtitle, then cards; it scrolls when the window is small."""

    def __init__(self, title: str = "", subtitle: str = ""):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("page")
        body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(36, 28, 36, 28)
        self.body.setSpacing(14)
        self.title = text(title, "h1")
        self.subtitle = text(subtitle, muted=True)
        if title:
            self.body.addWidget(self.title)
        if subtitle:
            self.body.addWidget(self.subtitle)
            self.body.addSpacing(4)
        self.setWidget(body)

    def add(self, item) -> None:
        if isinstance(item, QLayout):
            self.body.addLayout(item)
        else:
            self.body.addWidget(item)


# ---------------------------------------------------------------- the microphone box (settings and welcome)

class MicrophoneBox(QWidget):
    """A microphone choice with a live level bar, so the user sees at once that the microphone hears them."""

    changed = Signal(str)  # the chosen device name ("" = the Windows default)

    def __init__(self, current: str, microphones: list[str]):
        super().__init__()
        self.combo = QComboBox()
        self.combo.addItem("Windows default", "")
        for name in microphones:
            self.combo.addItem(name, name)
        if current and self.combo.findData(current) < 0:
            self.combo.addItem(f"{current} (not connected)", current)
        self.combo.setCurrentIndex(max(0, self.combo.findData(current)))
        self.combo.currentIndexChanged.connect(self._chosen)
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        self.level.setFixedHeight(6)
        self.note = text("Say something: the bar should move.", muted=True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.combo)
        layout.addWidget(self.level)
        layout.addWidget(self.note)
        self.meter = LevelMeter(current or None)
        self._timer = QTimer(self, interval=50, timeout=self._show_level)

    def device(self) -> str:
        return self.combo.currentData()

    def _chosen(self) -> None:
        self.meter.device = self.device() or None
        if self._timer.isActive():
            self._restart()
        self.changed.emit(self.device())

    def _restart(self) -> None:
        self.meter.stop()
        try:
            self.meter.start()
            self.note.setText("Say something: the bar should move.")
        except Exception as e:
            self.note.setText(f"Could not open this microphone: {e}")

    def _show_level(self) -> None:
        db = 20 * math.log10(self.meter.level + 1e-6)  # speech is roughly -45..-15 dBFS
        self.level.setValue(int(min(1.0, max(0.0, (db + 55) / 40)) * 100))

    def showEvent(self, event):
        super().showEvent(event)
        if QGuiApplication.platformName() != "offscreen":  # tests and the self-test don't open a microphone
            self._restart()
            self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        self.meter.stop()
        self.level.setValue(0)
        super().hideEvent(event)


# ---------------------------------------------------------------- Home

def _greeting(hour: int) -> str:
    return "Good morning" if 5 <= hour < 12 else "Good afternoon" if 12 <= hour < 18 else "Good evening"


def _day_title(day: date, today: date) -> str:
    if day == today:
        return "TODAY"
    if day == today - timedelta(days=1):
        return "YESTERDAY"
    return day.strftime("%A %d %B").upper() if day.year == today.year else day.strftime("%d %B %Y").upper()


def how_to_dictate(label: str) -> str:
    extra = f" {label}+Space starts hands-free as well." if label == "Ctrl+Win" else ""
    return (f"Let go, and the text is typed where your cursor is. Tap {label} for hands-free, and tap it again to "
            f"stop.{extra} Esc cancels.")


class HomePage(Page):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.greeting = text("", "h1")
        self.add(self.greeting)
        hero = QFrame()
        hero.setObjectName("hero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(22, 18, 22, 18)
        self.hero_title = text("", "h2")
        self.hero_title.setStyleSheet("font-size: 14pt;")
        self.hero_text = text()
        hero_layout.addWidget(self.hero_title)
        hero_layout.addWidget(self.hero_text)
        self.add(hero)
        self.tiles = QGridLayout()
        self.tiles.setSpacing(12)
        self.stat_values = {}
        for column, (key, icon, label) in enumerate([("week", "week", "words this week"), ("total", "words", "words in total"),
                                                     ("speed", "speed", "words per minute"), ("streak", "streak", "day streak")]):
            frame, layout = card(4)
            value = text("0", "stat", wrap=False)
            layout.addWidget(glyph(icon, 14))
            layout.addWidget(value)
            layout.addWidget(text(label, muted=True, wrap=False))
            self.stat_values[key] = value
            self.tiles.addWidget(frame, 0, column)
        self.add(self.tiles)
        self.add(text("Recent dictations", "h2"))
        self.history = QVBoxLayout()
        self.history.setSpacing(8)
        self.add(self.history)
        self.body.addStretch()

    def refresh(self) -> None:
        now = datetime.now()
        today = now.date()
        stats: Stats = self.app.stats
        label = self.app.hotkey_label()
        name = self.app.profiles.current.name.split()
        self.greeting.setText(_greeting(now.hour) + (f", {name[0]}" if name else ""))
        self.hero_title.setText(f"Hold {label} in any app and speak")
        self.hero_text.setText(how_to_dictate(label))
        wpm = stats.words_per_minute
        self.stat_values["week"].setText(f"{stats.words_this_week(today):,}")
        self.stat_values["total"].setText(f"{stats.words:,}")
        self.stat_values["speed"].setText(str(wpm) if wpm else "–")
        self.stat_values["streak"].setText(str(stats.streak(today)))
        self.stat_values["speed"].parent().setToolTip("" if wpm else "Shown after half a minute of dictation.")
        self._fill_history(self.app.history_entries(), today)

    def _fill_history(self, entries: list[dict], today: date) -> None:
        clear(self.history)
        if not entries:
            frame, layout = card()
            layout.addWidget(text("Nothing dictated yet", "h2"))
            layout.addWidget(text(f"Click in any text box, hold {self.app.hotkey_label()} and speak. Your dictations "
                                  "appear here, to copy again later.", muted=True))
            self.history.addWidget(frame)
            return
        day_card, day = None, None
        for entry in entries:
            try:
                when = datetime.strptime(entry.get("time", ""), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if when.date() != day:
                day = when.date()
                self.history.addWidget(text(_day_title(day, today), "section"))
                day_card, day_layout = card(0)
                day_layout.setContentsMargins(6, 4, 6, 4)
                self.history.addWidget(day_card)
            elif day_layout.count():
                divider = QFrame()
                divider.setObjectName("divider")
                day_layout.addWidget(divider)
            day_layout.addWidget(self._entry(when, entry))

    def _entry(self, when: datetime, entry: dict) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(10, 8, 6, 8)
        time_label = text(when.strftime("%H:%M"), muted=True, wrap=False)
        time_label.setFixedWidth(44)
        time_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        body = text(entry["text"])
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if entry.get("heard"):
            body.setToolTip(f"Heard: {entry['heard']}")
        copy = icon_button("copy", "Copy")
        copy.clicked.connect(lambda: self._copy(copy, entry["text"]))
        layout.addWidget(time_label)
        layout.addWidget(body, 1)
        layout.addWidget(copy, 0, Qt.AlignmentFlag.AlignTop)
        return widget

    def _copy(self, source: QToolButton, value: str) -> None:
        QGuiApplication.clipboard().setText(value)
        source.setText(GLYPHS["check"])
        source.setToolTip("Copied")
        QTimer.singleShot(1200, lambda: source.setText(GLYPHS["copy"]) if source else None)


# ---------------------------------------------------------------- Dictionary

class DictionaryPage(Page):
    def __init__(self, app, go_to):
        super().__init__("Dictionary", "Names, products and terms that Rflow should hear and spell your way, such as "
                                       "your name, your company, or GitHub and CodeQL. Speech recognition listens for "
                                       "them, and the AI cleanup uses them too. Add names and terms, not everyday "
                                       "words: those would be heard where you didn't say them.")
        self.app = app
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Add a word or name (several: separate them with commas)")
        self.entry.returnPressed.connect(self._add)
        self.add(row(self.entry, button("Add", self._add, primary=True)))
        self.cleanup_off = QFrame()
        self.cleanup_off.setObjectName("card")
        off = QHBoxLayout(self.cleanup_off)
        off.setContentsMargins(18, 10, 18, 10)
        off.addWidget(text("Speech recognition uses your words; AI cleanup is off, so it doesn't.", muted=True))
        off.addWidget(button("Set up AI cleanup", lambda: go_to("cleanup"), link=True), 0)
        self.add(self.cleanup_off)
        self.count = text("", "section")
        self.add(self.count)
        self.list_card, self.list = card(0)
        self.list.setContentsMargins(6, 4, 6, 4)
        self.add(self.list_card)
        self.body.addStretch()

    def refresh(self) -> None:
        settings: Settings = self.app.settings
        self.cleanup_off.setVisible(not settings.cleanup)
        clear(self.list)
        words = settings.vocabulary
        self.count.setText(f"{len(words)} WORD{'S' if len(words) != 1 else ''}")
        self.list_card.setVisible(bool(words))
        for word in sorted(words, key=str.lower):
            line = QWidget()
            layout = QHBoxLayout(line)
            layout.setContentsMargins(10, 4, 4, 4)
            layout.addWidget(text(word, wrap=False), 1)
            layout.addWidget(icon_button("delete", f"Remove {word}", lambda _=False, w=word: self._remove(w)))
            self.list.addWidget(line)

    def _add(self) -> None:
        new = [w.strip() for w in re.split(r"[,\n]", self.entry.text()) if w.strip()]
        if new:
            self.app.add_words(new)
            self.entry.clear()
            self.refresh()

    def _remove(self, word: str) -> None:
        self.app.remove_word(word)
        self.refresh()


# ---------------------------------------------------------------- Reading test

class ReadingTest(QWidget):
    """The reading test: read a set of sentences aloud, then see how many words each setup gets wrong (sst/evaluate.py)."""

    scored = Signal(object)  # from the scoring thread
    progressed = Signal(str)
    failed = Signal(str)
    restart = Signal()  # "New test"

    def __init__(self, recorder, score, add_words, microphone: str, folder: Path | None = None, block: str = "A"):
        super().__init__()
        self.recorder, self._score, self._add_words, self.microphone = recorder, score, add_words, microphone
        self.folder = folder or bench.BENCH_DIR / time.strftime("%Y-%m-%d_%H%M%S")
        self.report_folder = self.folder  # where the shown results were saved: this test, or 'summary' for all
        # A test already started keeps its set; a new one reads the set it was given.
        started = (self.folder / bench.SESSION_FILE).exists() or bool(self.recorded())
        self.block = bench.read_session(self.folder)["block"] if started else block
        self.sentences = bench.BLOCKS[self.block]
        self.index, self.recording = 0, False
        self.scored.connect(self._show_results)
        self.progressed.connect(lambda message: self.status.setText(message))
        self.failed.connect(self._score_failed)

        # page 1: reading
        purpose = "a practice set" if self.block in bench.TUNING else "a test set"
        intro = text(f"Read each sentence aloud the way you normally dictate. Set {self.block} of {len(bench.BLOCKS)} "
                     f"({purpose}), microphone: {microphone}. Rflow then counts the words it gets wrong, with and "
                     "without AI cleanup. About 6 minutes; you can stop and continue later.", muted=True)
        self.counter = text(wrap=False)
        self.sentence = text("", "sentence")
        self.sentence.setMinimumHeight(110)
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        self.level.setFixedHeight(6)
        self.status = text("Press Record (or Space), read the sentence, then Stop.")
        self.record_button = button("Record", self.toggle_recording, primary=True)
        self.redo_button = button("Redo", self.toggle_recording)
        self.back_button = button("Back", lambda: self.go(self.index - 1))
        self.next_button = button("Next", lambda: self.go(self.index + 1))
        self.score_button = button("Score", self.start_scoring)
        reading, reading_layout = card(12)
        for widget in (intro, self.counter, self.sentence, self.level, self.status):
            reading_layout.addWidget(widget)
        reading_layout.addStretch()
        reading_layout.addLayout(row(self.record_button, self.redo_button, self.back_button, self.next_button,
                                     self.score_button, stretch_at=4))

        # page 2: results
        self.report = QTextBrowser()
        self.report.setOpenExternalLinks(False)
        self.report.setMinimumHeight(200)
        self.suggestions = QListWidget()
        self.suggestions.setMaximumHeight(130)
        self.results_status = text("", muted=True)
        results, results_layout = card(10)
        results_layout.addWidget(self.report, 1)
        results_layout.addWidget(text("Worth adding to Your words (untick any you don't want):"))
        results_layout.addWidget(self.suggestions)
        add_row = row(button("Add to Your words", self._add_selected, primary=True), self.results_status)
        add_row.setStretch(1, 1)  # the note takes the rest of the line
        results_layout.addLayout(add_row)
        # Two rows: one would make the page wider than the window's smallest size.
        results_layout.addLayout(row(button("Score again", self.start_scoring),
                                     button("Score all tests", lambda: self.start_scoring(every=True)),
                                     button("Open folder", lambda: open_folder(self.report_folder)),
                                     button("New test", self.restart.emit), stretch_at=4))

        self.pages = QStackedWidget()
        self.pages.addWidget(reading)
        self.pages.addWidget(results)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.pages)
        # Only while the test has the focus: Space must still type spaces on the other pages.
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self._space,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.meter = QTimer(self)
        self.meter.setInterval(50)
        self.meter.timeout.connect(lambda: self.level.setValue(min(100, int(self.recorder.level * 400))))
        self.go(next(iter(self._to_read()), 0))  # a resumed test opens at the first sentence not read yet
        if self.recorded():
            self.status.setText(f"Continuing your unfinished test ({self.recorded()} of {len(self.sentences)} read).")

    def recorded(self) -> int:
        return len(bench.recordings(self.folder)) if self.folder.exists() else 0

    def _to_read(self) -> list[int]:
        return [i for i in range(len(self.sentences)) if not (self.folder / f"{i + 1:02}.wav").exists()]

    def go(self, index: int) -> None:
        if self.recording:
            return
        self.index = max(0, min(index, len(self.sentences) - 1))
        done = (self.folder / f"{self.index + 1:02}.wav").exists()
        self.counter.setText(f"Set {self.block}  ·  sentence {self.index + 1} of {len(self.sentences)}"
                             + ("  ·  recorded" if done else ""))
        self.sentence.setText(self.sentences[self.index])
        self.record_button.setEnabled(not done)
        self.redo_button.setEnabled(done)
        self.back_button.setEnabled(self.index > 0)
        self.next_button.setEnabled(self.index < len(self.sentences) - 1)
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
            for b in (self.redo_button, self.back_button, self.next_button, self.score_button):
                b.setEnabled(False)
            self.record_button.setEnabled(True)
            self.record_button.setText("Stop")
            self.status.setText("Recording... read the sentence, then press Stop (or Space).")
            return
        self.recording = False
        self.meter.stop()
        self.level.setValue(0)
        self.record_button.setText("Record")
        stop_later = getattr(self.recorder, "stop_later", None)
        take = stop_later() if stop_later else Take.ready(self.recorder.stop(), self.recorder.rate)
        if take.seconds < 0.5:
            self.status.setText("That was too short; press Record and read the sentence again.")
            self.go(self.index)
            return
        if take.done.is_set():
            self._save(take)
        else:  # the moment after Stop is still being recorded, as in dictation; don't hold up the window for it
            self.record_button.setEnabled(False)
            QTimer.singleShot(int(self.recorder.tail * 1000) + 50, lambda: self._save(take))

    def _save(self, take: Take) -> None:
        audio = take.audio()
        if not (self.folder / bench.SESSION_FILE).exists():  # which set, microphone and rate: for the report
            describe = getattr(self.recorder, "describe", None)
            bench.write_session(self.folder, self.block, self.microphone,
                                describe() if describe else {"rate": self.recorder.rate})
        stem = self.folder / f"{self.index + 1:02}"
        save_wav(stem.with_suffix(".wav"), audio, take.rate)
        stem.with_suffix(".txt").write_text(self.sentences[self.index], encoding="utf-8")
        remaining = self._to_read()
        if remaining:
            self.status.setText("Saved. Next sentence:")
            later = [i for i in remaining if i > self.index]
            self.go(later[0] if later else remaining[0])
        else:
            self.status.setText("All sentences recorded. Press Score.")
            self.go(self.index)

    def stop(self) -> None:
        """Leaving the page: don't leave the microphone open (not even warm)."""
        if self.recording:
            self.recording = False
            self.meter.stop()
            self.recorder.stop()
            self.record_button.setText("Record")
            self.status.setText("Recording stopped. Press Record to read the sentence again.")
            self.go(self.index)
        close = getattr(self.recorder, "close", None)
        if close:
            close()

    def start_scoring(self, every: bool = False) -> None:
        """This test, or every test of the profile together (their folders sit next to this one)."""
        if self.recording or not self.recorded():
            return
        folders = bench.sessions(self.folder.parent) if every else [self.folder]
        self.score_button.setEnabled(False)
        self.results_status.setText("Scoring...")
        self.status.setText("Scoring...")

        def work() -> None:
            try:
                self.scored.emit(self._score(folders, self.progressed.emit))
            except Exception as e:
                log.exception("Scoring the reading test failed")
                self.failed.emit(str(e))
        threading.Thread(target=work, name="reading-test-score", daemon=True).start()

    def _show_results(self, results) -> None:
        best = min(results.scores, key=lambda s: s.error_rate)
        rows = "".join(
            f"<tr><td>{'<b>' if s is best else ''}{s.name}{'</b>' if s is best else ''}</td>"
            f"<td align=right>{s.error_rate:.1%}</td><td align=right>{_interval(s.interval)}</td>"
            f"<td>{_verdict(s.difference)}</td><td align=right>{s.term_error_rate:.1%}</td>"
            f"<td align=right>{s.percentile(50):.2f} s</td></tr>" for s in results.scores)
        misheard = "".join(f"<li>{said or '<i>(extra word)</i>'} → {heard or '<i>(missed)</i>'} ({times}×)</li>"
                           for said, heard, times in results.misheard[:10])
        sessions = len({r.session for r in results.recordings})
        self.report.setHtml(
            f"<h3>{len(results.recordings)} sentences scored" + (f" from {sessions} tests" if sessions > 1 else "") + "</h3>"
            "<table cellpadding=5><tr><th align=left>Setup</th><th>Word errors</th><th>95% range</th>"
            f"<th align=left>Against the first</th><th>Names and terms</th><th>Time</th></tr>{rows}</table>"
            f"<p>Lowest error rate: <b>{best.name}</b>."
            + (" One set gives a wide range: read more sets and score all tests for a surer answer." if sessions == 1
               else "") + "</p>"
            + "".join(f"<p>⚠ {warning}</p>" for warning in microphone_warnings(results))
            + (f"<h4>Most misheard (by speech recognition)</h4><ul>{misheard}</ul>" if misheard else ""))
        self.suggestions.clear()
        for word in results.suggestions:
            item = QListWidgetItem(word)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.suggestions.addItem(item)
        # The folder name only: a full path would make the window wider than the screen allows for it.
        self.report_folder = self.folder.parent / "summary" if sessions > 1 else self.folder
        self.results_status.setText(f"Saved as report.md in the {self.report_folder.name} folder.")
        self.results_status.setToolTip(str(self.report_folder))
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


def _interval(interval) -> str:
    return f"{interval[0]:.0%}–{interval[1]:.0%}" if interval else ""


def _verdict(difference) -> str:
    """A setup against the first one: better or worse only when the whole 95% range says so."""
    if difference is None:
        return "the baseline"
    mean, low, high = (100 * x for x in difference)
    return f"{mean:+.1f} points, " + ("better" if high < 0 else "worse" if low > 0 else "no clear difference")


def microphone_warnings(results) -> list[str]:
    """Plain advice from the measured audio: phone-quality Bluetooth, clipping, a very quiet microphone."""
    out = []
    for microphone, indexes in results.microphones().items():
        flags = [flag for k in indexes for flag in results.recordings[k].stats.flags]
        if flags.count("narrowband") * 2 > len(indexes):
            out.append(f"<b>{microphone}</b> sounds like a phone call (nothing above 4 kHz), as a Bluetooth headset "
                       "does while its microphone is on. Speech recognition loses consonants there; the laptop's own "
                       "microphone is usually better.")
        if "clipped" in flags:
            out.append(f"<b>{microphone}</b> was too loud in {flags.count('clipped')} recording(s): lower its level in "
                       "Windows' sound settings.")
        if flags.count("quiet") * 2 > len(indexes):
            out.append(f"<b>{microphone}</b> is very quiet: speak closer to it or raise its level.")
    return out


class ReadingTestPage(Page):
    def __init__(self, app):
        super().__init__("Reading test", "How well does Rflow understand your voice, microphone and words? Read a "
                                         "set of 30 short sentences, then compare speech recognition alone and with "
                                         "AI cleanup. There are 5 sets; the more you read, the surer the numbers.")
        self.app = app
        self.test: ReadingTest | None = None
        self.holder = QVBoxLayout()
        self.add(self.holder)

    def ensure_test(self, folder: Path | None = None, new: bool = False) -> ReadingTest:
        if self.test is None or new:
            if self.test is not None:
                self.test.stop()
                self.test.deleteLater()
            microphone, root = self.app.settings.microphone, self.app.bench_dir()  # each profile has its own tests
            folder = None if new else folder or bench.unfinished(root)
            self.test = ReadingTest(self.app.new_recorder(), self.app.score_reading, self.app.add_words,
                                    microphone or "Windows default", folder=folder or root / time.strftime("%Y-%m-%d_%H%M%S"),
                                    block=bench.next_block(root))
            self.test.restart.connect(lambda: self.ensure_test(new=True))
            self.holder.addWidget(self.test)
        return self.test

    def showEvent(self, event):
        super().showEvent(event)
        self.ensure_test()

    def hideEvent(self, event):
        if self.test:
            self.test.stop()
        super().hideEvent(event)


# ---------------------------------------------------------------- AI cleanup

def _model_box(current: str, hint: str) -> QComboBox:
    box = QComboBox()
    box.setEditable(True)  # pick from the loaded list, or type any model name
    box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    box.lineEdit().setPlaceholderText(hint)
    if current:
        box.addItem(current)
    box.setCurrentText(current)
    return box


class CleanupPage(Page):
    def __init__(self, app):
        super().__init__("AI cleanup", "An AI model adds punctuation, removes filler words and spells your words right. "
                                       "Your voice stays on this computer; only the finished text goes to the provider.")
        self.app = app
        settings, gateway = app.settings, app.gateway
        # Each provider's (address, key, model, backup model) while the page is open, so switching back loses nothing.
        self._memory = {key: (url, secret, "", "") for key, (url, secret) in gateway.others.items()}
        # Nothing chosen yet: start with the first provider in the list rather than an empty custom server.
        self._provider = gateway.service.key if gateway.provider or gateway.base_url else next(iter(PROVIDERS))
        frame, layout = card(12)
        self.cleanup_on = QCheckBox("Clean up the text before typing it")
        self.cleanup_on.setChecked(settings.cleanup)
        self.cleanup_on.setToolTip("If the model fails, the backup model is used; if the provider can't help in time, "
                                   "the text is typed as heard.")
        layout.addWidget(self.cleanup_on)
        self.form = QFormLayout()
        self.form.setHorizontalSpacing(14)
        self.form.setVerticalSpacing(10)
        self.provider = QComboBox()
        for provider in PROVIDERS.values():
            self.provider.addItem(provider.name, provider.key)
        self.provider.setCurrentIndex(self.provider.findData(self._provider))
        self.form.addRow("Provider", self.provider)
        self.gateway_url = QLineEdit(gateway.base_url)
        self.form.addRow("Address", self.gateway_url)
        self.api_key = QLineEdit(gateway.api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.load_button = button("Load models", self._load_models)
        self.form.addRow("API key", row(self.api_key, self.load_button))
        self.key_link = button("Get a key", self._open_key_page, link=True)
        self.key_note = text("", muted=True)
        self.key_row = row(self.key_link, self.key_note, stretch_at=2)
        self.form.addRow("", self.key_row)
        self.model = _model_box(settings.cleanup_model, "")
        self.test_button = button("Test", self._test)
        model_row = row(self.model, self.test_button)
        model_row.setStretch(0, 1)
        self.form.addRow("Model", model_row)
        self.fallback = _model_box(settings.cleanup_fallback, "optional: used if the model fails")
        self.form.addRow("Backup model", self.fallback)
        layout.addLayout(self.form)
        self.test_result = text("", muted=True)
        layout.addWidget(self.test_result)
        self.add(frame)
        self.saved = text("", muted=True)
        self.add(row(button("Save", self._save, primary=True), self.saved, stretch_at=2))
        self.body.addStretch()
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self._show_provider()

    def _show_provider(self) -> None:
        """What the chosen provider needs: a key (cloud), an address (own server), or both."""
        p = PROVIDERS[self._provider]
        self.form.setRowVisible(self.gateway_url, p.own_server)
        self.gateway_url.setPlaceholderText(p.url or "e.g. http://localhost:8000/v1, or your company's AI gateway")
        self.api_key.setPlaceholderText("Encrypted on this computer" if p.needs_key
                                        else "Only if your server needs one (encrypted on this computer)")
        self.form.setRowVisible(self.key_row, bool(p.key_page))
        self.key_link.setVisible(bool(p.key_page))
        self.key_note.setText(f"from {p.name}" if p.key_page else "")
        self.model.lineEdit().setPlaceholderText(p.hint or "choose after Load models, or type a model name")

    def _provider_changed(self) -> None:
        new = self.provider.currentData()
        self._memory[self._provider] = (self.gateway_url.text().strip(), self.api_key.text().strip(),
                                        self.model.currentText().strip(), self.fallback.currentText().strip())
        p = PROVIDERS[new]
        url, key, model, fallback = self._memory.get(new, (p.url if p.own_server else "", "", "", ""))
        self._provider = new
        self.gateway_url.setText(url)
        self.api_key.setText(key)
        for box, value in ((self.model, model), (self.fallback, fallback)):
            box.clear()
            box.setCurrentText(value)
        self.test_result.setText("")
        self._show_provider()

    def _open_key_page(self) -> None:
        QDesktopServices.openUrl(QUrl(PROVIDERS[self._provider].key_page))

    def result(self) -> tuple[bool, str, str, GatewayConfig]:
        p = PROVIDERS[self._provider]
        others = {key: (url, secret) for key, (url, secret, _, _) in self._memory.items() if key != p.key and (url or secret)}
        gateway = GatewayConfig(self.gateway_url.text().strip() if p.own_server else "", self.api_key.text().strip(),
                                p.key, others)
        return self.cleanup_on.isChecked(), self.model.currentText().strip(), self.fallback.currentText().strip(), gateway

    def _save(self) -> None:
        on, model, fallback, gateway = self.result()
        self.app.save_cleanup(on, model, fallback, gateway)
        self.saved.setText("Saved. Active from the next dictation." if on and model else "Saved. AI cleanup is off.")

    def _busy(self, busy: bool, message: str = "") -> None:
        self.load_button.setEnabled(not busy)
        self.test_button.setEnabled(not busy)
        if message:
            self.test_result.setText(message)

    def _load_models(self) -> None:
        gateway = self.result()[3]
        if not gateway.address:
            self.test_result.setText("Enter your server's address first.")
            return
        self._busy(True, "Loading models...")

        def done(models, error) -> None:
            self._busy(False)
            if error:
                self.test_result.setText(f"Failed: {error}")
                return
            for box in (self.model, self.fallback):
                current = box.currentText()
                box.clear()
                if box is self.fallback:
                    box.addItem("")  # no backup model
                box.addItems(models)
                box.setCurrentText(current)
            self.test_result.setText(f"Loaded {len(models)} models: choose one, then Test." if models
                                     else "The provider lists no models; type a model name.")
        run_in_background(self, lambda: Polisher(gateway, "").models(), done)

    def _test(self) -> None:
        on, model, _, gateway = self.result()
        if not model:
            self.test_result.setText("Choose a model first (Load models).")
            return
        self._busy(True, "Testing...")
        words = self.app.settings.vocabulary

        def done(answer, error) -> None:
            self._busy(False)
            self.test_result.setText(f"Failed: {error}" if error else f"OK: {answer}")
        run_in_background(self, lambda: Polisher(gateway, model, words).check(), done)


# ---------------------------------------------------------------- Settings

class SettingsPage(Page):
    def __init__(self, app):
        super().__init__("Settings")
        self.app = app
        s: Settings = app.settings

        dictation, layout = card()
        layout.addWidget(text("Dictation key", "h2"))
        self.hotkey = QComboBox()
        for label, value in HOTKEY_CHOICES:
            self.hotkey.addItem(label, value)
        if self.hotkey.findData(s.hotkey) < 0:
            self.hotkey.addItem(s.hotkey, s.hotkey)  # a custom one set with --hotkey or by hand
        self.hotkey.setCurrentIndex(self.hotkey.findData(s.hotkey))
        layout.addWidget(self.hotkey)
        layout.addWidget(text("Hold to talk, tap for hands-free. With Ctrl+Win, Ctrl+Win+Space is hands-free as well.",
                              muted=True))
        self.add(dictation)

        microphone, layout = card()
        layout.addWidget(text("Microphone", "h2"))
        self.microphone = MicrophoneBox(s.microphone, app.microphones())
        layout.addWidget(self.microphone)
        self.call_warning = text("This is a Bluetooth headset's microphone. It records in call quality (like a phone), "
                                 "so Rflow gets more words wrong, and your headset plays sound in call quality while it "
                                 "is open. The laptop's own microphone is usually clearer.", "warning")
        layout.addWidget(self.call_warning)
        self.warm_mic = QCheckBox("Keep the microphone ready for 5 minutes after dictating")
        self.warm_mic.setChecked(s.warm_mic)
        self.warm_mic.setToolTip("Dictation then starts at once and keeps the moment before you pressed the key, so "
                                 "first words aren't cut off. Windows shows the microphone icon meanwhile; nothing is "
                                 "recorded or sent until you press the key. Never done for Bluetooth headsets.")
        self.raw_audio = QCheckBox("Turn off Windows' voice effects for this microphone")
        self.raw_audio.setChecked(s.raw_audio)
        self.raw_audio.setToolTip("Records the microphone as it is, without Windows' or the driver's noise suppression "
                                  "and gain. Try it with the Reading test: it may help or hurt, depending on the "
                                  "microphone and the room.")
        layout.addWidget(self.warm_mic)
        layout.addWidget(self.raw_audio)
        self.add(microphone)
        self._show_call_warning()

        behaviour, layout = card()
        layout.addWidget(text("While dictating", "h2"))
        self.sounds = QCheckBox("Beep when recording starts and stops")
        self.sounds.setChecked(s.sounds)
        self.save_recordings = QCheckBox("Keep recordings (audio and text) on this laptop")
        self.save_recordings.setChecked(s.save_recordings)
        self.start_with_windows = QCheckBox("Start Rflow when I sign in to Windows")
        self.start_with_windows.setChecked(starts_with_windows())
        self.start_with_windows.setEnabled(can_start_with_windows())
        if not can_start_with_windows():
            self.start_with_windows.setToolTip("Available in the installed app")
        layout.addWidget(self.sounds)
        layout.addLayout(row(self.save_recordings, button("Open folder", lambda: open_folder(RECORDINGS_DIR)),
                             stretch_at=1))
        layout.addWidget(self.start_with_windows)
        self.add(behaviour)

        about, layout = card()
        layout.addWidget(text(f"{APP_NAME} {__version__}", "h2"))
        self.update_status = text("Rflow checks for updates by itself and tells you when one is ready.", muted=True)
        layout.addWidget(self.update_status)
        layout.addLayout(row(button("Check for updates", lambda: app.check_for_updates(manual=True)),
                             button("Open logs folder", lambda: open_folder(LOG_DIR)), stretch_at=2))
        layout.addLayout(row(button("Website", lambda: QDesktopServices.openUrl(QUrl(WEBSITE)), link=True),
                             button("Report a problem", lambda: QDesktopServices.openUrl(QUrl(REPO + "/issues")), link=True),
                             stretch_at=2))
        self.add(about)
        self.body.addStretch()

        self.hotkey.currentIndexChanged.connect(self._apply)
        self.microphone.changed.connect(self._apply)
        self.microphone.changed.connect(self._show_call_warning)
        self.warm_mic.toggled.connect(self._apply)
        self.raw_audio.toggled.connect(self._apply)
        self.sounds.toggled.connect(self._apply)
        self.save_recordings.toggled.connect(self._apply)
        self.start_with_windows.toggled.connect(lambda on: set_start_with_windows(on) if can_start_with_windows() else None)

    def result(self, current: Settings) -> Settings:
        """The current settings with this page's choices (the other pages own the rest)."""
        return dataclasses.replace(current, hotkey=self.hotkey.currentData(), microphone=self.microphone.device(),
                                   sounds=self.sounds.isChecked(), save_recordings=self.save_recordings.isChecked(),
                                   warm_mic=self.warm_mic.isChecked(), raw_audio=self.raw_audio.isChecked())

    def _show_call_warning(self, *_) -> None:
        self.call_warning.setVisible(call_quality(self.microphone.device() or None))

    def _apply(self, *_) -> None:
        self.app.apply_settings(self.result(self.app.settings))  # changes apply at once, like a phone's settings


# ---------------------------------------------------------------- the first-run welcome

class WelcomePage(Page):
    def __init__(self, app, go_to):
        super().__init__("Welcome to Rflow", "Speak anywhere, Rflow types it. Your voice is recognised on this computer "
                                             "and never uploaded. A few quick steps:")
        self.app = app
        self.go_to = go_to
        step0, layout = card()
        layout.addWidget(text("1   Your name", "h2"))
        self.name = QLineEdit(app.profiles.current.name)
        self.name.setPlaceholderText("What should Rflow call you? (optional)")
        layout.addWidget(self.name)
        self.add(step0)

        step1, layout = card()
        layout.addWidget(text("2   Choose your microphone", "h2"))
        self.microphone = MicrophoneBox(app.settings.microphone, app.microphones())
        self.microphone.changed.connect(self._microphone_chosen)
        layout.addWidget(self.microphone)
        self.add(step1)

        step2, layout = card()
        layout.addWidget(text("3   Try it", "h2"))
        self.try_text = text("", muted=True)
        layout.addWidget(self.try_text)
        self.try_box = QPlainTextEdit()
        self.try_box.setPlaceholderText("Click here first. Your words will appear here.")
        self.try_box.setFixedHeight(84)
        layout.addWidget(self.try_box)
        self.status = text("", muted=True)
        layout.addWidget(self.status)
        self.add(step2)

        step3, layout = card()
        layout.addWidget(text("4   Optional: AI cleanup", "h2"))
        layout.addWidget(text("Connect an AI model to add punctuation, remove filler words and spell your names "
                              "right. You can do this later too.", muted=True))
        layout.addLayout(row(button("Set up AI cleanup", self._to_cleanup), stretch_at=1))
        self.add(step3)
        self.add(row(button("Start using Rflow", self.finish, primary=True), stretch_at=1))
        self.body.addStretch()

    def refresh(self, ready: bool) -> None:
        label = self.app.hotkey_label()
        self.try_text.setText(f"Click in the box below, hold {label}, say \"Hello Rflow, this is my first dictation\", "
                              "then let go.")
        self.status.setText("Ready: go ahead." if ready else "Loading the speech model (a few seconds)...")

    def _microphone_chosen(self, device: str) -> None:
        self.app.apply_settings(dataclasses.replace(self.app.settings, microphone=device))

    def _to_cleanup(self) -> None:
        self.finish()
        self.go_to("cleanup")

    def finish(self) -> None:
        self.app.finish_welcome(self.name.text())
        self.go_to("home")


# ---------------------------------------------------------------- profiles

class ProfilesPage(Page):
    """People sharing this computer: each profile has its own setup."""

    def __init__(self, app):
        super().__init__("Profiles", "Each profile has its own dictation key and microphone, words, AI provider and "
                                     "keys, dictations, stats and reading tests. Useful when several people share "
                                     "this computer, or to keep a work and a private setup apart. Click a name to "
                                     "change it.")
        self.app = app
        self.list = QVBoxLayout()
        self.list.setSpacing(10)
        self.add(self.list)
        new, layout = card()
        layout.addWidget(text("New profile", "h2"))
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("Name, e.g. Rahul")
        self.new_name.returnPressed.connect(self._create)
        layout.addLayout(row(self.new_name, button("Create and switch to it", self._create, primary=True)))
        layout.addWidget(text("A new profile starts with the welcome: microphone, a first dictation, AI cleanup.",
                              muted=True))
        self.add(new)
        self.body.addStretch()

    def refresh(self) -> None:
        clear(self.list)
        current = self.app.profiles.current
        for profile in self.app.profiles.items:
            frame, layout = card(6)
            name = QLineEdit(profile.name)
            name.setPlaceholderText(profile.label)
            name.setToolTip("Type to rename, then press Enter")
            name.editingFinished.connect(lambda p=profile, box=name: self.app.rename_profile(p.id, box.text()))
            buttons = []
            if profile.id == current.id:
                buttons.append(text("In use", muted=True, wrap=False))
            else:
                buttons.append(button("Switch to this profile", lambda _=False, p=profile: self.app.switch_profile(p.id)))
                if profile.id != "default":  # the first profile's files are the settings folder itself
                    buttons.append(button("Delete", lambda _=False, p=profile: self._delete(p)))
            layout.addLayout(row(name, *buttons))
            self.list.addWidget(frame)

    def _create(self) -> None:
        name = self.new_name.text().strip()
        if name:
            self.new_name.clear()
            self.app.create_profile(name)

    def _delete(self, profile) -> None:
        if self.confirm(f"Delete the profile {profile.label}, with its words, keys, dictations, stats and reading tests?"):
            self.app.delete_profile(profile.id)
            self.refresh()

    def confirm(self, question: str) -> bool:
        return QMessageBox.question(self, APP_NAME, question) == QMessageBox.StandardButton.Yes


# ---------------------------------------------------------------- the window

NAV = [("home", "Home"), ("dictionary", "Dictionary"), ("reading", "Reading test"), ("cleanup", "AI cleanup"),
       ("settings", "Settings"), ("profiles", "Profiles")]


class MainWindow(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setObjectName("root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(ICON_FILE)))
        self.resize(1000, 700)
        self.setMinimumSize(780, 540)
        self.ready = False

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(214)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 18, 14, 16)
        side.setSpacing(4)
        logo = QLabel()
        logo.setPixmap(QIcon(str(ICON_FILE)).pixmap(28, 28))
        brand = row(logo, text(APP_NAME, "brand", wrap=False), stretch_at=2)
        brand.setContentsMargins(6, 0, 0, 12)
        side.addLayout(brand)
        self.profile_button = QPushButton()  # whose setup this is; a click switches to another profile
        self.profile_button.setObjectName("profile")
        self.profile_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.profile_button.setToolTip("Switch profile")
        self.profile_button.clicked.connect(self._profile_menu)
        side.addWidget(self.profile_button)
        side.addSpacing(10)
        self.nav: dict[str, QPushButton] = {}
        for key, label in NAV:
            b = QPushButton(f"{GLYPHS[key]}    {label}")
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.show_page(k))
            self.nav[key] = b
            side.addWidget(b)
        side.addStretch()
        self.update_link = button("", lambda: app.start_update(), link=True)
        self.update_link.hide()
        side.addWidget(self.update_link)
        self.status_label = text("", muted=True)
        side.addWidget(self.status_label)
        side.addWidget(text(f"Version {__version__}", muted=True, wrap=False))

        self.banner = QFrame()
        self.banner.setObjectName("banner")
        banner = QHBoxLayout(self.banner)
        banner.setContentsMargins(16, 10, 12, 10)
        self.banner_text = text()
        self.notes_button = button("What's new", lambda: app.open_release_notes())
        self.update_button = button("Update now", lambda: app.start_update())
        banner.addWidget(self.banner_text, 1)
        banner.addWidget(self.notes_button)
        banner.addWidget(self.update_button)
        self.banner.hide()

        self.pages = {"home": HomePage(app), "dictionary": DictionaryPage(app, self.show_page),
                      "reading": ReadingTestPage(app), "cleanup": CleanupPage(app), "settings": SettingsPage(app),
                      "profiles": ProfilesPage(app), "welcome": WelcomePage(app, self.show_page)}
        self.stack = QStackedWidget()
        for page in self.pages.values():
            self.stack.addWidget(page)
        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        banner_holder = QVBoxLayout()
        banner_holder.setContentsMargins(24, 14, 24, 0)
        banner_holder.addWidget(self.banner)
        content.addLayout(banner_holder)
        content.addWidget(self.stack, 1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(sidebar)
        layout.addLayout(content, 1)

        self._font_for_icons()
        self.apply_theme()
        QGuiApplication.styleHints().colorSchemeChanged.connect(self.apply_theme)
        self.show_page("home" if app.settings.welcomed else "welcome")

    def _font_for_icons(self) -> None:
        # The nav buttons mix the icon font with Segoe UI: listing both lets Qt take each character from the right one.
        font = QFont()
        font.setFamilies(["Segoe UI", *ICON_FONTS])
        font.setPointSize(10)
        for b in (*self.nav.values(), self.update_link, self.profile_button):
            b.setFont(font)

    def apply_theme(self, *_) -> None:
        self.setStyleSheet(stylesheet("dark" if dark_mode() else "light"))

    def show_page(self, key: str) -> None:
        page = self.pages[key]
        self._show_profile()
        if key in ("home", "dictionary", "profiles"):
            page.refresh()
        elif key == "welcome":
            page.refresh(self.ready)
        self.stack.setCurrentWidget(page)
        if key in self.nav:
            self.nav[key].setChecked(True)
        else:
            for b in self.nav.values():  # the welcome isn't in the sidebar
                b.setAutoExclusive(False)
                b.setChecked(False)
                b.setAutoExclusive(True)

    def _show_profile(self) -> None:
        self.profile_button.setText(f"{GLYPHS['profile']}   {self.app.profiles.current.label}")

    def _profile_menu(self) -> None:
        menu = QMenu(self)
        current = self.app.profiles.current
        for profile in self.app.profiles.items:
            action = menu.addAction(profile.label, lambda p=profile: self.app.switch_profile(p.id))
            action.setCheckable(True)
            action.setChecked(profile.id == current.id)
        menu.addSeparator()
        menu.addAction("New profile...", self._new_profile)
        menu.addAction("Manage profiles", lambda: self.show_page("profiles"))
        menu.exec(self.profile_button.mapToGlobal(self.profile_button.rect().bottomLeft()))

    def _new_profile(self) -> None:
        self.show_page("profiles")
        self.pages["profiles"].new_name.setFocus()

    def current_page(self) -> str:
        return next(key for key, page in self.pages.items() if page is self.stack.currentWidget())

    def open(self, page: str | None = None) -> None:
        if not self.app.settings.welcomed:
            page = "welcome"
        self.show_page(page or self.current_page())
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh(self) -> None:
        """New dictation, words, settings or profile name: update what is on screen."""
        self._show_profile()
        current = self.current_page()
        if current in ("home", "dictionary", "profiles"):
            self.pages[current].refresh()
        elif current == "welcome":
            self.pages[current].refresh(self.ready)

    def set_status(self, message: str, ready: bool) -> None:
        self.ready = ready
        self.status_label.setText(message)
        if self.current_page() == "welcome":
            self.pages["welcome"].refresh(ready)

    def show_update(self, message: str, busy: bool = False, version: str = "") -> None:
        self.banner_text.setText(message)
        self.update_button.setEnabled(not busy)
        self.banner.show()
        if version:
            self.update_link.setText(f"{GLYPHS['update']}  Update to {version}")
            self.update_link.show()

    def set_update_status(self, message: str) -> None:
        self.pages["settings"].update_status.setText(message)

    def closeEvent(self, event):
        # Closing the window doesn't quit: dictation keeps working from the tray, like Wispr Flow.
        event.ignore()
        self.hide()  # hiding also stops a reading-test recording and the level meters (their hideEvent)
        self.app.window_closed()


# ---------------------------------------------------------------- a stand-in for the TrayApp

class PreviewApp:
    """Stands in for the TrayApp: the self-test, the tests and the website's screenshots use it (no model, no hook)."""

    def __init__(self, settings: Settings | None = None, history: list[dict] | None = None, stats: Stats | None = None,
                 microphones: list[str] | None = None, gateway: GatewayConfig | None = None,
                 profiles: Profiles | None = None, bench: Path | None = None):
        self.settings = settings or Settings(welcomed=True)
        self.profiles = profiles or Profiles()
        self._bench = bench or Path(os.environ.get("TEMP", ".")) / "rflow-preview-bench"
        self.gateway = gateway or GatewayConfig()
        self.history = history or []
        self.stats = stats or Stats()
        self._microphones = microphones if microphones is not None else ["Microphone (Realtek(R) Audio)"]
        self.calls: list[tuple] = []  # what the window asked for

    def hotkey_label(self) -> str:
        return parse_hotkey(self.settings.hotkey).label

    def history_entries(self) -> list[dict]:
        return self.history

    def microphones(self) -> list[str]:
        return self._microphones

    def new_recorder(self):
        from sst.audio import Recorder
        return Recorder(self.settings.microphone or None, raw=self.settings.raw_audio)

    def bench_dir(self) -> Path:
        return self.profiles.current.folder(self._bench)

    def apply_settings(self, new: Settings) -> None:
        self.settings = new
        self.calls.append(("apply_settings", new))

    def save_cleanup(self, on: bool, model: str, fallback: str, gateway: GatewayConfig) -> None:
        self.settings.cleanup, self.settings.cleanup_model, self.settings.cleanup_fallback = on, model, fallback
        self.gateway = gateway
        self.calls.append(("save_cleanup", on, model, fallback, gateway))

    def add_words(self, words: list[str]) -> int:
        known = {w.lower() for w in self.settings.vocabulary}
        added = [w for w in dict.fromkeys(words) if w.lower() not in known]
        self.settings.vocabulary = self.settings.vocabulary + added
        return len(added)

    def remove_word(self, word: str) -> None:
        self.settings.vocabulary = [w for w in self.settings.vocabulary if w != word]

    def score_reading(self, folders, progress):
        raise RuntimeError("No speech model in the preview.")

    def finish_welcome(self, name: str = "") -> None:
        if name.strip():
            self.profiles.current.name = name.strip()
        self.settings.welcomed = True
        self.calls.append(("finish_welcome", name))

    def switch_profile(self, profile_id: str) -> None:
        self.profiles.active = profile_id
        self.calls.append(("switch_profile", profile_id))

    def create_profile(self, name: str) -> None:
        self.switch_profile(self.profiles.add(name).id)

    def rename_profile(self, profile_id: str, name: str) -> None:
        if name.strip():
            self.profiles.get(profile_id).name = name.strip()

    def delete_profile(self, profile_id: str) -> None:
        self.profiles.remove(profile_id)
        self.calls.append(("delete_profile", profile_id))

    def window_closed(self) -> None:
        self.calls.append(("window_closed",))

    def check_for_updates(self, manual: bool = False) -> None:
        self.calls.append(("check_for_updates", manual))

    def start_update(self) -> None:
        self.calls.append(("start_update",))

    def open_release_notes(self) -> None:
        self.calls.append(("open_release_notes",))
