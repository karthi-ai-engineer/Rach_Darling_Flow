"""Microphone recording and WAV read/write.

Capture goes through WASAPI, Windows' own audio interface, at the microphone's own rate (the speech engine resamples):
the old MME interface reports 44.1 kHz for every microphone and resamples silently, which hid that a Bluetooth headset
only delivers call-quality audio. A microphone can be kept open ("warm") for a while after a dictation: opening one
takes about 0.4 s, which cut off first words, and a warm one also keeps the moment before the key press.
"""
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

import numpy as np
import sounddevice as sd

from sst import RECORDINGS_DIR

TARGET_RATE = 16_000  # what speech models expect; other rates are resampled by the engine
PREROLL_SECONDS = 0.4  # kept from before the key press while the microphone is warm
TAIL_SECONDS = 0.3  # recorded after the key is let go: the last word often runs past it
WASAPI_RAW = 1  # AUDCLNT_STREAMOPTIONS_RAW: no Windows or driver voice effects (noise suppression, gain, gating)


def _pick_rate(device: int | None) -> int:
    try:
        sd.check_input_settings(device=device, samplerate=TARGET_RATE, channels=1, dtype="float32")
        return TARGET_RATE
    except Exception:
        return int(sd.query_devices(device, "input")["default_samplerate"])


def _host_api() -> int:
    """WASAPI when Windows has it (always, on Windows 10/11), else PortAudio's default."""
    for i, api in enumerate(sd.query_hostapis()):
        if api["name"] == "Windows WASAPI" and api["default_input_device"] >= 0:
            return i
    return sd.query_devices(kind="input")["hostapi"]


_open_streams = 0  # recordings and level meters running right now


def _refresh_devices() -> None:
    # PortAudio reads the device list only once; re-reading it (~45 ms) shows a headset plugged in since, or a new
    # Windows default. It restarts PortAudio, which would pull the rug from under any open stream, so not while one
    # is open (e.g. a warm microphone, or the settings page's level meter while a dictation starts).
    if not _open_streams:
        sd._terminate()
        sd._initialize()


def input_device_names(refresh: bool = True) -> list[str]:
    """Microphones as Windows lists them, for the settings window."""
    if refresh:
        _refresh_devices()
    api = _host_api()
    return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0 and d["hostapi"] == api
            and "Sound Mapper" not in d["name"]]


def _resolve(device: int | str | None) -> int | None:
    """A device number, or a microphone name (stable across restarts); None or an unplugged name = Windows default.
    Names saved before WASAPI are MME's, cut at 31 characters: they match the start of the full name."""
    if not isinstance(device, str):
        return device
    api = _host_api()
    inputs = [(i, d["name"]) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0 and d["hostapi"] == api]
    exact = next((i for i, name in inputs if name == device), None)
    return exact if exact is not None else next((i for i, name in inputs if name.startswith(device)), None)


def _default_input() -> int:
    return sd.query_hostapis(_host_api())["default_input_device"]


def call_quality(device: int | str | None) -> bool:
    """A Bluetooth headset's microphone: Windows opens it in call mode (16 or 8 kHz, "Hands-Free"). It hears worse,
    and while it is open the headset plays everything in call quality too."""
    try:
        index = _resolve(device)
        info = sd.query_devices(_default_input() if index is None else index)
        return info["default_samplerate"] <= 16_000 or "hands-free" in info["name"].lower()
    except Exception:
        return False


class Take:
    """One recording. stop_later() hands it over while the tail after the key release is still being recorded;
    audio() waits for that tail."""

    def __init__(self, chunks: list[np.ndarray], rate: int):
        self.rate, self.chunks = rate, chunks
        self.preroll = sum(len(c) for c in chunks)  # samples from before start()
        self.remaining = 0  # tail samples still to come
        self.done = threading.Event()

    @classmethod
    def ready(cls, audio: np.ndarray, rate: int) -> "Take":
        take = cls([audio], rate)
        take.preroll = 0  # a finished recording: all of it counts
        take.done.set()
        return take

    @property
    def seconds(self) -> float:
        """What has been recorded since start(), so far (not the moment before it)."""
        return (sum(len(c) for c in self.chunks) - self.preroll) / self.rate

    def audio(self, timeout: float = 2.0) -> np.ndarray:
        self.done.wait(timeout)  # a stream closed early simply ends the tail
        return np.concatenate(self.chunks) if self.chunks else np.zeros(0, dtype=np.float32)


