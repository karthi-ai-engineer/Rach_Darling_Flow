import json

from sst import settings
from sst.settings import Settings, add_to_history, read_history


def test_missing_file_gives_defaults(tmp_path):
    assert Settings.load(tmp_path / "none.json") == Settings()


def test_round_trip(tmp_path):
    path = tmp_path / "sub" / "settings.json"
    Settings(hotkey="menu", microphone="マイク (Realtek(R) Audio)", sounds=False, save_recordings=False).save(path)
    assert Settings.load(path) == Settings(hotkey="menu", microphone="マイク (Realtek(R) Audio)", sounds=False,
                                           save_recordings=False)


def test_damaged_file_gives_defaults_so_the_app_still_starts(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    assert Settings.load(path) == Settings()
    path.write_text("[1, 2]", encoding="utf-8")
    assert Settings.load(path) == Settings()


def test_unknown_keys_and_wrong_types_are_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"hotkey": "menu", "sounds": "yes", "save_recordings": 1, "future_option": 3}),
                    encoding="utf-8")
    assert Settings.load(path) == Settings(hotkey="menu")


def test_history_is_newest_first_and_skips_damaged_lines(tmp_path):
    path = tmp_path / "history.jsonl"
    add_to_history("first", path=path)
    add_to_history("second — with ünïcödé", path=path)
    with path.open("a", encoding="utf-8") as f:
        f.write("{ broken line\n")
    assert [e["text"] for e in read_history(path)] == ["second — with ünïcödé", "first"]


def test_history_is_trimmed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "HISTORY_KEEP", 5)
    path = tmp_path / "history.jsonl"
    for i in range(12):
        add_to_history(f"entry {i}", path=path)
    assert [e["text"] for e in read_history(path)] == [f"entry {i}" for i in range(11, 6, -1)]
    assert len(path.read_text(encoding="utf-8").splitlines()) == 5


def test_cleanup_model_and_vocabulary_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    chosen = Settings(cleanup_model="unsloth/Qwen3.8-27B-NVFP4", vocabulary=["Claude Code", "தமிழ்", "GitHub"])
    chosen.save(path)
    assert Settings.load(path) == chosen


def test_a_vocabulary_that_is_not_a_list_of_words_is_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"vocabulary": ["ok", 3], "cleanup_model": 5}), encoding="utf-8")
    assert Settings.load(path) == Settings()


def test_history_keeps_what_was_heard_when_the_cleanup_changed_it(tmp_path):
    path = tmp_path / "history.jsonl"
    add_to_history("Hello, world.", "hello world", path=path)
    add_to_history("Same.", "Same.", path=path)
    newest, oldest = read_history(path)
    assert oldest == {"time": oldest["time"], "text": "Hello, world.", "heard": "hello world"}
    assert "heard" not in newest
