"""Learning from corrections: a pattern the user fixes again and again becomes a suggestion, and only an accepted
suggestion becomes a dictionary rule. Ordinary edits (grammar, casing, rewrites) teach nothing."""
import time

import pytest

from sst.pipeline.contracts import DictionaryConfig
from sst.pipeline.dictionary import DictionaryEngine, DictionaryStore, TermMode
from sst.pipeline.learning import CorrectionEvent, Learner


@pytest.fixture
def store():
    s = DictionaryStore()
    yield s
    s.close()


def event(original, corrected, at=None):
    return CorrectionEvent(original, corrected, app="notepad.exe", timestamp=at or time.time(), session_id="sess_1")


def test_one_correction_is_only_observed(store):
    learner = Learner(store)
    assert learner.observe(event("Post grass is broken", "PostgreSQL is broken")) == []
    assert learner.suggestions() == []


def test_the_same_correction_twice_is_suggested_once(store):
    learner = Learner(store)
    learner.observe(event("Post grass is broken", "PostgreSQL is broken", at=100.0))
    [suggestion] = learner.observe(event("restart post grass now.", "restart PostgreSQL now.", at=200.0))
    assert (suggestion.original_phrase, suggestion.corrected_phrase) == ("post grass", "PostgreSQL")
    assert (suggestion.seen_count, suggestion.confirmed_count, suggestion.last_seen) == (2, 0, 200.0)
    assert learner.suggestions() == [suggestion]
    assert learner.observe(event("post grass again", "PostgreSQL again")) == []  # not announced a second time
    assert learner.suggestions()[0].seen_count == 3


def test_an_accepted_suggestion_is_applied_by_the_engine(store):
    learner = Learner(store)
    for _ in range(2):
        learner.observe(event("Post grass is broken", "PostgreSQL is broken"))
    [suggestion] = learner.suggestions()
    term = learner.accept(suggestion)
    assert (term.preferred, term.aliases, term.mode, term.source) == ("PostgreSQL", ["post grass"], TermMode.CAREFUL, "learned")
    assert DictionaryEngine(store, DictionaryConfig()).correct("Post grass, please.").text == "PostgreSQL, please."
    assert learner.suggestions() == []
    row = store._db.execute("SELECT status, confirmed_count FROM learned_candidates").fetchone()
    assert row == ("accepted", 1)


def test_accepting_onto_an_existing_term_adds_an_alias(store):
    store.sync_vocabulary(["Kubernetes"])
    learner = Learner(store)
    for _ in range(2):
        learner.observe(event("scale cooper netties up", "scale Kubernetes up"))
    term = learner.accept(learner.suggestions()[0])
    assert (term.preferred, term.aliases, term.source) == ("Kubernetes", ["cooper netties"], "vocabulary")
    assert DictionaryEngine(store).correct("cooper netties").text == "Kubernetes"


def test_a_rejected_suggestion_never_comes_back(store):
    learner = Learner(store)
    for _ in range(2):
        learner.observe(event("call jon", "call John-Paul"))
    [suggestion] = learner.suggestions()
    learner.reject(suggestion)
    assert learner.suggestions() == []
    for _ in range(3):
        assert learner.observe(event("call jon", "call John-Paul")) == []
    assert learner.suggestions() == []
    assert store.find("John-Paul") is None


def test_casing_and_punctuation_of_ordinary_words_teach_nothing(store):
    learner = Learner(store)
    for _ in range(3):
        assert learner.observe(event("hello world this is fine", "Hello World, this is fine!")) == []
    assert learner.pairs("i think so", "I think so.") == []


def test_casing_of_a_name_or_term_is_learned(store):
    learner = Learner(store)
    learner.observe(event("I pushed it to github today", "I pushed it to GitHub today"))
    [suggestion] = learner.observe(event("is github down", "is GitHub down"))
    assert (suggestion.original_phrase, suggestion.corrected_phrase) == ("github", "GitHub")
    term = learner.accept(suggestion)
    assert (term.preferred, term.aliases) == ("GitHub", [])
    assert DictionaryEngine(store).correct("on github").text == "on GitHub"


