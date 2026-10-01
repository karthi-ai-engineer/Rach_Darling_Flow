"""Learning from the user's corrections (the plan's §50, §70-71): when the user fixes the typed text the same way more than
once ("post grass" -> "PostgreSQL"), suggest adding the original words as an alias of the corrected term.

One correction proves little (a typo, a change of mind), so a pattern is only suggested once it was seen `min_seen`
times, and only the user's "accept" turns it into a dictionary rule; a rejected pattern is never suggested again.
Edits that say nothing about the user's words are ignored: casing or punctuation of ordinary words, one ordinary word
swapped for another ("their" -> "there" is grammar, not vocabulary), rewrites longer than a few words, insertions and
deletions, numbers, and undoing one of the dictionary's own replacements.
"""
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from sst.pipeline.dictionary import STOPWORDS, DictionaryStore, Term, TermMode, Token, compact_key, is_common, joinable, tokenize

MAX_SPAN = 4  # words on either side of one correction; more is a rewrite, not a mistaken term
MIN_SIMILARITY = 0.5  # of the whole texts (difflib's ratio over words): below it the user rewrote rather than corrected


@dataclass
class CorrectionEvent:
    original: str  # what Rflow typed
    corrected: str  # what the user changed it to
    app: str = ""
    timestamp: float = field(default_factory=time.time)
    session_id: str = ""


@dataclass
class Suggestion:
    id: int
    original_phrase: str  # normalized: lowercase, words separated by single spaces
    corrected_phrase: str  # as the user wrote it: it becomes the preferred spelling
    seen_count: int
    confirmed_count: int
    last_seen: float


def _suggestion(row) -> Suggestion:
    return Suggestion(*row)


