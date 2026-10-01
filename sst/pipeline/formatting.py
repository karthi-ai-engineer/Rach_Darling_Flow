"""Formatting: spoken structures in the recognised text become their written form (inverse text normalization), after
the dictionary and before the optional LLM polish (the owner's plan, sections 51-54):

    "meeting october first twenty twenty six at three thirty pm" -> "meeting October 1, 2026 at 3:30 PM"

It looks for structures, never single words: "twenty five percent" is a percentage (25%), while "I have two options" is
prose and stays. It is deterministic and conservative, because a wrong conversion changes what the user said: whatever
is ambiguous stays as spoken ("at five", "I may go", "a thirty second clip", "twenty twenty six" with no date around it).
Speech engines often write part of it themselves ("25%", "$5", "3:30 PM", "Twenty five percent"): written forms are
never changed, mixed ones are completed ("25 percent" -> "25%"), and formatting the output again changes nothing. Text
outside a change keeps every byte (spacing, punctuation, casing).

The policy (FormattingConfig) as decided for v1:
- numbers: from ten up as digits ("20 people"); zero to nine stay words in prose ("two options") and become digits when
  attached to a unit, currency, percent, time, date, version or label ("number 5", "page 3"), or in a list or range with
  a bigger number ("5 to 10", "5, 10 and 15"). Thousands get commas ("2,500"); million, billion, lakh and crore keep
  the word ("2 million", "$1.5 billion"). "a hundred", "a million" stay words in prose ("a hundred times", "thanks a
  million"). Numbers said back to back stay ("twenty four seven", "five six"): a code, a year or an idiom.
- ordinals: first to ninth stay words ("the first time", "a second opinion"); from tenth up "10th", "21st". Fractions
  ("a tenth", "one hundredth") and "thirty second" (32nd, or a 30-second clip?) stay.
- dates (date_style "long"): "October 1, 2026", "October 1", "October 2026". A month needs a day or a year, and "may" and
  "march" need more ("on"/"in"/"of"... before them, a year, or a day other than first/second): "you may first...".
  A year alone ("twenty twenty six") only after "in", "since", "by", "until"...
- times (time_style "12h" or "24h"): with am/pm, "in the morning", o'clock ("5:00"), or after "at" with minutes ("at
  3:30"); "at five" stays. In 24h "15:30"; a time without am/pm can't be converted and stays "3:30".
- currency (currency_style "symbol"): "$5", "$25.50", "$1 million", "€10", "₹500", "¥500"; "£" only for "pounds
  sterling" (pounds may be a weight: "20 pounds"); cents as "50¢", as said ("$0.50" would add precision nobody spoke).
  "bucks", "quid" and "grand" are prose. Any other style keeps the currency word ("25 dollars").
- units (unit_style "abbreviated"): "5 km", "10 GB", "200 ms", "3.5 GHz", "100 MB/s", "60 km/h", "20 °C"; miles, feet,
  pounds, degrees... keep their word ("5 miles"); durations are prose ("two hours", "45 minutes"). Any other style keeps
  every unit word.
- versions after "version" or "v" ("version 2.1", "v2.1.3"); emails and web addresses only with a known top-level
  domain ("john.smith@gmail.com", "github.com/karthi"); seven or more digits said one by one ("984 123 4567": grouped
  as said, at the commas).
"""
import re
from dataclasses import dataclass
from decimal import Decimal

from sst.pipeline.contracts import FormatChange, FormattedTranscript, FormattingConfig

_UNITS = {w: n for n, w in enumerate("zero one two three four five six seven eight nine".split())}
_TEENS = {w: n for n, w in enumerate("ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(),
                                      10)}
_TENS = {w: 10 * n for n, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)}
_SCALES = {"thousand": 10**3, "lakh": 10**5, "million": 10**6, "crore": 10**7, "billion": 10**9, "trillion": 10**12}
_KEPT_SCALES = {"lakh", "million", "crore", "billion", "trillion"}  # "2 million" reads better than "2,000,000"
_ORDINALS = {  # word -> (value, the cardinal it stands for in the grammar: unit, teen, tens, hundred, scale)
    **{w: (n, "u") for n, w in enumerate("first second third fourth fifth sixth seventh eighth ninth".split(), 1)},
    **{w: (n, "teen") for n, w in enumerate(("tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth"
                                              " eighteenth nineteenth").split(), 10)},
    **{w: (10 * n, "tens") for n, w in enumerate("twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth"
                                                 " ninetieth".split(), 2)},
    "hundredth": (100, "h"), "thousandth": (1000, "s"), "millionth": (10**6, "s"), "billionth": (10**9, "s"),
}
_NUMBER_WORDS = _UNITS.keys() | _TEENS.keys() | _TENS.keys()
_DIGITS = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")  # (fullmatch) "25", "2,500", "3.5"
_DIGIT_ORDINAL = re.compile(r"(\d+)(?:st|nd|rd|th)")


@dataclass(slots=True)
class _Num:
    end: int  # the token after the number
    value: Decimal
    text: str  # written: "25", "2,500", "3.5", "2 million", "21st"
    spoken: bool = True  # False: it was written already ("25", "2 million")
    ordinal: bool = False
    small: bool = False  # one word from zero to nine: stays a word in prose
    article: bool = False  # "a hundred", "a million": idioms in prose
    bare_point: bool = False  # "point five" (no whole part): "the point five..." could be prose
    ambiguous: bool = False  # "three point twelve" (a version?), "thirty second" (32nd or 30 s?): nothing safe to write
    scale: str = ""  # the scale word kept after it ("million")


def _plain(value: Decimal) -> str:
    whole, _, frac = f"{value:f}".partition(".")
    return f"{int(whole):,}" + (f".{frac}" if frac else "")


def _suffix(n: int) -> str:
    return "th" if n % 100 in (11, 12, 13) else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _two(w: list[str], jn: list[bool], i: int) -> tuple[int, int] | None:
    """0 to 99 said as one or two words ("seven", "fifteen", "forty five") -> (value, end)."""
    t = w[i]
    if t in _UNITS or t in _TEENS:
        return _UNITS.get(t, _TEENS.get(t)), i + 1
    if t in _TENS:
        if i + 1 < len(w) and jn[i] and w[i + 1] in _UNITS and w[i + 1] != "zero":
            return _TENS[t] + _UNITS[w[i + 1]], i + 2
        return _TENS[t], i + 1
    return None


