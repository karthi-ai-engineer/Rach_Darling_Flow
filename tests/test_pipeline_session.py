"""The voice pipeline end to end, with synthetic speech and fake speech engines: chunks cut while the user speaks,
results ordered and merged, then dictionary, formatting, LLM and guard (no model, no network, no microphone)."""
import threading
import time

import numpy as np
import pytest

from sst.audio import Take
from sst.dictate import Dictation
from sst.pipeline.asr import ASRScheduler, EngineBackend
from sst.pipeline.contracts import Provenance, RawTranscript, VoiceConfig, WordInfo
from sst.pipeline.dictionary import DictionaryEngine, DictionaryStore, TermMode
from sst.pipeline.formatting import Formatter
from sst.pipeline.guard import Guard
from sst.pipeline.session import LazyBackend, Stages, VoicePipeline

RATE = 16_000


def speech(seconds: float, level: float = 0.05, seed: int = 0) -> np.ndarray:
    """Speech-like audio: noise shaped by a 4.5 Hz syllable rhythm."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    envelope = 0.55 + 0.45 * np.sin(2 * np.pi * 4.5 * t)
    return (rng.standard_normal(len(t)) * level * envelope).astype(np.float32)


def silence(seconds: float, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(seconds * RATE)) * 0.001).astype(np.float32)


class ScriptedBackend:
    """Answers each chunk with the next scripted text (by sequence), optionally slowly or failing."""
    name = "scripted"

    def __init__(self, texts, delays=None, fail=()):
        self.texts, self.delays, self.fail = texts, delays or {}, set(fail)
        self.calls = []

    def transcribe(self, chunk):
        self.calls.append(chunk.sequence)
        time.sleep(self.delays.get(chunk.sequence, 0))
        if chunk.sequence in self.fail:
            raise ValueError("the provider refused this audio")  # permanent: no retries
        text = self.texts[chunk.sequence - 1]
        n = len(text.split())
        words = [WordInfo(w, i * chunk.duration / max(n, 1), (i + 0.9) * chunk.duration / max(n, 1))
                 for i, w in enumerate(text.split())]
        return RawTranscript(text, words, backend=self.name)


class FakeLLM:
    name = "fake-llm"

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.seen = answer, error, []

    def polish(self, text, protected_terms=()):
        self.seen.append((text, list(protected_terms)))
        if self.error:
            raise RuntimeError(self.error)
        return self.answer(text) if callable(self.answer) else (self.answer if self.answer is not None else text)


def pipeline(backend, stages=None, **asr):
    config = VoiceConfig()
    for key, value in asr.items():
        setattr(config.asr, key, value)
    return VoicePipeline(ASRScheduler(backend, config.asr, sleep=lambda s: None), config, stages or Stages())


def two_sentences():
    return np.concatenate([silence(0.3), speech(2.0), silence(1.6), speech(1.8, seed=3), silence(0.5)])


def test_a_pause_splits_the_dictation_and_the_parts_are_joined_in_order():
    backend = ScriptedBackend(["I want to order a pizza.", "Please deliver it tonight."], delays={1: 0.15})
    final = pipeline(backend, max_in_flight=2).process_audio(two_sentences(), RATE)
    assert sorted(backend.calls) == [1, 2]
    assert final.provenance is Provenance.FORMATTED
    assert final.text == "I want to order a pizza. Please deliver it tonight."
    assert final.stages["merged"] == final.text and final.metrics["chunks"] == 2


def test_chunks_go_to_the_engine_while_the_user_is_still_speaking():
    backend = ScriptedBackend(["first part", "second part"])
    session = pipeline(backend).start(RATE)
    audio = two_sentences()
    first_half = int(4.2 * RATE)  # through the pause: the first chunk is cut here
    for i in range(0, first_half, 800):
        session.feed(audio[i:min(i + 800, first_half)])
    for _ in range(100):
        if backend.calls:
            break
        time.sleep(0.01)
    assert backend.calls == [1]  # before the key was let go
    session.feed(audio[first_half:])
    session.finish()
    assert session.result().text == "first part second part"


def test_a_failed_part_fails_the_whole_dictation_rather_than_typing_a_hole():
    backend = ScriptedBackend(["one", "two"], fail={2})
    final = pipeline(backend).process_audio(two_sentences(), RATE)
    assert final.provenance is Provenance.FAILED and final.text == ""
    assert "part 2" in final.error


def test_parakeet_stands_in_for_a_failed_part_and_says_so():
    backend = ScriptedBackend(["one", "two"], fail={2})
    stand_in = ScriptedBackend(["one", "two from parakeet"])
    stand_in.name = "parakeet"
    config = VoiceConfig()
    scheduler = ASRScheduler(backend, config.asr, fallback=LazyBackend("parakeet", lambda: stand_in), sleep=lambda s: None)
    final = VoicePipeline(scheduler, config).process_audio(two_sentences(), RATE)
    assert final.text == "one two from parakeet" and final.notes and "parakeet" in final.notes[0].lower()


def test_the_text_stages_run_in_order_and_each_is_kept():
    store = DictionaryStore()
    store.add_term("PostgreSQL", aliases=["post grass"], mode=TermMode.CAREFUL)
    config = VoiceConfig()
    stages = Stages(dictionary=DictionaryEngine(store, config.dictionary), formatter=Formatter(config.formatting),
                    llm=FakeLLM(lambda text: text.replace("um, ", "").replace("Um, ", "")), guard=Guard(config.guard),
                    terms=store.hint_terms)
    backend = ScriptedBackend(["Um, the post grass upgrade is twenty five percent done."])
    final = pipeline(backend, stages).process_audio(np.concatenate([speech(2.5), silence(0.4)]), RATE)
    assert final.stages["dictionary"] == "Um, the PostgreSQL upgrade is twenty five percent done."
    assert final.stages["formatted"] == "Um, the PostgreSQL upgrade is 25% done."
    assert final.text == "the PostgreSQL upgrade is 25% done." and final.provenance is Provenance.LLM_POLISHED
    assert stages.llm.seen[0][1] == ["PostgreSQL"]  # only the terms in this text are protected


def test_the_guard_keeps_the_text_from_before_the_llm_when_it_changes_a_number():
    config = VoiceConfig()
    llm = FakeLLM(lambda text: text.replace("5", "10"))
    stages = Stages(formatter=Formatter(config.formatting), llm=llm, guard=Guard(config.guard))
    backend = ScriptedBackend(["I need 5 servers by Friday."])
    final = pipeline(backend, stages).process_audio(np.concatenate([speech(2.0), silence(0.4)]), RATE)
    assert final.text == "I need 5 servers by Friday." and final.provenance is Provenance.FORMATTED_FALLBACK
    assert final.guard is not None and not final.guard.accepted


def test_an_unreachable_llm_types_the_trusted_text_and_says_why():
    stages = Stages(llm=FakeLLM(error="the AI endpoint could not be reached"), guard=Guard())
    final = pipeline(ScriptedBackend(["hello there"]), stages).process_audio(
        np.concatenate([speech(1.0), silence(0.4)]), RATE)
    assert final.text == "hello there" and final.provenance is Provenance.FORMATTED_FALLBACK
    assert "could not be reached" in final.error


def test_silence_types_nothing_and_asks_the_engine_nothing():
    backend = ScriptedBackend([])
    final = pipeline(backend).process_audio(silence(3.0), RATE)
    assert final.text == "" and backend.calls == [] and final.provenance is not Provenance.FAILED


def test_the_debug_folder_keeps_every_stage_and_the_audio(tmp_path):
    p = pipeline(ScriptedBackend(["hello there"]))
    p.debug_dir, p.keep_audio = tmp_path, True
    final = p.process_audio(np.concatenate([speech(1.0), silence(0.4)]), RATE)
    folder = tmp_path / final.session_id
    assert (folder / "session.json").exists() and list(folder.glob("chunk_*.wav"))


def test_engine_backend_wraps_the_existing_engines():
    class OldEngine:
        name, title = "parakeet", "Parakeet"

        def transcribe(self, audio, rate):
            return "plain text"
    final = pipeline(EngineBackend(OldEngine())).process_audio(np.concatenate([speech(1.0), silence(0.4)]), RATE)
    assert final.text == "plain text"


# ---- the dictation: the live session fed from the recorder's take

class LiveRecorder:
    """Like the real Recorder: start() opens a take with a pre-roll; the test appends audio while the key is "held"."""
    rate = RATE

    def __init__(self, preroll: np.ndarray):
        self.preroll, self.current_take = preroll, None

    def start(self):
        self.current_take = Take([self.preroll.copy()], self.rate)

    def stop_later(self):
        take, self.current_take = self.current_take, None
        take.done.set()
        return take


def test_dictation_uses_the_pipeline_live_and_types_the_final_text():
    typed, states, results = [], [], []
    recorder = LiveRecorder(np.concatenate([silence(1.5), speech(0.3)]))  # the user started just before the key
    backend = ScriptedBackend(["send the report tomorrow"])
    d = Dictation(None, recorder, paste=typed.append, sounds=False, save=False)
    d.pipeline = pipeline(backend)
    d.on_state = lambda state, message: states.append(state)
    d.on_result = lambda heard, text, seconds: results.append((heard, text))
    d.handle("press", 0.0)
    recorder.current_take.chunks += [speech(1.5, seed=5), silence(0.4)]
    d.handle("release", 2.0)
    d.wait()
    assert typed == ["send the report tomorrow "] and states[-1] == "typed"
    assert results == [("send the report tomorrow", "send the report tomorrow")]


def test_a_failed_dictation_keeps_the_recording_for_a_retry(monkeypatch):
    from sst import dictate
    saved = []
    monkeypatch.setattr(dictate, "save_recording", lambda audio, rate, text: saved.append(len(audio)) or "x")
    typed, states = [], []
    recorder = LiveRecorder(silence(0.2))
    backend = ScriptedBackend(["hello again"], fail={1})
    d = Dictation(None, recorder, paste=typed.append, sounds=False, save=True)
    d.pipeline = pipeline(backend)
    d.on_state = lambda state, message: states.append((state, message))
    d.handle("press", 0.0)
    recorder.current_take.chunks += [speech(1.2), silence(0.4)]
    d.handle("release", 2.0)
    d.wait()
    assert typed == [] and states[-1][0] == "error" and "Nothing was typed" in states[-1][1] and saved
    assert d.last_failed is not None
    backend.fail.clear()
    assert d.retry_last()
    d.wait()
    assert typed == ["hello again "]


def test_overlapping_dictations_never_mix():
    gate = threading.Event()

    class SlowFirst(ScriptedBackend):
        def transcribe(self, chunk):
            if chunk.session_id == first.session_id:
                gate.wait(2)
            return RawTranscript(f"text of {chunk.session_id[-3:]}")
    p = pipeline(SlowFirst([]), max_in_flight=2)
    first = p.start(RATE)
    second = p.start(RATE)
    for session in (first, second):
        session.feed(np.concatenate([speech(1.0), silence(0.4)]))
        session.finish()
    second_text = second.result(5).text
    gate.set()
    first_text = first.result(5).text
    assert second_text.endswith(second.session_id[-3:]) and first_text.endswith(first.session_id[-3:])


@pytest.mark.parametrize("pause, chunks", [(0.3, 1), (1.6, 2)])
def test_only_a_real_pause_splits(pause, chunks):
    backend = ScriptedBackend(["a", "b"])
    audio = np.concatenate([speech(1.5), silence(pause), speech(1.5, seed=7), silence(0.5)])
    final = pipeline(backend).process_audio(audio, RATE)
    assert final.metrics["chunks"] == chunks