def test_capitalising_the_first_word_of_a_sentence_is_grammar(store):
    learner = Learner(store)
    assert learner.pairs("kubectl is great", "Kubectl is great") == []
    assert learner.pairs("done. kubectl next", "done. Kubectl next") == []
    assert learner.pairs("we speak tamil", "we speak Tamil") == [("tamil", "Tamil")]


def test_swapping_one_ordinary_word_for_another_teaches_nothing(store):
    learner = Learner(store)
    for _ in range(3):
        assert learner.observe(event("put it over their", "put it over there")) == []
        assert learner.observe(event("I am polishing the post", "I am publishing the post")) == []
    assert learner.suggestions() == []


def test_long_rewrites_teach_nothing(store):
    learner = Learner(store)
    assert learner.pairs("post grass is broken today", "the database server needs a restart") == []
    assert learner.pairs("we will meet at the post office on monday morning early",
                         "we will meet Kubernetes PostgreSQL Terraform Grafana Jenkins team on monday morning early") == []


def test_insertions_and_deletions_teach_nothing(store):
    learner = Learner(store)
    assert learner.pairs("restart the server", "restart the PostgreSQL server") == []
    assert learner.pairs("restart the old server", "restart the server") == []


def test_a_swallowed_function_word_is_trimmed(store):
    learner = Learner(store)
    assert learner.pairs("ask the cloud about it", "ask Claude about it") == [("cloud", "Claude")]


def test_numbers_are_the_formatters_business(store):
    learner = Learner(store)
    assert learner.pairs("I need five servers", "I need 5 servers") == []


def test_undoing_a_dictionary_replacement_is_not_learned(store):
    store.add_term("PostgreSQL", ["post grass"], mode=TermMode.AUTOMATIC)
    learner = Learner(store)
    assert learner.pairs("the PostgreSQL is wet", "the post grass is wet") == []
    assert learner.pairs("post grass works", "PostgreSQL works") == []  # already an alias: nothing new


def test_suggestions_are_ordered_by_how_often_they_were_seen(store):
    learner = Learner(store)
    for _ in range(2):
        learner.observe(event("ask clawed", "ask Claude"))
    for _ in range(4):
        learner.observe(event("use kuber netties", "use Kubernetes"))
    assert [s.corrected_phrase for s in learner.suggestions()] == ["Kubernetes", "Claude"]


def test_min_seen_is_configurable(store):
    learner = Learner(store, min_seen=3)
    assert learner.observe(event("ask clawed", "ask Claude")) == []
    assert learner.observe(event("ask clawed", "ask Claude")) == []
    assert len(learner.observe(event("ask clawed", "ask Claude"))) == 1


def test_an_ambiguous_suggestion_is_refused_and_stays_pending(store):
    learner = Learner(store)
    for _ in range(2):
        learner.observe(event("ask clawed", "ask Claude"))
    [suggestion] = learner.suggestions()
    store.add_term("Clawed Inc", ["clawed"])  # meanwhile the phrase became another term's alias
    with pytest.raises(ValueError):
        learner.accept(suggestion)
    assert learner.suggestions() == [suggestion]


def test_candidates_survive_reopening(tmp_path):
    path = tmp_path / "dictionary.sqlite3"
    store = DictionaryStore(path)
    Learner(store).observe(event("ask clawed", "ask Claude"))
    store.close()
    store = DictionaryStore(path)
    [suggestion] = Learner(store).observe(event("ask clawed", "ask Claude"))
    assert suggestion.seen_count == 2
    store.close()


def test_each_event_gets_its_own_timestamp():
    first = CorrectionEvent("a", "b")
    time.sleep(0.01)
    assert CorrectionEvent("a", "b").timestamp > first.timestamp
