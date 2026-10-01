"""OpenAI Whisper large-v3 turbo on this computer, through faster-whisper (CTranslate2): the second speech model.

It knows 99 languages, and an NVIDIA graphics card makes it fast. On the owner's laptop (i5-1334U, no NVIDIA card,
60 reading-test recordings) it made fewer errors than Parakeet (17.5% against 20.2%; names 29% against 46%) but took
about 10 s per sentence against 1.2 s, so Parakeet stays the default for English. sherpa-onnx's own Whisper was both
less accurate (22.5%) and slow (5.9 s). Greedy decoding was as accurate as beam search (18.1%) and faster.

The model is downloaded when the user chooses it (sst.downloads), from a pinned revision with every file checked.
"""
import hashlib
import logging
import os
import threading
from pathlib import Path

import numpy as np

from sst.audio import TARGET_RATE, condition, resample
from sst.downloads import Download, ModelFile
from sst.pipeline.contracts import RawTranscript, WordInfo

REPO = "dropbox-dash/faster-whisper-large-v3-turbo"  # formerly mobiuslabsgmbh/...; Hugging Face redirects the old name
REVISION = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
MODEL = Download("faster-whisper-large-v3-turbo", f"https://huggingface.co/{REPO}/resolve/{REVISION}/", (
    ModelFile("config.json", 2263, "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"),
    ModelFile("preprocessor_config.json", 340, "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"),
    ModelFile("tokenizer.json", 2710337, "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"),
    ModelFile("vocabulary.json", 1068114, "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"),
    ModelFile("model.bin", 1617884929, "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
))
# The languages offered in the window ("" = Whisper detects it). Whisper knows 99; these are the likely ones first.
LANGUAGES = {"": "Automatic", "en": "English", "ta": "Tamil", "ja": "Japanese", "hi": "Hindi", "te": "Telugu",
             "ml": "Malayalam", "kn": "Kannada", "bn": "Bengali", "mr": "Marathi", "zh": "Chinese", "ko": "Korean",
             "ar": "Arabic", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese", "ru": "Russian"}

log = logging.getLogger(__name__)


def segment_words(segments) -> list[WordInfo]:
    """faster-whisper's word times (word_timestamps=True) as WordInfo. Its words carry the space before them
    (" Hello"), which is dropped; its probability becomes the confidence."""
    words = []
    for segment in segments:
        for word in getattr(segment, "words", None) or []:
            if text := word.word.strip():
                probability = getattr(word, "probability", None)
                words.append(WordInfo(text, float(word.start), max(float(word.start), float(word.end)),
                                      None if probability is None else float(probability)))
    return words


def _cuda_devices() -> int:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count()
    except Exception:  # no NVIDIA driver
        return 0


class WhisperEngine:
    name = "whisper-turbo"
    title = "Whisper turbo"

    def __init__(self, root: Path | None = None, language: str = "", threads: int | None = None):
        if not MODEL.installed(root):
            raise FileNotFoundError("Whisper turbo isn't downloaded yet: choose it on the Speech recognition page.")
        from faster_whisper import WhisperModel

        self.language = language  # "" = detected per recording; the app keeps it up to date
        self.words: list[str] = []  # Your words, given to Whisper as a hint; the app keeps it up to date
        self._lock = threading.Lock()  # dictation and the reading test may transcribe at the same time
        path = str(MODEL.path(root))
        self.device, self._model = "cpu", None
        if _cuda_devices():
            try:  # fast, if NVIDIA's libraries (cuBLAS, cuDNN) are installed
                self._model = WhisperModel(path, device="cuda", compute_type="float16")
                self.device = "cuda"
            except Exception as e:
                log.info("No NVIDIA acceleration for Whisper (%s); using the processor", e)
        if self._model is None:
            self._model = WhisperModel(path, device="cpu", compute_type="int8",
                                       cpu_threads=threads or min(8, os.cpu_count() or 4))

    @property
    def signature(self) -> str:
        """What the text depends on (sst.evaluate caches by it)."""
        words = hashlib.sha1("/".join(self.words).encode("utf-8")).hexdigest()[:10] if self.words else "none"
        return f"whisper|{REPO}@{REVISION[:8]}|{self.device}|greedy|lang:{self.language or 'auto'}|words:{words}|peak-1"

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        with self._lock:
            audio = resample(condition(audio), sample_rate, TARGET_RATE)
            words = ", ".join(w for w in self.words if w.strip())  # read once: the app may change them meanwhile
            segments, _ = self._model.transcribe(
                audio, language=self.language or None, beam_size=1, condition_on_previous_text=False,
                without_timestamps=True, vad_filter=False, hotwords=words or None)
            return " ".join(s.text.strip() for s in segments).strip()

    def transcribe_chunk(self, audio: np.ndarray, sample_rate: int) -> RawTranscript:
        """transcribe() with word times in seconds from the chunk's start, and the language it heard. The decoding is
        the same; faster-whisper lines the words up with the audio afterwards (beyond 30 s that moves where its next
        window starts, but a chunk is at most about 20 s)."""
        with self._lock:
            audio = resample(condition(audio), sample_rate, TARGET_RATE)
            words = ", ".join(w for w in self.words if w.strip())
            segments, info = self._model.transcribe(
                audio, language=self.language or None, beam_size=1, condition_on_previous_text=False,
                without_timestamps=True, vad_filter=False, hotwords=words or None, word_timestamps=True)
            segments = list(segments)  # decoded while iterated: inside the lock
        text = " ".join(s.text.strip() for s in segments).strip()
        return RawTranscript(text, segment_words(segments), getattr(info, "language", None) or self.language, self.name)
