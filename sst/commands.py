"""Voice commands for Text Transform: hold the dictation key and say "make it concise" instead of a sentence.

A dictation is a command only when the whole of it is one command phrase (with at most a filler or "please" around it):
"make it concise" transforms the last dictation or the selected text, "make it concise and send it to John" is typed as
dictated. Each transform has default phrases; the user can replace them with their own on the Text Transform page
(Settings.command_phrases: transform -> phrases separated by commas or new lines). Phrases of one word aren't offered by
default ("professional" alone is a word people dictate), but a user may add them.
"""
import difflib
import re

UNDO = "undo"

DEFAULT_PHRASES: dict[str, tuple[str, ...]] = {
    "concise": ("make it concise", "make it more concise", "make it shorter", "shorten it", "make it short",
                "make it brief", "concise version"),
    "professional": ("make it professional", "make it more professional", "make it formal", "make it more formal",
                     "professional version", "formal version"),
    "bullets": ("make it bullet points", "bullet points", "make it a list", "turn it into bullet points",
                "turn it into a list", "bullet list", "make it bullets"),
    "actions": ("action items", "make it action items", "turn it into action items", "make it a to do list",
                "to do list", "make it a task list", "task list"),
    "rewrite": ("rewrite it", "make it clearer", "rewrite it more clearly"),
    UNDO: ("undo", "undo that", "undo it", "undo the transform", "restore the original", "put it back"),
}
MAX_WORDS = 8  # a command is short: anything longer is dictation
FUZZY = 0.9  # a slightly misheard phrase still counts ("make it consise"); a different sentence doesn't

# Said around a command without changing it: "okay, make it concise please".
_BEFORE = {"um", "uh", "er", "okay", "ok", "so", "please", "hey", "now", "rflow", "and", "alright", "right"}
_AFTER = {"please", "thanks", "now", "for", "me", "thank", "you"}
_PRONOUNS = {"this", "that", "it", "them"}  # "make this shorter" is "make it shorter"


def normalize(text: str) -> str:
    """Lowercase words without punctuation, "this"/"that" as "it", "to-do" as "to do", fillers around it removed."""
    text = text.casefold().replace("’", "'").replace("-", " ")
    words = ["it" if w in _PRONOUNS else w for w in re.findall(r"[\w']+", text)]
    while words and words[0] in _BEFORE:
        words.pop(0)
    while words and words[-1] in _AFTER:
        words.pop()
    return " ".join("to do" if w == "todo" else w for w in words)


def parse_phrases(text: str) -> list[str]:
    """The user's phrases as typed in the page's box: separated by commas or new lines, each once."""
    return list(dict.fromkeys(p for part in re.split(r"[,\n;]", text) if (p := " ".join(part.split()))))


def phrases_for(custom: dict[str, str]) -> dict[str, list[str]]:
    """Every command's phrases: the user's where they set some, else the defaults."""
    return {key: parse_phrases(custom[key]) if key in custom else list(default) for key, default in DEFAULT_PHRASES.items()}


def match_command(text: str, phrases: dict[str, list[str]]) -> str | None:
    """The command (a transform key, or UNDO) the whole of `text` says, or None for dictation."""
    said = normalize(text)
    if not said or len(said.split()) > MAX_WORDS:
        return None
    best, score = None, 0.0
    for key, options in phrases.items():
        for phrase in options:
            wanted = normalize(phrase)
            if not wanted:
                continue
            if said == wanted:
                return key
            # Close enough only for longer phrases: "make it consise", not "make it" for "make it concise".
            if len(wanted) >= 10 and abs(len(said) - len(wanted)) <= 3:
                ratio = difflib.SequenceMatcher(None, said, wanted).ratio()
                if ratio >= FUZZY and ratio > score:
                    best, score = key, ratio
    return best
