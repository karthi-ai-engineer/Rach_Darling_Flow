"""Text cleanup with a model behind any OpenAI-compatible endpoint (OpenAI, Groq, a local Ollama or LM Studio, a
company AI gateway...). The endpoint, key and models are the user's settings; nothing here is tied to one provider.

Parakeet transcribes on the laptop; only the finished text goes to the endpoint. Built never to hold up typing:
if the chosen model fails the backup model is tried, a slow answer or an unreachable endpoint gives the text as heard
(and an unreachable endpoint is skipped for a minute), and the connection is opened while the user is still speaking.
Standard library only; connections go direct (no proxy).
"""
import base64
import ctypes
import http.client
import json
import logging
import re
import ssl
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from sst.settings import CONFIG_DIR

GATEWAY_FILE = CONFIG_DIR / "gateway.json"  # the API key, encrypted for the Windows user; never in git
DEFAULT_URL = ""  # the user enters their own endpoint in Settings

CONNECT_TIMEOUT = 1.5    # a first connect sometimes stalls (seen from Python); a retry gets through in milliseconds
CONNECT_ATTEMPTS = 3
ANSWER_TIMEOUT = 2.0     # seconds, plus ANSWER_PER_WORD for each word; a normal sentence takes 0.5-0.7 s
ANSWER_PER_WORD = 0.04
DOWN_FOR = 60.0          # after the gateway couldn't be reached (e.g. off the office network), skip cleanup this long
IDLE_RECONNECT = 30.0    # servers drop idle connections; refresh an older one while the user is speaking

SYSTEM_PROMPT = (
    "You clean up text from a speech recognizer. Fix recognition errors using the context and the user's vocabulary "
    "(a word that sounds like a vocabulary word usually is that word), add punctuation and capital letters, and remove "
    "filler words such as um, uh and you know. Keep the user's own wording and meaning; do not rephrase, summarize, add "
    "anything or answer questions in the text. Output only the cleaned text.")

log = logging.getLogger(__name__)


class Unreachable(OSError):
    pass


class GatewayError(Exception):
    pass


@dataclass
class GatewayConfig:
    """Where the gateway is and the user's key. On disk the key is encrypted for the Windows user (DPAPI), so only
    that user on this laptop can read it; a key pasted into the file by hand ("api_key") is encrypted on first load."""

    base_url: str = DEFAULT_URL
    api_key: str = ""

    def __repr__(self) -> str:  # never let the key reach a log
        return f"GatewayConfig(base_url={self.base_url!r}, api_key={'set' if self.api_key else 'missing'})"

    @classmethod
    def load(cls, path: Path = GATEWAY_FILE) -> "GatewayConfig":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            base_url = str(data.get("base_url") or DEFAULT_URL).strip()
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError, AttributeError) as e:
            log.warning("Ignoring unreadable %s: %s", path, e)
            return cls()
        if data.get("api_key_protected"):
            try:
                return cls(base_url, _unprotect(base64.b64decode(data["api_key_protected"])).decode("utf-8"))
            except (OSError, ValueError) as e:  # e.g. the file was copied from another user or laptop
                log.warning("Cannot decrypt the gateway key in %s (%s); enter it again in Settings", path, e)
                return cls(base_url)
        config = cls(base_url, str(data.get("api_key") or "").strip())
        if config.api_key:
            config.save(path)  # a key typed into the file: store it encrypted from now on
        return config

    def save(self, path: Path = GATEWAY_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"base_url": self.base_url}
        if self.api_key:
            data["api_key_protected"] = base64.b64encode(_protect(self.api_key.encode("utf-8"))).decode("ascii")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)


# ---- Windows DPAPI: encryption tied to the signed-in Windows user

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
for _f in (_crypt32.CryptProtectData, _crypt32.CryptUnprotectData):
    _f.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   wintypes.DWORD, ctypes.POINTER(_Blob)]
    _f.restype = wintypes.BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