def _parse(w: list[str], jn: list[bool], i: int, ordinals: bool = True, decimals: bool = True) -> _Num | None:
    """The number said (or written) from token i. w: lower-case words; jn[k]: token k+1 may continue a span from token k
    (no punctuation between them)."""
    n = len(w)

    def on(k: int) -> bool:
        return k < n and jn[k - 1]

    t = w[i]
    if m := _DIGIT_ORDINAL.fullmatch(t):
        return _Num(i + 1, Decimal(m[1]), t, spoken=False, ordinal=True) if ordinals else None
    if _DIGITS.fullmatch(t):  # written, maybe completed by words: "5 hundred", "25 thousand", "2.5 million"
        value, k, text, spoken, scale = Decimal(t.replace(",", "")), i + 1, t, False, ""
        if on(k) and w[k] == "hundred" and value == value.to_integral_value() and 0 < value < 100:
            value, k, spoken = value * 100, k + 1, True
            text = _plain(value)
        if on(k) and w[k] in _SCALES and value > 0:
            if w[k] in _KEPT_SCALES:
                text, scale = f"{text} {w[k]}", w[k]
                value *= _SCALES[w[k]]
            else:
                value, spoken = (value * _SCALES[w[k]]).normalize(), True
                text = _plain(value)
            k += 1
        return _Num(k, value, text, spoken, scale=scale)
    total = cur = 0
    last = None  # the previous word: "u" unit, "teen", "tens", "h" hundred, "s" scale, "a", "and"
    top, saved, ordinal, k = 10**15, None, False, i
    while t != "point" and k < n and (k == i or jn[k - 1]):
        word = w[k]
        if word in _UNITS:
            if last not in (None, "tens", "h", "s", "and") or (word == "zero" and last is not None):
                break
            cur, last = cur + _UNITS[word], "u"
        elif word in _TEENS or word in _TENS:
            if last not in (None, "h", "s", "and"):
                break
            cur, last = cur + (_TEENS.get(word) or _TENS[word]), "teen" if word in _TEENS else "tens"
        elif word == "hundred":
            if last not in ("u", "teen", "tens", "a") or not 0 < cur < 100:
                break
            cur, last = cur * 100, "h"
        elif word in _SCALES:
            if last not in ("u", "teen", "tens", "h", "a") or not cur or _SCALES[word] >= top:
                break
            total, cur, top, last = total + cur * _SCALES[word], 0, _SCALES[word], "s"
        elif word == "and":  # "one hundred and five", not "one and only"
            nxt = w[k + 1] if on(k + 1) else ""  # "one hundred and first" too
            if last not in ("h", "s") or nxt not in _NUMBER_WORDS and not (ordinals and nxt in _ORDINALS) or nxt == "zero":
                break
            saved, last = (k, total, cur, last), "and"
        elif word == "a" and k == i:  # "a hundred", "a million"
            if not on(k + 1) or (w[k + 1] != "hundred" and w[k + 1] not in _SCALES):
                return None
            cur, last = 1, "a"
        elif ordinals and word in _ORDINALS:
            v, kind = _ORDINALS[word]
            if kind == "u" and last not in (None, "tens", "h", "s", "and") or kind in ("teen", "tens") and last not in (
                    None, "h", "s", "and"):
                break
            if kind == "h":
                if last not in (None, "u", "teen", "tens", "a") or cur >= 100:
                    break
                cur = (cur or 1) * 100
            elif kind == "s":
                if last not in (None, "u", "teen", "tens", "h", "a") or v >= top:
                    break
                total, cur = total + (cur or 1) * v, 0
            else:
                cur += v
            ordinal, k = True, k + 1
            break
        else:
            break
        k += 1
    if saved and not ordinal and (last == "and" or on(k) and (w[k] == "hundred" or w[k] in _SCALES)):
        k, total, cur, last = saved  # "between one hundred and two hundred": two numbers
    if t != "point" and k == i:
        return None
    if ordinal:
        v = total + cur
        return _Num(k, Decimal(v), f"{v:,}{_suffix(v)}", ordinal=True,
                    ambiguous=w[k - 1] == "second" and k - i > 1 and w[k - 2] in _TENS)
    frac = ""
    if decimals and last != "s" and (t == "point" or on(k)) and k < n and w[k] == "point":
        j = k + 1
        while j < n and jn[j - 1] and (w[j] in _UNITS or w[j] == "oh"):
            frac, j = frac + str(_UNITS.get(w[j], 0)), j + 1
        if j < n and jn[j - 1] and (w[j] in _NUMBER_WORDS or w[j] == "point" or _DIGITS.fullmatch(w[j])):
            # "three point twelve", "two point one point three": a version, or a number we can't write safely
            while j < n and jn[j - 1] and (w[j] in _NUMBER_WORDS or w[j] == "point" or _DIGITS.fullmatch(w[j])):
                j += 1
            return _Num(j, Decimal(0), "", ambiguous=True)
        if frac:
            k = j
    if t == "point" and not frac:
        return None
    if frac:
        value = Decimal(f"{total + cur}.{frac}")
        if on(k) and w[k] in _SCALES:
            s, k = w[k], k + 1
            if s in _KEPT_SCALES:
                return _Num(k, value * _SCALES[s], f"{_plain(value)} {s}", scale=s, bare_point=t == "point")
            value = (value * _SCALES[s]).normalize()
        return _Num(k, value, _plain(value), bare_point=t == "point")
    value, article = total + cur, t == "a" and k == i + 2
    if last == "s" and w[k - 1] in _KEPT_SCALES:
        return _Num(k, Decimal(value), f"{value // top:,} {w[k - 1]}", scale=w[k - 1], article=article)
    return _Num(k, Decimal(value), f"{value:,}", small=k == i + 1 and value < 10, article=article)


def words_to_number(tokens: list[str]) -> tuple[int | float | None, int]:
    """The number said at the start of `tokens` and how many tokens it takes: ["twenty", "five", "people"] -> (25, 2),
    ["one", "point", "five", "billion"] -> (1500000000, 4), ["minus", "five"] -> (-5, 2), ["5", "hundred"] -> (500, 2);
    (None, 0) when there is none. Ordinals are not numbers here."""
    w = [t.lower() for t in tokens]
    start = int(w[:1] in (["minus"], ["negative"]))
    num = _parse(w, [True] * len(w), start, ordinals=False) if len(w) > start else None
    if num is None or num.ambiguous:
        return None, 0
    value = -num.value if start else num.value
    return (int(value) if value == value.to_integral_value() else float(value)), num.end


@dataclass(slots=True)
class _Tok:
    start: int  # the word, without the punctuation around it
    end: int
    text: str
    lead: str = ""
    trail: str = ""


_LEAD = "\"'([{“‘«¿¡"
_TRAIL = ".,;:!?\"')]}”’»…"
_HYPHENATED = _NUMBER_WORDS | _ORDINALS.keys()


