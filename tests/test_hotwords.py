"""Hotwords: the user's words given to the recogniser, the guard against boosting that runs away, and fetching the
model's word-piece vocabulary out of NVIDIA's archive. No model and no network: the recogniser and HTTP are fakes."""
import importlib.util
import io
import tarfile
from pathlib import Path

import numpy as np

from sst.engines import parakeet
from sst.engines.parakeet import ParakeetEngine, hotword_text, runaway


def test_hotwords_are_phrases_separated_by_slashes():
    assert hotword_text(["Karthi", " sherpa-onnx ", "Q3  roadmap", "a/b", "", "Karthi"]) == "Karthi/sherpa-onnx/Q3 roadmap/a b"


def test_a_repeated_boosted_word_is_boosting_run_away():
    words = ["Claude", "Bluetooth"]
    assert runaway("Claude Claude Claude storage will went up", words)
    assert runaway("The Bluetooth bluetooth showed no signs", words)
    assert not runaway("Ask Claude whether the cloud version is faster", words)
    assert not runaway("that that is fine", words)  # repeating an ordinary word is how people talk


def test_the_vocabulary_must_be_the_model_s_own_word_pieces(tmp_path):
    (tmp_path / "tokens.txt").write_text("<unk> 0\n▁t 1\n▁th 2\n<blk> 3\n", encoding="utf-8")
    (tmp_path / "bpe.vocab").write_text("<unk>\t0\n▁t\t-0\n▁th\t-1\n", encoding="utf-8")
    assert parakeet._vocab_matches(tmp_path / "bpe.vocab", tmp_path / "tokens.txt")
    (tmp_path / "bpe.vocab").write_text("<unk>\t0\n▁th\t-0\n▁t\t-1\n", encoding="utf-8")  # another order
    assert not parakeet._vocab_matches(tmp_path / "bpe.vocab", tmp_path / "tokens.txt")
    assert not parakeet._vocab_matches(tmp_path / "missing.vocab", tmp_path / "tokens.txt")


class FakeStream:
    def __init__(self, hotwords):
        self.hotwords, self.result = hotwords, None

    def accept_waveform(self, rate, audio):
        pass


class FakeRecognizer:
    """Answers "Claude Claude Claude" when given hotwords (boosting run away), and the plain text without."""

    def __init__(self):
        self.asked = []

    def create_stream(self, hotwords=None):
        self.asked.append(hotwords)
        return FakeStream(hotwords)

    def decode_stream(self, stream):
        stream.result = type("Result", (), {"text": "Claude Claude Claude" if stream.hotwords else "the cloud"})()


def _engine(words, biased=True):
    engine = object.__new__(ParakeetEngine)
    engine.conditioned, engine.biased, engine.words, engine._model = False, biased, words, "model"
    engine._recognizer = FakeRecognizer()
    return engine


def test_a_run_away_result_is_decoded_again_without_the_words():
    engine = _engine(["Claude", "Vercel"])
    assert engine._decode(np.zeros(16000, dtype=np.float32), 16000) == "the cloud"
    assert engine._recognizer.asked == ["Claude/Vercel", None]


def test_without_the_vocabulary_no_words_are_given():
    engine = _engine(["Claude"], biased=False)
    assert engine._decode(np.zeros(16000, dtype=np.float32), 16000) == "the cloud"
    assert engine._recognizer.asked == [None]


def test_the_cache_signature_changes_with_the_words_and_the_decoding():
    engine = _engine(["Karthi"])
    first = engine.signature
    engine.words = ["Karthi", "Vercel"]
    assert engine.signature != first and "beam" in engine.signature
    assert "greedy" in _engine(["Karthi"], biased=False).signature


# ---- scripts/download_model.py: the vocabulary out of the .nemo archive

def _download_script():
    path = Path(__file__).resolve().parent.parent / "scripts" / "download_model.py"
    spec = importlib.util.spec_from_file_location("download_model", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _nemo(vocab: str) -> bytes:
    """A small .nemo-like tar in the PAX format NeMo writes, with long hashed member names."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, data in [("./model_config.yaml", b"x: 1\n"),
                           ("./aa68b93b03344274b0c0e2a96333de24_" + "x" * 80 + "_tokenizer.vocab", vocab.encode()),
                           ("./model_weights.ckpt", b"\0" * 5000)]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def test_only_the_tokenizer_vocabulary_is_read_out_of_the_archive(tmp_path, monkeypatch):
    script = _download_script()
    archive = _nemo("<unk>\t0\n▁t\t-0\n")
    read = []

    def ranged(url, start, size):
        read.append(size)
        return archive[start:start + size]
    monkeypatch.setattr(script, "_range", ranged)
    (tmp_path / "tokens.txt").write_text("<unk> 0\n▁t 1\n<blk> 2\n", encoding="utf-8")
    script.fetch_vocab("https://example/model.nemo", tmp_path)
    assert (tmp_path / "bpe.vocab").read_text(encoding="utf-8") == "<unk>\t0\n▁t\t-0\n"
    assert 5000 not in read  # the weights were skipped, not downloaded


def test_a_vocabulary_that_does_not_match_the_model_is_refused(tmp_path, monkeypatch):
    script = _download_script()
    archive = _nemo("<unk>\t0\n▁x\t-0\n")
    monkeypatch.setattr(script, "_range", lambda url, start, size: archive[start:start + size])
    (tmp_path / "tokens.txt").write_text("<unk> 0\n▁t 1\n", encoding="utf-8")
    try:
        script.fetch_vocab("https://example/model.nemo", tmp_path)
    except SystemExit:
        pass
    assert not (tmp_path / "bpe.vocab").exists()
