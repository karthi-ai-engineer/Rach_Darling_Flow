"""Offline replay: run the reading test's recordings through setups and compare them fairly.

Every change meant to improve accuracy (how audio is captured or trimmed, how the recogniser decodes, how the text is
corrected) is judged here on the same recordings of the user's own voice, so the only difference is the change:

- word errors, and errors on names and terms (bench.TERMS) apart from the other words, plus terms put where they
  weren't said (e.g. every "cloud" turned into "Claude")
- a 95% range for each setup's difference from the first one (paired bootstrap over the recordings, within each
  session), so a 1-point gain on 150 sentences isn't mistaken for progress
- the tuning sets (A, B) and the held-out test sets apart, per microphone, with each recording's audio measured
- time per recording, median and slowest 5%

Speech recognition results are cached next to the recordings (asr_cache.json), keyed by the engine's configuration, the
audio and any degradation, so comparing cleanup models doesn't transcribe everything again.
"""
import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from sst import bench
from sst.audio import AudioStats, load_wav, measure

CACHE_FILE = "asr_cache.json"
RESAMPLES = 1000  # bootstrap resamples for the 95% ranges


@dataclass
class Pipeline:
    """One setup to score. The first one is the baseline the others are compared with."""
    name: str
    degrade: str = ""  # make the audio worse on purpose, e.g. "narrowband" or "gain:-20" (see degrade())
    polisher: object = None  # a sst.gateway.Polisher (or anything with polish(text)) run on the recogniser's text


@dataclass
class Recording:
    wav: str
    sentence: str
    session: str  # the test folder's name
    block: str
    microphone: str
    stats: AudioStats


@dataclass
class Score:
    """One setup's results, per recording, so they can be resampled and split by set or microphone."""
    name: str
    texts: list[str] = field(default_factory=list)
    wrong: list[int] = field(default_factory=list)
    total: list[int] = field(default_factory=list)
    term_wrong: list[int] = field(default_factory=list)
    term_total: list[int] = field(default_factory=list)
    term_inserted: list[int] = field(default_factory=list)  # a term where something else was said
    chars_wrong: list[int] = field(default_factory=list)
    chars_total: list[int] = field(default_factory=list)
    seconds: list[float] = field(default_factory=list)
    interval: tuple[float, float] | None = None  # 95% range of the word error rate
    difference: tuple[float, float, float] | None = None  # (difference from the baseline, low, high); None for it

    @property
    def error_rate(self) -> float:
        return _rate(self.wrong, self.total)

    @property
    def term_error_rate(self) -> float:
        return _rate(self.term_wrong, self.term_total)

    @property
    def other_error_rate(self) -> float:
        return _rate([w - t for w, t in zip(self.wrong, self.term_wrong, strict=True)],
                     [w - t for w, t in zip(self.total, self.term_total, strict=True)])

    @property
    def char_error_rate(self) -> float:
        return _rate(self.chars_wrong, self.chars_total)

    def rate_where(self, keep: list[bool]) -> float:
        pairs = [(w, t) for w, t, k in zip(self.wrong, self.total, keep, strict=True) if k]
        return _rate([w for w, _ in pairs], [t for _, t in pairs])

    def percentile(self, q: float) -> float:
        return float(np.percentile(self.seconds, q)) if self.seconds else 0.0

    def add(self, sentence: str, text: str, seconds: float, terms: set[str]) -> None:
        ref, hyp = bench.compared(sentence, text)
        pairs = bench.align(ref, hyp)
        self.texts.append(text)
        self.wrong.append(sum(r != h for r, h in pairs))
        self.total.append(len(ref))
        self.term_wrong.append(sum(r != h for r, h in pairs if r in terms))
        self.term_total.append(sum(r in terms for r in ref))
        self.term_inserted.append(sum(h in terms and h != r for r, h in pairs))
        self.chars_wrong.append(_edit_distance(" ".join(ref), " ".join(hyp)))
        self.chars_total.append(len(" ".join(ref)))
        self.seconds.append(seconds)


