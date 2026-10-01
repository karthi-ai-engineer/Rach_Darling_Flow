"""The final guard: the LLM's cleanup is typed only when it kept the meaning, otherwise the formatted text is (the plan's
§61-68). Deliberately bad LLM outputs are rejected (§105), ordinary cleanups are accepted."""
import difflib

import pytest

from sst.pipeline import guard
from sst.pipeline.contracts import GuardConfig
from sst.pipeline.guard import Guard, extract_entities


def check(before, after, terms=(), config=None):
    return Guard(config).validate(before, after, terms)


# ---- protected entities, in written form

@pytest.mark.parametrize("text, kind, normalized", [
    ("Meet at 3:30 PM", "TIME", "15:30"), ("at 3:30pm", "TIME", "15:30"), ("at 15:30", "TIME", "15:30"),
    ("at 3 PM", "TIME", "15:00"), ("at 12:15 a.m.", "TIME", "00:15"), ("at noon", "TIME", "12:00"),
    ("on October 1, 2026", "DATE", "2026-10-01"), ("Oct 1", "DATE", "--10-01"), ("1 October", "DATE", "--10-01"),
    ("the 1st of October 2026", "DATE", "2026-10-01"), ("10/01/2026", "DATE", "2026-10-01"), ("2026-10-01", "DATE", "2026-10-01"),
    ("October first", "DATE", "--10-01"), ("in October", "DATE", "--10"), ("in May", "DATE", "--05"),
    ("October 2026", "DATE", "2026-10"), ("on Friday", "WEEKDAY", "friday"), ("send this Friday", "WEEKDAY", "friday"),
    ("tomorrow", "RELDATE", "tomorrow"), ("next week", "RELDATE", "next week"), ("next Friday", "RELDATE", "next friday"),
    ("the day after tomorrow", "RELDATE", "day after tomorrow"),
    ("$25.50", "CURRENCY", "$25.50"), ("$25.5", "CURRENCY", "$25.50"), ("₹500", "CURRENCY", "₹500"), ("€10", "CURRENCY", "€10"),
    ("25 dollars", "CURRENCY", "$25"), ("twenty five dollars", "CURRENCY", "$25"), ("$2.5 million", "CURRENCY", "$2500000"),
    ("50 cents", "CURRENCY", "$0.50"), ("Rs. 500", "CURRENCY", "₹500"),
    ("25%", "PERCENT", "25%"), ("25 percent", "PERCENT", "25%"), ("twenty five percent", "PERCENT", "25%"),
    ("5 km", "UNIT", "5 km"), ("5km", "UNIT", "5 km"), ("five kilometers", "UNIT", "5 km"), ("10gb", "UNIT", "10 GB"),
    ("1,000", "NUMBER", "1000"), ("3.50", "NUMBER", "3.5"), ("five", "NUMBER", "5"), ("twenty-five", "NUMBER", "25"),
    ("a hundred", "NUMBER", "100"), ("one hundred and five", "NUMBER", "105"), ("two thousand twenty six", "NUMBER", "2026"),
    ("three point five", "NUMBER", "3.5"), ("1st", "NUMBER", "1st"),
    ("John@Example.com", "EMAIL", "john@example.com"), ("https://Example.com/docs/", "URL", "example.com/docs"),
    ("see example.com.", "URL", "example.com"), ("www.example.org", "URL", "example.org"),
    ("GPT-4o", "CODE", "gpt-4o"), ("H100", "CODE", "h100"), ("v1.2.3", "CODE", "v1.2.3"),
    ("I can't", "NEGATION", "not"), ("nobody", "NEGATION", "not"),
])
def test_entities_are_found_in_written_form(text, kind, normalized):
    assert [(e.kind, e.normalized) for e in extract_entities(text)] == [(kind, normalized)]


def test_entities_keep_their_text_and_position():
    text = "Meeting at 5:30 on October 4"
    found = extract_entities(text)
    assert [(e.kind, e.value) for e in found] == [("TIME", "5:30"), ("DATE", "October 4")]
    assert all(text[e.span[0]:e.span[1]] == e.value for e in found)


