"""Shared test setup: no test sees the speech models downloaded on the machine running it."""
import pytest

from sst import downloads


@pytest.fixture(autouse=True)
def no_downloaded_models(monkeypatch, tmp_path_factory):
    """An empty download folder for every test, so results don't depend on what this laptop has downloaded."""
    monkeypatch.setattr(downloads, "DOWNLOADS_DIR", tmp_path_factory.mktemp("downloads"))


@pytest.fixture
def whisper_downloaded(monkeypatch):
    """Whisper turbo counts as downloaded (its real files are 1.6 GB)."""
    monkeypatch.setattr(downloads.Download, "installed", lambda self, root=None: True)
