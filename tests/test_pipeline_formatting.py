"""Formatting (sst/pipeline/formatting.py): spoken structures become their written form, prose stays as said, written
forms are never broken, and formatting the output again changes nothing."""
import random
import time

import pytest

from sst.pipeline.contracts import FormatChange, FormattingConfig
from sst.pipeline.formatting import Formatter, words_to_number

CARDINALS = [
    ("twenty people came", "20 people came"),
    ("about fifteen minutes", "about 15 minutes"),
    ("twenty five", "25"),
    ("one hundred and twenty three", "123"),
    ("we sold two thousand five hundred units", "we sold 2,500 units"),
    ("a hundred and fifty people", "150 people"),
    ("a hundred thousand people", "100,000 people"),
    ("three point five", "3.5"),
    ("the ratio is point five", "the ratio is 0.5"),
    ("three point one four", "3.14"),
    ("it was minus five", "it was -5"),
    ("negative five", "-5"),
    ("two million people", "2 million people"),
    ("one point five billion", "1.5 billion"),
    ("5 hundred", "500"),
    ("ten thousand", "10,000"),
    ("between one hundred and two hundred", "between 100 and 200"),
    ("twenty-five people", "25 people"),
    ("ten", "10"),
]
ORDINALS = [
    ("the twenty first century", "the 21st century"),
    ("his tenth birthday", "his 10th birthday"),
    ("the hundredth time", "the 100th time"),
    ("the twenty third time", "the 23rd time"),
    ("one hundred and first", "101st"),
    ("the eleventh and twelfth", "the 11th and 12th"),
    ("the twenty-first", "the 21st"),
]
DATES = [
    ("october first twenty twenty six", "October 1, 2026"),
    ("october first", "October 1"),
    ("the first of october", "October 1"),
    ("march fifth", "March 5"),
    ("the twenty first of june twenty twenty five", "June 21, 2025"),
    ("born on may third nineteen ninety nine", "born on May 3, 1999"),
    ("in twenty twenty six", "in 2026"),
    ("since nineteen ninety nine", "since 1999"),
    ("by two thousand thirty", "by 2030"),
    ("october twenty twenty six", "October 2026"),
    ("from twenty twenty to twenty twenty five", "from 2020 to 2025"),
    ("december thirty first, twenty twenty five", "December 31, 2025"),
    ("on the fifth of november", "on November 5"),
    ("september the third", "September 3"),
    ("in may twenty twenty four", "in May 2024"),
    ("july fourth two thousand and one", "July 4, 2001"),
    ("january first nineteen oh five", "January 1, 1905"),
    ("on march first", "on March 1"),
    ("october 5", "October 5"),
    ("twenty-first of may", "May 21"),
    ("december twenty fifth", "December 25"),
]
TIMES = [
    ("three thirty pm", "3:30 PM"),
    ("at three pm", "at 3 PM"),
    ("three p m", "3 PM"),
    ("at 3 p.m. we met", "at 3 PM we met"),
    ("call at 3 p.m.", "call at 3 PM."),
    ("seven fifteen in the morning", "7:15 AM"),
    ("eleven oh five am", "11:05 AM"),
    ("at half past three", "at 3:30"),
    ("at quarter to four", "at 3:45"),
    ("quarter past six pm", "6:15 PM"),
    ("five o'clock", "5:00"),
    ("at three thirty", "at 3:30"),
    ("from two to three pm", "from 2 to 3 PM"),
    ("the 3pm meeting", "the 3 PM meeting"),
    ("Ten thirty AM", "10:30 AM"),
    ("twelve pm", "12 PM"),
    ("at seven in the morning", "at 7 AM"),
    ("at a quarter to one", "at 12:45"),
    ("at twenty past three", "at 3:20"),
    ("ten minutes after five pm", "5:10 PM"),
]
CURRENCY = [
    ("five dollars", "$5"),
    ("twenty five dollars and fifty cents", "$25.50"),
    ("fifty cents", "50¢"),
    ("a million dollars", "$1 million"),
    ("five hundred rupees", "₹500"),
    ("ten euros", "€10"),
    ("twenty pounds sterling", "£20"),
    ("twenty pounds", "20 pounds"),
    ("1.5 million dollars", "$1.5 million"),
    ("five to ten dollars", "$5 to $10"),
    ("two point five dollars", "$2.50"),
    ("five lakh rupees", "₹5 lakh"),
    ("one dollar", "$1"),
    ("a thousand dollars", "$1,000"),
    ("five hundred yen", "¥500"),
    ("a five dollar bill", "a $5 bill"),
    ("it costs five dollars fifty", "it costs $5.50"),
    ("five euros and twenty cents", "€5.20"),
]
PERCENT = [
    ("twenty five percent", "25%"),
    ("point five percent", "0.5%"),
    ("five per cent", "5%"),
    ("a hundred percent", "100%"),
    ("25 percent", "25%"),
    ("between five and ten percent", "between 5% and 10%"),
    ("Twenty five percent of users", "25% of users"),
    ("minus three percent", "-3%"),
]
UNITS = [
    ("five kilometers", "5 km"),
    ("ten gigabytes", "10 GB"),
    ("two hundred milliseconds", "200 ms"),
    ("five kilograms", "5 kg"),
    ("three point five gigahertz", "3.5 GHz"),
    ("sixteen kilohertz", "16 kHz"),
    ("one hundred megabytes per second", "100 MB/s"),
    ("100 MB per second", "100 MB/s"),
    ("sixty kilometers per hour", "60 km/h"),
    ("forty five minutes", "45 minutes"),
    ("twenty degrees celsius", "20 °C"),
    ("minus five degrees celsius", "-5 °C"),
    ("five miles", "5 miles"),
    ("five GB", "5 GB"),
    ("two to three kilometers", "2 to 3 km"),
]
VERSIONS = [
    ("version two point one", "version 2.1"),
    ("version three point twelve", "version 3.12"),
    ("v two point one point three", "v2.1.3"),
    ("upgrade to version two", "upgrade to version 2"),
    ("version two point oh", "version 2.0"),
]
ADDRESSES = [
    ("john at example dot com", "john@example.com"),
    ("john dot smith at gmail dot com", "john.smith@gmail.com"),
    ("email me at john underscore doe at example dot co dot uk", "email me at john_doe@example.co.uk"),
    ("write to info at company dot io", "write to info@company.io"),
    ("example dot com", "example.com"),
    ("w w w dot example dot com", "www.example.com"),
    ("github dot com slash karthi", "github.com/karthi"),
    ("open docs dot example dot org slash setup slash windows", "open docs.example.org/setup/windows"),
    ("we work at example dot com", "we work at example.com"),
]
DIGIT_STRINGS = [
    ("nine eight four one two three four five six seven", "9841234567"),
    ("call nine eight four, one two three, four five six seven", "call 984 123 4567"),
    ("plus nine one nine eight four one two three four five six seven", "+919841234567"),
    ("double five one two three four five", "5512345"),
    ("oh nine eight four four one two three four five", "0984412345"),
]
ATTACHED = [  # zero to nine become digits next to a label or in a list with a bigger number
    ("room number three", "room number 3"),
    ("page two", "page 2"),
    ("five, ten and fifteen", "5, 10 and 15"),
    ("on a scale of one to ten", "on a scale of 1 to 10"),
    ("nine out of ten", "9 out of 10"),
    ("5 or six", "5 or 6"),
]
MIXED = [
    ("meeting october first twenty twenty six at three thirty pm", "meeting October 1, 2026 at 3:30 PM"),
    ("send twenty five dollars to john at example dot com by march fifth", "send $25 to john@example.com by March 5"),
    ("the file is ten gigabytes and took forty five minutes", "the file is 10 GB and took 45 minutes"),
    ("twenty five percent.", "25%."),
    ("(twenty five percent)", "(25%)"),
    ("It costs twenty dollars, not ten.", "It costs $20, not 10."),
    ("I  have   twenty\tfive  apples", "I  have   25  apples"),
    ("Line one\ntwenty\nfive", "Line one\n20\nfive"),  # a line break ends a number
    ("On October first, twenty twenty six, we grew twenty percent.", "On October 1, 2026, we grew 20%."),
    ("Wake me at seven fifteen in the morning, I have two calls.", "Wake me at 7:15 AM, I have two calls."),
    ("sales in october five percent higher", "sales in october 5% higher"),  # the number belongs to the percent
    ("see you october five pm", "see you october 5 PM"),
    ("between one hundred and two hundred people", "between 100 and 200 people"),
]
CASES = (CARDINALS + ORDINALS + DATES + TIMES + CURRENCY + PERCENT + UNITS + VERSIONS + ADDRESSES + DIGIT_STRINGS + ATTACHED
         + MIXED)