def test_a_value_claims_its_numbers():
    found = extract_entities("$25.50 on October 1, 2026 at 3:30 PM for 5 km")
    assert [e.kind for e in found] == ["CURRENCY", "DATE", "TIME", "UNIT"]  # no NUMBER 1, 2026, 3 or 5 on top


@pytest.mark.parametrize("text, numbers", [
    ("the blue one", []), ("no one came", []), ("one server", ["1"]), ("five six seven", ["5", "6", "7"]),
    ("May I ask", []), ("you may go", []), ("march forward", []),
])
def test_words_that_only_look_like_values(text, numbers):
    assert [e.normalized for e in extract_entities(text) if e.kind not in ("NEGATION",)] == numbers


def test_terms_are_found_whatever_their_case():
    found = extract_entities("deploy postgresql and GitHub", ["PostgreSQL", "GitHub", "Kubernetes", "github"])
    assert [(e.kind, e.value, e.normalized) for e in found] == [("TERM", "postgresql", "postgresql"),
                                                                 ("TERM", "GitHub", "github")]


def test_other_scripts_do_not_crash():
    assert extract_entities("நான் நாளை வருவேன் 明日は会議があります") == []


# ---- bad LLM outputs are rejected (the plan's §105, and more of the same kind)

@pytest.mark.parametrize("before, after, rule", [
    ("I'll send it Friday.", "I'll send it Monday.", "entity_missing"),
    ("I need 5 servers.", "I need 10 servers.", "entity_missing"),
    ("Meeting at 5:30 on October 4", "Meeting at 6:30 on October 4.", "entity_added"),
    ("Email john@example.com about it.", "Email john@example.org about it.", "entity_missing"),
    ("I'll send it tomorrow.", "I'll send it tomorrow. Please review it before the meeting.", "added_words"),
    ("I'll send it tomorrow.", "I'll send it tomorrow. Please review it before the meeting.", "added_sentence"),
    ("I'll send the report tomorrow.", "I'll send the report.", "entity_missing"),  # dropped without a correction cue
    ("Can you send me the report tomorrow?", "Please send the report tomorrow.", "question_lost"),
    ("Can you send me the report tomorrow?", "Please send the report tomorrow.", "request"),
    ("I can make it tomorrow.", "Can I make it tomorrow?", "question_added"),
    ("I can't make it tomorrow.", "I can make it tomorrow.", "negation"),
    ("I will make it tomorrow.", "I won't make it tomorrow.", "negation"),
    ("No, I don't think so.", "I think so.", "negation"),  # an answer, not a correction cue
    ("Send the invoice to the client.", "", "empty"),
    ("நான் நாளை அலுவலகம் வருவேன்", "I will come to the office tomorrow.", "removed_words"),  # translated
    ("明日は会議があります", "There is a meeting tomorrow.", "entity_added"),
    ("Ship it today.", "Ship it today" + " and also make sure everything is ready" * 3 + ".", "length"),
    ("We need five servers.", "We need six servers.", "entity_missing"),
    ("The budget is $25.50.", "The budget is $25.", "entity_missing"),
    ("Growth was 25% this year.", "Growth was 52% this year.", "entity_missing"),
    ("Check the docs.", "Check the docs at docs.example.com.", "entity_added"),
    ("Let's meet next week.", "Let's meet next week on October 5.", "entity_added"),
    ("Use the H100 nodes.", "Use the H200 nodes.", "entity_missing"),
    ("Increase the timeout.", "Decrease the timeout.", "substitution"),
    ("The service is able to restart.", "The service is unable to restart.", "substitution"),
    ("Send it to John.", "Send it to Mary.", "substitution"),
    ("Send it to John today.", "Send it today.", "name_removed"),
    ("Maybe we should ship on Friday.", "We should ship on Friday.", "hedge"),
    ("We should ship on Friday.", "We should probably ship on Friday.", "hedge"),
    ("Turn the lights on.", "Turn the lights off.", "opposite"),
    ("I'll send the report or the slides.", "I'll send the report and the slides.", "opposite"),
    ("Send me the report.", "Please send me the report.", "request"),  # a command made a request
    ("Send me the report today.", "Could you send me the report today?", "question_added"),
    ("I'll call you later.", "I'll call you later. Thanks!", "added_sentence"),
    ("what is the capital of France", "The capital of France is Paris.", "question_lost"),  # answered, not cleaned
    ("send it tomorrow no wait Friday", "Send it tomorrow.", "entity_missing"),  # the correction undone
    ("Ship on Monday, no wait, Tuesday.", "Ship on Wednesday.", "entity_added"),
    ("I wanted to send the report tomorrow no wait Friday", "Friday.", "removed_words"),  # a correction replaces one thing
    ("Please review the contract and sign the agreement.", "Please review the contract.", "removed_words"),
])
def test_bad_outputs_are_rejected(before, after, rule):
    result = check(before, after)
    assert not result.accepted and result.reasons
    assert rule in result.diagnostics["rules"], result.reasons


