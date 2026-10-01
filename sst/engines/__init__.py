"""Speech recognition: the building block that turns the voice into text, chosen apart from the AI cleanup.

SPEECH_MODELS is the catalog the window shows: each model says where it runs, which languages it knows, its size and
what it is good at. `ready` models can be used in this version; the others are listed as coming next.

An engine has a `name` (its catalog key), a `title` (what reports call it), `transcribe(audio, sample_rate) -> str`,
and a `signature` (what its text depends on: sst.evaluate caches by it). Engines that can use the user's words have a
`words` list, which the app keeps up to date. To add one, create engines/<name>.py and list it below.

Cloud models (sst.engines.cloud) need the user's key for the provider, shared with AI cleanup (sst.gateway).
"""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from sst.downloads import Download
from sst.engines import whisper
from sst.engines.cloud import CLOUD, CloudEngine


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
    SpeechModel("openai", "OpenAI", "cloud",
                "OpenAI's transcription models: gpt-4o-mini-transcribe is quick, gpt-4o-transcribe the most accurate, "
                "whisper-1 the original Whisper.",
                "Many languages", "Nothing to download", language_choice=True),
    SpeechModel("groq", "Groq", "cloud",
                "Whisper large-v3 turbo on Groq's servers: the same model as on this computer, back in a fraction of "
                "a second.",
                "99 languages", "Nothing to download", language_choice=True),
    SpeechModel("gemini", "Google Gemini", "cloud",
                "Gemini's Flash models listen to the recording and write it down; good with names and mixed "
                "languages.",
                "Many languages", "Nothing to download", language_choice=True),
]}
DEFAULT_MODEL = "parakeet"
ENGINES = [key for key, model in SPEECH_MODELS.items() if model.ready]


def usable(key: str, keys=None) -> str:
    """The model to load for a setting: the chosen one if this version can use it, it is downloaded, and a cloud model
    has its key (`keys`: the sst.gateway.GatewayConfig holding the user's keys); else the default. So a removed
    download or a lost key never stops Rflow from starting."""
    model = SPEECH_MODELS.get(key)
    if not model or not model.ready or not model.installed():
        return DEFAULT_MODEL
    if model.where == "cloud" and not (keys and keys.key_for(key)):
        return DEFAULT_MODEL
    return key


def load_engine(name: str, language: str = "", api_key: str = "", model: str = "",
                fallback: Callable[[], Engine] | None = None) -> Engine:
    """`api_key` and `model` are a cloud model's; `fallback` loads the engine it uses when the provider fails."""
    if name == "parakeet":
        from .parakeet import ParakeetEngine

        return ParakeetEngine()
    if name == "whisper-turbo":
        return whisper.WhisperEngine(language=language)
    if name in CLOUD:
        return CloudEngine(name, api_key, model, language, fallback)
    raise ValueError(f"Unknown engine '{name}'. Available: {', '.join(ENGINES)}")