PROSE = [  # ordinary prose: must come out exactly as it went in
    "I have two options", "one of the best", "the one and only", "I won one", "a second opinion", "wait a second",
    "the first time", "I may go", "march forward", "meet at home", "it costs a lot", "two or three people",
    "at ten to one odds", "thanks a million", "one in a million", "a hundred times better", "give me five minutes",
    "it takes two hours", "a nine to five job", "open twenty four seven", "you may first want to check",
    "a thirty second video", "a tenth of a second", "on second thought", "first come, first served",
    "the dot com bubble", "the point is", "point taken", "I'll call you at five", "see you at noon",
    "midnight is late", "hundreds of people", "one by one", "zero tolerance", "may I ask a question",
    "March is cold", "five bucks", "I work at home", "connect the dots", "once or twice", "the second half",
    "twenty twenty six", "five minus three", "one, two, three", "the team minus two players", "I'm at the office",
    "one two three four five six seven", "a dozen eggs", "we march second", "half past is fine", "June is lovely",
    "send it at once", "two-thirds of them", "a one-on-one meeting", "a five-year plan", "the price is two fifty",
    "in the nineteen nineties", "a negative one", "two and a half hours", "five dollars fifty people",
    "an august first edition", "I have five in the morning", "it closed at two fifty", "the score was three to one",
    "python three point twelve", "the twenty second floor", "a number of people", "dot net framework", "plan b",
    "it's twenty past three", "the forty niners", "the first of many", "I've been here since two",
]
WRITTEN = [  # already written forms stay as they are
    "25%", "$5", "3:30 PM", "October 1, 2026", "$5 million", "john@example.com", "version 2.1", "the 21st century",
    "2,500 people", "5 km", "20 °C", "100 MB/s", "984 123 4567", "-5", "50¢", "₹500", "3 PM", "15:30", "v2.1",
    "github.com/karthi", "1.5 billion", "May 5", "at 5:00", "$25.50", "0.5%", "2 million", "October 21st, 2026",
    "the 21st of May", "01:00", "at 3 PM tomorrow", "in 2020 dollars", "a 9 m dot in", "12:45",
]


