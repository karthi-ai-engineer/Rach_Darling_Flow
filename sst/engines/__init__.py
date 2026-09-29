"""Speech-to-text engines. Each engine has a `name` and `transcribe(audio, sample_rate) -> str`.

To add one (e.g. Whisper), create engines/<name>.py with a class like ParakeetEngine
and register it in ENGINES below.
"""
from typing import Protocol

import numpy as np


class Engine(Protocol):
    name: str

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str: ...


ENGINES = ["parakeet"]


def load_engine(name: str) -> Engine:
    if name == "parakeet":
        from .parakeet import ParakeetEngine

        return ParakeetEngine()
    raise ValueError(f"Unknown engine '{name}'. Available: {', '.join(ENGINES)}")
