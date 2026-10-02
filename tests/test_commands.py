"""Voice commands for Text Transform (sst.commands): a whole dictation that is one command phrase is a command; anything
else, even with a command's words in it, is dictation and is typed."""
import pytest

from sst.commands import DEFAULT_PHRASES, MAX_WORDS, UNDO, match_command, normalize, parse_phrases, phrases_for

DEFAULTS = phrases_for({})


@pytest.mark.parametrize("said, command", [
    ("Make it concise.", "concise"),
    ("make it shorter", "concise"),
    ("Make this shorter, please.", "concise"),  # "this" is "it"; "please" around it
    ("Okay, make it concise.", "concise"),
    ("Make it more professional.", "professional"),
    ("make that formal", "professional"),
    ("Bullet points.", "bullets"),
    ("Turn it into a list.", "bullets"),
    ("Action items.", "actions"),
    ("Make it a to-do list.", "actions"),
    ("make it a todo list", "actions"),
    ("Rewrite it.", "rewrite"),
    ("Undo that.", UNDO),
    ("Undo.", UNDO),
    ("Put it back.", UNDO),
    ("Make it consise.", "concise"),  # misheard a little
    ("Make it profesional.", "professional"),
    ("MAKE IT CONCISE!", "concise"),
])
def test_command_phrases_are_found(said, command):
    assert match_command(said, DEFAULTS) == command


@pytest.mark.parametrize("said", [
    "Make it concise and send it to John.",  # more than the command: dictation
    "Please make it concise before the meeting on Monday.",
    "Professional.",  # one word people dictate: not a default phrase
    "Concise.",
    "Make it.",  # a fragment of a phrase is not the phrase
    "The action items are on the board.",
    "Can you make it shorter for the client?",
    "I need bullet points for the slides tomorrow.",
    "",
    "   ",
    "Make it count.",
])
def test_dictation_is_not_a_command(said):
    assert match_command(said, DEFAULTS) is None


def test_long_dictation_is_never_a_command():
    long = " ".join(["make it concise"] * 3)
    assert len(long.split()) > MAX_WORDS and match_command(long, DEFAULTS) is None


def test_the_users_own_phrases_replace_the_defaults():
    phrases = phrases_for({"concise": "trim it, tighten this up", "bullets": ""})
    assert match_command("Trim it.", phrases) == "concise"
    assert match_command("Tighten this up.", phrases) == "concise"
    assert match_command("Make it concise.", phrases) is None  # the defaults for Concise were replaced
    assert match_command("Bullet points.", phrases) is None  # no phrase: that command is off
    assert match_command("Make it professional.", phrases) == "professional"  # untouched: the defaults
    assert phrases["professional"] == list(DEFAULT_PHRASES["professional"])


def test_a_one_word_phrase_works_when_the_user_adds_it():
    assert match_command("Professional!", phrases_for({"professional": "professional"})) == "professional"


@pytest.mark.parametrize("text, phrases", [
    ("make it short, trim it", ["make it short", "trim it"]),
    ("make it short\ntrim it;  shorten   this ", ["make it short", "trim it", "shorten this"]),
    (" , ,, ", []),
    ("trim it, trim it", ["trim it"]),
])
def test_parse_phrases(text, phrases):
    assert parse_phrases(text) == phrases


def test_normalize():
    assert normalize("Um, so make THIS shorter, please!") == "make it shorter"
    assert normalize("Make it a to-do list") == "make it a to do list"
    assert normalize("Hey Rflow, rewrite that for me, thanks.") == "rewrite it"
