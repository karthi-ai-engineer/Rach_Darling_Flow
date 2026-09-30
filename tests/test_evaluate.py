"""Offline replay: setups scored on the same recordings, terms apart, 95% ranges, caching, degradations (fakes only)."""
import json

import numpy as np
import pytest

from sst import bench, evaluate
from sst.audio import save_wav
from sst.evaluate import Pipeline, degrade

RATE = 16_000


def _speech(seconds=1.0, seed=0):
    """Noise standing in for speech: loud, full band."""
    return np.random.default_rng(seed).uniform(-0.3, 0.3, int(seconds * RATE)).astype(np.float32)


def _session(root, name, block, sentences, microphone="Laptop mic"):
    folder = root / name
    bench.write_session(folder, block, microphone, {"device": microphone, "rate": RATE})
    for k, sentence in enumerate(sentences, 1):
        save_wav(folder / f"{k:02}.wav", _speech(seed=k + len(name)), RATE)
        (folder / f"{k:02}.txt").write_text(sentence, encoding="utf-8")
    return folder


class FakeEngine:
    """Answers by sentence number: the WAV files differ, so the heard text is looked up by the order of the calls."""

    signature = "fake|v1"

    def __init__(self, heard):
        self.heard, self.calls = list(heard), 0

    def transcribe(self, audio, rate):
        self.calls += 1
        return self.heard.pop(0)


class FakePolisher:
    def __init__(self, fixes):
        self.fixes = fixes

    def polish(self, text):
        for wrong, right in self.fixes.items():
            text = text.replace(wrong, right)
        return text


def test_every_setup_is_scored_on_the_same_recordings_with_terms_apart(tmp_path):
    folder = _session(tmp_path, "2026-10-01_090000", "A", ["I would like to learn Tamil and Japanese.",
                                                          "Please create a merge commit in CodeQL."])
    engine = FakeEngine(["i would like to learn tamar and japanese", "please create a merge clot in code ql"])
    pipelines = evaluate.pipelines_for({"model-a": FakePolisher({"tamar": "Tamil", "clot": "commit"})})
    results = evaluate.run([folder], engine, pipelines)

    alone, cleaned = results.scores
    assert alone.name == "Parakeet alone" and cleaned.name == "Parakeet + model-a"
    assert (sum(alone.wrong), sum(alone.total)) == (4, 15)
    assert (sum(cleaned.wrong), sum(cleaned.total)) == (2, 15)  # Tamil and commit fixed, not CodeQL
    # Terms: Tamil, Japanese, commit, CodeQL. Alone gets 3 of 4 wrong; the other 11 words have 1 extra ("ql").
    assert (sum(alone.term_wrong), sum(alone.term_total)) == (3, 4) and alone.other_error_rate == pytest.approx(1 / 11)
    assert cleaned.term_error_rate == pytest.approx(1 / 4)
    assert ("tamil", "tamar", 1) in results.misheard
    assert results.suggestions[:1] == ["Tamil"] and "CodeQL" in results.suggestions


def test_a_term_put_where_it_was_not_said_is_counted(tmp_path):
    folder = _session(tmp_path, "2026-10-01_090000", "C", ["We keep the backups in the cloud."])
    results = evaluate.run([folder], FakeEngine(["we keep the backups in the cloud"]),
                           evaluate.pipelines_for({"eager": FakePolisher({"cloud": "Claude"})}))
    alone, eager = results.scores
    assert sum(alone.term_inserted) == 0 and sum(eager.term_inserted) == 1 and sum(eager.wrong) == 1


def test_suggestions_come_from_the_tuning_sets_only(tmp_path):
    tuning = _session(tmp_path, "2026-10-01_090000", "B", ["Tamil is spoken in India."])
    test = _session(tmp_path, "2026-10-01_100000", "C", ["Rahul merged the fix."])
    results = evaluate.run([tuning, test], FakeEngine(["tamar is spoken in india", "raul merged the fix"]),
                           [Pipeline("Parakeet alone")])
    assert results.suggestions == ["Tamil"]  # "Rahul" was misheard too, but in the held-out test
    assert {("tamil", "tamar", 1), ("rahul", "raul", 1)} <= set(results.misheard)
    assert "## Tuning sets and test sets" in results.report()


