"""The reading test's scoring: fair word comparison, error counts, suggestions (no microphone or model needed)."""
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
    ("um so uh the the plan", ["so", "the", "the", "plan"]),  # fillers aren't in the sentences: hearing one isn't wrong
    ("Hold Ctrl and Win", ["hold", "control", "and", "win"]),  # Parakeet may write the key's name short
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


def _recording(folder, name, sentence):
    folder.mkdir(parents=True, exist_ok=True)
    save_wav(folder / f"{name}.wav", np.zeros(1600, dtype=np.float32), 16000)
    (folder / f"{name}.txt").write_text(sentence, encoding="utf-8")


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
    for k, sentence in enumerate(bench.SENTENCES[1:], 2):  # a test from before the sets: set A
        _recording(newest, f"{k:02}", sentence)
    assert bench.unfinished(tmp_path) is None  # all read: the next test gets a new folder


def test_five_sets_of_thirty_distinct_sentences_with_set_a_unchanged():
    everything = [sentence for block in bench.BLOCKS.values() for sentence in block]
    assert list(bench.BLOCKS) == ["A", "B", "C", "D", "E"] and all(len(b) == 30 for b in bench.BLOCKS.values())
    assert len(set(everything)) == 150 and bench.BLOCKS["A"] is bench.SENTENCES
    assert set(bench.TUNING) < set(bench.BLOCKS)


def test_the_test_sets_use_look_alike_words_literally():
    test_words = {w for block in "CDE" for sentence in bench.BLOCKS[block] for w in words(sentence)}
    assert {"cloud", "clot", "publishing", "claude", "commit"} <= test_words


def test_every_term_appears_in_the_sentences_and_terms_skip_common_words():
    said = {w for block in bench.BLOCKS.values() for sentence in block for w in words(sentence)}
    terms = bench.term_words()
    assert {"sherpa", "onnx", "claude", "codeql"} <= terms and not terms & {"the", "and"}
    assert terms <= said


def test_a_session_remembers_its_set_and_microphone(tmp_path):
    folder = tmp_path / "2026-10-01_090000"
    assert bench.read_session(folder)["block"] == "A"  # no notes: a test from before the sets
    bench.write_session(folder, "C", "Laptop mic", {"device": "Microphone Array", "host_api": "MME", "rate": 44100})
    session = bench.read_session(folder)
    assert (session["block"], session["device"], session["rate"]) == ("C", "Microphone Array", 44100)
    assert session["version"] and bench.sentences_for(folder) == bench.BLOCKS["C"]


def test_the_next_set_is_the_one_read_the_fewest_times(tmp_path):
    assert bench.next_block(tmp_path) == "A"
    for block, count in (("A", 30), ("B", 12)):  # A finished, B only started
        folder = tmp_path / f"2026-10-01_09000{ord(block) - 65}"
        bench.write_session(folder, block, "mic", {})
        for k, sentence in enumerate(bench.BLOCKS[block][:count], 1):
            _recording(folder, f"{k:02}", sentence)
    assert bench.next_block(tmp_path) == "B"  # an unfinished set still counts as not read
    assert [f.name for f in bench.sessions(tmp_path)] == ["2026-10-01_090000", "2026-10-01_090001"]


def test_sessions_leave_out_summaries_and_other_profiles(tmp_path):
    _recording(tmp_path / "2026-10-01_090000", "01", "A sentence.")
    _recording(tmp_path / "summary", "01", "Not a test.")
    (tmp_path / "profiles" / "rahul").mkdir(parents=True)
    assert [f.name for f in bench.sessions(tmp_path)] == ["2026-10-01_090000"]


def test_suggestions_are_spelled_as_in_the_sentences_and_skip_common_words():
    misheard = {("tamil", "tamar"): 2, ("in", "on"): 3, ("codeql", "code"): 1}
    assert bench.suggest(misheard, ["Learn Tamil in CodeQL."]) == ["Tamil", "CodeQL"]


def test_ordinary_words_are_not_suggested_even_when_misheard():
    # From the owner's first test: "lunch -> once", "tray -> trap", "Vercel -> versal", "Control -> ..."
    misheard = {("lunch", "once"): 1, ("tray", "trap"): 1, ("vercel", "versal"): 1, ("control", "cntrl"): 1,
                ("worse", "was"): 1}
    sentences = ["Review it before lunch.", "The tray app shows a pill.", "The website on Vercel is live.",
                 "Hold Control and speak.", "It sounds worse."]
    assert bench.suggest(misheard, sentences) == ["Vercel", "Control"]
