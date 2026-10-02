"""Text Transform's flow: the shortcut, the text (a selection, or the last dictation), the menu, the transform, the
replacement and undo. Windows is faked (no key is pressed, the clipboard isn't touched) and so is the AI model."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import queue  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sst.settings import Settings  # noqa: E402
from sst.transform import TransformResult  # noqa: E402
from sst.transformui import MENU_KEYS, TransformController, edges  # noqa: E402

ORIGINAL = "I checked the deployment and everything looks good, but we still have one issue with the database migration."
CONCISE = "Deployment looks good, but the database migration issue remains."


@pytest.fixture(scope="module", autouse=True)
def qt():
    return QApplication.instance() or QApplication([])


class FakeAccess:
    """The focused app: what is selected, what was typed last, what gets pasted."""

    def __init__(self, selection=None, window=7):
        self.selection, self.window = selection, window
        self.selected_last, self.pasted = [], []
        self.select_ok = True
        self.cls = "Chrome_WidgetWin_1"
        self.copied = 0

    def foreground_window(self):
        return self.window

    def window_class(self, hwnd):
        return self.cls

    def copy_selection(self, timeout=0.6):
        self.copied += 1
        return self.selection

    def select_last(self, text):
        self.selected_last.append(text)
        return self.select_ok

    def paste_rich(self, text, html=None):
        self.pasted.append((text, html))


class FakeListener:
    def __init__(self, hotkey):
        self.hotkey, self.events, self.captured, self.running = hotkey, queue.Queue(), None, False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def capture(self, keys):
        self.captured = keys


class FakeApp:
    def __init__(self, ready=True, answer=None):
        self.settings = Settings(welcomed=True)
        self.ready, self.answer = ready, answer
        self.said, self.remembered, self.asked = [], [], []

    def transform_ready(self):
        return self.ready

    def run_transform(self, text, key):
        self.asked.append((key, text))
        if isinstance(self.answer, Exception):
            raise self.answer
        if self.answer is not None:
            return self.answer
        return TransformResult(key, text, CONCISE, CONCISE, f"<p>{CONCISE}</p>", True, [], 1, 0.5)

    def say(self, state, message=""):
        self.said.append((state, message))

    def remember(self, text, original):
        self.remembered.append((text, original))


_LIVE: list[TransformController] = []  # never collected while a worker thread may still signal them


@pytest.fixture(autouse=True)
def finish_work():
    yield
    for controller in _LIVE:  # a test may end while the transform or the paste is still on its thread
        wait_until(lambda c=controller: not c.busy)
        controller.stop()


def make(selection=None, **app):
    access, fake_app = FakeAccess(selection), FakeApp(**app)
    controller = TransformController(fake_app, access=access, listener_factory=FakeListener)
    controller.start("ctrl+alt+t")
    _LIVE.append(controller)
    return controller, access, fake_app


def wait_until(condition, ms=2000):
    for _ in range(ms // 10):
        if condition():
            return True
        QTest.qWait(10)
    return condition()


def open_menu(controller):
    controller.listener.events.put(("release", 0.0))
    assert wait_until(lambda: controller.menu.isVisible() or controller.app.said)


def test_selected_text_is_transformed_and_replaced(qt):
    controller, access, app = make(selection=" " + ORIGINAL + "\n")
    open_menu(controller)
    menu = controller.menu
    assert [label for _, _, label in menu.items] == ["Concise", "Professional", "Bullet points", "Action items"]
    assert menu.source.startswith("Selected text") and controller.listener.captured == MENU_KEYS
    controller.listener.events.put(("key:49", 0.0))  # 1: Concise
    assert wait_until(lambda: access.pasted)
    assert access.pasted == [(" " + CONCISE + "\n", f"<p>{CONCISE}</p>")]  # the text's own spaces kept around it
    assert app.asked == [("concise", ORIGINAL)] and app.remembered == [(CONCISE, ORIGINAL)]
    assert wait_until(lambda: ("transformed", "Transformed: Concise") in app.said)
    assert controller.listener.captured is None and not controller.busy


def test_with_nothing_selected_the_last_dictation_is_used(qt):
    controller, access, app = make(selection=None)
    controller.note_typed("so I checked the deployment ", hwnd=7)
    open_menu(controller)
    assert access.selected_last == ["so I checked the deployment "]
    assert controller.menu.source.startswith("Your last dictation")
    controller.menu.key(0x0D)  # Enter: the first row
    assert wait_until(lambda: access.pasted)
    assert access.pasted[0][0] == CONCISE + " "


@pytest.mark.parametrize("case", ["nothing", "other window", "old", "not found"])
def test_without_text_nothing_is_changed_and_the_user_is_told(qt, case):
    controller, access, app = make(selection=None)
    if case != "nothing":
        controller.note_typed("hello there ", hwnd=99 if case == "other window" else 7)
    if case == "old":
        text, _, hwnd = controller.last_typed
        controller.last_typed = (text, -1e9, hwnd)
    access.select_ok = case != "not found"
    open_menu(controller)
    assert not controller.menu.isVisible() and not access.pasted
    assert app.said[-1] == ("warning", "Select some text first, then press the shortcut again.")


def test_a_rejected_transform_keeps_the_text_and_says_why(qt):
    rejected = TransformResult("concise", ORIGINAL, "x", "x", "", False, ["lost number '30'"], 2, 1.0)
    controller, access, app = make(selection=ORIGINAL, answer=rejected)
    open_menu(controller)
    controller.menu.key(0x31)
    assert wait_until(lambda: not controller.busy and app.said and app.said[-1][0] == "warning")
    assert app.said[-1] == ("warning", "Kept your text: lost number '30'") and not access.pasted


def test_a_failing_provider_changes_nothing(qt):
    controller, access, app = make(selection=ORIGINAL, answer=RuntimeError("HTTP 429 quota"))
    open_menu(controller)
    controller.menu.key(0x31)
    assert wait_until(lambda: not controller.busy and app.said[-1][0] == "warning")
    assert "HTTP 429 quota" in app.said[-1][1] and not access.pasted


def test_switching_windows_meanwhile_pastes_nothing(qt):
    controller, access, app = make(selection=ORIGINAL)
    open_menu(controller)
    access.window = 8  # the user went elsewhere while the model answered
    controller.menu.key(0x31)
    assert wait_until(lambda: not controller.busy and app.said[-1][0] == "warning")
    assert "switched windows" in app.said[-1][1] and not access.pasted and app.remembered  # the result is on Home


def test_undo_restores_the_original(qt):
    controller, access, app = make(selection=ORIGINAL)
    open_menu(controller)
    controller.menu.key(0x31)
    assert wait_until(lambda: controller.last is not None and not controller.busy)
    access.selection = None  # right after the transform nothing is selected: the transform is "the last text"
    app.said.clear()
    open_menu(controller)
    assert access.selected_last[-1] == CONCISE and controller.menu.items[-1][:2] == ("undo", "U")
    assert controller.menu.source.startswith("Your last transform")
    controller.menu.key(0x55)  # U
    assert wait_until(lambda: len(access.pasted) == 2)
    assert access.pasted[1] == (ORIGINAL, None) and controller.last is None
    assert wait_until(lambda: ("transformed", "Original restored") in app.said)


def test_esc_or_the_shortcut_again_closes_the_menu(qt):
    controller, access, app = make(selection=ORIGINAL)
    open_menu(controller)
    controller.menu.key(0x1B)
    assert not controller.menu.isVisible() and controller.listener.captured is None and controller.pending is None
    open_menu(controller)
    controller.trigger()
    assert not controller.menu.isVisible() and not app.asked


def test_without_an_ai_model_the_user_is_told(qt):
    controller, access, app = make(selection=ORIGINAL, ready=False)
    controller.trigger()
    assert app.said == [("warning", "Text Transform needs an AI model: choose one in AI cleanup.")]


def test_the_menu_follows_the_chosen_transforms(qt):
    controller, access, app = make(selection=ORIGINAL)
    app.settings.transforms = ["actions", "rewrite"]
    open_menu(controller)
    assert [(hint, label) for _, hint, label in controller.menu.items] == [("1", "Action items"), ("2", "Rewrite")]
    controller.menu.key(0x62)  # numpad 2
    assert wait_until(lambda: app.asked)
    assert app.asked[0][0] == "rewrite"


def test_off_means_no_shortcut(qt):
    controller, _, _ = make()
    controller.start("")
    assert controller.listener is None
    controller.start("not a key")
    assert controller.listener is None  # a bad setting is logged, never a crash


@pytest.mark.parametrize("text, lead, trail", [("  hi there \n", "  ", " \n"), ("hi", "", ""), ("   ", "   ", "")])
def test_edges(text, lead, trail):
    assert edges(text) == (lead, trail)


@pytest.mark.parametrize("cls", ["ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"])
def test_no_ctrl_c_is_ever_pressed_in_a_terminal(qt, cls):
    controller, access, app = make(selection=ORIGINAL)
    access.cls = cls
    open_menu(controller)
    assert access.copied == 0 and not access.selected_last and not controller.menu.isVisible()
    assert "terminal" in app.said[-1][1]
