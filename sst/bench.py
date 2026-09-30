"""The reading test: how well Rflow understands this user's voice, microphone and setup.

The user reads SENTENCES aloud (the tray app's "Reading test" window records them into BENCH_DIR/<date>/). score()
then transcribes every recording with Parakeet, cleans the text up with each chosen model (one request at a time),
and compares everything with what was read: the word error rate per setup, the time per sentence, and the words
that were misheard most, which are good candidates for the user's "Your words".
"""
import json
import os
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from sst.audio import load_wav

BENCH_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "sst" / "bench"

# Everyday dictation with the kind of words this user says: names, tools, tech terms, numbers.
SENTENCES = [
    "Can you review the pull request before lunch and leave a comment if anything looks wrong?",
    "I merged the fix into main and the release workflow published the new installer.",
    "Please create a merge commit instead of squashing, so the history stays readable.",
    "The tray app shows a small pill at the bottom of the screen while I am speaking.",
    "Let's move the stand-up meeting to Thursday morning and share the notes in Slack.",
    "Open a new branch for phase seven, then push three small commits to GitHub.",
    "Rflow uses the Parakeet model to turn my voice into text on this laptop.",
    "The website on Vercel always links to the latest version of the installer.",
    "I would like to learn Tamil and Japanese, but English comes first for now.",
    "Send the API key to the endpoint only after the test button shows a green result.",
    "Our CI pipeline runs the tests on Windows and checks the code with CodeQL.",
    "Thanks for the quick reply, I will send you the updated draft this afternoon.",
    "The download finished in about 70 seconds and the checksum matched.",
    "Please book a room for 6 people on Friday at 2 o'clock.",
    "If the gateway is slow, the text is typed exactly as it was heard.",
    "I need 20 to 50 names and terms for my personal dictionary.",
    "The microphone on my earbuds sounds worse than the one built into the laptop.",
    "Hold Control and the Windows key, speak, and let go when you are done.",
    "Python makes it easy to write a small script that renames all these files.",
    "We should add a settings file so that people can change the hotkey without the command line.",
    "The model loads in about 2 seconds and transcribes a short sentence almost instantly.",
    "Could you check whether the new version works on the other laptop as well?",
    "Our customers in Japan asked for a Japanese version of the documentation.",
    "I will be working from home tomorrow, so please call me on Teams if it is urgent.",
    "The installer is not code signed yet, so Windows shows a warning the first time.",
    "Deploy the website again after you update the screenshots in the site folder.",
    "My name is Karthi and I build tools that make everyday work a little faster.",
    "Please summarize the three main risks and suggest one next step for each of them.",
    "The quick brown fox jumps over the lazy dog near the riverbank.",
    "Double-check the numbers before the report goes out to the whole team on Monday.",
]