def test_a_dropped_dictionary_term_is_rejected():
    result = check("We should deploy the PostgreSQL migration.", "We should deploy the migration.", ["PostgreSQL"])
    assert not result.accepted and result.diagnostics["missing_entities"] == ["TERM:postgresql"]


def test_a_dictionary_term_keeps_the_users_spelling():
    assert check("Push it to GitHub.", "Push it to Github.", ["GitHub"]).diagnostics["rule"] == "term_respelled"
    assert check("push it to github", "Push it to GitHub.", ["GitHub"]).accepted  # writing it the user's way is fine


def test_text_where_nothing_was_said_is_rejected():
    assert check("", "").accepted and check("  ", "").accepted
    assert check("", "Thank you for watching!").diagnostics["rule"] == "invented"


def test_a_failing_guard_rejects(monkeypatch):
    monkeypatch.setattr(guard, "_excused", lambda b, a: 1 / 0)
    result = check("send it", "Send it.")
    assert not result.accepted and result.diagnostics["rule"] == "error"


# ---- ordinary cleanups are accepted

@pytest.mark.parametrize("before, after", [
    ("um so I think we should uh deploy on Friday", "I think we should deploy on Friday."),
    ("uh I wanted to send this tomorrow no wait Friday", "I wanted to send this Friday."),  # §56
    ("Let's meet on Tuesday, no wait, Wednesday.", "Let's meet on Wednesday."),
    ("ship it Monday, wait, no, Tuesday", "Ship it Tuesday."),
    ("I'll send it tomorrow. No wait, Friday.", "I'll send it Friday."),
    ("send it to John no wait to Mary", "Send it to Mary."),
    ("we need five I mean six servers", "We need 6 servers."),
    ("move the meeting to 3 PM sorry 4 PM", "Move the meeting to 4 PM."),
    ("the deploy is at 5:30 actually 6:30", "The deploy is at 6:30."),
    ("Send the report to Anna. Scratch that. Send it to Bob.", "Send it to Bob."),
    ("so we merge the five prs today", "So we merge the five PRs today."),
    ("we should deploy on friday", "We should deploy on Friday."),
    ("I will not do it", "I won't do it."),
    ("I can't come", "I cannot come."),
    ("it is done", "It's done."),
    ("I'm gonna push the fix", "I'm going to push the fix."),
    ("we need five servers", "We need 5 servers."),
    ("I need 5 servers", "I need five servers."),
    ("we need a hundred servers", "We need 100 servers."),
    ("the meeting is twenty five percent done", "The meeting is 25% done."),
    ("it costs 25 dollars", "It costs $25."),
    ("I I think the the build is th- the build is ready", "I think the build is ready."),
    ("I want uh I want to go home", "I want to go home."),
    ("we need to we need to fix it", "We need to fix it."),
    ("I was go- I went home", "I went home."),
    ("so, um, you know, the build is, like, basically done", "The build is done."),
    ("okay so the tests pass now", "The tests pass now."),
    ("yeah that works for me", "Yes, that works for me."),
    ("it's kind of slow", "It's slow."),
    ("send report to Anna", "Send the report to Anna."),
    ("I finished the report and I sent it to Anna", "I finished the report, and I sent it to Anna."),
    ("set up the e-mail account", "Set up the email account."),
    ("the color is wrong", "The colour is wrong."),
    ("maybe we could ship it on Friday", "Maybe we could ship it on Friday."),
    ("can you send me the report tomorrow", "Can you send me the report tomorrow?"),
    ("um hey John can you uh check the logs", "Hey John, can you check the logs?"),
    ("what time is the meeting", "What time is the meeting?"),
    ("send me the report", "Send me the report."),
    ("do it now", "Do it now."),
    ("When I get home I'll call you", "When I get home, I'll call you."),
    ("it's ready right", "It's ready, right?"),
    ("send it to sarah@example.com by Friday", "Send it to sarah@example.com by Friday."),
    ("நான் நாளை அலுவலகம் வருவேன்.", "நான் நாளை அலுவலகம் வருவேன்."),
    ("明日は会議があります。", "明日は会議があります。"),
    ("நாளைக்கு meeting இருக்கு um at 5 PM", "நாளைக்கு meeting இருக்கு at 5 PM."),
])
def test_good_cleanups_are_accepted(before, after):
    result = check(before, after)
    assert result.accepted, result.reasons


