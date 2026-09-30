"""The Rflow window, built off-screen with PreviewApp in place of the tray app (no model, no microphone, no hook)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import date, datetime, timedelta  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from sst import bench  # noqa: E402
from sst import window as w  # noqa: E402
from sst.gateway import GatewayConfig  # noqa: E402
from sst.settings import Settings, Stats  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt():
    return QApplication.instance() or QApplication([])


def _labels(widget) -> str:
    return " | ".join(label.text() for label in widget.findChildren(QLabel))


def _button(widget, caption: str) -> QPushButton:
    return next(b for b in widget.findChildren(QPushButton) if b.text() == caption)


def _window(**kwargs) -> tuple[w.MainWindow, w.PreviewApp]:
    app = w.PreviewApp(**kwargs)
    return w.MainWindow(app), app


# ---- the window

def test_a_new_user_sees_the_welcome_and_then_home():
    window, app = _window(settings=Settings())
    assert window.current_page() == "welcome"
    window.set_status("Loading the speech model...", False)
    assert "Loading the speech model" in window.pages["welcome"].status.text()
    window.set_status("Ready: hold Ctrl+Win", True)
    assert "Ready" in window.pages["welcome"].status.text()
    _button(window.pages["welcome"], "Start using Rflow").click()
    assert app.settings.welcomed and window.current_page() == "home"


def test_someone_who_has_been_welcomed_starts_at_home_and_can_open_every_page():
    window, _ = _window()
    assert window.current_page() == "home"
    for key, _label in w.NAV:
        window.nav[key].click()
        assert window.current_page() == key and window.nav[key].isChecked()
        assert not window.grab().isNull()


def test_home_shows_the_stats_and_the_dictations_by_day():
    today = date.today()
    stats = Stats()
    stats.add("one two three", 1.5, today)
    stats.add("four five", 1.0, today - timedelta(days=1))
    history = [{"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "text": "Hello from today.", "heard": "hello from today"},
               {"time": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"), "text": "And yesterday."}]
    window, _ = _window(history=history, stats=stats)
    home = window.pages["home"]
    labels = _labels(home)
    assert "TODAY" in labels and "YESTERDAY" in labels and "Hello from today." in labels and "And yesterday." in labels
    assert home.stat_values["week"].text() == "5" and home.stat_values["total"].text() == "5"
    assert home.stat_values["streak"].text() == "2"
    assert home.stat_values["speed"].text() == "–"  # not yet half a minute of speech
    assert "Hold Ctrl+Win in any app and speak" in labels


def test_home_explains_what_to_do_before_the_first_dictation():
    window, _ = _window(settings=Settings(welcomed=True, hotkey="menu"))
    labels = _labels(window.pages["home"])
    assert "Nothing dictated yet" in labels and "hold Menu key" in labels


def test_a_dictation_can_be_copied_again():
    window, _ = _window(history=[{"time": "2026-09-30 10:15:00", "text": "Copy me."}])
    copy = next(b for b in window.pages["home"].findChildren(w.QToolButton) if b.toolTip() == "Copy")
    copy.click()
    assert QApplication.clipboard().text() == "Copy me." and copy.text() == w.GLYPHS["check"]


def test_the_dictionary_adds_several_words_and_removes_one():
    window, app = _window(settings=Settings(welcomed=True, vocabulary=["GitHub"]))
    window.show_page("dictionary")
    page = window.pages["dictionary"]
    assert page.cleanup_off.isVisibleTo(window)  # AI cleanup is off: the page says so
    page.entry.setText("Tamil,  CodeQL , github")
    page._add()
    assert app.settings.vocabulary == ["GitHub", "Tamil", "CodeQL"] and page.entry.text() == ""
    assert page.count.text() == "3 WORDS"
    remove = next(b for b in page.findChildren(w.QToolButton) if b.toolTip() == "Remove GitHub")
    remove.click()
    assert app.settings.vocabulary == ["Tamil", "CodeQL"]


def test_settings_apply_at_once_and_keep_the_rest():
    window, app = _window(settings=Settings(welcomed=True, vocabulary=["Tamil"], cleanup=True, cleanup_model="m"),
                          microphones=["Mic A", "Mic B"])
    page = window.pages["settings"]
    page.hotkey.setCurrentIndex(page.hotkey.findData("menu"))
    page.microphone.combo.setCurrentIndex(page.microphone.combo.findData("Mic B"))
    page.sounds.setChecked(False)
    s = app.settings
    assert (s.hotkey, s.microphone, s.sounds) == ("menu", "Mic B", False)
    assert (s.vocabulary, s.cleanup, s.cleanup_model, s.welcomed) == (["Tamil"], True, "m", True)  # untouched


def test_settings_keep_a_custom_hotkey_and_an_unplugged_microphone():
    chosen = Settings(welcomed=True, hotkey="ctrl+shift+f9", microphone="Old headset")
    window, _ = _window(settings=chosen, microphones=["Mic A"])
    page = window.pages["settings"]
    assert page.result(chosen) == chosen
    assert "not connected" in page.microphone.combo.currentText()


def test_the_cleanup_page_saves_what_was_typed():
    window, app = _window(settings=Settings(welcomed=True, cleanup_model="model-a"),
                          gateway=GatewayConfig("https://gw.example/v1", "key-1"))
    page = window.pages["cleanup"]
    assert page.result() == (False, "model-a", "", GatewayConfig("https://gw.example/v1", "key-1"))
    page.cleanup_on.setChecked(True)
    page.api_key.setText("  key-2 ")
    page.model.setCurrentText("  typed-model ")  # any model name can be typed
    _button(page, "Save").click()
    assert app.calls[-1] == ("save_cleanup", True, "typed-model", "", GatewayConfig("https://gw.example/v1", "key-2"))
    assert "Active from the next dictation" in page.saved.text()


def test_a_new_install_has_no_endpoint_or_model_and_cleanup_off():
    window, _ = _window()
    assert window.pages["cleanup"].result() == (False, "", "", GatewayConfig())


def test_the_update_banner_leads_to_the_update():
    window, app = _window()
    window.show_update("Rflow 9.9.9 is available (you have 1.1.0).", version="9.9.9")
    assert window.banner.isVisibleTo(window) and "9.9.9" in window.update_link.text()
    window.update_button.click()
    window.update_link.click()
    assert app.calls.count(("start_update",)) == 2
    window.show_update("Downloading... 40%", busy=True)
    assert not window.update_button.isEnabled()


def test_closing_the_window_keeps_rflow_running():
    window, app = _window()
    window.open()
    window.close()
    assert not window.isVisible() and ("window_closed",) in app.calls


def test_both_themes_have_every_colour_and_image():
    for theme, colours in w.THEMES.items():
        sheet = w.stylesheet(theme)
        assert "None" not in sheet and colours["accent"] in sheet and f"arrow-{theme}.png" in sheet
    for name in ("check", "arrow-light", "arrow-dark"):  # made by scripts/make_ui_images.py, shipped in sst/static
        assert (w.UI_IMAGES / f"{name}.png").exists() and (w.UI_IMAGES / f"{name}@2x.png").exists()


# ---- the reading test

class FakeRecorder:
    rate, level = 16_000, 0.0

    def __init__(self, seconds=1.0):
        self.seconds, self.starts, self.stops = seconds, 0, 0

    def start(self):
        self.starts += 1

    def stop(self):
        self.stops += 1
        return np.zeros(int(self.seconds * self.rate), dtype=np.float32)


def _reading_test(tmp_path, recorder=None, score=None, add_words=None):
    return w.ReadingTest(recorder or FakeRecorder(), score or (lambda folder, progress: None),
                         add_words or (lambda words: 0), "Test microphone", folder=tmp_path / "test")


def test_reading_test_saves_each_sentence_and_moves_on(tmp_path):
    test = _reading_test(tmp_path)
    assert test.sentence.text() == bench.SENTENCES[0] and not test.score_button.isEnabled()
    test.toggle_recording()
    assert test.recording and test.record_button.text() == "Stop"
    test.toggle_recording()
    assert (tmp_path / "test" / "01.wav").exists()
    assert (tmp_path / "test" / "01.txt").read_text(encoding="utf-8") == bench.SENTENCES[0]
    assert test.index == 1 and test.sentence.text() == bench.SENTENCES[1]  # moved on to the next sentence
    assert test.score_button.isEnabled() and "1 recorded" in test.score_button.text()


def test_space_records_and_a_too_short_recording_is_not_kept(tmp_path):
    test = _reading_test(tmp_path, recorder=FakeRecorder(seconds=0.2))
    test._space()
    test._space()
    assert not (tmp_path / "test" / "01.wav").exists() and test.index == 0 and "too short" in test.status.text()


def test_an_unfinished_test_continues_at_the_first_sentence_not_read(tmp_path):
    first = _reading_test(tmp_path)
    for _ in range(2):
        first.toggle_recording()
        first.toggle_recording()
    again = _reading_test(tmp_path)  # the same folder, as the page passes bench.unfinished()
    assert again.index == 2 and again.sentence.text() == bench.SENTENCES[2] and "2 of 30 read" in again.status.text()


def test_redo_replaces_a_recording(tmp_path):
    recorder = FakeRecorder()
    test = _reading_test(tmp_path, recorder=recorder)
    test.toggle_recording()
    test.toggle_recording()
    test.go(0)
    assert test.redo_button.isEnabled() and not test.record_button.isEnabled()
    test.redo_button.click()
    test.toggle_recording()
    assert recorder.starts == 2 and test.recorded() == 1


def test_leaving_the_page_stops_a_recording_without_keeping_it(tmp_path):
    recorder = FakeRecorder()
    test = _reading_test(tmp_path, recorder=recorder)
    test.toggle_recording()
    test.stop()
    assert not test.recording and recorder.stops == 1 and test.recorded() == 0


def test_results_show_every_setup_and_add_the_ticked_words(tmp_path):
    from sst.bench import Results, Setup
    results = Results(str(tmp_path), ["s1"], [Setup("Parakeet alone", 4, 20, [0.5]), Setup("Parakeet + m", 2, 20, [0.7])],
                      [("tamil", "tamar", 2)], ["Tamil", "CodeQL"])
    added = []
    test = _reading_test(tmp_path, add_words=lambda words: added.extend(words) or len(words))
    test._show_results(results)
    assert test.pages.currentIndex() == 1
    html = test.report.toHtml()
    assert "Parakeet alone" in html and "20.0%" in html and "10.0%" in html and "tamar" in html
    test.suggestions.item(1).setCheckState(Qt.CheckState.Unchecked)
    test._add_selected()
    assert added == ["Tamil"] and "Added 1 word" in test.results_status.text()


def test_new_test_starts_a_fresh_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "BENCH_DIR", tmp_path)
    window, _ = _window()
    page = window.pages["reading"]
    first = page.ensure_test()
    first.restart.emit()
    assert page.test is not first and page.test.index == 0