def _tokenize(text: str) -> list[_Tok]:
    toks = []
    for m in re.finditer(r"\S+", text):
        s, e = a, b = m.span()
        while a < b and text[a] in _LEAD:
            a += 1
        while b > a and text[b - 1] in _TRAIL:
            b -= 1
        parts = text[a:b].split("-")
        if len(parts) > 1 and all(p.lower() in _HYPHENATED for p in parts):  # "twenty-five", "twenty-first"
            pos = a
            for x, p in enumerate(parts):
                toks.append(_Tok(pos, pos + len(p), p, text[s:a] if x == 0 else "", text[b:e] if x == len(parts) - 1 else ""))
                pos += len(p) + 1
        else:
            toks.append(_Tok(a, b, text[a:b], text[s:a], text[b:e]))
    return toks


# ---------------------------------------------------------------- what the structures are made of

_MONTHS = {m.lower(): m for m in "January February March April May June July August September October November December".split()}
_MONTH_DAYS = dict(zip(_MONTHS.values(), (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31), strict=True))
_VERB_MONTHS = {"may", "march", "august"}  # "you may first check...", "we march second", "an august first edition"
_MONTH_CUES = {"on", "in", "of", "since", "until", "till", "by", "from", "before", "after", "during", "early", "late", "mid",
               "next", "last", "this", "every"}
_YEAR_CUES = {"in", "since", "by", "until", "till", "before", "after", "year", "from", "circa"}
_YEAR_START = _TEENS.keys() | {"twenty", "two", "one"}
_RANGE = {"to", "and", "or", "through", "until", "till"}
_TIME_RANGE = {"to", "until", "till", "through", "and", "-"}
_TIME_CUES = {"at", "by", "until", "till", "from", "around", "before", "after"}
_DAYPARTS = ((("in", "the", "morning"), "AM", range(1, 12)), (("in", "the", "afternoon"), "PM", (12, 1, 2, 3, 4, 5, 6)),
             (("in", "the", "evening"), "PM", range(4, 12)), (("at", "night"), "PM", range(6, 12)))
_PRICE_WORDS = {"priced", "sold", "selling", "sell", "sells", "trading", "traded", "trades", "closed", "opened", "cost", "costs",
                "valued", "bought", "buy", "buying", "offered", "listed", "retails"}  # "it closed at two fifty" is a price
_LABELS = {"number", "#", "page", "pages", "chapter", "section", "step", "phase", "episode", "season", "volume", "figure",
           "gate", "platform"}
_NEG_CUES = {"is", "was", "are", "were", "be", "to", "of", "at", "from", "equals", "reached", "hit", "by", "about", "around",
             "below"}
_POINT_CUES = {"is", "was", "are", "were", "be", "of", "by", "about", "around", "roughly", "approximately", "nearly", "almost",
               "only", "just", "equals", "to", "at", "from", "than", "under", "over", "below", "above", "and", "or"}
_NUMBER_START = _HYPHENATED | {"a", "minus", "negative", "point"}
_PLURALS = {w[:-1] + "ies" if w.endswith("y") else w + "s" for w in _NUMBER_WORDS | {"hundred", *_SCALES}} | {
    "sixes", "dozens", "niners"}  # "the nineteen nineties", "the forty niners"
_DETERMINERS = {"a", "an", "the", "this", "that", "another"}  # "a negative one" is prose

_CURRENCY = {"dollar": "$", "dollars": "$", "euro": "€", "euros": "€", "rupee": "₹", "rupees": "₹", "yen": "¥"}
_SUBUNITS = {"$": ("cent", "cents"), "€": ("cent", "cents"), "₹": ("paisa", "paise"), "£": ("penny", "pence"), "¥": ()}
_DATA = {"KB", "MB", "GB", "TB", "PB"}
_ABBREVIATIONS = _DATA | {"km", "cm", "mm", "kg", "mg", "mL", "Hz", "kHz", "MHz", "GHz", "ms", "°C", "°F", "kW", "kWh", "mAh",
                          "mph", "km/h", "fps", "Mbps", "Gbps", "dB"}  # written units (case matters: MB is not Mb)


def _unit_index() -> dict[str, list[tuple[tuple[str, ...], str | None]]]:
    """First word -> [(the unit's words, its abbreviation or None to keep the words)], longest first."""
    index: dict[str, list] = {}
    for names, abbr in (
        ("kilometer|kilometers|kilometre|kilometres", "km"), ("meter|meters|metre|metres", "m"),
        ("centimeter|centimeters|centimetre|centimetres", "cm"), ("millimeter|millimeters|millimetre|millimetres", "mm"),
        ("micrometer|micrometers|micron|microns", "µm"), ("nanometer|nanometers|nanometre|nanometres", "nm"),
        ("kilogram|kilograms", "kg"), ("gram|grams", "g"), ("milligram|milligrams", "mg"), ("microgram|micrograms", "µg"),
        ("liter|liters|litre|litres", "L"), ("milliliter|milliliters|millilitre|millilitres", "mL"),
        ("kilobyte|kilobytes", "KB"), ("megabyte|megabytes", "MB"), ("gigabyte|gigabytes", "GB"),
        ("terabyte|terabytes", "TB"), ("petabyte|petabytes", "PB"),
        ("hertz", "Hz"), ("kilohertz", "kHz"), ("megahertz", "MHz"), ("gigahertz", "GHz"),
        ("millisecond|milliseconds", "ms"), ("microsecond|microseconds", "µs"), ("nanosecond|nanoseconds", "ns"),
        ("watt|watts", "W"), ("kilowatt|kilowatts", "kW"), ("megawatt|megawatts", "MW"),
        ("kilowatt hour|kilowatt hours", "kWh"), ("milliamp hour|milliamp hours", "mAh"), ("volt|volts", "V"),
        ("decibel|decibels", "dB"), ("kelvin", "K"),
        ("degree celsius|degrees celsius|degree centigrade|degrees centigrade|celsius", "°C"),
        ("degree fahrenheit|degrees fahrenheit|fahrenheit", "°F"),
        ("kilometer per hour|kilometers per hour|kilometre per hour|kilometres per hour", "km/h"),
        ("mile per hour|miles per hour", "mph"), ("meter per second|meters per second|metre per second|metres per second", "m/s"),
        ("frames per second", "fps"), ("revolutions per minute", "rpm"), ("beats per minute", "bpm"),
        ("kilobits per second", "kbps"), ("megabits per second", "Mbps"), ("gigabits per second", "Gbps"),
        # these keep their word: no abbreviation reads well in prose, or it would be ambiguous (pounds: weight or money)
        ("mile|miles|foot|feet|inch|inches|yard|yards|pound|pounds|ounce|ounces|ton|tons|tonne|tonnes|degree|degrees|calorie"
         "|calories|byte|bytes|bit|bits|pixel|pixels|megapixel|megapixels|kilo|kilos|gigs|amp|amps|percentage point"
         "|percentage points", None),
    ):
        for name in names.split("|"):
            words = tuple(name.split())
            index.setdefault(words[0], []).append((words, abbr))
    for options in index.values():
        options.sort(key=lambda o: -len(o[0]))
    return index


