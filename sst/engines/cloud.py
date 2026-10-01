"""Speech recognition by a cloud provider (OpenAI, Groq, Google Gemini) with the user's own key, or by the user's own
server (vLLM, a company AI gateway, any server with OpenAI's transcription API).

The voice is sent to the provider: the window asks before a cloud model is used. OpenAI, Groq and own servers share
OpenAI's transcription API (a multipart upload of the recording); Gemini gets the recording inline in a generateContent
request, with an instruction to write down exactly what was said. Your words and the chosen language go along as hints.

Built never to lose a dictation: the connection is opened while the user speaks (prepare), the first-connection stall
seen on the dev laptop is retried with a short connect timeout, and when the provider can't be reached or fails, the
recording is transcribed on this computer instead (Parakeet), with a note saying so (`last_error`). A provider that
couldn't be reached is skipped for a minute, so being offline costs one wait, not one per dictation.
"""
import base64
import hashlib
import http.client
import io
import json
import logging
import re
import ssl
import threading
import time
import uuid
import wave
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import numpy as np

from sst.audio import TARGET_RATE, condition, resample
from sst.engines.whisper import LANGUAGES

CONNECT_TIMEOUT = 1.5  # a first connection sometimes stalls (seen from Python); a retry gets through in milliseconds
CONNECT_ATTEMPTS = 3
ANSWER_TIMEOUT = 10.0  # seconds, plus ANSWER_PER_SECOND for each second of audio (the upload is 32 KB a second)
ANSWER_PER_SECOND = 0.5
DOWN_FOR = 60.0  # after the provider couldn't be reached, go straight to Parakeet this long
RATE_LIMIT_WAITS = (10, 20, 30, 60)  # seconds; scoring reading tests waits out a provider's per-minute limit
IDLE_RECONNECT = 30.0  # servers drop idle connections; refresh an older one while the user is speaking

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CloudProvider:
    key: str  # also its sst.gateway.PROVIDERS key: the API key is shared with AI cleanup
    name: str
    url: str
    api: str  # "openai": /audio/transcriptions (multipart); "gemini": generateContent with the audio inline
    models: tuple[str, ...]  # the first is the default
    key_page: str


CLOUD = {p.key: p for p in [
    CloudProvider("openai", "OpenAI", "https://api.openai.com/v1", "openai",
                  ("gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"), "https://platform.openai.com/api-keys"),
    CloudProvider("groq", "Groq", "https://api.groq.com/openai/v1", "openai",
                  ("whisper-large-v3-turbo", "whisper-large-v3"), "https://console.groq.com/keys"),
    CloudProvider("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta", "gemini",
                  ("gemini-flash-lite-latest", "gemini-flash-latest", "gemini-3.5-flash-lite", "gemini-3.6-flash"),
                  "https://aistudio.google.com/apikey"),
]}

# Your own server: its address (and a key, if it needs one) is the user's; the model is whatever the server offers.
SERVER = CloudProvider("server", "Your server", "", "openai", (), "")
REMOTE = {**CLOUD, SERVER.key: SERVER}  # every speech model that runs somewhere else

SPEECH = re.compile(r"whisper|transcri|speech|asr|parakeet|canary|voxtral|stt|audio", re.IGNORECASE)  # "Load models"

INSTRUCTION = ("Write down exactly what is said in this recording, word for word, with punctuation and capital letters. "
               "Output only the transcript: no introduction, no notes, no translation.")


class CloudError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status  # the HTTP status, when the provider answered


class Unreachable(CloudError):
    pass


