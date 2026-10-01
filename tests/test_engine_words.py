"""Word times from the speech engines on this computer, without their models (Parakeet is 660 MB, Whisper 1.6 GB):
Parakeet's word pieces grouped into words, faster-whisper's words, and both engines' transcribe_chunk with fake
decoders."""
import threading
import types

import numpy as np
import pytest

from sst.engines import parakeet
from sst.engines.parakeet import ParakeetEngine, token_words
from sst.engines.whisper import WhisperEngine, segment_words

RATE = 16_000


def _times(words):
    return [(w.text, round(w.start, 3), round(w.end, 3)) for w in words]


# ---- Parakeet: word pieces into words

def test_word_pieces_make_words_that_end_where_the_next_begins():
    # sherpa-onnx's tokens for the sample sentence: the "▁" that begins a word comes as a space, and a lone one may
    # come before the piece that spells the word
    tokens = [" We", "ll", ",", " I", " don", "'", "t", " ", "very"]
    times = [1.04, 1.04, 1.12, 1.2, 1.2, 1.2, 1.28, 1.36, 1.36]
    assert _times(token_words(tokens, times, end=2.0)) == [("Well,", 1.04, 1.2), ("I", 1.2, 1.2), ("don't", 1.2, 1.36),
                                                           ("very", 1.36, 2.0)]  # the last ends with the audio


def test_raw_word_piece_markers_and_an_offset_work_too():
    words = token_words(["▁the", "▁cl", "oud", "▁"], [0.0, 0.3, 0.4, 0.9], end=1.0, offset=19.0)
    assert _times(words) == [("the", 19.0, 19.3), ("cloud", 19.3, 20.0)]  # a trailing lone marker spells nothing


def test_a_first_piece_without_a_marker_still_starts_a_word():
    assert _times(token_words(["he", "llo"], [0.2, 0.3], 1.0)) == [("hello", 0.2, 1.0)]


def test_no_times_without_one_per_piece():
    assert token_words([], [], 1.0) == [] and token_words([" a", " b"], [0.1], 1.0) == []


class FakeStream:
    def __init__(self, hotwords):
        self.hotwords, self.result = hotwords, None

    def accept_waveform(self, rate, audio):
        pass


class FakeRecognizer:
    """Decodes every piece to "hello world" with its word pieces and their times; with hotwords to "Karthi Karthi"
    (boosting run away). `timed=False`: a model that gives no times."""

    def __init__(self, timed=True):
        self.timed, self.asked = timed, []

    def create_stream(self, hotwords=None):
        self.asked.append(hotwords)
        return FakeStream(hotwords)

    def decode_stream(self, stream):
        text, tokens = ("Karthi Karthi", [" Kar", "thi", " Kar", "thi"]) if stream.hotwords else ("hello world",
                                                                                                    [" hel", "lo", " world"])
        times = [0.1 * i for i in range(len(tokens))]
        stream.result = types.SimpleNamespace(text=text, tokens=tokens if self.timed else [],
                                              timestamps=times if self.timed else [])


def _parakeet(recognizer, words=(), conditioned=False):
    engine = object.__new__(ParakeetEngine)  # no model: the decoder is a fake
    engine.conditioned, engine.biased, engine.words, engine._model = conditioned, True, list(words), "model"
    engine._recognizer, engine._lock = recognizer, threading.Lock()
    return engine


def test_parakeet_gives_the_text_transcribe_gives_with_word_times():
    engine = _parakeet(FakeRecognizer(), words=["Karthi"])
    raw = engine.transcribe_chunk(np.zeros(RATE, dtype=np.float32), RATE)
    assert raw.text == "hello world" == engine.transcribe(np.zeros(RATE, dtype=np.float32), RATE)
    assert engine._recognizer.asked[:2] == ["Karthi", None]  # the runaway guard decoded again without the words...
    assert _times(raw.words) == [("hello", 0.0, 0.2), ("world", 0.2, 1.0)]  # ...and the times are that decoding's
    assert raw.language == "en" and raw.backend == "parakeet"