def test_a_clear_improvement_is_called_better_and_noise_is_not(tmp_path):
    sentences = [f"Please send the report number {k} to the team today." for k in range(40)]
    folder = _session(tmp_path, "2026-10-01_090000", "C", sentences)
    heard = [s.lower().replace("report", "rapport").rstrip(".") for s in sentences]  # 1 wrong word in every sentence
    engine = FakeEngine(heard * 3)
    pipelines = [Pipeline("Parakeet alone"),
                 Pipeline("fixed", polisher=FakePolisher({"rapport": "report"})),
                 Pipeline("same", polisher=FakePolisher({}))]
    results = evaluate.run([folder], engine, pipelines, cache=False)
    alone, fixed, same = results.scores
    low, high = alone.interval
    assert low <= alone.error_rate <= high
    assert fixed.error_rate == 0 and fixed.difference[2] < 0 and "better" in results.report()
    assert same.difference == (0.0, 0.0, 0.0) and "no clear difference" in results.report()


def test_transcriptions_are_cached_by_engine_audio_and_degradation(tmp_path):
    folder = _session(tmp_path, "2026-10-01_090000", "A", ["One sentence."])
    engine = FakeEngine(["one sentence", "one sentence", "one sentence"])
    evaluate.run([folder], engine, [Pipeline("Parakeet alone")])
    evaluate.run([folder], engine, [Pipeline("Parakeet alone")])
    assert engine.calls == 1  # the second run used the cache
    evaluate.run([folder], engine, [Pipeline("Parakeet alone"), Pipeline("narrow", degrade="narrowband")])
    assert engine.calls == 2  # the degraded audio is new
    engine.signature = "fake|v2"  # e.g. another decoding method
    evaluate.run([folder], engine, [Pipeline("Parakeet alone")])
    assert engine.calls == 3
    assert len(json.loads((folder / evaluate.CACHE_FILE).read_text(encoding="utf-8"))) == 3


def test_narrowband_keeps_only_the_telephone_band():
    t = np.arange(RATE) / RATE
    audio = (np.sin(2 * np.pi * 1000 * t) + np.sin(2 * np.pi * 6000 * t)).astype(np.float32) * 0.4
    narrow = degrade(audio, RATE, "narrowband")
    spectrum = np.abs(np.fft.rfft(narrow))
    assert spectrum[6000] < 1e-3 * spectrum[1000]  # 1 Hz bins: 6 kHz gone, 1 kHz kept


def test_gain_clips_and_rounds_like_a_real_recording():
    louder = degrade(np.array([0.1, -0.5, 0.9], dtype=np.float32), RATE, "gain:12")
    assert louder.max() < 1.0 and louder.min() == -1.0
    quieter = degrade(np.array([0.00002], dtype=np.float32), RATE, "gain:-20")
    assert quieter[0] == 0.0  # below one 16-bit step: lost, as it would be


@pytest.mark.parametrize("spec", ["telephone", "gain:loud", "narrowband:2"])
def test_an_unknown_degradation_is_reported_before_any_work(spec):
    with pytest.raises(ValueError):
        evaluate.pipelines_for({}, [spec])


def test_the_report_has_every_setup_microphone_and_warning(tmp_path):
    laptop = _session(tmp_path, "2026-10-01_090000", "A", ["Push the branch."], microphone="Laptop mic")
    earbuds = tmp_path / "2026-10-01_100000"
    bench.write_session(earbuds, "A", "Earbuds", {"device": "Headset (Earbuds)", "rate": RATE})
    save_wav(earbuds / "01.wav", degrade(_speech(), RATE, "narrowband"), RATE)
    (earbuds / "01.txt").write_text("Push the branch.", encoding="utf-8")
    results = evaluate.run([laptop, earbuds], FakeEngine(["push the branch", "push the brand"]), [Pipeline("Parakeet alone")])
    report = results.report()
    assert "2 recordings from 2 tests" in report and "| Laptop mic | 1 |" in report
    assert "| Headset (Earbuds) | 1 |" in report and "narrowband ×1" in report
    out = evaluate.output_folder([laptop, earbuds])
    results.save(out)
    saved = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert out.name == "summary" and saved["scores"][0]["error_rate"] == pytest.approx(1 / 6)
    assert saved["recordings"][1]["stats"]["high_band_db"] < -45