def _rate(wrong: list[int], total: list[int]) -> float:
    return sum(wrong) / sum(total) if sum(total) else 0.0


def _edit_distance(a: str, b: str) -> int:
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        previous, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (ca != cb))
    return row[-1]


@dataclass
class Results:
    folders: list[str]
    recordings: list[Recording]
    scores: list[Score]
    misheard: list[tuple[str, str, int]]  # (said, heard, times) by the baseline
    suggestions: list[str]  # words worth adding to "Your words", from the tuning sets only

    @property
    def sentences(self) -> list[str]:
        return [r.sentence for r in self.recordings]

    @property
    def tuning(self) -> list[bool]:
        return [r.block in bench.TUNING for r in self.recordings]

    def microphones(self) -> dict[str, list[int]]:
        """Microphone -> the recordings made with it."""
        out: dict[str, list[int]] = {}
        for k, r in enumerate(self.recordings):
            out.setdefault(r.microphone, []).append(k)
        return out

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        for score, saved in zip(self.scores, data["scores"], strict=True):
            saved.update(error_rate=score.error_rate, term_error_rate=score.term_error_rate,
                         other_error_rate=score.other_error_rate, char_error_rate=score.char_error_rate,
                         seconds_p50=score.percentile(50), seconds_p95=score.percentile(95))
        (folder / "results.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        (folder / "report.md").write_text(self.report(), encoding="utf-8")

    def report(self) -> str:
        n, sessions = len(self.recordings), len({r.session for r in self.recordings})
        lines = [f"# Accuracy: {n} recording{'s' if n != 1 else ''} from {sessions} test{'s' if sessions != 1 else ''}", "",
                 "| Setup | Word errors (95% range) | Against the first | Names and terms | Other words "
                 "| Terms put in wrongly | Character errors | Time p50 / p95 |",
                 "|---|---|---|---|---|---|---|---|"]
        for s in self.scores:
            lines.append(f"| {s.name} | {s.error_rate:.1%} ({_range(s.interval)}) | {_difference(s.difference)} "
                         f"| {s.term_error_rate:.1%} ({sum(s.term_wrong)} / {sum(s.term_total)}) | {s.other_error_rate:.1%} "
                         f"| {sum(s.term_inserted)} | {s.char_error_rate:.1%} "
                         f"| {s.percentile(50):.2f} / {s.percentile(95):.2f} s |")
        tuning = self.tuning
        if any(tuning) and not all(tuning):
            lines += ["", "## Tuning sets and test sets", "", "Improve with the tuning sets (A, B); trust the test sets (C-E).",
                      "", "| Setup | Tuning | Test |", "|---|---|---|"]
            lines += [f"| {s.name} | {s.rate_where(tuning):.1%} | {s.rate_where([not t for t in tuning]):.1%} |"
                      for s in self.scores]
        lines += ["", "## Microphones", "", "| Microphone | Recordings | Speech level | Signal to noise | High band "
                  "| Warnings | " + " | ".join(s.name for s in self.scores) + " |",
                  "|---|---|---|---|---|---|" + "---|" * len(self.scores)]
        for microphone, indexes in self.microphones().items():
            stats = [self.recordings[k].stats for k in indexes]
            flags = Counter(flag for st in stats for flag in st.flags)
            keep = [k in indexes for k in range(n)]
            lines.append(f"| {microphone} | {len(indexes)} | {np.median([s.speech_db for s in stats]):.0f} dBFS "
                         f"| {np.median([s.snr_db for s in stats]):.0f} dB | {np.median([s.high_band_db for s in stats]):.0f} dB "
                         f"| {', '.join(f'{flag} ×{times}' for flag, times in flags.items()) or '-'} | "
                         + " | ".join(f"{s.rate_where(keep):.1%}" for s in self.scores) + " |")
        lines += ["", "High band: the 4-7 kHz level against 0.3-3 kHz on speech. Below -45 dB the microphone is "
                  "narrowband (a Bluetooth headset in call mode); speech loses consonants there."]
        if self.misheard:
            lines += ["", f"## Most misheard ({self.scores[0].name})", ""]
            lines += [f"- {said or '(extra word)'} → {heard or '(missed)'} ({times}×)" for said, heard, times in self.misheard]
        if self.suggestions:
            lines += ["", "## Worth adding to Your words (from the tuning sets)", "", ", ".join(self.suggestions)]
        return "\n".join(lines) + "\n"


def _range(interval: tuple[float, float] | None) -> str:
    return f"{interval[0]:.1%}-{interval[1]:.1%}" if interval else "-"


def _difference(difference: tuple[float, float, float] | None) -> str:
    if difference is None:
        return "(the baseline)"
    mean, low, high = (100 * x for x in difference)
    verdict = "better" if high < 0 else "worse" if low > 0 else "no clear difference"
    return f"{mean:+.1f} points ({low:+.1f} to {high:+.1f}): {verdict}"


# ---- degrading audio on purpose: what would this recording give through a worse microphone?

def degrade(audio: np.ndarray, rate: int, spec: str) -> np.ndarray:
    """ "narrowband": only 300-3400 Hz, as through a Bluetooth headset in call mode or a phone line.
    "gain:<dB>": louder or quieter, clipped at full scale and rounded to 16 bits like a real recording."""
    if not spec:
        return audio
    name, _, value = spec.partition(":")
    if name == "narrowband" and not value:
        spectrum = np.fft.rfft(audio)
        freqs = np.fft.rfftfreq(len(audio), 1 / rate)
        spectrum[(freqs < 300) | (freqs > 3400)] = 0
        return np.fft.irfft(spectrum, len(audio)).astype(np.float32)
    if name == "gain":
        try:
            louder = audio * 10 ** (float(value) / 20)
        except ValueError:
            raise ValueError(f"'{spec}': the gain must be a number of dB, e.g. gain:-20") from None
        return (np.round(np.clip(louder, -1.0, 32767 / 32768) * 32768) / 32768).astype(np.float32)
    raise ValueError(f"unknown degradation '{spec}'. Use narrowband or gain:<dB> (e.g. gain:-20)")


# ---- the replay

def engine_signature(engine) -> str:
    """What the cached text depends on: the engine and its configuration (model, decoding...)."""
    return getattr(engine, "signature", type(engine).__name__)


def _load_cache(folder: Path) -> dict:
    try:
        data = json.loads((folder / CACHE_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def run(folders: list[Path], engine, pipelines: list[Pipeline], progress: Callable[[str], None] = lambda text: None,
        terms: list[str] = bench.TERMS, cache: bool = True) -> Results:
    """Score every recording in `folders` with each pipeline. Transcriptions are cached per session; cleanup models are
    asked one request at a time (a small server shouldn't get a burst) and never cached, since their speed counts."""
    term_set = bench.term_words(terms)
    recordings, audio_by_recording = [], []
    for folder in folders:
        session = bench.read_session(folder)
        for wav, sentence in bench.recordings(folder):
            audio, rate = load_wav(wav)
            recordings.append(Recording(str(wav), sentence, folder.name, session["block"],
                                        session.get("device") or session.get("microphone") or "unknown", measure(audio, rate)))
            audio_by_recording.append((folder, wav, audio, rate))
    scores = []
    for pipeline in pipelines:
        score = Score(pipeline.name)
        heard = _transcribe(engine, pipeline.degrade, audio_by_recording, progress, cache, pipeline.name)
        for k, (recording, (text, seconds)) in enumerate(zip(recordings, heard, strict=True), 1):
            if pipeline.polisher is not None:
                progress(f"{pipeline.name}: cleaning up {k} of {len(recordings)}...")
                t0 = time.perf_counter()
                text = pipeline.polisher.polish(text) if text else text
                seconds += time.perf_counter() - t0
            score.add(recording.sentence, text, seconds, term_set)
        scores.append(score)
    _compare(scores, recordings)
    misheard: Counter[tuple[str, str]] = Counter()
    tuning_misheard: Counter[tuple[str, str]] = Counter()
    for recording, text in zip(recordings, scores[0].texts if scores else [], strict=False):
        wrong = bench.errors(recording.sentence, text)[2]
        misheard.update(wrong)
        if recording.block in bench.TUNING:  # suggestions from the test sets would be learning the test by heart
            tuning_misheard.update(wrong)
    suggestions = bench.suggest(tuning_misheard, [r.sentence for r in recordings if r.block in bench.TUNING])
    return Results([str(f) for f in folders], recordings, scores,
                   [(said, heard, times) for (said, heard), times in misheard.most_common(15)], suggestions)


def _transcribe(engine, spec: str, items, progress, cache: bool, name: str) -> list[tuple[str, float]]:
    signature, out = engine_signature(engine), []
    caches: dict[Path, dict] = {}
    for k, (folder, wav, audio, rate) in enumerate(items, 1):
        store = caches.setdefault(folder, _load_cache(folder) if cache else {})
        key = f"{signature}|{spec}|{hashlib.sha1(wav.read_bytes()).hexdigest()[:16]}"
        hit = store.get(key)
        if isinstance(hit, dict) and isinstance(hit.get("text"), str):
            out.append((hit["text"], float(hit.get("seconds", 0.0))))
            continue
        progress(f"{name}: transcribing {k} of {len(items)}...")
        t0 = time.perf_counter()
        text = engine.transcribe(degrade(audio, rate, spec), rate)
        store[key] = {"text": text, "seconds": round(time.perf_counter() - t0, 3)}
        out.append((text, store[key]["seconds"]))
    if cache:
        for folder, store in caches.items():
            (folder / CACHE_FILE).write_text(json.dumps(store, indent=1, ensure_ascii=False), encoding="utf-8")
    return out


def _compare(scores: list[Score], recordings: list[Recording], resamples: int = RESAMPLES) -> None:
    """95% ranges by a paired bootstrap: draw the recordings again with replacement, within each session (so every
    draw keeps the mix of microphones and sets), and recompute. The same draws serve every setup, which is what makes
    the comparison with the baseline paired."""
    if not scores or not recordings:
        return
    rng = np.random.default_rng(0)  # the same data always gives the same ranges
    by_session: dict[str, list[int]] = {}
    for k, r in enumerate(recordings):
        by_session.setdefault(r.session, []).append(k)
    draws = np.concatenate([rng.choice(np.array(indexes), size=(resamples, len(indexes)))
                            for indexes in by_session.values()], axis=1)
    total = np.array(scores[0].total)[draws].sum(axis=1)
    total = np.maximum(total, 1)
    rates = [np.array(s.wrong)[draws].sum(axis=1) / total for s in scores]
    for score, rate in zip(scores, rates, strict=True):
        score.interval = (float(np.percentile(rate, 2.5)), float(np.percentile(rate, 97.5)))
    for score, rate in zip(scores[1:], rates[1:], strict=True):
        diff = rate - rates[0]
        score.difference = (score.error_rate - scores[0].error_rate, float(np.percentile(diff, 2.5)),
                            float(np.percentile(diff, 97.5)))


def pipelines_for(models: dict[str, object], degradations: list[str] = ()) -> list[Pipeline]:
    """The usual comparison: the recogniser alone, then each degradation, then each cleanup model."""
    out = [Pipeline("Parakeet alone")]
    out += [Pipeline(f"Parakeet, {spec}", degrade=spec) for spec in degradations]
    out += [Pipeline(f"Parakeet + {name}", polisher=polisher) for name, polisher in models.items()]
    for spec in degradations:
        degrade(np.zeros(16, dtype=np.float32), 16000, spec)  # a typo is reported before any slow work
    return out


def output_folder(folders: list[Path]) -> Path:
    """Where the report goes: the test's own folder, or a 'summary' folder next to the tests for several."""
    if len(folders) == 1:
        return folders[0]
    return folders[0].parent / "summary"