def wav_bytes(audio: np.ndarray, rate: int) -> bytes:
    """16-bit mono WAV, what every provider accepts."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


class CloudEngine:
    def __init__(self, provider: str, api_key: str, model: str = "", language: str = "",
                 fallback: Callable[[], object] | None = None, url: str | None = None):
        if provider not in REMOTE:
            raise ValueError(f"Unknown cloud speech provider '{provider}'")
        if not api_key and provider in CLOUD:  # an own server may need none
            raise ValueError(f"{CLOUD[provider].name} needs an API key: enter it on the Speech recognition page.")
        if not (url or REMOTE[provider].url):
            raise ValueError("Your own server needs its address: enter it on the Speech recognition page.")
        self.provider = REMOTE[provider]
        self.name = provider
        # The app keeps the model, the key and a server's address up to date.
        self.model = model or (self.provider.models[0] if self.provider.models else "")
        self.api_key = api_key
        self.language = language  # "" = the provider detects it; the app keeps it up to date
        self.words: list[str] = []  # Your words, sent as a hint; the app keeps it up to date
        self.biased = True  # it uses Your words (sst eval says so)
        self.last_error = ""  # set when the provider failed and the recording was transcribed on this computer
        self._fallback_loader, self._fallback = fallback, None
        self._down_until = 0.0
        self._conn: http.client.HTTPConnection | None = None
        self._conn_target: tuple | None = None  # where the open connection goes
        self._used = 0.0
        self._lock = threading.Lock()
        self.url = ""
        self.set_url(url or self.provider.url)

    def set_url(self, url: str) -> None:
        """Where requests go: an own server's address can change while it is in use. Taken from the next request on,
        so the window never waits for a dictation being transcribed."""
        url = url.strip().rstrip("/")
        if url != self.url:
            parts = urlsplit(url)
            self._target = (parts.scheme == "https", parts.hostname, parts.port, parts.path)
            self.url, self._down_until = url, 0.0

    def __repr__(self) -> str:  # never let the key reach a log
        return f"CloudEngine({self.name!r}, {self.model!r})"

    @property
    def title(self) -> str:
        return f"{self.provider.name} {self.model}"

    @property
    def signature(self) -> str:
        words = hashlib.sha1("/".join(self.words).encode("utf-8")).hexdigest()[:10] if self.words else "none"
        where = self.name if self.name in CLOUD else f"{self.name}@{hashlib.sha1(self.url.encode()).hexdigest()[:8]}"
        return f"cloud|{where}|{self.model}|lang:{self.language or 'auto'}|words:{words}|peak-1"

    def prepare(self) -> None:
        """Open or refresh the connection in the background, while the user is still speaking."""
        threading.Thread(target=self._prepare, name="cloud-connect", daemon=True).start()

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        """The provider's text; if it can't be had, Parakeet's on this computer, and `last_error` says why."""
        self.last_error = ""
        audio16 = resample(condition(audio), sample_rate, TARGET_RATE)
        try:
            if time.monotonic() < self._down_until:
                raise CloudError("it couldn't be reached a moment ago")
            return self._ask_patiently(wav_bytes(audio16, TARGET_RATE), len(audio16) / TARGET_RATE)
        except TimeoutError:
            reason = "no answer in time"
        except Unreachable as e:
            self._down_until = time.monotonic() + DOWN_FOR
            reason = str(e)
        except (CloudError, OSError, http.client.HTTPException) as e:
            reason = str(e) or type(e).__name__
        log.warning("%s failed (%s); transcribing on this computer", self.title, reason)
        if self._fallback_loader is None:
            raise CloudError(f"{self.provider.name}: {reason}")
        try:
            if self._fallback is None:
                self._fallback = self._fallback_loader()
        except Exception as e:  # e.g. Parakeet isn't downloaded (a cloud-only user)
            log.warning("No speech model to fall back on: %s", e)
            raise CloudError(f"{self.provider.name}: {reason} ({e})") from None
        if hasattr(self._fallback, "words"):
            self._fallback.words = list(self.words)
        self.last_error = f"{self.provider.name}: {reason}"
        return self._fallback.transcribe(audio, sample_rate)

    def check(self, audio: np.ndarray, sample_rate: int) -> str:
        """For the Test button: one recording through the provider, never Parakeet. Raises with a readable reason."""
        audio16 = resample(condition(audio), sample_rate, TARGET_RATE)
        t0 = time.perf_counter()
        try:
            text = self.ask(wav_bytes(audio16, TARGET_RATE), len(audio16) / TARGET_RATE)
        except TimeoutError:
            raise CloudError("no answer in time") from None
        except (OSError, http.client.HTTPException) as e:
            raise CloudError(f"could not reach {self.provider.name}: {e}") from None
        return f"{self.model} answered in {time.perf_counter() - t0:.1f} s: {text or '(nothing recognised)'}"

    def _ask_patiently(self, wav: bytes, seconds: float) -> str:
        """ask(), waiting out a rate limit (HTTP 429) when nothing falls back: scoring reading tests sends many
        recordings in a row. Dictation doesn't wait; Parakeet types at once."""
        waits = list(RATE_LIMIT_WAITS) if self._fallback_loader is None else []
        while True:
            try:
                return self.ask(wav, seconds)
            except CloudError as e:
                if e.status != 429 or not waits:
                    raise
                wait = waits.pop(0)
                log.info("%s is rate limited; trying again in %d s", self.title, wait)
                time.sleep(wait)

    def ask(self, wav: bytes, seconds: float) -> str:
        """The provider's transcript of one WAV recording. Raises CloudError with a readable reason, or OSError."""
        timeout = ANSWER_TIMEOUT + ANSWER_PER_SECOND * seconds
        words = ", ".join(w for w in self.words if w.strip())
        if self.provider.api == "gemini":
            instruction = INSTRUCTION
            if self.language:
                instruction += f" The speech is in {LANGUAGES.get(self.language, self.language)}."
            if words:
                instruction += f" These names and terms may occur; spell them like this: {words}."
            body = json.dumps({"contents": [{"parts": [
                {"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(wav).decode("ascii")}},
                {"text": instruction}]}], "generationConfig": {"temperature": 0}}).encode("utf-8")
            status, data = self._request(f"/models/{self.model}:generateContent", body,
                                         {"Content-Type": "application/json", "x-goog-api-key": self.api_key}, timeout)
            answer = _json(status, data)
            parts = ((answer.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
        else:
            fields = {"model": self.model, "response_format": "json"} if self.model else {"response_format": "json"}
            if self.language:
                fields["language"] = self.language
            if words:
                fields["prompt"] = f"Names and terms: {words}."
            body, content_type = _multipart(fields, "recording.wav", wav)
            status, data = self._request("/audio/transcriptions", body, {"Content-Type": content_type, **self._auth()},
                                         timeout)
            text = str(_json(status, data).get("text") or "")
        return " ".join(text.split())

    def models(self) -> list[str]:
        """For "Load models": the server's models, the speech ones first (a gateway lists its chat models too).
        Raises CloudError with a readable reason."""
        try:
            status, data = self._request("/models", None, self._auth(), 10.0, method="GET")
        except TimeoutError:
            raise CloudError("no answer in time") from None
        except (OSError, http.client.HTTPException) as e:
            raise CloudError(f"could not reach the server: {e}") from None
        answer = _json(status, data)
        ids = [m["id"].removeprefix("models/") for m in answer.get("data") or []
               if isinstance(m, dict) and isinstance(m.get("id"), str)]
        return sorted(ids, key=lambda name: (not SPEECH.search(name), name.lower()))

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}  # an own server may need no key

    # ---- HTTP: one kept-alive connection, like sst.gateway's

    def _request(self, path: str, body: bytes | None, headers: dict, timeout: float,
                 method: str = "POST") -> tuple[int, str]:
        with self._lock:
            for attempt in (1, 2):
                if self._stale():
                    self._connect()
                self._conn.sock.settimeout(timeout)
                try:
                    self._conn.request(method, self._conn_target[3] + path, body=body, headers=headers)
                    response = self._conn.getresponse()
                    data = response.read().decode("utf-8", "replace")
                except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError):
                    self._close()  # the server dropped a kept-alive connection: reconnect once
                    if attempt == 2:
                        raise
                    continue
                except BaseException:
                    self._close()
                    raise
                self._used = time.monotonic()
                return response.status, data
        raise AssertionError("unreachable")

    def _prepare(self) -> None:
        with self._lock:
            if time.monotonic() >= self._down_until and self._stale():
                try:
                    self._connect()
                except Unreachable as e:  # the dictation finds out too, and uses Parakeet at once
                    self._down_until = time.monotonic() + DOWN_FOR
                    log.info("Could not connect to %s ahead of time: %s", self.provider.name, e)

    def _stale(self) -> bool:
        """No connection, an idle one the server may have dropped, or one to an address no longer chosen."""
        return self._conn is None or self._conn_target != self._target or time.monotonic() - self._used > IDLE_RECONNECT

    def _connect(self) -> None:
        self._close()
        target = self._target
        https, host, port, _ = target
        error: OSError | None = None
        for _ in range(CONNECT_ATTEMPTS):
            conn = (http.client.HTTPSConnection(host, port, timeout=CONNECT_TIMEOUT, context=ssl.create_default_context())
                    if https else http.client.HTTPConnection(host, port, timeout=CONNECT_TIMEOUT))
            try:
                conn.connect()
            except OSError as e:
                error = e
                conn.close()
                continue
            self._conn, self._conn_target, self._used = conn, target, time.monotonic()
            return
        raise Unreachable(f"cannot reach {host}: {error}")

    def _close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _multipart(fields: dict[str, str], filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    for name, value in fields.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
              "Content-Type: audio/wav\r\n\r\n".encode())
    out.write(data)
    out.write(f"\r\n--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def _json(status: int, data: str) -> dict:
    try:
        answer = json.loads(data)
    except ValueError:
        raise CloudError(f"HTTP {status}, not JSON") from None
    if not isinstance(answer, dict):
        raise CloudError(f"HTTP {status}, an unexpected answer")
    if status != 200 or "error" in answer:
        error = answer.get("error") or answer.get("message") or data[:120]
        if isinstance(error, dict):
            error = error.get("message") or error
        raise CloudError(f"HTTP {status} {str(error)[:200]}", status)
    return answer