_UNIT_INDEX = _unit_index()
_WRITTEN_UNITS = _ABBREVIATIONS | {abbr for options in _UNIT_INDEX.values() for _, abbr in options if abbr}  # also our own

_TLDS = set("com org net io dev ai in co uk edu gov us app me info biz de fr jp au ca tech xyz cloud gg tv ly nl eu es ch se"
            " sg nz za br".split())
_LABEL = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SPOKEN = {"dot", "at", "slash", "underscore", "dash", "hyphen"}
_LOCAL_SEPS = {"dot": ".", "underscore": "_", "dash": "-", "hyphen": "-"}
_DOMAIN_SEPS = {"dot": ".", "dash": "-", "hyphen": "-"}
_NOT_LABEL = set("the a an this that these those my your our their his her its and or but to of in on at is was it i we you"
                 " they he she".split())  # "the dot com bubble"
_NOT_LOCAL = _NOT_LABEL | set("me us him them be am are were been work works worked working live lives lived stay stays stayed"
                              " meet met look looking arrive arrived available here there home job people someone anyone"
                              " everyone everybody nobody something anything nothing reach contact email mail find found"
                              " sign signed log logged registered hosted host online only just even also still not now then"
                              " yes no ok okay right up down out back over".split())  # "we work at example dot com"
_CLOCK = re.compile(r"(\d{1,2})(?::(\d{2}))?(?:([ap])\.?m\.?)?")  # "3", "3:30", "3pm", "3:30p.m"


# ---------------------------------------------------------------- the scanner

