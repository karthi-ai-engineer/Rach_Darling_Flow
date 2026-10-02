"""Translate (the owner's idea of 2026-10-02, like DeepL): select text in any app, press Ctrl+C twice, read it translated.

  Ctrl+C+C (the shortcut)   the app copies as usual; Rflow reads that copy, presses nothing, and opens a small popup
                            at the pointer: the language at the top, the translation below (sst.translate)
  the language list         translates again into the language picked, and remembers it
  Copy                      the translation on the clipboard
  Replace                   the translation in place of the selected text (its window brought back first, like Text
                            Transform; if the text isn't there any more, the translation goes on the clipboard)
  Esc, x, another window    closes the popup

The popup never takes the keyboard focus, so the app keeps its selection; Esc is taken from the keyboard hook while the
popup is open. Another shortcut (not a double copy) copies the selection itself, with Ctrl+Insert, never in a terminal.
"""
import dataclasses
import logging
import os
import threading
import time

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sst.hotkey import VK_ESCAPE, HotkeyListener, parse_hotkey
from sst.transformui import TERMINALS, place
from sst.translate import LANGUAGES, MAX_CHARS, Translation

COPY_WAIT = 0.4  # seconds the app gets to copy after the second Ctrl+C, before the clipboard is read as it is

log = logging.getLogger(__name__)

_STYLE = """
#popup { background: rgba(24, 24, 32, 246); border: 1px solid rgba(255, 255, 255, 40); border-radius: 12px; }
QLabel { color: #F5F5FA; background: transparent; }
QLabel#muted { color: #A8A8B8; }
QLabel#warning { color: #FBBF24; }
QTextBrowser { color: #F5F5FA; background: transparent; border: none; font-size: 11pt; }
QComboBox { color: #F5F5FA; background: rgba(255, 255, 255, 18); border: 1px solid rgba(255, 255, 255, 40);
            border-radius: 6px; padding: 2px 8px; }
QComboBox QAbstractItemView { color: #F5F5FA; background: #20202A; selection-background-color: #3B82F6; }
QPushButton { color: #FFFFFF; background: rgba(255, 255, 255, 22); border: none; border-radius: 6px; padding: 5px 14px; }
QPushButton:hover { background: rgba(255, 255, 255, 40); }
QPushButton#primary { background: #3B82F6; }
QPushButton#primary:hover { background: #2563EB; }
QPushButton:disabled { color: #77778A; background: rgba(255, 255, 255, 10); }
QToolButton { color: #A8A8B8; background: transparent; border: none; font-size: 12pt; }
QToolButton:hover { color: #FFFFFF; }
"""


class TranslatePopup(QWidget):
    """The small panel at the pointer: the language, the text, its translation, Copy and Replace. It never takes focus."""

    language = Signal(str)  # a language picked in the list
    copy = Signal()
    replace = Signal()
    closed = Signal()

    WIDTH = 440
    RESULT_MAX = 300

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(_STYLE)
        self.setFixedWidth(self.WIDTH)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setObjectName("popup")
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel("Translate into")
        title.setStyleSheet("font-weight: 600;")
        head.addWidget(title)
        self.languages = QComboBox()
        self.languages.addItems(list(LANGUAGES))
        self.languages.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.languages.activated.connect(lambda i: self.language.emit(self.languages.itemText(i)))
        head.addWidget(self.languages)
        head.addStretch()
        self.close_button = QToolButton()
        self.close_button.setText("✕")
        self.close_button.setToolTip("Close (Esc)")
        self.close_button.clicked.connect(self.close_popup)
        head.addWidget(self.close_button)
        layout.addLayout(head)
        self.original = QLabel()
        self.original.setObjectName("muted")
        self.original.setWordWrap(True)
        layout.addWidget(self.original)
        self.result = QTextBrowser()
        self.result.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.result.setFixedHeight(44)
        self.result.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.result)
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.copy_button = QPushButton("Copy")
        self.copy_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.copy_button.clicked.connect(self.copy.emit)
        self.replace_button = QPushButton("Replace")
        self.replace_button.setObjectName("primary")
        self.replace_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.replace_button.setToolTip("Put the translation in place of the selected text")
        self.replace_button.clicked.connect(self.replace.emit)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.replace_button)
        layout.addLayout(buttons)

    def open_at(self, pos: QPoint, source: str, target: str) -> None:
        shown = " ".join(source.split())
        self.original.setText(shown if len(shown) <= 160 else shown[:157].rstrip() + "…")
        self.busy(target)
        self.adjustSize()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        height = max(self.sizeHint().height(), 180)
        x = min(max(area.left(), pos.x() + 12), area.right() - self.WIDTH)
        y = pos.y() + 18 if pos.y() + 18 + height <= area.bottom() else pos.y() - height - 10
        self.move(x, max(area.top(), y))
        self.show()
        try:
            from sst.app import _no_activate  # clicks never take the app's focus: its selection stays for Replace
            _no_activate(int(self.winId()))
        except Exception:  # the off-screen test platform has no real window
            pass

    def _fit(self) -> None:
        """The translation box as tall as its text (a short one stays short), at most RESULT_MAX; then it scrolls."""
        self.result.ensurePolished()  # the style sheet's font, which the height depends on
        document = self.result.document()
        document.setDefaultFont(self.result.font())
        document.setTextWidth(self.WIDTH - 32 - 2 * self.result.frameWidth())
        height = document.size().height() + 2 * self.result.frameWidth() + 4
        self.result.setFixedHeight(int(min(max(height, 44), self.RESULT_MAX)))
        self.adjustSize()

    def busy(self, target: str) -> None:
        self.languages.setCurrentText(target)
        self.result.setPlainText("")
        self._fit()
        self.result.setPlaceholderText(f"Translating into {target}…")
        self.status.setText("")
        self.status.setObjectName("muted")
        self._buttons(False)

    def show_result(self, translation: Translation, model: str = "") -> None:
        self.languages.setCurrentText(translation.target)
        self.result.setPlainText(translation.text)
        details = [f"{translation.seconds:.1f} s"] + ([model] if model else [])
        self.status.setObjectName("warning" if translation.warnings else "muted")
        self.status.setText("; ".join(translation.warnings) if translation.warnings else " · ".join(details))
        self.status.setStyleSheet("")  # the object name changed: the style sheet applies again
        self._buttons(True)
        self._fit()

    def show_error(self, message: str) -> None:
        self.result.setPlainText("")
        self.result.setPlaceholderText("")
        self.status.setObjectName("warning")
        self.status.setText(message)
        self.status.setStyleSheet("")
        self._buttons(False)

    def _buttons(self, on: bool) -> None:
        self.copy_button.setEnabled(on)
        self.replace_button.setEnabled(on)

    def close_popup(self) -> None:
        if self.isVisible():
            self.hide()
            self.closed.emit()


