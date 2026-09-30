"""The tray app's windows, built off-screen (nothing appears on the screen, nothing takes focus)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from sst import app as sst_app  # noqa: E402
from sst.settings import Settings  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("state, message", [("recording", ""), ("transcribing", ""), ("typed", ""), ("cancelled", ""),
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


def test_settings_dialog_returns_what_was_chosen():
    chosen = Settings(hotkey="menu", microphone="Mic B", sounds=False, save_recordings=True)
    dialog = sst_app.SettingsDialog(chosen, ["Mic A", "Mic B"])
    assert dialog.result_settings() == chosen


def test_settings_dialog_keeps_a_custom_hotkey_and_an_unplugged_microphone():
    chosen = Settings(hotkey="ctrl+shift+f9", microphone="Old headset")
    dialog = sst_app.SettingsDialog(chosen, ["Mic A"])
    assert dialog.result_settings() == chosen
    assert "not connected" in dialog.microphone.currentText()


def test_history_window_lists_entries_and_copies(monkeypatch):
    monkeypatch.setattr(sst_app, "read_history", lambda: [{"time": "2026-09-30 10:15:00", "text": "hello world"}])
    window = sst_app.HistoryWindow()
    window.refresh()
    assert window.list.count() == 1 and "hello world" in window.list.item(0).text()
    window._copy(window.list.item(0))
    assert QApplication.clipboard().text() == "hello world"