class Recorder:
    """Records mono audio between start() and stop() / stop_later().

    warm_seconds > 0 keeps the microphone open that long after a recording (tick() closes it), so the next one starts at
    once, with `preroll_seconds` from before start(); math.inf keeps it open while the app runs (keep_open() opens it
    at start-up), the voice pipeline's always-on microphone. The moment before start() lives only in RAM. A
    call-quality (Bluetooth) microphone is never kept open. `tail` seconds are recorded after stop_later(). raw asks
    Windows for the microphone without its voice effects."""

    def __init__(self, device: int | str | None = None, *, warm_seconds: float = 0.0, tail: float = 0.0,
                 raw: bool = False):
        self.device = device  # number, microphone name, or None for the Windows default
        self.warm_seconds, self.tail, self.raw = warm_seconds, tail, raw
        self.preroll_seconds = PREROLL_SECONDS
        self.rate = TARGET_RATE
        self.level = 0.0  # loudness of the latest block (RMS), for the recording indicator
        self.info: dict = {}  # the device, interface, rate and mode actually opened
        self._take: Take | None = None
        self._closing: list[Take] = []  # takes still recording their tail
        self._ring: deque[np.ndarray] = deque()  # the last PREROLL_SECONDS while warm and not recording
        self._ring_samples = 0
        self._stream = None
        self._opened_for: tuple | None = None
        self._idle_since = 0.0
        self._lock = threading.Lock()

    @property
    def warm(self) -> bool:
        return self._stream is not None

    @property
    def current_take(self) -> Take | None:
        """The recording in progress (its chunks grow while the key is held), for the voice pipeline's live chunking."""
        return self._take

    def keep_open(self) -> None:
        """Open the microphone now, without recording, so that even the first dictation has its pre-roll (always-on
        mode). Does nothing for a microphone that is never kept open."""
        if self._stream is not None and self._opened_for != (self.device, self.raw):
            self.close()  # another microphone or mode was chosen
        if self._stream is None and self._keep_warm():
            self._open()
            self._idle_since = time.monotonic()
            if not self._keep_warm():  # it turned out to be a call-quality microphone
                self.close()

    def start(self) -> None:
        if self._take is not None:
            self.stop_later()
        if self._stream is not None and self._opened_for != (self.device, self.raw):
            self.close()  # another microphone or mode was chosen meanwhile
        preroll = self._stream is not None
        if self._stream is None:
            self._open()
        with self._lock:
            chunks = list(self._ring) if preroll else []
            self._ring.clear()
            self._ring_samples = 0
            self._take = Take(chunks, self.rate)
        self.level = 0.0

    def stop_later(self) -> Take:
        """Stop recording now, but keep the `tail` seconds that follow; the take's audio() waits for them."""
        with self._lock:
            take, self._take = self._take or Take.ready(np.zeros(0, dtype=np.float32), self.rate), None
            if self._stream is not None and self.tail > 0 and not take.done.is_set():
                take.remaining = int(self.tail * self.rate)
                self._closing.append(take)
            else:
                take.done.set()
            self._idle_since = time.monotonic()
        self.level = 0.0
        if not self._closing and not self._keep_warm():
            self.close()
        return take

    def stop(self) -> np.ndarray:
        """Stop recording and return the samples, in [-1, 1] at self.rate (after the tail, if any)."""
        audio = self.stop_later().audio()
        if not self._keep_warm():
            self.close()
        return audio

    def tick(self, now: float) -> None:
        """Close the microphone once its tail is recorded and it has been idle for warm_seconds."""
        if self._stream is None or self._take is not None or self._closing:
            return
        if not self._keep_warm() or now - self._idle_since > self.warm_seconds:
            self.close()

    def close(self) -> None:
        global _open_streams
        with self._lock:
            stream, self._stream = self._stream, None
            closing, self._closing = self._closing, []
            self._ring.clear()
            self._ring_samples = 0
        for take in closing:
            take.done.set()
        if stream is not None:
            stream.close()
            _open_streams -= 1

    def _keep_warm(self) -> bool:
        return self.warm_seconds > 0 and not self.info.get("call_quality")

    def _open(self) -> None:
        global _open_streams
        _refresh_devices()  # a long-running app must follow the microphones plugged in since the last recording
        device = _resolve(self.device)
        try:
            stream = self._open_wasapi(device)
        except Exception:  # an odd driver: the old way (PortAudio's default interface) still records
            index = self._legacy_index(device)
            self.rate = _pick_rate(index)
            stream = sd.InputStream(samplerate=self.rate, channels=1, dtype="float32", device=index, callback=self._on_audio)
            self.info = self._describe(index, "windows")
        stream.start()
        self._stream, self._opened_for = stream, (self.device, self.raw)
        _open_streams += 1

    def _open_wasapi(self, device: int | None):
        index = _default_input() if device is None else device
        if sd.query_hostapis(sd.query_devices(index)["hostapi"])["name"] != "Windows WASAPI":
            raise OSError("not a WASAPI device")
        self.rate = int(sd.query_devices(index)["default_samplerate"])  # shared mode runs at the device's own rate
        mode = "raw" if self.raw else "windows"
        settings = sd.WasapiSettings(auto_convert=True)  # mono from a 2- or 4-channel microphone array
        if self.raw:
            settings._streaminfo.streamOption = WASAPI_RAW  # not in sounddevice's API yet, but in PortAudio's struct
        try:
            stream = sd.InputStream(samplerate=self.rate, channels=1, dtype="float32", device=index,
                                    callback=self._on_audio, extra_settings=settings)
        except Exception:
            if not self.raw:
                raise
            mode = "windows"  # a driver without raw mode: record with its effects rather than not at all
            stream = sd.InputStream(samplerate=self.rate, channels=1, dtype="float32", device=index,
                                    callback=self._on_audio, extra_settings=sd.WasapiSettings(auto_convert=True))
        self.info = self._describe(index, mode)
        return stream

    @staticmethod
    def _legacy_index(device: int | None) -> int | None:
        if device is None:
            return None
        name = sd.query_devices(device)["name"]
        api = sd.query_devices(kind="input")["hostapi"]
        return next((i for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0 and d["hostapi"] == api
                     and name.startswith(d["name"])), None)

    def _describe(self, index: int | None, mode: str) -> dict:
        try:
            info = sd.query_devices(index, "input") if index is not None else sd.query_devices(kind="input")
            return {"device": info["name"], "host_api": sd.query_hostapis(info["hostapi"])["name"], "rate": self.rate,
                    "mode": mode, "call_quality": call_quality(index), "warm": self.warm_seconds > 0}
        except Exception:  # only a note: never let it stop a recording
            return {"rate": self.rate, "mode": mode}

    def _on_audio(self, indata, frames, time_info, status):
        block = indata[:, 0].copy()
        self.level = float(np.sqrt(np.mean(block * block)))
        with self._lock:
            used = 0  # the start of the block that is a take's tail
            for take in self._closing:
                take.chunks.append(block[:take.remaining])
                used = max(used, min(len(block), take.remaining))
                take.remaining -= len(block)
                if take.remaining <= 0:
                    take.done.set()
            self._closing = [t for t in self._closing if not t.done.is_set()]
            # The tail belongs to the take just ended: the next take's pre-roll starts after it, or a dictation started
            # right after the last one would begin with that one's last word again.
            block = block[used:]
            if not len(block):
                return
            if self._take is not None:
                self._take.chunks.append(block)
            else:
                self._ring.append(block)
                self._ring_samples += len(block)
                while self._ring and self._ring_samples - len(self._ring[0]) >= self.preroll_seconds * self.rate:
                    self._ring_samples -= len(self._ring.popleft())

    def describe(self) -> dict:
        """The microphone actually used, its audio interface, rate and mode, for the reading test's notes."""
        return dict(self.info) if self.info else {"rate": self.rate}


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


