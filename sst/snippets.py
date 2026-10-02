"""Snippets (the owner's idea of 2026-10-02, like Wispr Flow's): say a short phrase, get your own text typed.

  "my email"                 -> xyz@gmail.com              alone: the whole dictation is the cue (the default)
  "my signature"             -> Best regards,\\nKarthi       the text exactly as entered, line breaks included
  "send it to my email"      -> send it to xyz@gmail.com   only for a snippet set to count anywhere

A snippet is found in the words heard, before the dictionary, the formatting and the AI cleanup, and its text goes in at
the very last step: no stage changes it, and the AI never sees it. Inside a sentence the cue becomes a placeholder
("RFSNIP1"), which the AI is told to keep and the guard protects like code; if it doesn't come back exactly once, the
text from before the AI is used. "Anywhere" is never the default: "I checked my email this morning" must stay as said.
"""
import difflib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

PLACEHOLDER = "RFSNIP{}"  # letters and a number: the guard treats it as code, kept character for character
FUZZY = 0.9  # a whole dictation slightly misheard ("my emil") still counts, for a cue of 8 letters or more
MAX_CUE_WORDS = 8

# Said around a cue without changing it: "um, my email please".
_BEFORE = {"um", "uh", "er", "erm", "hmm", "okay", "ok", "so", "please", "and"}
_AFTER = {"please", "thanks", "thank", "you"}
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")


@dataclass(frozen=True)
class Snippet:
    cue: str  # what the user says
    text: str  # what is typed, exactly
    anywhere: bool = False  # also inside a sentence (else only as the whole dictation)

    @classmethod
    def from_dict(cls, data: dict) -> "Snippet | None":
        """From the profile's settings; None for an entry without a usable cue or text."""
        cue, text = " ".join(str(data.get("cue", "")).split()), str(data.get("text", ""))
        if not compact(cue) or not text.strip() or len(cue.split()) > MAX_CUE_WORDS:
            return None
        return cls(cue, text, bool(data.get("anywhere", False)))

    def to_dict(self) -> dict:
        return {"cue": self.cue, "text": self.text, "anywhere": self.anywhere}


def load(entries: Iterable[dict]) -> list[Snippet]:
    """The usable snippets of the profile's settings, each cue once (the first wins)."""
    out, seen = [], set()
    for data in entries:
        if isinstance(data, dict) and (snippet := Snippet.from_dict(data)) and compact(snippet.cue) not in seen:
            seen.add(compact(snippet.cue))
            out.append(snippet)
    return out


def compact(text: str) -> str:
    """Letters and digits only, lower case: "My e-mail." and "my email" are the same cue."""
    return "".join(_WORD.findall(text.casefold().replace("’", "'"))).replace("'", "")


def _words(text: str) -> list[re.Match]:
    return list(_WORD.finditer(text))


def alone(text: str, snippets: Sequence[Snippet]) -> Snippet | None:
    """The snippet whose cue is the whole of `text` (with a filler or "please" around it), or None."""
    words = [w.group().casefold() for w in _words(text)]
    while words and words[0] in _BEFORE:
        words.pop(0)
    while words and words[-1] in _AFTER:
        words.pop()
    said = "".join(w.replace("'", "").replace("’", "") for w in words)
    if not said or len(words) > MAX_CUE_WORDS + 2:
        return None
    best, score = None, 0.0
    for snippet in snippets:
        cue = compact(snippet.cue)
        if said == cue:
            return snippet
        # Misheard a little, never a word short: "signature" alone isn't "my signature".
        if len(cue) >= 8 and abs(len(said) - len(cue)) <= 2 and len(words) == len(snippet.cue.split()):
            ratio = difflib.SequenceMatcher(None, said, cue).ratio()
            if ratio >= FUZZY and ratio > score:
                best, score = snippet, ratio
    return best


def protect(text: str, snippets: Sequence[Snippet]) -> tuple[str, dict[str, Snippet]]:
    """`text` with the cue of each anywhere-snippet replaced by a placeholder, and the placeholders' snippets. A cue
    counts as whole words in a row ("my e-mail" is "my email"), never inside another word."""
    slots: dict[str, Snippet] = {}
    for snippet in sorted((s for s in snippets if s.anywhere), key=lambda s: -len(compact(s.cue))):  # longest first
        cue = compact(snippet.cue)
        while True:
            words = _words(text)
            found = _find(words, cue)
            if found is None:
                break
            first, last = found
            key = PLACEHOLDER.format(len(slots) + 1)
            slots[key] = snippet
            text = text[:words[first].start()] + key + text[words[last].end():]
    return text, slots


def _find(words: list[re.Match], cue: str) -> tuple[int, int] | None:
    """The first run of words that spells `cue` (letters and digits only), as (first, last) word indexes."""
    for first in range(len(words)):
        joined = ""
        for last in range(first, len(words)):
            word = words[last].group()
            if re.fullmatch(r"RFSNIP\d+", word):
                break  # a placeholder is never part of a cue
            joined += compact(word)
            if joined == cue:
                return first, last
            if not cue.startswith(joined):
                break
    return None


def expand(text: str, slots: dict[str, Snippet]) -> str | None:
    """`text` with each placeholder replaced by its snippet's text, or None unless every placeholder is there exactly
    once (an AI that dropped, doubled or moved one into another word): the caller then uses an earlier text."""
    if not slots:
        return text
    for key in slots:
        if len(re.findall(rf"(?<![^\W_]){key}(?![^\W_])", text, re.I)) != 1:
            return None
    return re.sub(r"(?<![^\W_])RFSNIP\d+(?![^\W_])",
                  lambda m: slots[m.group().upper()].text if m.group().upper() in slots else m.group(), text, flags=re.I)