def test_long_audio_is_decoded_in_pieces_with_times_from_the_chunk_start(monkeypatch):
    monkeypatch.setattr(parakeet, "split_at_pauses", lambda audio, rate, seconds: [audio[:2 * rate], audio[2 * rate:]])
    raw = _parakeet(FakeRecognizer()).transcribe_chunk(np.zeros(5 * RATE, dtype=np.float32), RATE)
    assert raw.text == "hello world hello world"
    assert _times(raw.words) == [("hello", 0.0, 0.2), ("world", 0.2, 2.0), ("hello", 2.0, 2.2), ("world", 2.2, 5.0)]


def test_audible_audio_decoded_to_nothing_is_tried_in_halves_with_their_times():
    recognizer = FakeRecognizer()
    decode = recognizer.decode_stream

    def first_one_empty(stream):
        decode(stream)
        if len(recognizer.asked) == 1:
            stream.result = types.SimpleNamespace(text="", tokens=[], timestamps=[])
    recognizer.decode_stream = first_one_empty
    speech = np.random.default_rng(0).uniform(-0.2, 0.2, 4 * RATE).astype(np.float32)
    raw = _parakeet(recognizer, conditioned=True).transcribe_chunk(speech, RATE)
    assert raw.text == "hello world hello world" and len(recognizer.asked) == 3
    assert [w.start for w in raw.words] == pytest.approx([0.0, 0.2, 2.0, 2.2])


def test_without_times_from_the_model_the_text_comes_alone():
    raw = _parakeet(FakeRecognizer(timed=False)).transcribe_chunk(np.zeros(RATE, dtype=np.float32), RATE)
    assert raw.text == "hello world" and raw.words == []


# ---- Whisper

def _word(word, start, end, probability=0.9):
    return types.SimpleNamespace(word=word, start=start, end=end, probability=probability)


def test_whisper_words_lose_the_space_before_them():
    segments = [types.SimpleNamespace(text=" Hello world.", words=[_word(" Hello", 0.0, 0.4), _word(" world.", 0.4, 0.9, 0.5)]),
                types.SimpleNamespace(text=" Again", words=[_word(" Again", 1.2, 1.6), _word(" ", 1.6, 1.6)]),
                types.SimpleNamespace(text="", words=None)]
    words = segment_words(segments)
    assert _times(words) == [("Hello", 0.0, 0.4), ("world.", 0.4, 0.9), ("Again", 1.2, 1.6)]
    assert words[1].confidence == 0.5


class FakeWhisperModel:
    def transcribe(self, audio, **options):
        self.samples, self.options = len(audio), options
        segment = types.SimpleNamespace(text=" Vanakkam, Karthi.", words=[_word(" Vanakkam,", 0.1, 0.7),
                                                                          _word(" Karthi.", 0.7, 1.2)])
        return iter([segment]), types.SimpleNamespace(language="ta")


def test_whisper_gives_word_times_and_the_language_it_heard():
    engine = object.__new__(WhisperEngine)  # no model: faster-whisper is a fake
    engine.language, engine.words, engine._lock, engine._model = "", ["Karthi"], threading.Lock(), FakeWhisperModel()
    raw = engine.transcribe_chunk(np.zeros(48_000, dtype=np.float32), 48_000)
    assert raw.text == "Vanakkam, Karthi." and raw.language == "ta" and raw.backend == "whisper-turbo"
    assert _times(raw.words) == [("Vanakkam,", 0.1, 0.7), ("Karthi.", 0.7, 1.2)]
    options = engine._model.options
    assert options["word_timestamps"] is True and options["hotwords"] == "Karthi" and options["beam_size"] == 1
    assert engine._model.samples == 16_000  # resampled from 48 kHz, as transcribe() does
