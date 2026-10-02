"""Snippets (sst.snippets): "my email" types the user's email. Found in the words heard; the text goes in last, exactly."""
import pytest

from sst.snippets import Snippet, alone, compact, expand, load, protect

EMAIL = Snippet("my email", "xyz@gmail.com")
SIGNATURE = Snippet("my signature", "Best regards,\nKarthi\nKarthi Labs")
ADDRESS = Snippet("insert my address", "1-2-3 Shibuya, Tokyo 150-0002", anywhere=True)
MINE = [EMAIL, SIGNATURE, ADDRESS]


@pytest.mark.parametrize("said, snippet", [
    ("My email.", EMAIL),
    ("my email", EMAIL),
    ("Um, my e-mail, please.", EMAIL),  # a filler, a hyphen, "please"
    ("MY EMAIL!", EMAIL),
    ("My signature.", SIGNATURE),
    ("My signeture.", SIGNATURE),  # misheard a little
    ("Insert my address.", ADDRESS),  # an anywhere-snippet counts alone too
])
def test_a_whole_dictation_that_is_a_cue_is_the_snippet(said, snippet):
    assert alone(said, MINE) == snippet


@pytest.mark.parametrize("said", [
    "I checked my email this morning.",  # the cue inside a sentence: dictation, for an alone-snippet
    "Signature.",  # a word short is not the cue
    "My emails.",
    "Email.",
    "",
    "my email my email",
])
def test_anything_else_is_dictation(said):
    assert alone(said, MINE) is None


def test_inside_a_sentence_only_an_anywhere_snippet_counts():
    text, slots = protect("Please send the parcel to insert my address, and check my email.", MINE)
    assert text == "Please send the parcel to RFSNIP1, and check my email." and slots == {"RFSNIP1": ADDRESS}
    assert expand(text, slots) == "Please send the parcel to 1-2-3 Shibuya, Tokyo 150-0002, and check my email."


def test_a_cue_is_whole_words_never_part_of_one():
    snippet = Snippet("my id", "ID-42", anywhere=True)
    assert protect("my idea is good", [snippet]) == ("my idea is good", {})
    assert protect("send my-id now", [snippet])[0] == "send RFSNIP1 now"


def test_every_occurrence_gets_its_own_placeholder_and_the_longest_cue_wins():
    short, long = Snippet("my mail", "short", anywhere=True), Snippet("my mail address", "long", anywhere=True)
    text, slots = protect("my mail address, then my mail", [short, long])
    assert text == "RFSNIP1, then RFSNIP2" and expand(text, slots) == "long, then short"


def test_the_snippets_text_comes_back_exactly_line_breaks_included():
    text, slots = protect("Thanks! insert my signature", [Snippet("insert my signature", SIGNATURE.text, anywhere=True)])
    assert expand(text, slots) == "Thanks! Best regards,\nKarthi\nKarthi Labs"


@pytest.mark.parametrize("after", [
    "Please send the parcel.",  # the AI dropped the placeholder
    "Send it to RFSNIP1 and RFSNIP1.",  # doubled
    "Send it to XRFSNIP1.",  # glued into a word
])
def test_a_placeholder_the_ai_lost_or_doubled_is_refused(after):
    assert expand(after, {"RFSNIP1": ADDRESS}) is None


def test_a_placeholder_in_other_case_still_counts():
    assert expand("Send it to rfsnip1.", {"RFSNIP1": ADDRESS}) == "Send it to 1-2-3 Shibuya, Tokyo 150-0002."


def test_loading_keeps_usable_snippets_once():
    entries = [EMAIL.to_dict(), {"cue": "My E-mail", "text": "other"}, {"cue": "", "text": "x"}, {"cue": "x"},
               {"cue": " ".join(["word"] * 9), "text": "too long a cue"}, "not a dict", ADDRESS.to_dict()]
    assert load(entries) == [EMAIL, ADDRESS]  # the second "my email" is the same cue: the first wins


def test_compact():
    assert compact("My e-mail.") == compact("my email") == "myemail"
    assert compact("Karthi’s laptop") == "karthislaptop"