@pytest.mark.parametrize(("spoken", "written"), CASES)
def test_spoken_structures_are_written(spoken, written):
    assert Formatter().format(spoken).text == written


@pytest.mark.parametrize("text", PROSE)
def test_prose_stays_as_said(text):
    result = Formatter().format(text)
    assert result.text == text and result.changes == []


@pytest.mark.parametrize("text", WRITTEN)
def test_written_forms_are_never_broken(text):
    result = Formatter().format(text)
    assert result.text == text and result.changes == []


@pytest.mark.parametrize("text", [s for s, _ in CASES] + [w for _, w in CASES] + PROSE + WRITTEN)
def test_formatting_twice_changes_nothing(text):
    once = Formatter().format(text).text
    assert Formatter().format(once).text == once


def test_random_phrases_format_once():
    """Structure words thrown together at random: no crash, and the output formatted again stays the same, whatever
    the words around a number turned into."""
    vocab = ("zero one two three four five seven nine ten eleven twelve fifteen twenty thirty forty ninety hundred thousand"
             " million lakh a and point minus negative oh double plus first second tenth twenty-first hundredth the of in"
             " on at since to or out past quarter half january may march october am pm p.m. o'clock morning night"
             " percent per cent dollars cents euros rupees pounds sterling kilometers meters gigabytes second degrees"
             " celsius minutes version v john example gmail dot com slash w people options 5 25 3:30 2026 21st 2,500"
             " 3.5 $5 25% , . 3pm PM Twenty October").split()
    rng = random.Random(19)
    for policy in (FormattingConfig(), FormattingConfig(time_style="24h")):
        formatter = Formatter(policy)
        for _ in range(1500):
            text = " ".join(rng.choice(vocab) for _ in range(rng.randint(1, 8))).replace(" ,", ",").replace(" .", ".")
            once = formatter.format(text).text
            assert formatter.format(once).text == once, text


