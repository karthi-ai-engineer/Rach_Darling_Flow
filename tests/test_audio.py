import numpy as np

from sst.audio import load_wav, save_wav, split_at_pauses

RATE = 16_000


def _speech_with_pauses(seconds: float, pauses_at: list[float]) -> np.ndarray:
    """Noise standing in for speech, with a 0.3 s silence starting at each of `pauses_at`."""
    audio = np.random.default_rng(0).uniform(-0.5, 0.5, int(seconds * RATE)).astype(np.float32)
    for start in pauses_at:
        audio[int(start * RATE):int((start + 0.3) * RATE)] = 0
    return audio


def test_short_audio_is_one_piece():
    audio = _speech_with_pauses(12, [])
    pieces = split_at_pauses(audio, RATE, max_seconds=30)
    assert len(pieces) == 1 and pieces[0] is audio


def test_long_audio_is_cut_inside_the_pauses():
    audio = _speech_with_pauses(70, pauses_at=[27.0, 54.0])
    pieces = split_at_pauses(audio, RATE, max_seconds=30)

    cuts = np.cumsum([len(p) for p in pieces])[:-1] / RATE
    assert len(pieces) == 3
    assert 27.0 <= cuts[0] <= 27.3 and 54.0 <= cuts[1] <= 54.3
    assert all(len(p) <= 30 * RATE for p in pieces)
    np.testing.assert_array_equal(np.concatenate(pieces), audio)  # nothing lost or duplicated


def test_without_pauses_pieces_still_respect_the_limit():
    audio = _speech_with_pauses(95, [])
    pieces = split_at_pauses(audio, RATE, max_seconds=30)
    assert len(pieces) == 4
    assert all(len(p) <= 30 * RATE for p in pieces)
    assert sum(map(len, pieces)) == len(audio)


def test_wav_round_trip(tmp_path):
    audio = np.sin(np.linspace(0, 2000, RATE)).astype(np.float32) * 0.8
    save_wav(tmp_path / "a.wav", audio, RATE)
    loaded, rate = load_wav(tmp_path / "a.wav")
    assert rate == RATE
    np.testing.assert_allclose(loaded, audio, atol=1e-4)  # 16-bit samples: ~0.00003 per step


def test_measure_tells_a_clean_wideband_recording_from_problem_ones():
    from sst.audio import measure
    from sst.evaluate import degrade
    rng = np.random.default_rng(1)
    speech = np.concatenate([np.zeros(RATE // 2), rng.uniform(-0.3, 0.3, RATE)]).astype(np.float32)
    speech += rng.normal(0, 1e-4, len(speech)).astype(np.float32)  # a quiet room
    clean = measure(speech, RATE)
    assert clean.flags == [] and clean.snr_db > 40 and clean.seconds == 1.5
    assert "narrowband" in measure(degrade(speech, RATE, "narrowband"), RATE).flags
    assert "clipped" in measure(np.clip(speech * 10, -1, 1), RATE).flags
    assert "quiet" in measure(speech * 0.001, RATE).flags
    assert "narrowband" in measure(speech[::2], 8000).flags  # recorded at 8 kHz: nothing above 4 kHz can exist
