"""The dictation loop's decisions: hold, tap, cancel, interrupt, auto-stop. The keyboard hook, microphone,
engine and paste are replaced by fakes, so no keys are pressed, the clipboard is untouched and no model
is needed."""
import queue
import time
from types import SimpleNamespace

import numpy as np
import pytest

from sst import dictate

TAP = 0.05  # released this soon after the press = a tap


class EndOfScript(Exception):
    pass


class ScriptedEvents:
    """Stands in for HotkeyListener.events: delivers (event, time) at the scripted moments."""

    def __init__(self, script: list[tuple[float, str]], end_at: float):
        self.t0 = time.monotonic()
        self.script, self.end_at = sorted(script), end_at

    def get(self, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            elapsed = time.monotonic() - self.t0
            if elapsed > self.end_at:
                raise EndOfScript
            if self.script and elapsed >= self.script[0][0]:
                at, event = self.script.pop(0)
                return event, self.t0 + at
            time.sleep(0.005)
        raise queue.Empty  # like queue.Queue.get: the loop uses these quiet moments to check the time limit


@pytest.fixture
def dictation(monkeypatch):
    """Run the loop against a script of hotkey events; returns what was typed and how often recording started."""
    def run(script, end_at, recorded_seconds=1.0, recognised="hello world"):
        typed, starts = [], []

        class FakeRecorder:
            def __init__(self, device=None):
                self.rate = 16_000

            def start(self):
                starts.append(time.monotonic())

            def stop(self):
                return np.full(int(recorded_seconds * self.rate), 0.1, dtype=np.float32)

        class FakeEngine:
            name = "fake"

            def transcribe(self, audio, rate):
                return recognised

        class FakeListener:
            def __init__(self, hotkey):
                self.hotkey = hotkey
                self.recording = False
                self.events = ScriptedEvents(script, end_at)

            def start(self):
                pass

            def stop(self):
                pass

        monkeypatch.setattr(dictate, "Recorder", FakeRecorder)
        monkeypatch.setattr(dictate, "HotkeyListener", FakeListener)
        monkeypatch.setattr(dictate, "_already_running", lambda: False)
        monkeypatch.setattr(dictate, "_wispr_flow_running", lambda: False)
        monkeypatch.setattr(dictate, "paste_text", typed.append)
        monkeypatch.setattr(dictate, "_beep", lambda frequency: None)
        with pytest.raises(EndOfScript):
            dictate.run(FakeEngine, save=False)
        time.sleep(0.5)  # the worker thread transcribes and pastes in the background
        return SimpleNamespace(typed=typed, starts=len(starts))

    return run


def test_hold_then_release_types_the_text(dictation):
    assert dictation([(0, "press"), (0.7, "release")], end_at=1.2).typed == ["hello world "]


def test_tap_then_tap_types_the_text(dictation):
    script = [(0, "press"), (TAP, "release"), (0.8, "press"), (0.85, "release")]
    assert dictation(script, end_at=1.2).typed == ["hello world "]


def test_a_tap_keeps_recording_until_the_next_press(dictation):
    assert dictation([(0, "press"), (TAP, "release")], end_at=1.0).typed == []


def test_esc_cancels_without_typing(dictation):
    assert dictation([(0, "press"), (TAP, "release"), (0.3, "cancel")], end_at=0.8).typed == []


def test_a_windows_shortcut_drops_the_recording(dictation):
    # Ctrl+Win+D: the hook reports "interrupt"; nothing may be typed, and the next hold works normally.
    script = [(0, "press"), (0.2, "interrupt"), (0.5, "press"), (1.2, "release")]
    assert dictation(script, end_at=1.6).typed == ["hello world "]


def test_recording_stops_by_itself_at_the_limit(dictation, monkeypatch):
    monkeypatch.setattr(dictate, "MAX_SECONDS", 0.3)
    assert dictation([(0, "press"), (TAP, "release")], end_at=0.8).typed == ["hello world "]


def test_accidental_short_press_is_ignored(dictation):
    assert dictation([(0, "press"), (0.5, "release")], end_at=0.9, recorded_seconds=0.1).typed == []


def test_nothing_is_typed_when_nothing_is_recognised(dictation):
    assert dictation([(0, "press"), (0.5, "release")], end_at=0.9, recognised="").typed == []


def test_ctrl_win_space_records_hands_free_until_the_next_press(dictation):
    # The Copilot key remapped to Ctrl+Win+Space: "press" and "handsfree" arrive together, then the keys go up.
    result = dictation([(0, "press"), (0.01, "handsfree"), (0.8, "press"), (0.85, "release")], end_at=1.2)
    assert result.typed == ["hello world "] and result.starts == 1


def test_ctrl_win_space_again_stops_without_starting_a_new_recording(dictation):
    result = dictation([(0, "press"), (0.01, "handsfree"), (0.8, "press"), (0.81, "handsfree")], end_at=1.4)
    assert result.typed == ["hello world "] and result.starts == 1


def test_space_while_holding_keeps_recording_after_the_keys_are_released(dictation):
    result = dictation([(0, "press"), (0.3, "handsfree"), (1.0, "press"), (1.05, "release")], end_at=1.4)
    assert result.typed == ["hello world "] and result.starts == 1
