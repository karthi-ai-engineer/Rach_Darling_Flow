"""Shared test setup: no test sees the speech models on the machine running it."""
import pytest

from sst import downloads
from sst.engines import parakeet


@pytest.fixture(autouse=True)
def no_downloaded_models(monkeypatch, tmp_path_factory):
    """An empty download folder for every test, so results don't depend on what this laptop has downloaded."""
    monkeypatch.setattr(downloads, "DOWNLOADS_DIR", tmp_path_factory.mktemp("downloads"))


@pytest.fixture(autouse=True)
def parakeet_on_disk(monkeypatch, tmp_path_factory):
    """Parakeet counts as being next to the program (as after an update from Rflow 1.4), whether or not this machine
    has the real model: no test loads it."""
    folder = tmp_path_factory.mktemp("parakeet")
    (folder / "encoder.int8.onnx").write_bytes(b"")
    monkeypatch.setattr(parakeet, "MODEL_DIR", folder)


@pytest.fixture
def no_parakeet(monkeypatch, tmp_path_factory):
    """A new install: Parakeet isn't downloaded."""
    monkeypatch.setattr(parakeet, "MODEL_DIR", tmp_path_factory.mktemp("no-parakeet"))


@pytest.fixture
def whisper_downloaded(monkeypatch):
    """Whisper turbo counts as downloaded (its real files are 1.6 GB)."""
    monkeypatch.setattr(downloads.Download, "installed", lambda self, root=None: True)