class TranslateController(QObject):
    """The shortcut, the copied text, the popup and the translation. `app` is the TrayApp (or a stand-in in the tests):
    settings, `translate_ready()`, `run_translation(text, target, second) -> Translation`, `transform_model()`,
    `say(state, message)` and `apply_settings(settings)`. `access` is sst.textaccess (fakes in the tests)."""

    _got = Signal(object)  # (text, window) or why there is none (str), from the reading thread
    _done = Signal(object)  # (request number, Translation or error str), from the translating thread
    _placed = Signal(str)  # what happened to Replace, from the pasting thread

    def __init__(self, app, access=None, listener_factory=HotkeyListener):
        super().__init__()
        if access is None:
            from sst import textaccess as access
        self.app, self.access, self._listener_factory = app, access, listener_factory
        self.listener: HotkeyListener | None = None
        self.double_copy = False  # the shortcut is a double copy: the app copies, Rflow reads
        self.popup = TranslatePopup()
        self.popup.language.connect(self._pick)
        self.popup.copy.connect(self._copy)
        self.popup.replace.connect(self._replace)
        self.popup.closed.connect(self._closed)
        self._got.connect(self._show)
        self._done.connect(self._finish)
        self._placed.connect(self._after_replace)
        self.pump = QTimer(self)
        self.pump.setInterval(15)
        self.pump.timeout.connect(self._pump)
        self.reading = False  # the copied text is being read
        self.request = 0  # each translation's number: an answer to an older one is dropped
        self.source: tuple[str, int] | None = None  # (the text, its window) the popup is for
        self.result: Translation | None = None

    def start(self, shortcut: str) -> None:
        """Listen for the shortcut ("" = Translate off)."""
        self.stop()
        if not shortcut:
            return
        try:
            hotkey = parse_hotkey(shortcut)
            self.listener = self._listener_factory(hotkey)
            self.listener.start()
        except (ValueError, OSError) as e:
            log.warning("Translate's shortcut %r doesn't work: %s", shortcut, e)
            self.listener = None
            return
        self.double_copy = hotkey.double and hotkey.key is not None
        self.pump.start()
        log.info("Translate on %s", shortcut)

    def stop(self) -> None:
        self.pump.stop()
        self.popup.close_popup()
        if self.listener is not None:
            self.listener.stop()
            self.listener = None

    @property
    def busy(self) -> bool:
        return self.reading

    def _pump(self) -> None:
        listener = self.listener
        if listener is not None:
            while not listener.events.empty():
                event, _ = listener.events.get_nowait()
                if event == "release":
                    self.trigger()
                elif event == f"key:{VK_ESCAPE}":
                    self.popup.close_popup()
        if self.popup.isVisible() and self.source:
            front = self.access.foreground_window()
            if front not in (self.source[1], 0) and self.access.window_process(front) != os.getpid():
                self.popup.close_popup()  # the user went to another app: the popup was for the text left behind
                # (Rflow's own windows, like the language list, don't count)

    def trigger(self) -> None:
        if self.reading:
            return
        if not self.app.translate_ready():
            self.app.say("warning", "Translate needs an AI model: choose one in AI cleanup.")
            return
        self.reading = True
        hwnd, before = self.access.foreground_window(), self.access.clipboard_sequence()
        threading.Thread(target=self._read, args=(hwnd, before), name="translate-read", daemon=True).start()

    def _read(self, hwnd: int, before: int) -> None:
        """The text: what the app copied for the double Ctrl+C (read as soon as it lands, before any clipboard tool
        rewrites it), or the selection copied by Rflow for another shortcut."""
        try:
            if self.double_copy:
                end = time.monotonic() + COPY_WAIT
                while self.access.clipboard_sequence() == before and time.monotonic() < end:
                    time.sleep(0.005)
                text = self.access.clipboard_text()
            elif self.access.window_class(hwnd) in TERMINALS:
                text = None
                self._got.emit("In a terminal, select the text and press Ctrl+C twice.")
                return
            else:
                text = self.access.copy_selection()
        except Exception as e:  # a closed window, a busy clipboard: say so, never leave Translate stuck
            log.exception("Reading the text to translate failed")
            self._got.emit(f"Couldn't read the text ({e})")
            return
        self._got.emit((text, hwnd) if text and text.strip() else "Select some text first, then try again.")

    def _show(self, payload) -> None:
        self.reading = False
        if isinstance(payload, str):
            self.app.say("warning", payload)
            return
        text, hwnd = payload
        if len(text) > MAX_CHARS:
            self.app.say("warning", f"That's {len(text):,} characters: Translate takes up to {MAX_CHARS:,} at a time.")
            return
        self.source, self.result = (text, hwnd), None
        s = self.app.settings
        self.popup.open_at(QCursor.pos(), text, s.translate_to)
        if self.listener is not None:
            self.listener.capture(frozenset({VK_ESCAPE}))
        self._translate(s.translate_to, s.translate_second)

    def _translate(self, target: str, second: str) -> None:
        self.request += 1
        request, (text, _) = self.request, self.source
        self.popup.busy(target)

        def work() -> None:
            try:
                self._done.emit((request, self.app.run_translation(text, target, second)))
            except Exception as e:  # no answer, no key, a refusal: shown in the popup
                log.warning("Translate failed: %s", e)
                self._done.emit((request, str(e) or type(e).__name__))
        threading.Thread(target=work, name="translate", daemon=True).start()

    def _finish(self, payload) -> None:
        request, result = payload
        if request != self.request or not self.popup.isVisible():
            return  # a newer translation was asked for, or the popup was closed
        if isinstance(result, str):
            self.popup.show_error(f"Couldn't translate: {result}")
            return
        self.result = result
        self.popup.show_result(result, self.app.transform_model())

    def _pick(self, language: str) -> None:
        """A language picked in the popup: translate into it (exactly that one), and remember it."""
        if self.source is None:
            return
        if language != self.app.settings.translate_to:
            self.app.apply_settings(dataclasses.replace(self.app.settings, translate_to=language))
        self._translate(language, "")

    def _copy(self) -> None:
        if self.result is None:
            return
        try:
            self.access.set_clipboard(self.result.text)
            self.app.say("transformed", "Translation copied")
        except OSError as e:
            self.app.say("warning", f"Couldn't copy the translation ({e})")
        self.popup.close_popup()

    def _replace(self) -> None:
        if self.result is None or self.source is None:
            return
        text, hwnd = self.source
        translation = self.result.text
        self.popup.close_popup()
        self.reading = True  # the shortcut waits until the paste is done

        def work() -> None:
            try:
                if place(self.access, text, hwnd):
                    self.access.paste_rich(translation)
                    self._placed.emit("")
                else:  # never paste over something else
                    self.access.set_clipboard(translation)
                    self._placed.emit("Your text wasn't where it was any more, so the translation is on the clipboard: "
                                      "press Ctrl+V.")
            except Exception as e:
                log.exception("Pasting the translation failed")
                self._placed.emit(f"Couldn't type the translation ({e})")
        threading.Thread(target=work, name="translate-paste", daemon=True).start()

    def _after_replace(self, message: str) -> None:
        self.reading = False
        if message:
            self.app.say("warning", message)
        else:
            self.app.say("transformed", "Translated")

    def _closed(self) -> None:
        if self.listener is not None:
            self.listener.capture(None)
