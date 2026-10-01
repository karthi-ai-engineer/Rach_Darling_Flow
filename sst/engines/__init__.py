"""Speech recognition: the building block that turns the voice into text, chosen apart from the AI cleanup.

SPEECH_MODELS is the catalog the window shows: each model says where it runs, which languages it knows, its size and
what it is good at. `ready` models can be used in this version; the others are listed as coming next.

An engine has a `name` (its catalog key), a `title` (what reports call it), `transcribe(audio, sample_rate) -> str`,
and a `signature` (what its text depends on: sst.evaluate caches by it). Engines that can use the user's words have a
`words` list, which the app keeps up to date. To add one, create engines/<name>.py and list it below.
"""
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from sst.downloads import Download
from sst.engines import whisper


class Engine(Protocol):
    name: str
    title: str

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str: ...


@dataclass(frozen=True)
class SpeechModel:
    key: str
    name: str
    where: str  # a WHERE key
    summary: str  # what it is good at, in one sentence
    languages: str
    size: str
    ready: bool = True  # usable in this version; the others are shown as coming next
    download: Download | None = None  # fetched when chosen (sst.downloads); None = comes with Rflow
    language_choice: bool = False  # the user can choose the language it listens for

    def installed(self) -> bool:
        return self.download is None or self.download.installed()


# Where a speech model runs, in the order the window shows them.
WHERE = {
    "local": "On this computer",
    "cloud": "Cloud",
    "server": "Your own server",
}

SPEECH_MODELS = {m.key: m for m in [
    SpeechModel("parakeet", "NVIDIA Parakeet", "local",
                "Fast and accurate English on any laptop's processor; listens for Your words.",
                "English", "640 MB, included"),
    SpeechModel("whisper-turbo", "OpenAI Whisper large-v3 turbo", "local",
                "Many languages, Tamil and Japanese included, and good with names. Without an NVIDIA graphics card "
                "it takes several seconds per sentence (Parakeet about one).",
                "99 languages", "1.6 GB, downloaded when chosen", download=whisper.MODEL, language_choice=True),
]}
DEFAULT_MODEL = "parakeet"
ENGINES = [key for key, model in SPEECH_MODELS.items() if model.ready]


def usable(key: str) -> str:
    """The model to load for a setting: the chosen one if this version can use it and it is downloaded, else the
    default (so a removed download never stops Rflow from starting)."""
    model = SPEECH_MODELS.get(key)
    return key if model and model.ready and model.installed() else DEFAULT_MODEL


def load_engine(name: str, language: str = "") -> Engine:
    if name == "parakeet":
        from .parakeet import ParakeetEngine

        return ParakeetEngine()
    if name == "whisper-turbo":
        return whisper.WhisperEngine(language=language)
    raise ValueError(f"Unknown engine '{name}'. Available: {', '.join(ENGINES)}")
