"""Microphone recording and WAV read/write."""
import wave
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

import numpy as np
import sounddevice as sd

from sst import RECORDINGS_DIR

TARGET_RATE = 16_000  # what speech models expect; other rates are resampled by the engine


def _pick_rate(device: int | None) -> int:
    try:
        sd.check_input_settings(device=device, samplerate=TARGET_RATE, channels=1, dtype="float32")
        return TARGET_RATE
    except Exception:
        return int(sd.query_devices(device, "input")["default_samplerate"])


def _default_host_api() -> int:
    return sd.query_devices(kind="input")["hostapi"]


_open_streams = 0  # recordings and level meters running right now


def _refresh_devices() -> None:
    # PortAudio reads the device list only once; re-reading it (~45 ms) shows a headset plugged in since, or a new
    # Windows default. It restarts PortAudio, which would pull the rug from under any open stream, so not while one
    # is open (e.g. the settings page's level meter while a dictation starts).
    if not _open_streams:
        sd._terminate()
        sd._initialize()


def input_device_names(refresh: bool = True) -> list[str]:
    """Microphones as Windows lists them (the default host API), for the settings window."""
    if refresh:
        _refresh_devices()
    api = _default_host_api()
    return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0 and d["hostapi"] == api
            and "Sound Mapper" not in d["name"]]


def _resolve(device: int | str | None) -> int | None:
    """A device number, or a microphone name (stable across restarts); None or an unplugged name = Windows default."""
    if not isinstance(device, str):
        return device
    api = _default_host_api()
    return next((i for i, d in enumerate(sd.query_devices())
                 if d["name"] == device and d["max_input_channels"] > 0 and d["hostapi"] == api), None)


class Recorder:
    """Records mono audio in the background between start() and stop()."""

    def __init__(self, device: int | str | None = None):
        self.device = device  # number, microphone name, or None for the Windows default
        self.rate = TARGET_RATE
        self.level = 0.0  # loudness of the latest block (RMS), for the recording indicator
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None

    def start(self) -> None:
        global _open_streams
        if self._stream is not None:
            self.stop()
        _refresh_devices()  # a long-running app must follow the microphones plugged in since the last recording
        device = _resolve(self.device)
        self.rate = _pick_rate(device)
        self._chunks, self.level = [], 0.0
        stream = sd.InputStream(samplerate=self.rate, channels=1, dtype="float32", device=device, callback=self._on_audio)
        stream.start()
        self._stream = stream
        _open_streams += 1

    def _on_audio(self, indata, frames, time, status):
        block = indata[:, 0].copy()
        self._chunks.append(block)
        self.level = float(np.sqrt(np.mean(block * block)))

    def stop(self) -> np.ndarray:
        """Stop recording and return the samples, in [-1, 1] at self.rate."""
        global _open_streams
        if self._stream is not None:
            self._stream.close()
            self._stream = None
            _open_streams -= 1
        self.level = 0.0
        chunks, self._chunks = self._chunks, []
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


class LevelMeter(Recorder):
    """Only the loudness, for a level bar in the window (e.g. while choosing a microphone); no audio is kept."""

    def _on_audio(self, indata, frames, time, status):
        block = indata[:, 0]
        self.level = float(np.sqrt(np.mean(block * block)))


def record_until_enter(device: int | None = None) -> tuple[np.ndarray, int]:
    """Record mono audio until the user presses Enter. Returns (samples in [-1, 1], sample_rate)."""
    recorder = Recorder(device)
    recorder.start()
    try:
        input()
    finally:
        audio = recorder.stop()
    return audio, recorder.rate


def split_at_pauses(audio: np.ndarray, rate: int, max_seconds: float, search_seconds: float = 5.0) -> list[np.ndarray]:
    """Cut audio into pieces of at most max_seconds. Each cut is placed at the quietest 100 ms
    within the last search_seconds before the limit, which is normally a pause between words."""
    max_n, search_n, window = int(max_seconds * rate), int(search_seconds * rate), int(0.1 * rate)
    pieces = []
    while len(audio) > max_n:
        energy = np.cumsum(audio[max_n - search_n:max_n].astype(np.float64) ** 2)
        cut = max_n - search_n + int(np.argmin(energy[window:] - energy[:-window])) + window // 2
        pieces.append(audio[:cut])
        audio = audio[cut:]
    pieces.append(audio)
    return pieces


def save_wav(path: Path, audio: np.ndarray, rate: int) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def save_recording(audio: np.ndarray, rate: int, text: str) -> str:
    """Save a recording and its transcript as recordings/<timestamp>.wav/.txt. Returns the stem."""
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    base = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    stem, n = RECORDINGS_DIR / base, 1
    while stem.with_suffix(".wav").exists():  # two recordings in the same second
        n += 1
        stem = RECORDINGS_DIR / f"{base}_{n}"
    save_wav(stem.with_suffix(".wav"), audio, rate)
    stem.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")
    return stem.name


def load_wav(src: Path | BinaryIO) -> tuple[np.ndarray, int]:
    """Load a 16-bit PCM WAV (file path or file-like object) as mono float32."""
    with wave.open(src if hasattr(src, "read") else str(src), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("only 16-bit PCM WAV is supported")
        rate, channels = w.getframerate(), w.getnchannels()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    audio = pcm.astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, rate


def list_input_devices() -> list[str]:
    default_in = sd.default.device[0]
    lines = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            mark = "*" if i == default_in else " "
            host = sd.query_hostapis(d["hostapi"])["name"]
            lines.append(f"{mark} [{i:2d}] {d['name']}  ({host})")
    return lines