def _dpapi(function, data: bytes) -> bytes:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in, blob_out = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), _Blob()
    if not function(ctypes.byref(blob_in), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)):
        raise OSError(ctypes.get_last_error(), "Windows data protection (DPAPI) failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        _kernel32.LocalFree(blob_out.pbData)


def _protect(data: bytes) -> bytes:
    return _dpapi(_crypt32.CryptProtectData, data)


def _unprotect(data: bytes) -> bytes:
    return _dpapi(_crypt32.CryptUnprotectData, data)


def plausible(heard: str, cleaned: str) -> bool:
    """A cleanup keeps roughly the same length; anything else is a model misbehaving (answering, truncating...)."""
    h, c = len(heard.split()), len(cleaned.split())
    return c > 0 and 0.5 * h - 2 <= c <= 1.5 * h + 5  # fillers may go, but a 7-word dictation doesn't become "Yes."


class Polisher:
    """Cleans up dictated text with a model on the endpoint. polish() never raises and never takes much longer than the
    answer timeout; when it can't help, it returns the text unchanged and says why in `last_error`."""

    def __init__(self, config: GatewayConfig, model: str, vocabulary: list[str] = (), fallback: str | None = None):
        self.config, self.model, self.fallback = config, model, fallback
        self.vocabulary = [w.strip() for w in vocabulary if w.strip()]
        self.last_error = ""
        url = urlsplit(config.base_url.rstrip("/"))
        self._https, self._host, self._port, self._path = url.scheme == "https", url.hostname, url.port, url.path
        self._conn: http.client.HTTPConnection | None = None
        self._used = 0.0
        self._down_until = 0.0
        self._lock = threading.Lock()

    # ---- used by the dictation

    def prepare(self) -> None:
        """Open or refresh the connection in the background, while the user is still speaking."""
        threading.Thread(target=self._prepare, name="gateway-connect", daemon=True).start()

    def polish(self, text: str) -> str:
        self.last_error = ""
        if not text.strip() or not self.config.base_url or not self.model:
            return text
        if time.monotonic() < self._down_until:
            self.last_error = "the AI endpoint could not be reached a moment ago"
            return text
        for model in [self.model] + ([self.fallback] if self.fallback and self.fallback != self.model else []):
            t0 = time.perf_counter()
            try:
                cleaned = self._ask(model, text)
            except TimeoutError:
                self.last_error = f"{_short(model)} took too long"
                return text  # the time for this dictation is used up; don't start on the other model
            except (OSError, http.client.HTTPException) as e:
                self._mark_down(e)
                return text
            except GatewayError as e:
                self.last_error = str(e)
                log.warning("Cleanup with %s failed: %s", model, e)
                continue
            if not plausible(text, cleaned):
                self.last_error = f"{_short(model)} gave an unusual answer"
                log.warning("Ignoring an implausible cleanup by %s: %r -> %r", model, text, cleaned)
                return text
            self.last_error = ""
            log.info("Cleaned by %s in %.2fs", model, time.perf_counter() - t0)
            return cleaned
        return text

    def check(self) -> str:
        """For the Settings "Test" button: clean one short sentence with the chosen model. Raises with a readable reason."""
        t0 = time.perf_counter()
        try:
            answer = self._ask(self.model, "so this is a quick test of the dictation clean up")
        except TimeoutError:
            raise GatewayError("no answer in time") from None
        except (OSError, http.client.HTTPException) as e:
            raise GatewayError(f"could not reach the endpoint: {e}") from None
        return f"{_short(self.model)} answered in {time.perf_counter() - t0:.1f} s: {answer}"

    def models(self) -> list[str]:
        """For the Settings "Load models" button: the endpoint's model ids, models on the endpoint's own server first
        (some gateways mark them `"is_cloud": false`). Raises GatewayError with a readable reason."""
        try:
            with self._lock:
                status, data = self._request("/models", None, 10.0, method="GET")
        except TimeoutError:
            raise GatewayError("no answer in time") from None
        except (OSError, http.client.HTTPException) as e:
            raise GatewayError(f"could not reach the endpoint: {e}") from None
        try:
            answer = json.loads(data)
        except ValueError:
            raise GatewayError(f"HTTP {status}, not JSON") from None
        if status != 200:
            detail = (answer.get("error") or answer.get("detail") or data[:120]) if isinstance(answer, dict) else data[:120]
            raise GatewayError(f"HTTP {status} {detail}")
        items = answer.get("data", []) if isinstance(answer, dict) else answer
        models = [m for m in items if isinstance(m, dict) and isinstance(m.get("id"), str)]
        return [m["id"] for m in sorted(models, key=lambda m: (m.get("is_cloud") is not False, m["id"].lower()))]

    # ---- HTTP

    def _ask(self, model: str, text: str) -> str:
        words = len(text.split())
        body = json.dumps({
            "model": model, "temperature": 0, "max_tokens": 64 + 3 * words,
            "messages": [{"role": "system", "content": self._system_prompt()}, {"role": "user", "content": text}],
            "chat_template_kwargs": {"enable_thinking": False},  # Qwen3: answer directly, no reasoning first
        })
        with self._lock:
            status, data = self._request("/chat/completions", body, ANSWER_TIMEOUT + ANSWER_PER_WORD * words)
        try:
            answer = json.loads(data)
        except ValueError:
            raise GatewayError(f"{_short(model)}: HTTP {status}, not JSON") from None
        if status != 200 or "error" in answer or not answer.get("choices"):
            detail = answer.get("error") or answer.get("detail") or answer.get("message") or data[:120]
            raise GatewayError(f"{_short(model)}: HTTP {status} {detail}")
        content = answer["choices"][0].get("message", {}).get("content") or ""
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        if len(content) > 1 and content[0] == content[-1] == '"' and not text.startswith('"'):
            content = content[1:-1].strip()  # some models wrap the answer in quotes
        return content

    def _request(self, path: str, body: str | None, timeout: float, method: str = "POST") -> tuple[int, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:  # local endpoints such as Ollama need none
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        for attempt in (1, 2):
            if self._conn is None:
                self._connect()
            self._conn.sock.settimeout(timeout)
            try:
                self._conn.request(method, self._path + path, body=body.encode("utf-8") if body else None, headers=headers)
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
            if time.monotonic() < self._down_until or not self.config.base_url:
                return
            if self._conn is None or time.monotonic() - self._used > IDLE_RECONNECT:
                try:
                    self._connect()
                except OSError as e:
                    self._mark_down(e)

    def _connect(self) -> None:
        self._close()
        error: OSError | None = None
        for _ in range(CONNECT_ATTEMPTS):
            conn = (http.client.HTTPSConnection(self._host, self._port, timeout=CONNECT_TIMEOUT,
                                                context=ssl.create_default_context())
                    if self._https else http.client.HTTPConnection(self._host, self._port, timeout=CONNECT_TIMEOUT))
            try:
                conn.connect()
            except OSError as e:
                error = e
                conn.close()
                continue
            self._conn, self._used = conn, time.monotonic()
            return
        raise Unreachable(f"cannot reach {self._host}: {error}")

    def _close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _mark_down(self, error: Exception) -> None:
        self._down_until = time.monotonic() + DOWN_FOR
        self.last_error = f"the AI endpoint could not be reached ({error})"
        log.warning("Endpoint unreachable, skipping cleanup for %.0fs: %s", DOWN_FOR, error)

    def _system_prompt(self) -> str:
        if not self.vocabulary:
            return SYSTEM_PROMPT
        return SYSTEM_PROMPT + "\nThe user's vocabulary: " + ", ".join(self.vocabulary)


def _short(model: str) -> str:
    return model.rsplit("/", 1)[-1]