# Common words: when they're misheard, adding them to "Your words" wouldn't help.
_COMMON = set("""a an and are as at be but by can could did do does for from had has have he her his i if in into is it
its me my no not of on or our she so that the their them then there these they this to too was we were what when
which while who will with would you your""".split())
_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen " \
        "eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _number_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        rest = n % 100
        return _ONES[n // 100] + " hundred" + ("" if rest == 0 else " " + _number_words(rest))
    rest = n % 1000
    return _number_words(n // 1000) + " thousand" + ("" if rest == 0 else " " + _number_words(rest))


def words(text: str) -> list[str]:
    """Normalised words for a fair comparison: case, punctuation and hyphens don't count, "70" equals "seventy"."""
    text = text.lower().replace("-", " ").replace("'", "").replace("’", "")
    out = []
    for token in re.findall(r"[a-z]+|\d+", text):
        if token.isdigit() and int(token) < 1_000_000:
            out.extend(_number_words(int(token)).split())
        else:
            out.append({"ok": "okay"}.get(token, token))
    return out


def align(reference: list[str], heard: list[str]) -> list[tuple[str, str]]:
    """Word-by-word alignment (fewest edits): (ref, heard) pairs; "" marks a missing or an extra word."""
    n, m = len(reference), len(heard)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        cost[i][0] = i
    for j in range(m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i][j] = min(cost[i - 1][j] + 1, cost[i][j - 1] + 1,
                             cost[i - 1][j - 1] + (reference[i - 1] != heard[j - 1]))
    pairs, i, j = [], n, m
    while i or j:
        # Among equally good moves, pair words that look alike ("tamil" with "them", not with "please"), so the list
        # of misheard words shows what was really confused; unlike words are then a missing or an extra word.
        diagonal = i and j and cost[i][j] == cost[i - 1][j - 1] + (reference[i - 1] != heard[j - 1])
        delete = i and cost[i][j] == cost[i - 1][j] + 1
        insert = j and cost[i][j] == cost[i][j - 1] + 1
        alike = diagonal and (reference[i - 1] == heard[j - 1]
                              or SequenceMatcher(None, reference[i - 1], heard[j - 1]).ratio() >= 0.5)
        if diagonal and (alike or not (delete or insert)):
            pairs.append((reference[i - 1], heard[j - 1]))
            i, j = i - 1, j - 1
        elif insert:
            pairs.append(("", heard[j - 1]))
            j -= 1
        else:
            pairs.append((reference[i - 1], ""))
            i -= 1
    return pairs[::-1]


def errors(reference: str, heard: str) -> tuple[int, int, list[tuple[str, str]]]:
    """(wrong words, words in the reference, the wrong (ref, heard) pairs)."""
    ref = words(reference)
    wrong = [pair for pair in align(ref, words(heard)) if pair[0] != pair[1]]
    return len(wrong), len(ref), wrong


@dataclass
class Setup:
    name: str  # e.g. "Parakeet alone" or "Parakeet + <model>"
    wrong: int = 0
    total: int = 0
    seconds: list[float] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)

    @property
    def error_rate(self) -> float:
        return self.wrong / self.total if self.total else 0.0

    @property
    def seconds_per_sentence(self) -> float:
        return sum(self.seconds) / len(self.seconds) if self.seconds else 0.0


@dataclass
class Results:
    folder: str
    sentences: list[str]
    setups: list[Setup]
    misheard: list[tuple[str, str, int]]  # (said, heard, times), by Parakeet alone
    suggestions: list[str]  # words worth adding to "Your words"

    def save(self) -> None:
        folder = Path(self.folder)
        data = asdict(self)
        for setup, saved in zip(self.setups, data["setups"], strict=True):
            saved["error_rate"], saved["seconds_per_sentence"] = setup.error_rate, setup.seconds_per_sentence
        (folder / "results.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        (folder / "report.md").write_text(self.report(), encoding="utf-8")

    def report(self) -> str:
        lines = [f"# Reading test ({len(self.sentences)} sentences)", "",
                 "| Setup | Word errors | Wrong / total | Time per sentence |", "|---|---|---|---|"]
        lines += [f"| {s.name} | {s.error_rate:.1%} | {s.wrong} / {s.total} | {s.seconds_per_sentence:.2f} s |"
                  for s in self.setups]
        if self.misheard:
            lines += ["", "## Most misheard", ""]
            lines += [f"- {said or '(extra word)'} → {heard or '(missed)'} ({times}×)" for said, heard, times in self.misheard]
        if self.suggestions:
            lines += ["", "## Worth adding to Your words", "", ", ".join(self.suggestions)]
        return "\n".join(lines) + "\n"


def recordings(folder: Path) -> list[tuple[Path, str]]:
    """The recorded sentences in a test folder: (wav, the sentence that was read), in order."""
    items = []
    for wav in sorted(folder.glob("*.wav")):
        ref = wav.with_suffix(".txt")
        if ref.exists():
            items.append((wav, ref.read_text(encoding="utf-8").strip()))
    return items


def unfinished(root: Path = BENCH_DIR) -> Path | None:
    """The newest test folder if it still has sentences to read, so closing the window halfway loses nothing."""
    newest = max((p for p in root.iterdir() if p.is_dir()), default=None) if root.exists() else None
    return newest if newest and 0 < len(recordings(newest)) < len(SENTENCES) else None


def score(folder: Path, engine, polishers: dict[str, object], progress: Callable[[str], None] = lambda text: None) -> Results:
    """Transcribe every recording, clean it up with each polisher (one request at a time) and compare with what was
    read. `polishers` maps a setup name to a sst.gateway.Polisher."""
    items = recordings(folder)
    alone = Setup("Parakeet alone")
    setups = [alone] + [Setup(name) for name in polishers]
    misheard: Counter[tuple[str, str]] = Counter()
    for k, (wav, sentence) in enumerate(items, 1):
        progress(f"Transcribing {k} of {len(items)}...")
        t0 = time.perf_counter()
        heard = engine.transcribe(*load_wav(wav))
        _add(alone, sentence, heard, time.perf_counter() - t0)
        misheard.update(errors(sentence, heard)[2])
    for setup, polisher in zip(setups[1:], polishers.values(), strict=True):
        for k, (heard, (_, sentence)) in enumerate(zip(alone.texts, items, strict=True), 1):
            progress(f"Cleaning up with {setup.name.split(' + ', 1)[-1]}: {k} of {len(items)}...")
            t0 = time.perf_counter()
            cleaned = polisher.polish(heard)
            _add(setup, sentence, cleaned, time.perf_counter() - t0)
    top = [(said, heard, times) for (said, heard), times in misheard.most_common(15)]
    suggestions = _suggest(misheard, [s for _, s in items])
    results = Results(str(folder), [s for _, s in items], setups, top, suggestions)
    results.save()
    return results


def _add(setup: Setup, sentence: str, text: str, seconds: float) -> None:
    wrong, total, _ = errors(sentence, text)
    setup.wrong, setup.total = setup.wrong + wrong, setup.total + total
    setup.seconds.append(seconds)
    setup.texts.append(text)


def _suggest(misheard: Counter, sentences: list[str]) -> list[str]:
    """Words the user said that were misheard, as written in the sentences (e.g. "Tamil", "CodeQL"), most frequent
    first; common words are left out, since adding them to the vocabulary wouldn't help."""
    spelled = {}
    for sentence in sentences:
        for token in re.findall(r"[A-Za-z][A-Za-z']*(?:-[A-Za-z]+)*", sentence):
            spelled.setdefault(token.lower().replace("'", "").replace("-", ""), token)
    counts: Counter[str] = Counter()
    for (said, _heard), times in misheard.items():
        if said and said not in _COMMON and len(said) > 2:
            counts[said] += times
    return [spelled.get(word, word) for word, _ in counts.most_common(20)]
