"""The reading test's scoring: fair word comparison, error counts, suggestions (no microphone or model needed)."""
import json

import numpy as np
import pytest

from sst import bench
from sst.audio import save_wav
from sst.bench import align, errors, words


@pytest.mark.parametrize("text, expected", [
    ("Let's move the Stand-up!", ["lets", "move", "the", "stand", "up"]),
    ("in about 70 seconds", ["in", "about", "seventy", "seconds"]),
    ("20 to 50 names", ["twenty", "to", "fifty", "names"]),
    ("room for 6 at 2 o'clock", ["room", "for", "six", "at", "two", "oclock"]),
    ("1,250 files", ["one", "two", "hundred", "fifty", "files"]),  # a comma splits the number, as a reader would say it
    ("OK, it's fine", ["okay", "its", "fine"]),
])
def test_words_are_normalised_for_a_fair_comparison(text, expected):
    assert words(text) == expected


def test_digits_and_spelled_numbers_are_the_same_answer():
    assert errors("I need 20 to 50 names.", "i need twenty to fifty names")[0] == 0


def test_alignment_finds_substitutions_missing_and_extra_words():
    pairs = align(["can", "you", "talk", "in", "tamil"], ["can", "you", "talk", "of", "them", "please"])
    wrong = [p for p in pairs if p[0] != p[1]]
    assert len(wrong) == 3
    assert ("in", "of") in wrong and ("tamil", "them") in wrong and ("", "please") in wrong


def test_errors_counts_against_the_sentence():
    wrong, total, pairs = errors("Please create a merge commit instead.", "please create a merge clot instead")
    assert (wrong, total, pairs) == (1, 6, [("commit", "clot")])


class FakeEngine:
    def __init__(self, heard):
        self.heard = heard

    def transcribe(self, audio, rate):
        return self.heard.pop(0)


class FakePolisher:
    def __init__(self, fixes):
        self.fixes = fixes

    def polish(self, text):
        for wrong, right in self.fixes.items():
            text = text.replace(wrong, right)
        return text


def _recording(folder, name, sentence):
    save_wav(folder / f"{name}.wav", np.zeros(1600, dtype=np.float32), 16000)
    (folder / f"{name}.txt").write_text(sentence, encoding="utf-8")


def test_score_compares_every_setup_and_suggests_words(tmp_path):
    _recording(tmp_path, "01", "I would like to learn Tamil and Japanese.")
    _recording(tmp_path, "02", "Please create a merge commit in CodeQL.")
    engine = FakeEngine(["i would like to learn tamar and japanese", "please create a merge clot in code ql"])
    polishers = {"Parakeet + model-a": FakePolisher({"tamar": "Tamil", "clot": "commit"})}
    results = bench.score(tmp_path, engine, polishers)

    alone, cleaned = results.setups
    assert alone.name == "Parakeet alone" and (alone.wrong, alone.total) == (4, 15)
    assert (cleaned.wrong, cleaned.total) == (2, 15)  # the model fixed Tamil and commit, not "CodeQL"
    assert cleaned.error_rate < alone.error_rate
    assert ("tamil", "tamar", 1) in results.misheard
    assert results.suggestions[:1] == ["Tamil"] and "CodeQL" in results.suggestions  # as written in the sentences
    assert "in" not in results.suggestions  # common words wouldn't help
    saved = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert saved["setups"][0]["error_rate"] == pytest.approx(4 / 15)
    assert "| Parakeet alone | 26.7% | 4 / 15 |" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_only_recorded_sentences_are_scored(tmp_path):
    _recording(tmp_path, "01", "One sentence.")
    save_wav(tmp_path / "02.wav", np.zeros(1600, dtype=np.float32), 16000)  # recorded, but no sentence saved
    assert [s for _, s in bench.recordings(tmp_path)] == ["One sentence."]


def test_only_the_newest_test_is_resumed_and_only_while_unfinished(tmp_path):
    assert bench.unfinished(tmp_path / "missing") is None
    (tmp_path / "profiles" / "rahul").mkdir(parents=True)  # another profile's tests: not a test folder
    (tmp_path / "2026-09-01_090000").mkdir()
    _recording(tmp_path / "2026-09-01_090000", "01", "Old sentence.")
    newest = tmp_path / "2026-09-30_090000"
    newest.mkdir()
    assert bench.unfinished(tmp_path) is None  # nothing read in the newest test: start afresh
    _recording(newest, "01", "A sentence.")
    assert bench.unfinished(tmp_path) == newest
    for k, sentence in enumerate(bench.SENTENCES[1:], 2):
        _recording(newest, f"{k:02}", sentence)
    assert bench.unfinished(tmp_path) is None  # all read: the next test gets a new folder


def test_there_are_thirty_distinct_sentences():
    assert len(bench.SENTENCES) == 30 and len(set(bench.SENTENCES)) == 30
