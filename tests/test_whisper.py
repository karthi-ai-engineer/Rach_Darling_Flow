"""Whisper turbo through faster-whisper, with a fake model (the real one is 1.6 GB and slow on a processor)."""
import sys
import types

import numpy as np
import pytest

from sst.audio import resample
from sst.engines import whisper


def test_resampling_keeps_the_sound_and_the_length():
    rate, target = 48_000, 16_000
    t = np.arange(rate) / rate
    tone = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)  # 1 s of 1 kHz
    out = resample(tone, rate, target)
    assert len(out) == target and out.dtype == np.float32
    spectrum = np.abs(np.fft.rfft(out[2000:-2000]))
    peak_hz = np.argmax(spectrum) * target / len(out[2000:-2000])
    assert abs(peak_hz - 1000) < 5 and abs(float(np.abs(out).max()) - 0.5) < 0.02
    assert len(resample(tone[:44_100], 44_100, target)) == target  # any ratio
    assert resample(tone, target, target) is not None and len(resample(tone, target, target)) == len(tone)


class FakeWhisperModel:
    created = []

    def __init__(self, path, device="cpu", compute_type="", cpu_threads=0):
        if device == "cuda":
            raise RuntimeError("cuBLAS not found")  # an NVIDIA card without NVIDIA's libraries
        self.path, self.device, self.calls = path, device, []
        FakeWhisperModel.created.append(self)

    def transcribe(self, audio, **options):
        self.calls.append((len(audio), options))
        return iter([types.SimpleNamespace(text=" Hello"), types.SimpleNamespace(text=" world. ")]), None


@pytest.fixture
def fake_whisper(monkeypatch, whisper_downloaded):
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeWhisperModel))
    FakeWhisperModel.created.clear()
    return FakeWhisperModel


def test_whisper_needs_its_download(no_downloaded_models):
    with pytest.raises(FileNotFoundError, match="downloaded"):
        whisper.WhisperEngine()


def test_whisper_hears_16_khz_with_the_language_and_your_words(fake_whisper):
    engine = whisper.WhisperEngine()
    engine.language, engine.words = "ta", ["Karthi", "Rflow"]
    assert engine.transcribe(np.zeros(48_000, dtype=np.float32), 48_000) == "Hello world."
    samples, options = fake_whisper.created[0].calls[0]
    assert samples == 16_000  # resampled from 48 kHz
    assert options["language"] == "ta" and options["hotwords"] == "Karthi, Rflow" and options["beam_size"] == 1
    engine.language, engine.words = "", []
    engine.transcribe(np.zeros(16_000, dtype=np.float32), 16_000)
    assert fake_whisper.created[0].calls[1][1]["language"] is None and fake_whisper.created[0].calls[1][1]["hotwords"] is None


def test_without_nvidia_libraries_whisper_runs_on_the_processor(fake_whisper, monkeypatch):
    monkeypatch.setattr(whisper, "_cuda_devices", lambda: 1)  # a card, but its libraries are missing
    engine = whisper.WhisperEngine()
    assert engine.device == "cpu" and fake_whisper.created[-1].device == "cpu"


def test_the_signature_follows_what_changes_the_text(fake_whisper):
    engine = whisper.WhisperEngine()
    first = engine.signature
    engine.language = "ja"
    assert engine.signature != first
    second = engine.signature
    engine.words = ["Karthi"]
    assert engine.signature != second and "whisper|" in engine.signature