class _Scan:
    """One pass over one text: at each word the matchers run in order, and the first that recognises a structure
    there writes it and moves past it. Each matcher returns the token after what it consumed, or None."""

    def __init__(self, text: str, policy: FormattingConfig):
        self.text, self.policy = text, policy
        self.toks = _tokenize(text)
        self.w = [t.text.lower().replace("’", "'") for t in self.toks]
        n = self.n = len(self.toks)
        self.jn, self.cm = [False] * n, [False] * n  # token k+1 continues token k: directly, or after a comma
        for k in range(n - 1):
            a, b = self.toks[k], self.toks[k + 1]
            if a.text and b.text and not b.lead and "\n" not in text[a.end:b.start]:
                self.jn[k], self.cm[k] = not a.trail, a.trail == ","
        self.edits: list[tuple[int, int, str, str]] = []
        self.year_end = -1  # the token after the last year, so "from 2020 to twenty twenty five" goes on as years

    def run(self) -> list[tuple[int, int, str, str]]:
        i, matchers = 0, (self._address, self._digit_string, self._date, self._time, self._version, self._number)
        while i < self.n:
            for match in matchers:
                if end := match(i):
                    i = end
                    break
            else:
                i += 1
        return self.edits

    # helpers
    def j(self, k: int) -> bool:  # may a span go on from token k to k+1?
        return k + 1 < self.n and self.jn[k]

    def cont(self, k: int) -> bool:  # does token k continue the span before it?
        return 0 < k < self.n and self.jn[k - 1]

    def on(self, k: int, *words: str) -> bool:  # do these words follow on at token k?
        return self.cont(k) and self.seq(k, *words)

    def seq(self, k: int, *words: str) -> bool:
        return k + len(words) <= self.n and all(self.w[k + x] == word and (x == 0 or self.jn[k + x - 1])
                                                for x, word in enumerate(words))

    def prev(self, i: int, back: int = 1) -> str | None:
        k = i - back
        return self.w[k] if k >= 0 and all(self.jn[k:i]) else None

    def num(self, k: int, **kw) -> _Num | None:
        return _parse(self.w, self.jn, k, **kw) if k < self.n else None

    def put(self, a: int, b: int, text: str, kind: str, end: int | None = None):
        s, e = self.toks[a].start, end or self.toks[b - 1].end
        if text != self.text[s:e]:
            self.edits.append((s, e, text, kind))

    # emails and web addresses: "john dot smith at gmail dot com", "github dot com slash karthi"
    def _address(self, i: int) -> int | None:
        w = self.w
        if not (self.j(i) and (w[i + 1] in _SPOKEN or w[i] == "w")) or _DIGITS.fullmatch(self.prev(i) or ""):
            return None  # after a number the word is a unit: "9 m dot in" is not the address "m.in"
        k, local = self._labels(i, _LOCAL_SEPS)
        if not local:
            return None
        at = None
        if self.on(k, "at", "the", "rate"):  # Indian English: "john at the rate gmail dot com"
            at = k + 3 + self.on(k + 3, "of")
        elif self.on(k, "at"):
            at = k + 1
        if at and at < self.n and self.jn[at - 1] and (len(local) > 1 or local[0] not in _NOT_LOCAL):
            e, domain = self._labels(at, _DOMAIN_SEPS)
            if self._is_domain(domain):
                self.put(i, e, "".join(local) + "@" + "".join(domain), "email")
                return e
        if "_" in local or not self._is_domain(local):
            return None
        e, path = k, ""
        while self.on(e, "slash") and self.j(e) and _LABEL.fullmatch(w[e + 1]) and w[e + 1] not in _SPOKEN:
            path, e = path + "/" + self.toks[e + 1].text, e + 2
        self.put(i, e, "".join(local) + path, "url")
        return e

    def _labels(self, i: int, seps: dict[str, str]) -> tuple[int, list[str]]:
        """Words joined by spoken separators from token i: "john dot smith" -> (i + 3, ["john", ".", "smith"])."""
        w = self.w
        if self.seq(i, "w", "w", "w"):
            parts, k = ["www"], i + 3
        elif _LABEL.fullmatch(w[i]) and w[i] not in _SPOKEN:
            parts, k = [w[i]], i + 1
        else:
            return i, []
        while self.cont(k) and w[k] in seps and self.j(k) and _LABEL.fullmatch(w[k + 1]) and w[k + 1] not in _SPOKEN:
            parts, k = [*parts, seps[w[k]], w[k + 1]], k + 2
        return k, parts

    @staticmethod
    def _is_domain(parts: list[str]) -> bool:
        return len(parts) >= 3 and parts[-2] == "." and parts[-1] in _TLDS and parts[0] not in _NOT_LABEL and "_" not in parts \
            and parts[0] not in _NUMBER_WORDS and not parts[0][:1].isdigit()  # "forty three dot in" is a number first

    # phone numbers and codes: seven or more digits said one by one
    def _digit_string(self, i: int) -> int | None:
        w = self.w
        if w[i] not in _UNITS and w[i] not in ("double", "triple", "plus", "oh"):
            return None
        k, groups, cur, said = i + (w[i] == "plus"), [], "", 0
        while k < self.n:
            t = w[k]
            if t in _UNITS or t == "oh" or (t == "o" and (cur or groups)):
                cur, said, k = cur + str(_UNITS.get(t, 0)), said + 1, k + 1
            elif t in ("double", "triple") and self.j(k) and w[k + 1] in _UNITS:
                cur, said, k = cur + str(_UNITS[w[k + 1]]) * (2 if t == "double" else 3), said + 1, k + 2
            elif (t in _TEENS or t in _TENS) and said:  # "... four five sixty seven"
                v, k = _two(w, self.jn, k)
                cur += str(v)
            else:
                break
            if k < self.n and self.jn[k - 1]:
                continue
            if k < self.n and self.cm[k - 1] and w[k] in _UNITS:  # "nine eight four, one two three...": groups as said
                groups, cur = [*groups, cur], ""
                continue
            break
        groups = [g for g in (*groups, cur) if g]
        digits = "".join(groups)
        if len(digits) < 7 or said < 4 or max(map(len, groups)) == 1 and len(groups) > 1 or digits in "01234567890123456789":
            return None  # too short to be a number said digit by digit, or counting ("one, two, three...")
        self.put(i, k, ("+" if w[i] == "plus" else "") + " ".join(groups), "number")
        return k

    # dates: "october first twenty twenty six", "the first of october", "in twenty twenty six"
    def _date(self, i: int) -> int | None:
        t = self.w[i]
        if t in _MONTHS:
            return self._month_first(i)
        if (t == "the" or t in _ORDINALS or t in ("twenty", "thirty")) and (end := self._day_first(i)):
            return end
        p = self.prev(i)
        if t in _YEAR_START and (p in _YEAR_CUES or p in _RANGE and self.year_end == i - 1):
            if (y := self._year(i, wide=False)) and not self._structure(y[1]):  # "in 2020 dollars" isn't $2,020
                self.put(i, y[1], y[2], "date")
                self.year_end = y[1]
                return y[1]
        return None

    def _month_first(self, i: int) -> int | None:
        w, month = self.w, _MONTHS[self.w[i]]
        if not self.j(i):
            return None
        readings = []  # (end, day, year)
        if y := self._year(i + 1, wide=True):
            readings.append((y[1], None, y))
        k = i + 1 + (w[i + 1] == "the" and self.j(i + 1))  # "october the first"
        if (d := self._day(k, ordinal_only=k > i + 1)) and d[0] <= _MONTH_DAYS[month]:
            readings.append((d[1], d, None))
            if y := self._date_year(d[1]):
                readings.append((y[1], d, y))
        # a number that a percent, currency or unit follows belongs to it ("october five percent"); otherwise the
        # reading that explains the most words wins, one with a day on a tie
        readings = [r for r in readings if not self._structure(r[0])]
        if not readings:
            return None
        end, d, y = max(readings, key=lambda r: (r[0], r[1] is not None))
        if w[i] in _VERB_MONTHS and not y and not self._month_cue(i):
            num = d[3]  # "march fifth" is a date, "you may first check" and "may five people" aren't
            if not (num.ordinal and num.spoken and not (d[1] == k + 1 and w[k] in ("first", "second"))):
                return None
        return self._put_date(i, end, month, d and d[2], y and y[2])

    def _month_cue(self, i: int) -> bool:
        return self.prev(i) in _MONTH_CUES or (self.prev(i) is not None and self.toks[i].text[:1].isupper())

    def _day_first(self, i: int) -> int | None:  # "the first of october", "the twenty first of june twenty twenty five"
        k = i + (self.w[i] == "the")
        if k > i and not self.j(i):
            return None
        d = self._day(k, ordinal_only=True)  # a written "the 21st of June" is a date already: left as it is
        if not d or not d[3].spoken or not self.on(d[1], "of") or not self.j(d[1]) or self.w[d[1] + 1] not in _MONTHS:
            return None
        month, e = _MONTHS[self.w[d[1] + 1]], d[1] + 2
        if d[0] > _MONTH_DAYS[month]:
            return None
        if (y := self._date_year(e)) and not self._structure(y[1]):
            return self._put_date(i, y[1], month, d[2], y[2])
        if self._structure(e) or self._number_goes_on(e) or self._oclock(e) or self._meridiem(e, d[0], False):
            return None  # written "October 1 rupees" would give the day away to the currency: leave it as said
        return self._put_date(i, e, month, d[2], None)

    def _number_goes_on(self, e: int, written: bool = True) -> bool:  # the next word would continue the day's number
        return self.cont(e) and (self.w[e] in ("hundred", "point", *_SCALES)
                                 or written and bool(re.fullmatch(r"\d*\.\d+", self.w[e])))  # "december 25 0.1 miles"

    def _date_year(self, e: int) -> tuple[int, int, str] | None:  # the year after a day: "first twenty twenty six"
        return self._year(e, wide=True) if e < self.n and (self.jn[e - 1] or self.cm[e - 1]) else None

    def _put_date(self, i: int, end: int, month: str, day: str | None, year: str | None) -> int:
        self.put(i, end, month + (f" {day}" if day else "") + (f", {year}" if day and year else f" {year}" if year else ""),
                 "date")
        if year:
            self.year_end = end
        return end

    def _day(self, k: int, ordinal_only: bool = False) -> tuple[int, int, str, _Num] | None:
        """A day of the month at token k: (day, end, how it is written, the number)."""
        num = self.num(k, decimals=False)
        if not num or num.scale or num.article or not 1 <= num.value <= 31 or num.value != int(num.value):
            return None
        if ordinal_only and not num.ordinal or not num.spoken and not num.ordinal and not self.w[k].isdigit():
            return None  # "4.0" is not a day
        e, v = num.end, int(num.value)
        if self._oclock(e) or self._meridiem(e, v) or self._number_goes_on(e, not num.spoken) or (
                (c := self._clock(k)) and (c[3] or c[4] or self._meridiem(c[2], c[0]))):
            return None  # "october five pm", "october third pm" are times in October; "march twenty thousand" isn't a date
        return int(num.value), num.end, str(int(num.value)) if num.spoken else self.toks[k].text, num

    def _year(self, k: int, wide: bool) -> tuple[int, int, str] | None:
        """A year at token k: "twenty twenty six", "nineteen oh five", "two thousand and one", "2026" -> (year, end, text).
        wide: in a date (1000-2999); otherwise only 1900-2099, after a cue like "in"."""
        w = self.w
        if k >= self.n:
            return None
        if re.fullmatch(r"\d{4}", w[k]):
            best, text = (int(w[k]), k + 1), self.toks[k].text
        else:
            best, text = None, ""
            if (w[k] in _TEENS or w[k] == "twenty") and self.j(k):  # two pairs: "nineteen ninety nine", "twenty oh five"
                first, s = _TEENS.get(w[k], 20), w[k + 1]
                if s == "hundred":
                    best = (first * 100, k + 2)
                elif s in ("oh", "o") and self.j(k + 1) and w[k + 2] in _UNITS and w[k + 2] != "zero":
                    best = (first * 100 + _UNITS[w[k + 2]], k + 3)
                elif s in _TEENS or s in _TENS:
                    v, e = _two(w, self.jn, k + 1)
                    best = (first * 100 + v, e)
            num = self.num(k, ordinals=False, decimals=False) if not w[k][:1].isdigit() else None
            if num and num.spoken and not num.scale and 1000 <= num.value <= 2999 and (not best or num.end > best[1]):
                best = (int(num.value), num.end)  # "two thousand twenty six", "nineteen hundred and five"
        if not best or not (1000 <= best[0] <= 2999 if wide else 1900 <= best[0] <= 2099):
            return None
        return best[0], best[1], text or str(best[0])

    # times: "three thirty pm", "seven in the morning", "five o'clock", "at quarter to four", "two to three pm"
    def _time(self, i: int) -> int | None:
        w = self.w
        if w[i] in ("a", "quarter", "half"):
            return self._quarter(i)
        if (two := _two(w, self.jn, i)) and (self.on(two[1], "past") or self.on(two[1], "after")
                                             or self.on(two[1], "minutes") or self.on(two[1], "minute")):
            if end := self._past(i, *two):
                return end
        c = self._clock(i)
        if not c:
            return None
        h, mi, e, oclock, mer, written = c
        if written and mi is not None and (w[i][:1] == "0" or h > 12):
            return None  # "01:00", "15:30": 24-hour already, an am/pm after it is not ours to merge
        # "in the morning" needs minutes or a cue: "I have five in the morning" may be five meetings
        if mer is None and (r := self._meridiem(e, h, mi is not None or self.prev(i) in _TIME_CUES)):
            mer, e = r
        if mer is None and not oclock and self.cont(e) and w[e] in _TIME_RANGE and self.j(e):
            if c2 := self._clock(e + 1):  # a range shares the second time's am/pm; only forwards, "eleven to one" is not
                h2, mi2, e2, _, mer2, _ = c2
                if mer2 is None and (r := self._meridiem(e2, h2)):
                    mer2, e2 = r
                if mer2 and 1 <= h <= 12 and 1 <= h2 <= 12 and h % 12 < h2 % 12:
                    self.put(i, e, self._clock_text(h, mi, mer2, suffix=False), "time")
                    self.put(e + 1, e2, self._clock_text(h2, mi2, mer2), "time", self._dot_end(e2))
                    return e2
        if mer:
            if not 1 <= h <= 12:
                return None
            twice = self._meridiem(e, h, False)
            if not twice and e < self.n and self.w[e - 1].endswith(("a.m", "p.m")) and self.toks[e - 1].trail == "." \
                    and self.w[e] in ("am", "a.m", "pm", "p.m"):
                twice = ("", e + 1)
            if twice:
                return twice[1]  # "eleven am pm", "twelve p.m. p.m.": which one was meant? left as said
            self.put(i, e, self._clock_text(h, mi, mer), "time", self._dot_end(e))
            return e
        if oclock and 1 <= h <= 12:
            self.put(i, e, f"{h}:00", "time")
            return e
        if mi is not None and not written and 1 <= h <= 12 and self.prev(i) == "at" and self.prev(i, 2) not in _PRICE_WORDS:
            self.put(i, e, f"{h}:{mi:02d}", "time")  # "at three thirty"; am or pm unknown, so 24h can't say more
            return e
        return None

    def _clock(self, k: int) -> tuple[int, int | None, int, bool, str | None, bool] | None:
        """An hour, maybe with minutes, at token k: (hour, minute, end, o'clock, am/pm written with it, written)."""
        if k >= self.n:
            return None
        w = self.w
        if m := _CLOCK.fullmatch(w[k]):
            h, mi = int(m[1]), (int(m[2]) if m[2] else None)
            if h > 23 or (mi or 0) > 59:
                return None
            e = self._oclock(k + 1) if mi is None and not m[3] else 0
            return h, 0 if e else mi, e or k + 1, bool(e), m[3] and m[3].upper() + "M", True
        two = _two(w, self.jn, k)
        if not two or not 1 <= two[0] <= 23:
            return None
        h, e = two
        if oc := self._oclock(e):
            return h, 0, oc, True, None, False
        if self.cont(e) and w[e] in ("oh", "o", "zero") and self.j(e) and w[e + 1] in _UNITS:
            return (h, _UNITS[w[e + 1]], e + 2, False, None, False) if w[e + 1] != "zero" else (h, None, e, False, None, False)
        if self.cont(e) and (w[e] in _TEENS or w[e] in _TENS):
            mi, e2 = _two(w, self.jn, e)
            if 10 <= mi <= 59:
                return h, mi, e2, False, None, False
        return h, None, e, False, None, False

    def _oclock(self, k: int) -> int:
        if self.on(k, "o'clock") or self.on(k, "oclock"):
            return k + 1
        return k + 2 if self.on(k, "o", "clock") else 0

    def _meridiem(self, k: int, h: int, dayparts: bool = True) -> tuple[str, int] | None:
        """am or pm after an hour at token k: "pm", "p.m.", "p m", "in the morning" (with dayparts) -> ("PM", end)."""
        if not (k < self.n and self.jn[k - 1] and 1 <= h <= 12):
            return None
        t = self.w[k]
        if t in ("am", "a.m", "pm", "p.m"):
            return t[0].upper() + "M", k + 1
        if t in ("a", "p") and self.seq(k, t, "m"):
            return t.upper() + "M", k + 2
        for words, code, hours in _DAYPARTS if dayparts else ():
            if self.seq(k, *words) and h in hours:
                return code, k + len(words)
        return None

    def _dot_end(self, e: int) -> int | None:
        """A time ending in "p.m.": its dot also ends the sentence, unless the sentence goes on ("at 3 p.m. we met")."""
        tok = self.toks[e - 1]
        if self.w[e - 1].endswith(("a.m", "p.m")) and tok.trail[:1] == "." and (
                tok.trail[1:2] in (",", ";", ":") or e < self.n and tok.trail == "." and self.toks[e].text[:1].islower()):
            return tok.end + 1
        return None

    def _clock_text(self, h: int, mi: int | None, mer: str, suffix: bool = True) -> str:
        if self.policy.time_style == "24h":
            return f"{h % 12 + (12 if mer == 'PM' else 0):02d}:{mi or 0:02d}"
        return f"{h}" + (f":{mi:02d}" if mi is not None else "") + (f" {mer}" if suffix else "")

    def _quarter(self, i: int) -> int | None:  # "(at) (a) quarter past three", "half past three pm"
        w, k = self.w, i + (self.w[i] == "a")
        if k > i and not self.j(i) or not (w[k] in ("quarter", "half") and self.j(k) and self.j(k + 1)):
            return None
        rel = w[k + 1]
        if rel not in ("past", "after", "to", "before") or w[k] == "half" and rel not in ("past", "after"):
            return None
        c = self._clock(k + 2)
        if not c or c[1] is not None or not 1 <= c[0] <= 12:
            return None
        h, e, mer = c[0], c[2], c[4]
        if mer is None and (r := self._meridiem(e, h)):
            mer, e = r
        if not mer and self.prev(i) != "at":  # "half past three" alone could be prose; "at" or am/pm make it a time
            return None
        delta = 30 if w[k] == "half" else 15 if rel in ("past", "after") else -15
        h24, mi = divmod(((h % 12 + (12 if mer == "PM" else 0)) * 60 + delta) % (24 * 60), 60)
        if mer:
            self.put(i, e, self._clock_text(h24 % 12 or 12, mi, "PM" if h24 >= 12 else "AM"), "time", self._dot_end(e))
        else:
            self.put(i, e, f"{h24 % 12 or 12}:{mi:02d}", "time")
        return e

    def _past(self, i: int, mi: int, k: int) -> int | None:  # "(at) twenty past three (pm)", "ten minutes after five pm"
        k += self.on(k, "minutes") or self.on(k, "minute")
        if not (self.on(k, "past") or self.on(k, "after")) or not 1 <= mi <= 59:
            return None
        c = self._clock(k + 1)
        if not c or c[1] is not None or not 1 <= c[0] <= 12:
            return None
        h, e, mer = c[0], c[2], c[4]
        if mer is None and (r := self._meridiem(e, h)):
            mer, e = r
        if mer:
            self.put(i, e, self._clock_text(h, mi, mer), "time", self._dot_end(e))
        elif self.prev(i) == "at":
            self.put(i, e, f"{h}:{mi:02d}", "time")
        return e  # without "at" or am/pm it stays as said, but whole: not "20 past three"

    # versions: "version two point one" -> "version 2.1", "v two point one point three" -> "v2.1.3"
    def _version(self, i: int) -> int | None:
        w = self.w
        if w[i] not in ("version", "v", "ver") or not self.j(i):
            return None
        parts, k, spoken = [], i + 1, False
        while True:
            if re.fullmatch(r"\d+(?:\.\d+)*", w[k]):
                parts.append(self.toks[k].text)
                k += 1
            elif w[k] in ("oh", "o") and parts:  # "two point oh"
                parts.append("0")
                k, spoken = k + 1, True
            elif (num := self.num(k, ordinals=False, decimals=False)) and not num.scale and not num.article and num.value < 1000:
                parts.append(str(int(num.value)))
                k, spoken = num.end, True
            else:
                break
            if not (self.cont(k) and w[k] in ("point", "dot") and self.j(k)
                    and (w[k + 1] in _NUMBER_WORDS or w[k + 1] in ("oh", "o") or w[k + 1].isdigit())):
                break
            k += 1
        if not parts:
            return None
        if spoken:
            if w[i] == "v":
                self.put(i, k, self.toks[i].text + ".".join(parts), "number")
            else:
                self.put(i + 1, k, ".".join(parts), "number")
        return k

    # numbers, and what they belong to: percent, currency, units, labels, lists and ranges
    def _number(self, i: int) -> int | None:
        w, t = self.w, self.w[i]
        if t not in _NUMBER_START and not t[:1].isdigit():
            return None
        neg = t in ("minus", "negative")
        if neg and (not self.j(i) or self.prev(i) in _NUMBER_WORDS or (self.prev(i) or "")[-1:].isdigit()
                    or self.prev(i) in _DETERMINERS):
            return None  # "five minus three" is arithmetic
        k = i + neg
        first = self.num(k)
        if first is None or neg and (first.ordinal or first.ambiguous):
            return None
        if first.ambiguous:
            return self._skip(first.end)
        if first.ordinal:
            return self._ordinal(k, first)
        if re.fullmatch(r"(?:19|20)\d\d", w[k]) and self.prev(k) in _YEAR_CUES:
            return first.end  # a year: "in 2020 dollars" is not $2020
        if end := self._is_time(first):  # a time the time matcher didn't take ("ninety two o'clock"): left as said
            return None if neg else end  # "minus three pm": the time matcher takes "three pm" next
        members, conns, e = [(k, first)], [], first.end
        while not neg and (c := self._connector(e)):
            m = self.num(c[1])
            if not m or m.ordinal or m.ambiguous or self._is_time(m):
                break
            members.append((c[1], m))
            conns.append(c[0])
            e = m.end
        if "," in conns and len(members) < 3:  # "in 2020, five people": a list needs three
            members, conns = members[:conns.index(",") + 1], conns[:conns.index(",")]
            e = members[-1][1].end
        if self.cont(e) and (w[e] in _NUMBER_WORDS or w[e] in _PLURALS or w[e] in _SCALES or w[e] == "hundred"
                             or _DIGITS.fullmatch(w[e])):
            return self._skip(e)  # back to back: a year, a code, an idiom ("twenty four seven", "nineteen nineties")
        values = [m.value for _, m in members]
        if "to" in conns and any(b <= a for a, b in zip(values, values[1:], strict=False)):
            return e  # "ten to one" (odds, or 12:50) and "nine to five" aren't ranges
        struct = self._structure(e)
        if struct and struct[0] == "skip":
            return self._skip(struct[1])
        if struct and struct[0] in ("percent", "cents") and any(m.scale for _, m in members):
            struct = None  # "two million percent" keeps its word: "2 million%" reads badly
        if self.on(e, "and", "a") or struct and self.on(struct[1], "and", "a"):
            return self._skip(e)  # "two and a half", "two dollars and a half": a fraction we don't write
        if neg and not (struct or e >= self.n or not self.jn[e - 1] or self.prev(i) in _NEG_CUES):
            return None  # "the team, minus two players"
        if first.bare_point and not struct and k > 0 and self.jn[k - 1] and self.prev(k) not in _POINT_CUES:
            return None  # "a good point five people made"
        label = self.prev(k) in _LABELS
        if not (struct or label or neg or any(not (m.small or m.article) for _, m in members)):
            return e  # prose: "two options", "two or three people", "a hundred times"
        kind, end, info = struct or (None, e, None)
        for x, (s, m) in enumerate(members):
            last = x == len(members) - 1
            text, change, b = ("-" if neg else "") + m.text, "number", m.end
            if kind == "percent":
                text, change = text + "%", "percent"
            elif kind == "cents":
                text, change = text + "¢", "currency"
            elif kind == "currency":
                sym, cents = info
                body = m.text
                if last and cents is not None and not m.scale and "." not in body:
                    body += f".{cents:02d}"
                elif "." in body and not m.scale and len(body.split(".")[1]) == 1:
                    body += "0"  # "$2.50"
                text, change = ("-" if neg else "") + sym + body, "currency"
            elif kind == "unit" and info and last:
                text, change = f"{text} {info}", "unit"
            if last and (kind in ("percent", "cents", "currency") or info and kind == "unit"):
                b = end
            self.put(s - neg, b, text, change)
        return end if kind in ("percent", "cents", "currency") or info else e

    def _is_time(self, m: _Num) -> int:
        """The end of the am/pm or o'clock after a number (0: none)."""
        if m.value != int(m.value):
            return 0
        return (r := self._meridiem(m.end, int(m.value), False)) and r[1] or self._oclock(m.end)

    def _connector(self, e: int) -> tuple[str, int] | None:
        """What joins the next number in a list or range: ("to", start of the next number) or None."""
        w = self.w
        if e >= self.n:
            return None
        if self.cm[e - 1]:  # "five, ten and fifteen"
            s = e + (w[e] in ("and", "or") and self.j(e))
            return (",", s) if s < self.n and (w[s] in _HYPHENATED or w[s][:1].isdigit()) else None
        if not self.jn[e - 1]:
            return None
        if w[e] in ("to", "and", "or", "through", "thru") and self.j(e):
            return w[e], e + 1
        if self.seq(e, "out", "of") and self.j(e + 1):  # "nine out of ten"
            return "out of", e + 2
        return None

    def _structure(self, e: int) -> tuple[str, int, object] | None:
        """What a number at the token before e belongs to: ("percent" | "currency" | "cents" | "unit", end, details)."""
        if not (e < self.n and self.jn[e - 1]):
            return None
        w, t, p = self.w, self.w[e], self.policy
        if t in ("percent", "pct"):
            return "percent", e + 1, None
        if self.seq(e, "per", "cent"):
            return "percent", e + 2, None
        sym, end = _CURRENCY.get(t), e + 1
        if t in ("pound", "pounds") and self.seq(e, t, "sterling"):
            sym, end = "£", e + 2
        if sym:
            if p.currency_style != "symbol":
                return "unit", end, None
            k = end + self.on(end, "and")  # "twenty five dollars and fifty cents", "five dollars fifty"
            m = self.num(k, ordinals=False, decimals=False) if self.cont(k) else None
            if m and not m.scale and not m.article and 1 <= m.value <= 99 and m.value == int(m.value):
                if self.cont(m.end) and w[m.end] in _SUBUNITS[sym]:
                    return "currency", m.end + 1, (sym, int(m.value))
                if k == end and not self.cont(m.end):
                    return "currency", m.end, (sym, int(m.value))
            if self.cont(end) and (w[end] in _NUMBER_WORDS or w[end][:1].isdigit()):
                return "skip", end, None  # "five dollars fifty people": not sure what was meant
            return "currency", end, (sym, None)
        if t in ("cent", "cents"):
            return ("cents" if p.currency_style == "symbol" else "unit"), e + 1, None
        for words, abbr in _UNIT_INDEX.get(t, ()):
            if self.seq(e, *words):
                end = e + len(words)
                if abbr in _DATA and self.on(end, "per", "second"):
                    abbr, end = abbr + "/s", end + 2
                return "unit", end, abbr if p.unit_style == "abbreviated" else None
        if (abbr := self.toks[e].text) in _WRITTEN_UNITS:  # written already: "5 GB", "100 MB per second", "5 m"
            if abbr in _DATA and self.on(e + 1, "per", "second"):
                return "unit", e + 3, abbr + "/s"
            return "unit", e + 1, abbr
        return None

    def _ordinal(self, k: int, num: _Num) -> int:
        # "a tenth", "one hundredth", "three tenth": fractions. After a number in digits too ("3 tenth", "v22 tenth"), so
        # that whatever a number before it became, formatting the output again changes nothing.
        p = self.prev(k) or ""
        fraction = p in ("a", "an") or p in _NUMBER_WORDS or p[-1:].isdigit() or self.w[k] == "one" and num.end - k == 2
        if num.spoken and num.value >= 10 and not fraction:
            self.put(k, num.end, num.text, "ordinal")
        return num.end

    def _skip(self, e: int) -> int:
        """Past a run of number words left as spoken."""
        while e < self.n and self.jn[e - 1] and (self.w[e] in _HYPHENATED or self.w[e] in _SCALES or self.w[e] in (
                "hundred", "point", "and") or _DIGITS.fullmatch(self.w[e])):
            e += 1
        return e


class Formatter:
    """Writes the structures said in recognised text: Formatter().format("it went up twenty five percent").text ->
    "it went up 25%". One instance can be shared; format() keeps no state between calls."""

    def __init__(self, policy: FormattingConfig | None = None):
        self.policy = policy or FormattingConfig()

    def format(self, text: str) -> FormattedTranscript:
        p = self.policy
        if not p.enabled or p.number_style == "off" or not text.strip():
            return FormattedTranscript(text)
        out, changes, pos = [], [], 0
        for s, e, new, kind in _Scan(text, p).run():
            out += [text[pos:s], new]
            changes.append(FormatChange(kind, text[s:e], new))
            pos = e
        out.append(text[pos:])
        return FormattedTranscript("".join(out), changes)
