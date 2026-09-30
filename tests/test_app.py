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


def test_settings_dialog_returns_the_cleanup_settings_words_and_endpoint():
    from sst.gateway import GatewayConfig
    chosen = Settings(cleanup=True, cleanup_model="model-a", cleanup_fallback="model-b", vocabulary=["Claude Code", "GitHub"])
    dialog = sst_app.SettingsDialog(chosen, [], GatewayConfig("https://gw.example/v1", "key-1"))
    assert dialog.result_settings() == chosen
    assert dialog.result_gateway() == GatewayConfig("https://gw.example/v1", "key-1")
    dialog.vocabulary.setPlainText("Tamil, Wispr Flow\n\n  SST  ")
    dialog.api_key.setText("  key-2 ")
    dialog.model.setCurrentText("  typed-model ")
    assert dialog.result_settings().vocabulary == ["Tamil", "Wispr Flow", "SST"]
    assert dialog.result_settings().cleanup_model == "typed-model"  # any model name can be typed
    assert dialog.result_gateway().api_key == "key-2"


def test_a_new_install_has_no_endpoint_or_model_and_cleanup_off():
    dialog = sst_app.SettingsDialog(Settings(), [])
    result = dialog.result_settings()
    assert (result.cleanup, result.cleanup_model, result.cleanup_fallback) == (False, "", "")
    assert dialog.result_gateway().base_url == ""


def test_cleanup_can_be_switched_off_keeping_the_model():
    dialog = sst_app.SettingsDialog(Settings(cleanup=True, cleanup_model="model-a"), [])
    dialog.cleanup_on.setChecked(False)
    assert dialog.result_settings().cleanup is False and dialog.result_settings().cleanup_model == "model-a"


def test_history_window_leads_to_settings(monkeypatch):
    monkeypatch.setattr(sst_app, "read_history", lambda: [])
    opened = []
    window = sst_app.HistoryWindow(open_settings=lambda: opened.append(True))
    button = next(b for b in window.findChildren(sst_app.QPushButton) if b.text() == "Settings...")
    button.click()
    assert opened == [True]
