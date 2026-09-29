"""NVIDIA Parakeet (English) running locally on the CPU via sherpa-onnx."""
from pathlib import Path

import numpy as np

from sst import MODELS_DIR
from sst.audio import split_at_pauses

MODEL_DIR = MODELS_DIR / "sherpa-onnx-nemo-parakeet-unified-en-0.6b-int8-non-streaming"
# Longer audio is transcribed in pieces cut at pauses: as one piece, onnxruntime fails somewhere
# between 4 and 9 minutes of audio (a 514 s recording crashed; split, it transcribes fine).
MAX_PIECE_SECONDS = 30


def _find(model_dir: Path, prefix: str) -> Path | None:
    # Prefer the int8 file when both float and int8 versions are present.
    matches = sorted(model_dir.glob(f"{prefix}*.onnx"), key=lambda p: "int8" not in p.name)
    return matches[0] if matches else None


class ParakeetEngine:
    name = "parakeet"

    def __init__(self, model_dir: Path = MODEL_DIR, num_threads: int = 4):
        if not model_dir.exists():
            raise FileNotFoundError(
                f"Parakeet model not found in {model_dir}\n"
                "Download it with:  uv run python scripts/download_model.py parakeet"
            )
        import sherpa_onnx

        tokens = str(model_dir / "tokens.txt")
        encoder = _find(model_dir, "encoder")
        if encoder:  # transducer (TDT) export: encoder + decoder + joiner
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(encoder),
                decoder=str(_find(model_dir, "decoder")),
                joiner=str(_find(model_dir, "joiner")),
                tokens=tokens,
                model_type="nemo_transducer",
                num_threads=num_threads,
            )
        else:  # CTC export: a single model file
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
                model=str(_find(model_dir, "model")),
                tokens=tokens,
                num_threads=num_threads,
            )

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        texts = (self._decode(piece, sample_rate) for piece in split_at_pauses(audio, sample_rate, MAX_PIECE_SECONDS))
        return " ".join(text for text in texts if text)

    def _decode(self, audio: np.ndarray, sample_rate: int) -> str:
        stream = self._recognizer.create_stream()
        stream.accept_waveform(sample_rate, audio)
        self._recognizer.decode_stream(stream)
        return stream.result.text.strip()