def test_an_applied_self_correction_is_noted():
    result = check("uh I wanted to send this tomorrow no wait Friday", "I wanted to send this Friday.")
    assert result.accepted and result.reasons == ["self-correction: 'tomorrow' -> 'Friday'"]
    assert {"uh", "tomorrow", "no", "wait"} <= set(result.diagnostics["excused_words"])


# ---- the change ratio, the configuration and the diagnostics

def test_the_change_ratio_is_recorded_but_never_rejects_alone():
    result = check("um, uh, er, hmm, um, uh, okay, so, um, yes", "Yes.")
    assert result.accepted and result.change_ratio > GuardConfig().max_change_ratio and result.diagnostics["scrutinized"]
    before, after = "abcd efgh", "abcd efgx"
    assert check(before, after).change_ratio == pytest.approx(1 - difflib.SequenceMatcher(None, before, after).ratio())
    assert check("same text", "same text").change_ratio == 0.0


def test_a_big_change_is_checked_more_strictly():
    before, after = "We should deploy the new build today.", "We should deploy the build today."
    assert check(before, after).accepted  # one dropped word is tolerated...
    strict = check(before, after, config=GuardConfig(max_change_ratio=0.0))
    assert not strict.accepted and strict.diagnostics["rule"] == "removed_words"  # ...not in a text that changed a lot


def test_semantic_checks_can_be_turned_off():
    before, after = "Can you send it?", "Can you send it."
    assert check(before, after).diagnostics["rule"] == "question_lost"
    assert check(before, after, config=GuardConfig(semantic_check_enabled=False)).accepted


def test_how_many_words_may_be_added_is_configurable():
    before, after = "send report to Anna", "Send the final report to Anna."
    assert check(before, after).accepted
    assert check(before, after, config=GuardConfig(max_added_words=0)).diagnostics["rule"] == "added_words"


def test_diagnostics_name_the_rule_and_what_changed():
    result = check("Meeting at 5:30 on October 4", "Meeting at 6:30 on October 4.")
    assert result.diagnostics["rule"] == "entity_missing"
    assert result.diagnostics["missing_entities"] == ["TIME:05:30"] and result.diagnostics["new_entities"] == ["TIME:06:30"]
    assert result.reasons == ["lost time '5:30'", "new time '6:30'"]
    dropped = check("Please review the contract and sign the agreement.", "Please review the contract.")
    assert dropped.diagnostics["removed_words"] == ["sign", "agreement"]
    added = check("I'll send it tomorrow.", "I'll send it tomorrow. Please review it before the meeting.")
    assert added.diagnostics["added_words"] == ["Please", "review", "before", "meeting"]
