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


class FakeRecorder:
    rate, level = 16_000, 0.0

    def __init__(self, seconds=1.0):
        self.seconds, self.starts = seconds, 0

    def start(self):
        self.starts += 1

    def stop(self):
        import numpy as np
        return np.zeros(int(self.seconds * self.rate), dtype=np.float32)


def _reading_test(tmp_path, recorder=None, score=None, add_words=None):
    return sst_app.ReadingTest(recorder or FakeRecorder(), score or (lambda folder, progress: None),
                               add_words or (lambda words: 0), "Test microphone", folder=tmp_path / "test")


def test_reading_test_saves_each_sentence_and_moves_on(tmp_path):
    from sst import bench
    window = _reading_test(tmp_path)
    assert window.sentence.text() == bench.SENTENCES[0] and not window.score_button.isEnabled()
    window.toggle_recording()
    assert window.recording and window.record_button.text() == "Stop"
    window.toggle_recording()
    assert (tmp_path / "test" / "01.wav").exists()
    assert (tmp_path / "test" / "01.txt").read_text(encoding="utf-8") == bench.SENTENCES[0]
    assert window.index == 1 and window.sentence.text() == bench.SENTENCES[1]  # moved on to the next sentence
    assert window.score_button.isEnabled() and "1 recorded" in window.score_button.text()


def test_space_records_and_a_too_short_recording_is_not_kept(tmp_path):
    window = _reading_test(tmp_path, recorder=FakeRecorder(seconds=0.2))
    window._space()
    window._space()
    assert not (tmp_path / "test" / "01.wav").exists() and window.index == 0 and "too short" in window.status.text()


def test_an_unfinished_test_continues_at_the_first_sentence_not_read(tmp_path):
    from sst import bench
    first = _reading_test(tmp_path)
    for _ in range(2):
        first.toggle_recording()
        first.toggle_recording()
    again = _reading_test(tmp_path)  # the same folder, as the tray app passes bench.unfinished()
    assert again.index == 2 and again.sentence.text() == bench.SENTENCES[2] and "2 of 30 read" in again.status.text()


def test_redo_replaces_a_recording(tmp_path):
    recorder = FakeRecorder()
    window = _reading_test(tmp_path, recorder=recorder)
    window.toggle_recording()
    window.toggle_recording()
    window.go(0)
    assert window.redo_button.isEnabled() and not window.record_button.isEnabled()
    window.redo_button.click()
    window.toggle_recording()
    assert recorder.starts == 2 and window.recorded() == 1


def test_results_show_every_setup_and_add_the_ticked_words(tmp_path):
    from sst.bench import Results, Setup
    results = Results(str(tmp_path), ["s1"], [Setup("Parakeet alone", 4, 20, [0.5]), Setup("Parakeet + m", 2, 20, [0.7])],
                      [("tamil", "tamar", 2)], ["Tamil", "CodeQL"])
    added = []
    window = _reading_test(tmp_path, add_words=lambda words: added.extend(words) or len(words))
    window._show_results(results)
    assert window.pages.currentIndex() == 1
    html = window.report.toHtml()
    assert "Parakeet alone" in html and "20.0%" in html and "10.0%" in html and "tamar" in html
    window.suggestions.item(1).setCheckState(sst_app.Qt.CheckState.Unchecked)
    window._add_selected()
    assert added == ["Tamil"] and "Added 1 word" in window.results_status.text()


def test_adding_words_skips_ones_already_there():
    from types import SimpleNamespace
    fake = SimpleNamespace(settings=Settings(vocabulary=["GitHub"]), _apply_cleanup=lambda: None)
    fake.settings.save = lambda: None
    assert sst_app.TrayApp._add_words(fake, ["github", "Tamil", "CodeQL"]) == 2
    assert fake.settings.vocabulary == ["GitHub", "Tamil", "CodeQL"]
