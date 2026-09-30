import importlib
import sys

import numpy as np

import sst
from sst import audio


def test_installed_app_uses_its_own_folder_and_localappdata(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Rflow" / "Rflow.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    try:
        importlib.reload(sst)
        assert sst.MODELS_DIR == tmp_path / "Rflow" / "models"
        assert sst.RECORDINGS_DIR == tmp_path / "Local" / "sst" / "recordings"
    finally:
        monkeypatch.undo()
        importlib.reload(sst)


def test_save_recording_creates_missing_folders(monkeypatch, tmp_path):
    # On a fresh laptop %LOCALAPPDATA%\sst does not exist yet, so its parents must be created too.
    folder = tmp_path / "Local" / "sst" / "recordings"
    monkeypatch.setattr(audio, "RECORDINGS_DIR", folder)
    stem = audio.save_recording(np.zeros(16_000, dtype=np.float32), 16_000, "hello")
    assert (folder / f"{stem}.txt").read_text(encoding="utf-8") == "hello\n"
    assert (folder / f"{stem}.wav").exists()
