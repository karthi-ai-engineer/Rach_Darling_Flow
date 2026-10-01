"""The Rflow window, built off-screen with PreviewApp in place of the tray app (no model, no microphone, no hook)."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import date, datetime, timedelta  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from sst import bench  # noqa: E402
from sst import window as w  # noqa: E402
from sst.audio import save_wav  # noqa: E402
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
    # settings from before the provider choice: the address says it's a server of the user's own (vLLM or the like)
    assert page.result() == (False, "model-a", "", GatewayConfig("https://gw.example/v1", "key-1", "vllm"))
    page.cleanup_on.setChecked(True)
    page.api_key.setText("  key-2 ")
    page.model.setCurrentText("  typed-model ")  # any model name can be typed
    _button(page, "Save").click()
    assert app.calls[-1] == ("save_cleanup", True, "typed-model", "", GatewayConfig("https://gw.example/v1", "key-2", "vllm"))
    assert "Active from the next dictation" in page.saved.text()


def test_a_new_install_has_no_endpoint_or_model_and_cleanup_off():
    window, _ = _window()
    assert window.pages["cleanup"].result() == (False, "", "", GatewayConfig("", "", "openai"))  # the first provider


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
    return w.ReadingTest(recorder or FakeRecorder(), score or (lambda folders, progress: None),
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


def test_a_test_notes_its_set_and_microphone_with_the_first_recording(tmp_path):
    class DescribedRecorder(FakeRecorder):
        def describe(self):
            return {"device": "Headset (Earbuds)", "host_api": "MME", "rate": self.rate}
    test = w.ReadingTest(DescribedRecorder(), lambda folders, progress: None, lambda words: 0, "Earbuds",
                         folder=tmp_path / "test", block="C")
    assert test.sentence.text() == bench.BLOCKS["C"][0] and "Set C" in test.counter.text()
    test.toggle_recording()
    test.toggle_recording()
    session = bench.read_session(tmp_path / "test")
    assert (session["block"], session["microphone"], session["device"]) == ("C", "Earbuds", "Headset (Earbuds)")
    assert (tmp_path / "test" / "01.txt").read_text(encoding="utf-8") == bench.BLOCKS["C"][0]
    again = w.ReadingTest(FakeRecorder(), lambda folders, progress: None, lambda words: 0, "Laptop",
                          folder=tmp_path / "test", block="A")  # resumed: keeps its own set
    assert again.block == "C" and again.sentence.text() == bench.BLOCKS["C"][1]


def test_score_all_tests_scores_every_test_next_to_this_one(tmp_path):
    asked = []
    test = w.ReadingTest(FakeRecorder(), lambda folders, progress: asked.append(folders), lambda words: 0, "mic",
                         folder=tmp_path / "2026-10-01_100000")
    old = tmp_path / "2026-10-01_090000"
    bench.write_session(old, "A", "mic", {})
    save_wav(old / "01.wav", np.zeros(16000, dtype=np.float32), 16000)
    (old / "01.txt").write_text("x", encoding="utf-8")
    test.toggle_recording()
    test.toggle_recording()
    test.start_scoring(every=True)
    test.start_scoring()
    deadline = time.monotonic() + 5
    while len(asked) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert sorted(asked, key=len) == [[test.folder], [old, test.folder]]


def test_results_show_every_setup_and_add_the_ticked_words(tmp_path):
    from sst.audio import AudioStats
    from sst.evaluate import Recording, Results, Score
    wide, narrow = AudioStats(2.0, -20, -70, -3, 0, -30), AudioStats(2.0, -20, -60, -3, 0, -80)
    recordings = [Recording("1.wav", "s1", "t1", "A", "Laptop", wide), Recording("2.wav", "s2", "t2", "C", "Earbuds", narrow)]
    alone = Score("Parakeet alone", ["a", "b"], [3, 1], [10, 10], [1, 0], [2, 1], [0, 0], [5, 1], [50, 50], [0.4, 0.6],
                  interval=(0.1, 0.3))
    cleaned = Score("Parakeet + m", ["a", "b"], [1, 1], [10, 10], [0, 0], [2, 1], [0, 0], [2, 1], [50, 50], [0.7, 0.9],
                    interval=(0.05, 0.15), difference=(-0.1, -0.2, -0.02))
    results = Results([str(tmp_path)], recordings, [alone, cleaned], [("tamil", "tamar", 2)], ["Tamil", "CodeQL"])
    added = []
    test = _reading_test(tmp_path, add_words=lambda words: added.extend(words) or len(words))
    test._show_results(results)
    assert test.pages.currentIndex() == 1
    html = test.report.toHtml()
    assert "Parakeet alone" in html and "20.0%" in html and "10.0%" in html and "tamar" in html
    assert "better" in html and "from 2 tests" in html
    assert "Earbuds" in html and "phone call" in html and "Laptop</b>" not in html  # only the narrowband one warned
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


# ---- providers and profiles

def test_each_provider_asks_for_what_it_needs():
    window, _ = _window()
    page = window.pages["cleanup"]
    for key, provider in w.PROVIDERS.items():
        page.provider.setCurrentIndex(page.provider.findData(key))
        assert page.form.isRowVisible(page.gateway_url) is provider.own_server  # an address only for your own server
        assert (not page.key_link.isHidden()) is bool(provider.key_page)  # "Get a key" for the cloud ones


def test_switching_providers_keeps_each_one_s_key_and_address():
    window, app = _window()
    page = window.pages["cleanup"]
    page.provider.setCurrentIndex(page.provider.findData("groq"))
    page.api_key.setText("groq-key")
    page.model.setCurrentText("llama-3.1-8b-instant")
    page.provider.setCurrentIndex(page.provider.findData("vllm"))
    assert page.api_key.text() == "" and page.model.currentText() == ""  # a fresh start for the other provider
    page.gateway_url.setText("http://my-server:8000/v1")
    page.provider.setCurrentIndex(page.provider.findData("groq"))
    assert page.api_key.text() == "groq-key" and page.model.currentText() == "llama-3.1-8b-instant"
    page.cleanup_on.setChecked(True)
    _button(page, "Save").click()
    on, model, _, gateway = app.calls[-1][1:]
    assert (on, model) == (True, "llama-3.1-8b-instant")
    assert gateway == GatewayConfig("", "groq-key", "groq", {"vllm": ("http://my-server:8000/v1", "")})
    assert gateway.address == "https://api.groq.com/openai/v1"


def test_a_saved_provider_opens_with_its_other_keys(qt):
    gateway = GatewayConfig("", "anthropic-key", "anthropic", {"openai": ("", "openai-key")})
    window, _ = _window(gateway=gateway)
    page = window.pages["cleanup"]
    assert page.provider.currentData() == "anthropic" and page.api_key.text() == "anthropic-key"
    page.provider.setCurrentIndex(page.provider.findData("openai"))
    assert page.api_key.text() == "openai-key"


def test_load_models_needs_an_address_for_your_own_server():
    window, _ = _window()
    page = window.pages["cleanup"]
    page.provider.setCurrentIndex(page.provider.findData("vllm"))
    page._load_models()
    assert "address" in page.test_result.text()


def _profiles_window():
    from sst.settings import Profiles
    profiles = Profiles()
    profiles.current.name = "Karthi Raj"
    profiles.add("Rahul")
    return _window(profiles=profiles)


def test_the_sidebar_and_home_show_whose_profile_it_is():
    window, _ = _profiles_window()
    assert "Karthi Raj" in window.profile_button.text()
    assert window.pages["home"].greeting.text().endswith(", Karthi")


def test_the_profiles_page_switches_renames_and_deletes():
    window, app = _profiles_window()
    window.show_page("profiles")
    page = window.pages["profiles"]
    assert "In use" in _labels(page)
    _button(page, "Switch to this profile").click()
    assert ("switch_profile", "rahul") in app.calls
    names = [box for box in page.findChildren(w.QLineEdit) if box.text() == "Rahul"]
    names[0].setText("Rahul K")
    names[0].editingFinished.emit()
    assert app.profiles.get("rahul").name == "Rahul K"
    app.profiles.active = "default"
    page.refresh()
    page.confirm = lambda question: "Rahul K" in question
    _button(page, "Delete").click()
    assert ("delete_profile", "rahul") in app.calls and [p.id for p in app.profiles.items] == ["default"]


def test_a_new_profile_is_made_from_its_name():
    window, app = _profiles_window()
    page = window.pages["profiles"]
    page.new_name.setText("Priya")
    page._create()
    assert app.profiles.current.name == "Priya" and page.new_name.text() == ""


def test_the_welcome_asks_for_a_name():
    window, app = _window(settings=Settings())
    window.pages["welcome"].name.setText("Priya")
    _button(window.pages["welcome"], "Start using Rflow").click()
    assert app.profiles.current.name == "Priya" and ("finish_welcome", "Priya") in app.calls


# ---- capture settings

def test_the_microphone_settings_apply_at_once_and_warn_about_bluetooth(monkeypatch):
    monkeypatch.setattr(w, "call_quality", lambda device: device == "Headset (Buds)")
    window, app = _window()
    page = window.pages["settings"]
    assert page.warm_mic.isChecked() and not page.raw_audio.isChecked()  # warm by default (the owner's choice)
    assert page.call_warning.isHidden()
    page.raw_audio.setChecked(True)
    page.warm_mic.setChecked(False)
    assert app.settings.raw_audio and not app.settings.warm_mic
    page.microphone.combo.addItem("Headset (Buds)", "Headset (Buds)")
    page.microphone.combo.setCurrentIndex(page.microphone.combo.findData("Headset (Buds)"))
    assert not page.call_warning.isHidden() and app.settings.microphone == "Headset (Buds)"


def test_the_reading_test_saves_what_came_after_record_not_the_lead_in(tmp_path):
    from sst.audio import Take

    class Warm(FakeRecorder):
        tail = 0.3

        def stop_later(self):
            self.stops += 1
            take = Take([np.full(int(0.4 * self.rate), 0.5, dtype=np.float32)], self.rate)  # lead-in
            take.chunks.append(np.zeros(int(0.3 * self.rate), dtype=np.float32))  # too short a reading
            take.done.set()
            return take

        def close(self):
            self.closed = True
    recorder = Warm()
    test = _reading_test(tmp_path, recorder=recorder)
    test.toggle_recording()
    test.toggle_recording()
    assert "too short" in test.status.text() and test.recorded() == 0
    test.stop()
    assert recorder.closed  # leaving the page closes even a warm microphone


# ---- speech recognition

def test_the_speech_page_shows_the_model_in_use_and_what_comes_next():
    window, _ = _window()
    window.show_page("speech")
    page = window.pages["speech"]
    assert page.where["local"].isChecked()
    parakeet, whisper = page.models["parakeet"], page.models["whisper-turbo"]
    assert "In use" in parakeet.status.text() and parakeet.choose.isHidden() and parakeet.download.isHidden()
    assert parakeet.remove.isHidden()  # it comes with Rflow
    assert not whisper.download.isHidden() and "1.6 GB" in whisper.download.text()  # not downloaded yet
    assert whisper.choose.isHidden() and whisper.language_row.isHidden()
    page.where["cloud"].click()
    assert page.groups.currentIndex() == list(w.WHERE).index("cloud")
    assert {"openai", "groq", "gemini"} <= set(page.models) and "Your voice is sent to OpenAI" in _labels(page)
    page.where["server"].click()
    assert "Your own server" in _labels(page)


def test_a_cloud_model_asks_first_then_keeps_its_key_and_model():
    window, app = _window()
    page = window.pages["speech"]
    page.show_where("cloud")
    groq = page.models["groq"]
    assert groq.choose.text() == "Use this model" and not groq.choose.isHidden() and groq.status.text() == ""
    groq.choose.click()
    assert "Enter your Groq API key" in groq.result.text() and not app.calls  # no key: nothing happens
    groq.key.setText("gsk-test")
    groq.model_box.setCurrentText("whisper-large-v3")
    questions = []
    page.confirm = lambda question: questions.append(question) or False
    groq.choose.click()
    assert "sent to Groq" in questions[0] and not app.calls  # the user said no
    page.confirm = lambda question: True
    groq.choose.click()
    assert ("use_cloud_speech", "groq", "whisper-large-v3") in app.calls
    assert app.gateway.key_for("groq") == "gsk-test" and app.settings.speech_model == "groq"
    page.refresh()
    assert "In use" in groq.status.text() and groq.choose.isHidden()  # nothing to save
    groq.model_box.setCurrentText("whisper-large-v3-turbo")
    assert groq.choose.text() == "Save" and not groq.choose.isHidden()
    questions.clear()
    page.confirm = lambda question: questions.append(question) or True
    groq.choose.click()
    assert not questions and app.settings.speech_cloud_models["groq"] == "whisper-large-v3-turbo"  # asked once only


def test_testing_a_cloud_model_shows_its_answer():
    window, app = _window()
    page = window.pages["speech"]
    openai = page.models["openai"]
    openai._test()
    assert "Enter your OpenAI API key" in openai.result.text()
    openai.key.setText("sk-test")
    openai._test()
    for _ in range(200):
        QApplication.processEvents()
        if openai.result.text().startswith("OK"):
            break
        time.sleep(0.01)
    assert openai.result.text().startswith("OK: gpt-4o-mini-transcribe answered")
    assert ("test_cloud_speech", "openai", "gpt-4o-mini-transcribe") in app.calls


def test_a_key_saved_on_one_page_shows_on_the_other():
    window, app = _window(gateway=GatewayConfig(provider="openai", api_key="sk-old"))
    speech, cleanup = window.pages["speech"], window.pages["cleanup"]
    openai = speech.models["openai"]
    assert openai.key.text() == "sk-old" and cleanup.api_key.text() == "sk-old"
    speech.confirm = lambda question: True
    openai.key.setText("sk-new")
    openai.choose.click()
    window.show_page("cleanup")
    assert cleanup.api_key.text() == "sk-new"
    _, _, _, gateway = cleanup.result()
    assert gateway.key_for("openai") == "sk-new"
    cleanup.api_key.setText("sk-typed")  # being typed: a refresh leaves it alone
    app.gateway = app.gateway.with_key("openai", "sk-other")
    cleanup.refresh()
    assert cleanup.api_key.text() == "sk-typed"


def test_saving_the_cleanup_keeps_a_key_saved_for_speech():
    window, app = _window(gateway=GatewayConfig(provider="openai", api_key="sk-cleanup"))
    cleanup = window.pages["cleanup"]
    app.gateway = app.gateway.with_key("groq", "gsk-speech")  # saved on the speech page after this page was built
    _, _, _, gateway = cleanup.result()
    assert gateway.key_for("groq") == "gsk-speech" and gateway.key_for("openai") == "sk-cleanup"
    cleanup.provider.setCurrentIndex(cleanup.provider.findData("groq"))
    assert cleanup.api_key.text() == "gsk-speech"  # the same key, ready for cleanup with Groq


def test_choosing_a_downloaded_speech_model_and_its_language(whisper_downloaded):
    window, app = _window()
    page = window.pages["speech"]
    page.refresh()
    whisper = page.models["whisper-turbo"]
    assert whisper.download.isHidden() and not whisper.choose.isHidden() and whisper.status.text() == "Downloaded"
    assert not whisper.remove.isHidden() and not whisper.language_row.isHidden()
    whisper.choose.click()
    assert ("choose_speech_model", "whisper-turbo") in app.calls
    app.loading_speech = "whisper-turbo"
    page.refresh()
    assert whisper.status.text() == "Loading..." and whisper.remove.isHidden()  # the chosen model can't be removed
    whisper.language.setCurrentIndex(whisper.language.findData("ta"))
    assert ("set_speech_language", "ta") in app.calls and app.settings.speech_language == "ta"


def test_downloading_a_speech_model_shows_its_progress():
    window, app = _window()
    page = window.pages["speech"]
    whisper, parakeet = page.models["whisper-turbo"], page.models["parakeet"]
    whisper.download.click()
    assert ("download_speech_model", "whisper-turbo") in app.calls
    app.downloading = ("whisper-turbo", 400_000_000, 1_600_000_000)
    page.refresh()
    assert whisper.status.text().startswith("Downloading 25%") and whisper.progress.value() == 250
    assert whisper.download.isHidden() and not whisper.cancel.isHidden()
    assert "In use" in parakeet.status.text()  # dictation goes on with Parakeet meanwhile
    whisper.cancel.click()
    assert ("cancel_download",) in app.calls


def test_the_scan_card_runs_shows_progress_and_the_verdicts(tmp_path):
    from sst import scan
    window, app = _window()
    card = window.pages["speech"].scan
    card.refresh()
    assert card.button.isEnabled() and card.button.text() == "Scan my computer" and card.result.isHidden()
    card.button.click()
    assert ("scan_computer",) in app.calls
    app.scanning = "Trying NVIDIA Parakeet on a short sentence..."
    card.refresh()
    assert not card.button.isEnabled() and "Trying" in card.status.text()
    pc = scan.Computer(processor="Test CPU", cores=10, threads=12, memory_gb=32, free_memory_gb=16, free_disk_gb=200,
                       graphics=["Intel(R) Iris(R) Xe Graphics"], score=scan.REFERENCE_SCORE)
    local = [m for m in w.SPEECH_MODELS.values() if m.where == "local"]
    app.scanning, app.last_scan = "", scan.save(pc, scan.judge(pc, local, {"parakeet": 0.9}),
                                                 tmp_path / "scan.json")
    card.refresh()
    lines = [label.text() for label in card.result.findChildren(w.QLabel)]
    assert any("Test CPU" in line and "no NVIDIA card" in line for line in lines)
    assert any("NVIDIA Parakeet: Recommended. About 0.9 s" in line for line in lines)
    assert any("Slow on this computer" in line and "other languages" in line for line in lines)
    assert card.button.text() == "Scan again" and "Last scan" in card.status.text()


def test_the_server_card_starts_from_ai_cleanups_server_and_lists_its_speech_models():
    window, app = _window(gateway=GatewayConfig("http://gateway.example/v1", "gw-key", "vllm"))
    page = window.pages["speech"]
    page.show_where("server")
    server = page.models["server"]
    assert (server.address.text(), server.key.text()) == ("http://gateway.example/v1", "gw-key")
    assert "Filled in from AI cleanup's server" in server.result.text()
    server.choose.click()
    assert "Choose a model first" in server.result.text() and not app.calls
    server._load_models()
    assert _wait_until(lambda: server.result.text().startswith("Loaded"))
    assert server.model_box.currentText() == "whisper-1" and "1 of them for speech" in server.result.text()
    server._test()
    assert _wait_until(lambda: server.result.text().startswith("OK"))
    assert ("test_server_speech", "http://gateway.example/v1", "whisper-1") in app.calls
    server.choose.click()  # no question: the user's own server
    assert ("use_server_speech", "http://gateway.example/v1", "whisper-1") in app.calls
    assert app.gateway.speech_server() == ("http://gateway.example/v1", "gw-key") and app.gateway.service.key == "vllm"
    page.refresh()
    assert "In use" in server.status.text() and server.choose.isHidden()
    server.address.setText("http://localhost:8000/v1")
    assert server.choose.text() == "Save" and not server.choose.isHidden()


def test_the_server_card_needs_an_address():
    window, app = _window()
    server = window.pages["speech"].models["server"]
    assert server.address.text() == "" and server.result.isHidden()
    server._load_models()
    assert "Enter your server's address first" in server.result.text() and not app.calls


def _wait_until(condition) -> bool:
    for _ in range(200):
        QApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()
