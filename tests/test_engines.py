"""The speech model catalog: what the Speech recognition page lists, and which model a setting loads."""
import json

from sst import engines, evaluate
from sst.settings import Settings


def test_every_model_says_where_it_runs_and_what_it_is():
    for key, model in engines.SPEECH_MODELS.items():
        assert model.key == key and model.where in engines.WHERE
        assert model.name and model.summary and model.languages and model.size


def test_parakeet_is_the_default_and_the_models_ready_now_can_be_loaded():
    assert engines.DEFAULT_MODEL == "parakeet" and engines.SPEECH_MODELS["parakeet"].ready
    assert engines.ENGINES == [k for k, m in engines.SPEECH_MODELS.items() if m.ready]
    assert engines.SPEECH_MODELS["whisper-turbo"].download is not None  # downloaded when chosen


def test_a_setting_this_version_cannot_use_falls_back_to_the_default():
    assert engines.usable("parakeet") == "parakeet"
    assert engines.usable("whisper-turbo") == "parakeet"  # chosen but not downloaded (removed, or another laptop)
    assert engines.usable("from-a-newer-version") == "parakeet"


def test_a_downloaded_model_is_used(whisper_downloaded):
    assert engines.usable("whisper-turbo") == "whisper-turbo"


def test_the_chosen_model_is_a_profile_setting_and_old_files_get_parakeet(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"hotkey": "menu"}), encoding="utf-8")  # from before the choice existed
    assert Settings.load(path).speech_model == "parakeet"
    Settings(speech_model="whisper-turbo").save(path)
    assert Settings.load(path).speech_model == "whisper-turbo"


def test_reports_name_the_model_in_use():
    names = [p.name for p in evaluate.pipelines_for({"qwen": object()}, ["narrowband"], title="Whisper")]
    assert names == ["Whisper alone", "Whisper, narrowband", "Whisper + qwen"]
    assert evaluate.pipelines_for({})[0].name == "Parakeet alone"  # the default, as before