@dataclass
class AudioStats:
    """What a recording sounds like to a machine: level, noise, clipping and bandwidth. Speech models cope with any
    steady level (they normalise), but not with clipping, speech buried in noise, or phone-quality audio: a Bluetooth
    headset's microphone in call mode keeps nothing above 4 kHz (or 8 kHz), where many consonants live."""
    seconds: float
    speech_db: float  # dBFS of the loud (speech) frames
    noise_db: float  # dBFS of the quiet frames: the room and the microphone's own hiss
    peak_db: float
    clipped: float  # share of samples at full scale
    high_band_db: float  # voiced frames' 4-7 kHz level against their 0.3-3 kHz level; very low = narrowband

    @property
    def snr_db(self) -> float:
        return self.speech_db - self.noise_db

    @property
    def flags(self) -> list[str]:
        out = []
        if self.high_band_db < NARROWBAND_DB:
            out.append("narrowband")
        if self.clipped > 0.001:
            out.append("clipped")
        if self.speech_db < QUIET_DB:
            out.append("quiet")
        if self.snr_db < NOISY_DB:
            out.append("noisy")
        return out


NARROWBAND_DB = -45.0  # wideband speech measures about -15 to -35 dB here; a call-mode Bluetooth mic, -60 or lower
QUIET_DB = -45.0
NOISY_DB = 15.0


