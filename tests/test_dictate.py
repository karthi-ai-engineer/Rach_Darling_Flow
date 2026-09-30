"""The dictation logic: hold, tap, Ctrl+Win+Space, cancel, interrupt, auto-stop. The microphone, engine and
typing are fakes, and hotkey events are fed in with explicit times, so the tests are exact and fast:
no keys are pressed, the clipboard is untouched and no model is needed."""
import numpy as np
import pytest

from sst import dictate
from sst.dictate import Dictation

TAP = 0.05  # released this soon after the press = a tap


class FakeRecorder:
    rate = 16_000

    def __init__(self, seconds=1.0, fail=False):
        self.seconds, self.fail, self.starts = seconds, fail, 0

    def start(self):
        if self.fail:
            raise OSError("no microphone")
        self.starts += 1

    def stop(self):
        return np.full(int(self.seconds * self.rate), 0.1, dtype=np.float32)


class FakeEngine:
    name = "fake"

    def __init__(self, text="hello world"):
        self.text = text

    def transcribe(self, audio, rate):
        return self.text


class FakeListener:
    recording = False


@pytest.fixture
def make():
    def make(text="hello world", seconds=1.0, fail=False):
        typed, states = [], []
        d = Dictation(FakeEngine(text), FakeRecorder(seconds, fail), paste=typed.append, sounds=False, save=False)
        d.listener = FakeListener()
        d.on_state = lambda state, message: states.append(state)
        return d, typed, states
    return make


def feed(d, *events):
    for at, event in events:
        d.handle(event, at)
    d.wait()


def test_hold_then_release_types_the_text(make):
    d, typed, states = make()
    feed(d, (0, "press"), (0.7, "release"))
    assert typed == ["hello world "] and states == ["recording", "transcribing", "typed"]


def test_tap_then_tap_types_the_text(make):
    d, typed, _ = make()
    feed(d, (0, "press"), (TAP, "release"), (5.0, "press"), (5.05, "release"))
    assert typed == ["hello world "]


def test_a_tap_keeps_recording_until_the_next_press(make):
    d, typed, _ = make()
    feed(d, (0, "press"), (TAP, "release"))
    assert d.recording and typed == []


def test_esc_is_captured_only_while_recording(make):
    d, typed, states = make()
    feed(d, (0, "press"))
    assert d.listener.recording
    feed(d, (TAP, "release"), (0.3, "cancel"))
    assert not d.listener.recording and typed == [] and states[-1] == "cancelled"


def test_a_windows_shortcut_drops_the_recording_quietly(make):
    d, typed, states = make()
    feed(d, (0, "press"), (0.2, "interrupt"))
    assert typed == [] and states == ["recording", "idle"]
    feed(d, (0.5, "press"), (1.2, "release"))  # the next hold works normally
    assert typed == ["hello world "]


def test_ctrl_win_space_records_hands_free_until_the_next_press(make):
    d, typed, _ = make()
    feed(d, (0, "press"), (0.01, "handsfree"))
    assert d.recording  # the keys were let go, but this is hands-free
    feed(d, (4.0, "press"), (4.05, "release"))
    assert typed == ["hello world "] and d.recorder.starts == 1


def test_ctrl_win_space_again_stops_without_starting_a_new_recording(make):
    d, typed, _ = make()
    feed(d, (0, "press"), (0.01, "handsfree"), (4.0, "press"), (4.01, "handsfree"))
    assert typed == ["hello world "] and not d.recording and d.recorder.starts == 1


def test_space_while_holding_keeps_recording_after_the_keys_are_released(make):
    d, typed, _ = make()
    feed(d, (0, "press"), (0.3, "handsfree"))
    assert d.recording
    feed(d, (3.0, "press"))
    assert typed == ["hello world "] and d.recorder.starts == 1


def test_recording_stops_by_itself_at_the_limit(make):
    d, typed, states = make()
    feed(d, (0, "press"), (TAP, "release"))
    d.tick(dictate.MAX_SECONDS - 1)
    assert d.recording
    d.tick(dictate.MAX_SECONDS + 1)
    d.wait()
    assert typed == ["hello world "] and "warning" in states


def test_accidental_short_press_is_ignored(make):
    d, typed, states = make(seconds=0.1)
    feed(d, (0, "press"), (0.5, "release"))
    assert typed == [] and states[-1] == "ignored"


def test_nothing_is_typed_when_nothing_is_recognised(make):
    d, typed, states = make(text="")
    feed(d, (0, "press"), (0.5, "release"))
    assert typed == [] and states[-1] == "idle"


def test_a_missing_microphone_is_reported_not_raised(make):
    d, typed, states = make(fail=True)
    feed(d, (0, "press"))
    assert not d.recording and states == ["error"]


def test_a_failing_paste_is_reported_and_the_next_dictation_still_works(make):
    d, typed, states = make()
    calls = []

    def flaky_paste(text):
        calls.append(text)
        if len(calls) == 1:
            raise OSError("the clipboard is busy")
        typed.append(text)

    d.paste = flaky_paste
    feed(d, (0, "press"), (0.7, "release"))
    assert states[-1] == "error"
    feed(d, (2, "press"), (2.7, "release"))
    assert typed == ["hello world "]


class FakeCleanup:
    def __init__(self, result="Hello, world.", error=""):
        self.result, self.error, self.prepared, self.last_error = result, error, 0, ""

    def prepare(self):
        self.prepared += 1

    def polish(self, text):
        self.last_error = self.error
        return text if self.error else self.result


def test_the_cleaned_text_is_typed_and_both_texts_are_reported(make):
    d, typed, states = make()
    results = []
    d.cleanup = FakeCleanup()
    d.on_result = lambda heard, text, seconds: results.append((heard, text, seconds))
    feed(d, (0, "press"), (0.7, "release"))
    assert typed == ["Hello, world. "] and states[-1] == "typed"
    assert results == [("hello world", "Hello, world.", 1.0)]  # with the recording's length, for the speaking speed
    assert d.cleanup.prepared == 1  # the gateway connection was opened while the user was speaking


def test_when_the_cleanup_cannot_help_the_heard_text_is_typed(make):
    d, typed, states = make()
    d.cleanup = FakeCleanup(error="the gateway could not be reached")
    feed(d, (0, "press"), (0.7, "release"))
    assert typed == ["hello world "] and states[-1] == "typed_raw"
