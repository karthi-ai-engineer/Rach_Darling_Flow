"""The dictation loop's decisions: tap, hold, cancel, auto-stop. The hotkey, microphone, engine and
paste are replaced by fakes, so no keys are pressed, the clipboard is untouched and no model is needed."""
import time

import numpy as np
import pytest

from sst import dictate

TAP = 0.05  # key released this soon after the press = a tap


class EndOfScript(Exception):
    pass


class Script:
    """Delivers hotkey presses at given times and reports the hotkey held until `release_at`."""

    def __init__(self, presses: list[tuple[float, int]], release_at: float, end_at: float):
        self.t0 = time.monotonic()
        self.presses, self.release_at, self.end_at = sorted(presses), release_at, end_at

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def wait_for_hotkey(self, timeout: float) -> int | None:
        time.sleep(min(timeout, 0.01))
        if self.elapsed() > self.end_at:
            raise EndOfScript
        if self.presses and self.elapsed() >= self.presses[0][0]:
            return self.presses.pop(0)[1]
        return None

    def is_key_down(self, vk: int) -> bool:
        return self.elapsed() < self.release_at


@pytest.fixture
def dictation(monkeypatch):
    """Run the loop against a script; returns what would have been typed."""
    def run(presses, release_at, end_at, recorded_seconds=1.0, recognised="hello world"):
        typed = []

        class FakeRecorder:
            def __init__(self, device=None):
                self.rate = 16_000

            def start(self):
                pass

            def stop(self):
                return np.full(int(recorded_seconds * self.rate), 0.1, dtype=np.float32)

        class FakeEngine:
            name = "fake"

            def transcribe(self, audio, rate):
                return recognised

        script = Script(presses, release_at, end_at)
        monkeypatch.setattr(dictate, "Recorder", FakeRecorder)
        monkeypatch.setattr(dictate, "register", lambda *args: True)
        monkeypatch.setattr(dictate, "unregister", lambda *args: None)
        monkeypatch.setattr(dictate, "wait_for_hotkey", script.wait_for_hotkey)
        monkeypatch.setattr(dictate, "is_key_down", script.is_key_down)
        monkeypatch.setattr(dictate, "paste_text", typed.append)
        monkeypatch.setattr(dictate, "_beep", lambda frequency: None)
        with pytest.raises(EndOfScript):
            dictate.run(FakeEngine, save=False)
        time.sleep(0.5)  # the worker thread transcribes and pastes in the background
        return typed

    return run


def test_tap_then_tap_types_the_text(dictation):
    typed = dictation(presses=[(0, dictate.TOGGLE_ID), (0.8, dictate.TOGGLE_ID)], release_at=TAP, end_at=1.2)
    assert typed == ["hello world "]


def test_a_tap_keeps_recording_until_the_next_press(dictation):
    assert dictation(presses=[(0, dictate.TOGGLE_ID)], release_at=TAP, end_at=1.0) == []


def test_hold_then_release_types_the_text(dictation):
    typed = dictation(presses=[(0, dictate.TOGGLE_ID)], release_at=0.7, end_at=1.2)
    assert typed == ["hello world "]


def test_esc_cancels_without_typing(dictation):
    typed = dictation(presses=[(0, dictate.TOGGLE_ID), (0.3, dictate.CANCEL_ID)], release_at=TAP, end_at=0.8)
    assert typed == []


def test_recording_stops_by_itself_at_the_limit(dictation, monkeypatch):
    monkeypatch.setattr(dictate, "MAX_SECONDS", 0.3)
    assert dictation(presses=[(0, dictate.TOGGLE_ID)], release_at=TAP, end_at=0.8) == ["hello world "]


def test_accidental_short_press_is_ignored(dictation):
    typed = dictation(presses=[(0, dictate.TOGGLE_ID), (0.5, dictate.TOGGLE_ID)], release_at=TAP, end_at=0.9,
                      recorded_seconds=0.1)
    assert typed == []


def test_nothing_is_typed_when_nothing_is_recognised(dictation):
    typed = dictation(presses=[(0, dictate.TOGGLE_ID), (0.5, dictate.TOGGLE_ID)], release_at=TAP, end_at=0.9,
                      recognised="")
    assert typed == []