@pytest.mark.parametrize(("spoken", "written"), [
    ("three thirty pm", "15:30"),
    ("seven fifteen in the morning", "07:15"),
    ("at three thirty", "at 3:30"),  # no am/pm: it can't be converted
    ("twelve am", "00:00"),
    ("from two to three pm", "from 14:00 to 15:00"),
    ("at quarter to four pm", "at 15:45"),
])
def test_24_hour_policy(spoken, written):
    formatter = Formatter(FormattingConfig(time_style="24h"))
    assert formatter.format(spoken).text == written
    assert formatter.format(written).text == written


def test_other_styles_keep_the_unit_and_currency_words():
    policy = FormattingConfig(unit_style="full", currency_style="words")
    assert Formatter(policy).format("five kilometers for twenty five dollars").text == "5 kilometers for 25 dollars"


@pytest.mark.parametrize("policy", [FormattingConfig(number_style="off"), FormattingConfig(enabled=False)])
def test_policy_off_changes_nothing(policy):
    text = "twenty five percent of october first"
    assert Formatter(policy).format(text).text == text
    assert Formatter(policy).format(text).changes == []


@pytest.mark.parametrize("text", ["", "   ", "\n\t", " \r\n "])
def test_empty_input(text):
    result = Formatter().format(text)
    assert result.text == text and result.changes == []


def test_every_change_is_recorded_with_its_kind():
    result = Formatter().format("On october first at three pm, twenty five percent of the twenty people paid five dollars.")
    assert result.text == "On October 1 at 3 PM, 25% of the 20 people paid $5."
    assert result.changes == [
        FormatChange("date", "october first", "October 1"),
        FormatChange("time", "three pm", "3 PM"),
        FormatChange("percent", "twenty five percent", "25%"),
        FormatChange("number", "twenty", "20"),
        FormatChange("currency", "five dollars", "$5"),
    ]
    kinds = {c.kind for text in ("the tenth", "john at example dot com", "example dot io", "five kilometers")
             for c in Formatter().format(text).changes}
    assert kinds == {"ordinal", "email", "url", "unit"}


def test_text_outside_a_change_keeps_every_byte():
    text = "  «Hello»,\tit's   twenty five percent… OK?  "
    assert Formatter().format(text).text == "  «Hello»,\tit's   25%… OK?  "


@pytest.mark.parametrize(("tokens", "expected"), [
    (["twenty", "five", "people"], (25, 2)),
    (["One", "hundred", "and", "twenty", "three"], (123, 5)),
    (["one", "point", "five", "billion"], (1_500_000_000, 4)),
    (["minus", "five"], (-5, 2)),
    (["5", "hundred"], (500, 2)),
    (["three", "point", "five"], (3.5, 3)),
    (["a", "hundred"], (100, 2)),
    (["apples"], (None, 0)),
    ([], (None, 0)),
])
def test_words_to_number(tokens, expected):
    assert words_to_number(tokens) == expected


def test_two_thousand_words_take_under_100_ms():
    words = " ".join(s for s, _ in CASES).split() + " ".join(PROSE).split()
    text = " ".join((words * (2000 // len(words) + 1))[:2000])
    formatter = Formatter()
    best = min(_timed(formatter, text) for _ in range(3))
    assert best < 0.1, f"{best * 1000:.0f} ms"


def _timed(formatter, text):
    start = time.perf_counter()
    formatter.format(text)
    return time.perf_counter() - start