class Learner:
    """Turns repeated corrections into suggestions, kept in the store's learned_candidates table."""

    def __init__(self, store: DictionaryStore, min_seen: int = 2):
        self.store = store
        self.min_seen = max(1, min_seen)

    def observe(self, event: CorrectionEvent) -> list[Suggestion]:
        """Record what this correction teaches; return the suggestions that reached min_seen with it."""
        pairs = self.pairs(event.original, event.corrected)
        out = []
        store = self.store
        with store._lock, store._db:  # the store's connection and lock: one writer at a time
            for original, corrected in pairs:
                key = compact_key(corrected)
                row = next((r for r in store._db.execute(
                    "SELECT id, corrected_phrase, seen_count, status FROM learned_candidates WHERE original_phrase = ?",
                    (original,)) if compact_key(r[1]) == key), None)
                if row is None:
                    row_id, seen, status = store._db.execute(
                        "INSERT INTO learned_candidates (original_phrase, corrected_phrase, seen_count, last_seen) "
                        "VALUES (?, ?, 1, ?)", (original, corrected, event.timestamp)).lastrowid, 1, "pending"
                else:
                    row_id, seen, status = row[0], row[2] + 1, row[3]
                    store._db.execute("UPDATE learned_candidates SET corrected_phrase = ?, seen_count = ?, last_seen = ? "
                                      "WHERE id = ?", (corrected, seen, event.timestamp, row_id))
                if status == "pending" and seen == self.min_seen:
                    out.append(self._load(row_id))
        return out

    def suggestions(self) -> list[Suggestion]:
        """Pending suggestions, the most often seen first."""
        with self.store._lock:
            rows = self.store._db.execute(
                "SELECT id, original_phrase, corrected_phrase, seen_count, confirmed_count, last_seen FROM learned_candidates "
                "WHERE status = 'pending' AND seen_count >= ? ORDER BY seen_count DESC, last_seen DESC", (self.min_seen,))
            return [_suggestion(r) for r in rows]

    def accept(self, suggestion: Suggestion) -> Term:
        """Teach the dictionary: the original phrase becomes an alias of the corrected term (created CAREFUL, "learned",
        if the user has no such term). A ValueError if the phrase already belongs to another term."""
        store = self.store
        with store._lock:
            term = store.find(suggestion.corrected_phrase)
            if term is None:
                term = store.add_term(suggestion.corrected_phrase, aliases=[suggestion.original_phrase], mode=TermMode.CAREFUL,
                                      source="learned")
            else:
                term = store.add_alias(term.id, suggestion.original_phrase)  # a no-op if it's the term's own spelling
            with store._db:
                store._db.execute("UPDATE learned_candidates SET status = 'accepted', confirmed_count = confirmed_count + 1 "
                                  "WHERE id = ?", (suggestion.id,))
            return term

    def reject(self, suggestion: Suggestion):
        """Never suggest this again (it is still counted, so it isn't recreated as a new candidate)."""
        with self.store._lock, self.store._db:
            self.store._db.execute("UPDATE learned_candidates SET status = 'rejected' WHERE id = ?", (suggestion.id,))

    def _load(self, row_id: int) -> Suggestion:
        return _suggestion(self.store._db.execute(
            "SELECT id, original_phrase, corrected_phrase, seen_count, confirmed_count, last_seen FROM learned_candidates "
            "WHERE id = ?", (row_id,)).fetchone())

    # ------------------------------------------------------------ what a correction teaches

    def pairs(self, original: str, corrected: str) -> list[tuple[str, str]]:
        """The (original phrase, corrected phrase) patterns worth counting in one correction."""
        a, b = tokenize(original), tokenize(corrected)
        if not a or not b:
            return []
        matcher = SequenceMatcher(None, [t.norm for t in a], [t.norm for t in b], autojunk=False)
        opcodes = matcher.get_opcodes()
        changed = max(sum(i2 - i1 for tag, i1, i2, _, _ in opcodes if tag != "equal"),
                      sum(j2 - j1 for tag, _, _, j1, j2 in opcodes if tag != "equal"))
        if matcher.ratio() < MIN_SIMILARITY and changed > MAX_SPAN:
            return []  # rewritten (a short sentence with one fixed phrase also scores low, so the size counts too)
        out = {}
        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                for k in range(i2 - i1):  # same word, other casing: a term's spelling ("github" -> "GitHub")?
                    old, new = a[i1 + k], b[j1 + k]
                    if old.text != new.text and old.key == new.key and self._casing_teaches(new, corrected):
                        out.setdefault((old.norm, new.key), (old.norm, new.text))
            elif tag == "replace" and i2 - i1 <= MAX_SPAN and j2 - j1 <= MAX_SPAN:
                if pair := self._pair(a[i1:i2], b[j1:j2], original, corrected):
                    out.setdefault((pair[0], compact_key(pair[1])), pair)
            # inserted or deleted words are content the user added or dropped, not a misheard term
        return list(out.values())

    @staticmethod
    def _casing_teaches(new: Token, corrected: str) -> bool:
        if is_common(new.norm) or new.text == new.text.lower():
            return False  # an ordinary word, or a term written in lowercase: nothing to learn
        before = corrected[:new.start].rstrip()
        sentence_start = not before or before[-1] in ".!?"
        return not (sentence_start and new.text[1:] == new.text[1:].lower())  # only the first letter: grammar

    def _pair(self, old: list[Token], new: list[Token], original: str, corrected: str) -> tuple[str, str] | None:
        # "ask the cloud" -> "ask Claude": the misheard word is "cloud"; "the" was only swallowed with it
        while len(old) > 1 and old[0].norm in STOPWORDS and old[0].norm != new[0].norm:
            old = old[1:]
        while len(old) > 1 and old[-1].norm in STOPWORDS and old[-1].norm != new[-1].norm:
            old = old[:-1]
        if not all(joinable(original[x.end:y.start]) for x, y in zip(old, old[1:], strict=False)) or \
                not all(joinable(corrected[x.end:y.start]) for x, y in zip(new, new[1:], strict=False)):
            return None  # the span crosses punctuation: two separate edits, not one phrase
        phrase = " ".join(t.norm for t in old)
        fixed = corrected[new[0].start:new[-1].end]
        if not any(c.isalpha() for c in phrase) or not any(c.isalpha() for c in fixed):
            return None  # numbers and symbols are the formatter's business ("five" -> "5")
        if all(is_common(t.norm) for t in old) and all(is_common(t.norm) for t in new):
            return None  # one ordinary word for another: grammar or a change of mind, not the user's vocabulary
        old_key, new_key = compact_key(phrase), compact_key(fixed)
        if len(old_key) < 2:
            return None
        if old_key == new_key:  # spacing or casing only ("java script" -> "JavaScript"): teaches the spelling
            return (phrase, fixed) if not is_common(new_key) and fixed != fixed.lower() else None
        owner = self.store.find(phrase)
        if owner is not None and compact_key(owner.preferred) != new_key:
            return None  # the user turned one of their terms into something else (perhaps undoing a replacement)
        with self.store._lock:
            if self.store._alias_owner(old_key) is not None:
                return None  # already this term's alias, or another term's (accepting it would be refused as ambiguous)
        return phrase, fixed
