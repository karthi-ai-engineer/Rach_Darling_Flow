"""Capture: the warm microphone's lead-in, the tail after the key release, closing when idle, Bluetooth never kept open,
old microphone names, and the audio's preparation for the engine. No microphone: the audio callback is fed by hand."""
import numpy as np
import pytest

from sst import audio
from sst.audio import PREROLL_SECONDS, Recorder, Take, condition

RATE = 16_000
BLOCK = 160  # 10 ms


class FakeStream:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _recorder(monkeypatch, call_quality=False, **options) -> Recorder:
    monkeypatch.setattr(audio, "_open_streams", 0)  # put back after the test: warm fakes stay "open"
    r = Recorder(**options)
    opened = []

    def fake_open():
        r.rate, r.info = RATE, {"call_quality": call_quality, "mode": "raw" if r.raw else "windows"}
        r._stream, r._opened_for = FakeStream(), (r.device, r.raw)
        audio._open_streams += 1
        opened.append(r._stream)
    monkeypatch.setattr(r, "_open", fake_open)
    r.opened = opened
    return r


def _feed(r: Recorder, seconds: float, value: float = 0.1) -> None:
    for _ in range(int(seconds * RATE / BLOCK)):
        r._on_audio(np.full((BLOCK, 1), value, dtype=np.float32), BLOCK, None, None)


def test_a_cold_start_has_no_lead_in_and_closes_after_the_recording(monkeypatch):
    r = _recorder(monkeypatch)
    r.start()
    _feed(r, 1.0)
    out = r.stop()
    assert len(out) == RATE and not r.warm and r.opened[0].closed


def test_a_warm_microphone_keeps_the_moment_before_the_key_press(monkeypatch):
    r = _recorder(monkeypatch, warm_seconds=300)
    r.start()
    _feed(r, 0.5)
    r.stop()
    assert r.warm  # kept open
    _feed(r, 2.0, value=0.2)  # waiting: only the last PREROLL_SECONDS are kept
    r.start()
    _feed(r, 1.0, value=0.3)
    take = r.stop_later()
    out = take.audio()
    assert len(r.opened) == 1  # no second open: instant start
    assert len(out) == pytest.approx((PREROLL_SECONDS + 1.0) * RATE, abs=BLOCK)
    assert np.all(out[:BLOCK] == np.float32(0.2)) and take.seconds == pytest.approx(1.0)  # the lead-in isn't counted


def test_the_tail_after_the_release_is_recorded_without_waiting_for_it(monkeypatch):
    r = _recorder(monkeypatch, warm_seconds=300, tail=0.3)
    r.start()
    _feed(r, 1.0)
    take = r.stop_later()
    assert not take.done.is_set()  # returned at once; the tail comes in on the audio thread
    _feed(r, 0.5, value=0.5)
    assert take.done.is_set()
    out = take.audio()
    assert len(out) == int(1.3 * RATE) and np.all(out[-BLOCK:] == np.float32(0.5))


def test_an_idle_warm_microphone_closes_after_its_time(monkeypatch):
    r = _recorder(monkeypatch, warm_seconds=300)
    r.start()
    _feed(r, 0.5)
    r.stop()
    r.tick(r._idle_since + 299)
    assert r.warm
    r.tick(r._idle_since + 301)
    assert not r.warm and r.opened[0].closed


def test_a_bluetooth_microphone_is_never_kept_open(monkeypatch):
    r = _recorder(monkeypatch, call_quality=True, warm_seconds=300)
    r.start()
    _feed(r, 0.5)
    r.stop()
    assert not r.warm  # it would keep the headset playing in call quality


def test_choosing_another_microphone_or_mode_reopens(monkeypatch):
    r = _recorder(monkeypatch, warm_seconds=300)
    r.start()
    r.stop()
    r.raw = True
    r.start()
    assert len(r.opened) == 2 and r.opened[0].closed and r.info["mode"] == "raw"


def test_closing_ends_a_tail_still_being_recorded(monkeypatch):
    r = _recorder(monkeypatch, warm_seconds=300, tail=0.3)
    r.start()
    _feed(r, 0.5)
    take = r.stop_later()
    r.close()
    assert take.done.is_set() and len(take.audio()) == RATE // 2


def test_a_finished_recording_counts_in_full():
    take = Take.ready(np.zeros(RATE, dtype=np.float32), RATE)
    assert take.seconds == 1.0 and take.done.is_set()


def test_old_mme_names_find_their_wasapi_microphone(monkeypatch):
    devices = [{"name": "Microphone Array (Intel® Smart ", "max_input_channels": 2, "hostapi": 0},
               {"name": "Microphone Array (Intel® Smart Sound Technology)", "max_input_channels": 4, "hostapi": 1},
               {"name": "Headset (Buds)", "max_input_channels": 1, "hostapi": 1}]
    monkeypatch.setattr(audio.sd, "query_devices", lambda *a, **k: devices)
    monkeypatch.setattr(audio.sd, "query_hostapis", lambda *a, **k: [{"name": "MME", "default_input_device": 0},
                                                                      {"name": "Windows WASAPI", "default_input_device": 1}])
    assert audio._resolve("Microphone Array (Intel® Smart ") == 1  # saved by an older Rflow (MME cuts names at 31)
    assert audio._resolve("Headset (Buds)") == 2 and audio._resolve("Unplugged mic") is None
    assert audio.input_device_names(refresh=False) == [d["name"] for d in devices[1:]]


def test_condition_removes_dc_and_raises_the_peak_to_minus_1_dbfs():
    t = np.arange(RATE) / RATE
    quiet = (0.02 * np.sin(2 * np.pi * 200 * t) + 0.01).astype(np.float32)  # speech around -37 dBFS, with an offset
    out = condition(quiet)
    assert abs(float(out.mean())) < 1e-4 and float(np.abs(out).max()) == pytest.approx(10 ** (-1 / 20), rel=1e-3)
    hiss = np.full(RATE, 1e-5, dtype=np.float32)
    assert float(np.abs(condition(hiss)).max()) < 1e-4  # near-silence isn't blown up into loud noise
    assert len(condition(np.zeros(0, dtype=np.float32))) == 0


def test_audible_audio_that_decodes_to_nothing_is_tried_again_in_halves():
    from sst.engines.parakeet import ParakeetEngine
    engine = object.__new__(ParakeetEngine)  # no model: the decoder is a fake
    engine.conditioned = True
    calls = []

    def decode(piece, rate):
        calls.append(len(piece))
        return "" if len(calls) == 1 else f"part {len(calls) - 1}"
    engine._decode = decode
    speech = np.random.default_rng(0).uniform(-0.2, 0.2, 4 * RATE).astype(np.float32)
    assert engine._transcribe(speech, RATE) == "part 1 part 2" and calls == [4 * RATE, 2 * RATE, 2 * RATE]
    calls.clear()
    assert engine._transcribe(np.zeros(4 * RATE, dtype=np.float32), RATE) == "" and len(calls) == 1  # silence: no retry
