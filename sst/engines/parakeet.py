"""NVIDIA Parakeet (English) running locally on the CPU via sherpa-onnx.

With the model's word-piece vocabulary (bpe.vocab, fetched by scripts/download_model.py) the recogniser decodes with
beam search and listens for the user's own words ("hotwords"): on the owner's reading test that halved the errors on
names and tech terms (40% -> 24.5%) without hurting the other words. Without it, it decodes greedily as before.
"""
import hashlib
import logging
import re
import threading
from pathlib import Path

import numpy as np

from sst import MODELS_DIR
from sst.audio import condition, split_at_pauses

MODEL_DIR = MODELS_DIR / "sherpa-onnx-nemo-parakeet-unified-en-0.6b-int8-non-streaming"
# Audio longer than this is transcribed in pieces cut at pauses: as one piece, onnxruntime fails somewhere
# between 4 and 9 minutes (257 s worked, 514 s crashed). Up to 3 minutes (a whole dictation) stays one piece:
# cutting at 30 s dropped a whole sentence that the model gets right with the full context.
MAX_PIECE_SECONDS = 180
# How strongly the user's words are favoured. Chosen on the owner's tuning sets (0.5 / 1.0 / 1.5 / 2.0): 1.0 was best
# there and on the held-out sets. From 1.5 the model starts hearing names where they weren't said ("the cloud" ->
# "Claude"), and at 2.5 it repeats them ("Claude Claude Claude").
HOTWORD_SCORE = 1.0
BEAM = 4

log = logging.getLogger(__name__)


def _find(model_dir: Path, prefix: str) -> Path | None:
    # Prefer the int8 file when both float and int8 versions are present.
    matches = sorted(model_dir.glob(f"{prefix}*.onnx"), key=lambda p: "int8" not in p.name)
    return matches[0] if matches else None


def _vocab_matches(vocab: Path, tokens: Path) -> bool:
    """bpe.vocab must list the model's own word pieces in the model's order, or hotwords would be spelled wrong."""
    try:
        pieces = [line.split("\t")[0] for line in vocab.read_text(encoding="utf-8").splitlines()]
        known = [line.rsplit(" ", 1)[0] for line in tokens.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return False
    return bool(pieces) and pieces == known[:len(pieces)]


def hotword_text(words: list[str]) -> str:
    """sherpa-onnx's per-recording hotwords: phrases separated by "/"."""
    cleaned = (re.sub(r"\s+", " ", w.replace("/", " ")).strip() for w in words)
    return "/".join(dict.fromkeys(w for w in cleaned if w))


def runaway(text: str, words: list[str]) -> bool:
    """Boosting gone wrong: one of the user's words said twice in a row (seen as "Claude Claude Claude" when the
    score was too high). Such a result is decoded again without the words."""
    boosted = {w.lower() for phrase in words for w in re.findall(r"[\w']+", phrase)}
    said = re.findall(r"[\w']+", text.lower())
    return any(a == b and a in boosted for a, b in zip(said, said[1:], strict=False))


class ParakeetEngine:
    name = "parakeet"

    def __init__(self, model_dir: Path = MODEL_DIR, num_threads: int = 4, conditioned: bool = True,
                 hotwords: bool = True):
        if not model_dir.exists():
            raise FileNotFoundError(
                f"Parakeet model not found in {model_dir}\n"
                "Download it with:  uv run python scripts/download_model.py parakeet"
            )
        import sherpa_onnx

        self.conditioned = conditioned  # audio.condition() first: the laptop microphone is quiet, see there
        self.words: list[str] = []  # the user's words to listen for ("Your words"); the app keeps it up to date
        self._model = model_dir.name
        self._lock = threading.Lock()  # dictation and the reading test may transcribe at the same time
        tokens = model_dir / "tokens.txt"
        encoder = _find(model_dir, "encoder")
        vocab = model_dir / "bpe.vocab"
        self.biased = bool(hotwords and encoder and _vocab_matches(vocab, tokens))
        if hotwords and encoder and not self.biased:
            log.warning("No usable bpe.vocab in %s: Your words won't be used while recognising. Run "
                        "scripts/download_model.py parakeet to fetch it.", model_dir)
        if encoder:  # transducer (TDT) export: encoder + decoder + joiner
            options = dict(decoding_method="modified_beam_search", max_active_paths=BEAM, modeling_unit="bpe",
                           bpe_vocab=str(vocab), hotwords_score=HOTWORD_SCORE) if self.biased else {}
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(encoder),
                decoder=str(_find(model_dir, "decoder")),
                joiner=str(_find(model_dir, "joiner")),
                tokens=str(tokens),
                model_type="nemo_transducer",
                num_threads=num_threads,
                **options,
            )
        else:  # CTC export: a single model file
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
                model=str(_find(model_dir, "model")),
                tokens=str(tokens),
                num_threads=num_threads,
            )

    @property
    def signature(self) -> str:
        """What the text depends on (sst.evaluate caches by it): model, decoding, the words, the audio's preparation."""
        decoding = "greedy"
        if self.biased:
            words = hashlib.sha1(hotword_text(self.words).encode("utf-8")).hexdigest()[:10]
            decoding = f"beam{BEAM}|hotwords{HOTWORD_SCORE}:{words}|guard"
        return f"parakeet|{self._model}|{decoding}" + ("|peak-1|retry" if self.conditioned else "")

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        with self._lock:
            return self._transcribe(audio, sample_rate)

    def _transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        if self.conditioned:
            audio = condition(audio)
        texts = (self._decode(piece, sample_rate) for piece in split_at_pauses(audio, sample_rate, MAX_PIECE_SECONDS))
        text = " ".join(text for text in texts if text)
        if not text and self.conditioned and len(audio) > sample_rate and float(np.abs(audio).max()) > 0.05:
            # Audible speech that decoded to nothing (seen on the owner's reading test): each half on its own worked.
            half = len(audio) // 2
            text = " ".join(t for t in (self._decode(audio[:half], sample_rate), self._decode(audio[half:], sample_rate)) if t)
        return text

    def _decode(self, audio: np.ndarray, sample_rate: int) -> str:
        words = list(self.words) if self.biased else []  # read once: the app may change them meanwhile
        text = self._run(audio, sample_rate, hotword_text(words))
        if words and runaway(text, words):
            log.warning("Hotwords ran away (%r); decoding again without them", text)
            text = self._run(audio, sample_rate, "")
        return text

    def _run(self, audio: np.ndarray, sample_rate: int, hotwords: str) -> str:
        stream = self._recognizer.create_stream(hotwords=hotwords) if hotwords else self._recognizer.create_stream()
        stream.accept_waveform(sample_rate, audio)
        self._recognizer.decode_stream(stream)
        return stream.result.text.strip()