def measure(audio: np.ndarray, rate: int) -> AudioStats:
    """Level, noise, clipping and bandwidth of a recording, from 20 ms frames (loudest 5% = speech, quietest 10% = noise)."""
    audio = np.asarray(audio, dtype=np.float64)
    seconds = len(audio) / rate
    frame = max(1, int(0.02 * rate))
    n = len(audio) // frame
    if n < 2:
        return AudioStats(seconds, -120.0, -120.0, -120.0, 0.0, 0.0)
    frames = audio[:n * frame].reshape(n, frame)
    frame_db = 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-12)
    speech_db, noise_db = float(np.percentile(frame_db, 95)), float(np.percentile(frame_db, 10))
    peak = float(np.max(np.abs(audio)))
    clipped = float(np.mean(np.abs(audio) >= 0.99))
    return AudioStats(seconds, speech_db, noise_db, 20 * np.log10(peak + 1e-12), clipped,
                      _high_band_db(frames[frame_db > speech_db - 15], rate))


def _high_band_db(voiced: np.ndarray, rate: int) -> float:
    if rate <= 8000:
        return -120.0  # nothing above 4 kHz can exist
    if len(voiced) == 0:
        return 0.0  # no speech to judge by
    spectrum = np.mean(np.abs(np.fft.rfft(voiced * np.hanning(voiced.shape[1]), axis=1)) ** 2, axis=0)
    freqs = np.fft.rfftfreq(voiced.shape[1], 1 / rate)
    high = spectrum[(freqs >= 4000) & (freqs <= min(7000, 0.45 * rate))].mean()
    low = spectrum[(freqs >= 300) & (freqs <= 3000)].mean()
    return float(10 * np.log10((high + 1e-20) / (low + 1e-20)))


PEAK = 10 ** (-1 / 20)  # -1 dBFS


def condition(audio: np.ndarray) -> np.ndarray:
    """Ready audio for the speech engine: no DC offset, and the loudest moment at -1 dBFS. The model normalises its own
    features, yet quiet laptop-microphone audio (speech around -35 dBFS) sometimes decoded to nothing at all; raised
    to full scale it didn't, and word errors on the owner's 150 reading-test sentences went from 9.2% to 8.1%."""
    audio = np.asarray(audio, dtype=np.float32)
    if len(audio) == 0:
        return audio
    audio = audio - np.float32(audio.mean())
    peak = float(np.max(np.abs(audio)))
    return audio * np.float32(PEAK / peak) if peak > 1e-4 else audio  # near-silence stays silence, not amplified hiss


def resample(audio: np.ndarray, rate: int, target: int = TARGET_RATE) -> np.ndarray:
    """Audio at another sample rate, for engines that want 16 kHz and don't resample themselves (sherpa-onnx does).
    The FFT method: an ideal low-pass at the lower rate's Nyquist, any ratio (48 or 44.1 kHz to 16 kHz). A little
    silence on both ends keeps the transform's wrap-around from ringing into the speech."""
    audio = np.asarray(audio, dtype=np.float32)
    if rate == target or len(audio) == 0:
        return audio
    pad = rate // 10
    padded = np.concatenate([np.zeros(pad, np.float32), audio, np.zeros(pad, np.float32)])
    n = round(len(padded) * target / rate)
    spectrum = np.fft.rfft(padded.astype(np.float64))
    keep = n // 2 + 1
    spectrum = spectrum[:keep] if len(spectrum) >= keep else np.pad(spectrum, (0, keep - len(spectrum)))
    out = np.fft.irfft(spectrum, n) * (n / len(padded))
    cut = round(pad * target / rate)
    return out[cut:cut + round(len(audio) * target / rate)].astype(np.float32)


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
