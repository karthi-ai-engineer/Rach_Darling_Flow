"""The tray app: the pill, what TrayApp does for the window, and "open Rflow again" (built off-screen: nothing appears
on the screen, nothing takes focus)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtNetwork import QLocalServer  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from sst import app as sst_app  # noqa: E402
from sst.gateway import GatewayConfig  # noqa: E402
from sst.settings import Settings  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("state, message", [("recording", ""), ("transcribing", ""), ("typed", ""), ("typed_raw", ""),
                                            ("cancelled", ""),
                                            ("ignored", ""), ("warning", "Almost silent: check the microphone."),
                                            ("error", "Could not open the microphone")])
def test_pill_draws_every_state(state, message):
    pill = sst_app.Pill(level=lambda: 0.05)
    pill.show_state(state, message)
    assert pill.isVisible() and not pill.grab().isNull()
    pill.show_state("idle")
    assert not pill.isVisible()


def test_pill_grows_to_fit_long_messages():
    pill = sst_app.Pill()
    pill.show_state("typed")
    short = pill.width()
    pill.show_state("warning", "Almost silent: check the microphone.")
    assert pill.width() > short


def _fake_tray_app(settings: Settings):
    """TrayApp's methods on a stand-in: apply_settings only records, so nothing touches the real settings file."""
    fake = SimpleNamespace(settings=settings, gateway=GatewayConfig(), dictation=None, applied=[], cleanups=0)

    def apply(new):
        fake.settings = new
        fake.applied.append(new)
    fake.apply_settings = apply
    fake._apply_cleanup = lambda: setattr(fake, "cleanups", fake.cleanups + 1)
    return fake


def test_adding_words_skips_ones_already_there():
    fake = _fake_tray_app(Settings(vocabulary=["GitHub"]))
    assert sst_app.TrayApp.add_words(fake, ["github", "Tamil", "CodeQL", "Tamil"]) == 2
    assert fake.settings.vocabulary == ["GitHub", "Tamil", "CodeQL"]
    assert sst_app.TrayApp.add_words(fake, ["TAMIL"]) == 0 and len(fake.applied) == 1  # nothing new: nothing saved


def test_removing_a_word():
    fake = _fake_tray_app(Settings(vocabulary=["GitHub", "Tamil"]))
    sst_app.TrayApp.remove_word(fake, "GitHub")
    assert fake.settings.vocabulary == ["Tamil"]


def test_saving_the_cleanup_keeps_the_other_settings(monkeypatch):
    monkeypatch.setattr(GatewayConfig, "save", lambda self: None)
    fake = _fake_tray_app(Settings(hotkey="menu", vocabulary=["Tamil"]))
    sst_app.TrayApp.save_cleanup(fake, True, "model-a", "model-b", GatewayConfig("http://localhost:11434/v1", ""))
    s = fake.settings
    assert (s.cleanup, s.cleanup_model, s.cleanup_fallback, s.hotkey, s.vocabulary) == (True, "model-a", "model-b", "menu",
                                                                                         ["Tamil"])
    assert fake.gateway.base_url == "http://localhost:11434/v1"


def test_the_first_close_tells_once_that_rflow_keeps_running():
    told = []
    fake = _fake_tray_app(Settings())
    fake._notify = lambda title, message, *_: told.append(title)
    fake.hotkey_label = lambda: "Ctrl+Win"
    sst_app.TrayApp.window_closed(fake)
    sst_app.TrayApp.window_closed(fake)
    assert told == ["Rflow is still running"] and fake.settings.told_about_tray


class FakeListener:
    """Stands in for the keyboard hook: no real keys are watched."""

    def __init__(self, hotkey):
        import queue
        self.hotkey, self.events, self.recording, self.running = hotkey, queue.Queue(), False, False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class FakeEngine:
    name = "fake"

    def transcribe(self, audio, rate):
        return "hello"


@pytest.fixture
def tray_app(monkeypatch, tmp_path):
    """The real TrayApp with everything outside the process faked: settings in memory, no model, no hook."""
    from PySide6.QtTest import QTest

    from sst.settings import Stats
    saved = {}
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls, path=None: Settings(welcomed=True)))
    monkeypatch.setattr(Settings, "save", lambda self, path=None: saved.__setitem__("settings", self))
    monkeypatch.setattr(Stats, "load", classmethod(lambda cls, path=None, history=None: Stats()))
    monkeypatch.setattr(Stats, "save", lambda self, path=None: saved.__setitem__("stats", self))
    monkeypatch.setattr(GatewayConfig, "load", classmethod(lambda cls, path=None: GatewayConfig()))
    history = []
    monkeypatch.setattr(sst_app, "add_to_history", lambda text, heard=None: history.insert(0, {
        "time": "2026-09-30 10:15:00", "text": text}))
    monkeypatch.setattr(sst_app, "read_history", lambda: history)
    monkeypatch.setattr(sst_app, "load_engine", lambda name: FakeEngine())
    monkeypatch.setattr(sst_app, "HotkeyListener", FakeListener)
    monkeypatch.setattr(sst_app, "wispr_flow_running", lambda: False)
    monkeypatch.setattr(sst_app, "input_device_names", lambda refresh=True: ["Mic A"])
    monkeypatch.setattr(sst_app, "SERVER_NAME", f"Rflow-test-app-{os.getpid()}")
    app = sst_app.TrayApp(quiet_start=True)
    for _ in range(100):  # the model "loads" on its thread
        if app.dictation:
            break
        QTest.qWait(20)
    yield app, saved, history
    app.window.hide()
    app.quit()


def test_the_tray_app_and_its_window_work_together(tray_app):
    app, saved, history = tray_app
    assert app.dictation and app.listener.running and app.window.ready
    assert "Ready: hold Ctrl+Win" in app.window.status_label.text()
    app.window.open("home")
    app._on_result("hello world", "Hello, world.", 2.0)  # what the dictation reports after typing
    assert history[0]["text"] == "Hello, world." and saved["stats"].words == 2
    assert "Hello, world." in [label.text() for label in app.window.pages["home"].findChildren(QLabel)]
    settings_page = app.window.pages["settings"]
    first_listener = app.listener
    settings_page.hotkey.setCurrentIndex(settings_page.hotkey.findData("menu"))  # applied at once
    assert saved["settings"].hotkey == "menu" and app.listener is not first_listener and not first_listener.running
    assert app.hotkey_label() == "Menu key"
    assert app.add_words(["Tamil"]) == 1 and saved["settings"].vocabulary == ["Tamil"]
    app.window.close()
    assert not app.window.isVisible() and saved["settings"].told_about_tray


def test_the_tray_app_shows_its_window_when_opened_again(tray_app):
    app, _, _ = tray_app
    assert not app.window.isVisible()  # started at sign-in: only the tray icon
    assert sst_app.show_running_window()
    from PySide6.QtTest import QTest
    for _ in range(50):
        if app.window.isVisible():
            break
        QTest.qWait(20)
    assert app.window.isVisible()


def test_opening_rflow_again_reaches_the_running_copy(monkeypatch):
    monkeypatch.setattr(sst_app, "SERVER_NAME", f"Rflow-test-{os.getpid()}")
    assert not sst_app.show_running_window()  # nothing is running: the caller starts normally
    server = QLocalServer()
    assert server.listen(sst_app.SERVER_NAME)
    assert sst_app.show_running_window()
    assert server.waitForNewConnection(2000) or server.hasPendingConnections()
    server.close()
