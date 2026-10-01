"""Cloud speech models against a fake provider on this machine (no real provider, no key): each provider's request
format, the hints, the errors, and Parakeet taking over whenever the provider can't help."""
import base64
import io
import json
import socket
import threading
import time
import wave
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from sst.engines import cloud, load_engine, usable
from sst.engines.cloud import CloudEngine, CloudError
from sst.gateway import SPEECH_SERVER, GatewayConfig

RATE = 48_000
AUDIO = (0.1 * np.sin(np.linspace(0, 2000, 2 * RATE))).astype(np.float32)  # two seconds at the microphone's rate


class FakeProvider:
    """Answers like OpenAI's /audio/transcriptions and Gemini's generateContent; `status` and `delay` make it fail."""

    def __init__(self):
        self.requests, self.connections, self.statuses, self.delay = [], 0, [], 0.0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # keep-alive, like the real providers

            def setup(self):
                fake.connections += 1
                super().setup()

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                request = {"path": self.path, "auth": self.headers.get("Authorization"),
                           "x-goog-api-key": self.headers.get("x-goog-api-key")}
                if self.path.endswith(":generateContent"):
                    request["json"] = json.loads(body)
                else:
                    message = BytesParser(policy=HTTP).parsebytes(
                        f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + body)
                    request["fields"] = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                                         for part in message.iter_parts()}
                fake.requests.append(request)
                time.sleep(fake.delay)
                status = fake.statuses.pop(0) if fake.statuses else 200
                if status != 200:
                    return self._reply(status, {"error": {"message": "Invalid API key" if status == 401 else "Slow down"}})
                if "json" in request:
                    return self._reply(200, {"candidates": [{"content": {"parts": [
                        {"text": "Thinking about it...", "thought": True}, {"text": " Hello  from\nGemini. "}]}}]})
                self._reply(200, {"text": " Hello  from OpenAI. "})

            def do_GET(self):
                fake.requests.append({"path": self.path, "auth": self.headers.get("Authorization")})
                self._reply(200, {"object": "list", "data": [{"id": "Qwen/Qwen3-30B"}, {"id": "whisper-1"},
                                                              {"id": "models/gemini-3.5-transcribe"}, {"id": "bge-m3"}]})

            def _reply(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.address = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def engine(self, provider="openai", model="", language="", fallback=None, path="/v1", key="test-key"):
        return CloudEngine(provider, key, model, language, fallback, url=self.address + path)


@pytest.fixture
def fake():
    provider = FakeProvider()
    yield provider
    provider.server.shutdown()


class Parakeet:
    """Stands in for the engine on this computer that a cloud model falls back on."""
    name = "parakeet"

    def __init__(self):
        self.words, self.calls = [], 0

    def transcribe(self, audio, rate):
        self.calls += 1
        return "hello from parakeet"


def _wav(data: bytes) -> tuple[int, int]:
    with wave.open(io.BytesIO(data)) as w:
        return w.getframerate(), w.getnframes()


def test_openai_and_groq_get_a_multipart_upload_with_the_hints(fake):
    engine = fake.engine("groq", language="ta")
    engine.words = ["Karthi", "Rflow"]
    assert engine.transcribe(AUDIO, RATE) == "Hello from OpenAI." and engine.last_error == ""
    request = fake.requests[0]
    assert request["path"] == "/v1/audio/transcriptions" and request["auth"] == "Bearer test-key"
    fields = request["fields"]
    assert fields["model"] == b"whisper-large-v3-turbo"  # Groq's first model is the default
    assert fields["language"] == b"ta" and fields["response_format"] == b"json"
    assert b"Karthi, Rflow" in fields["prompt"]
    assert _wav(fields["file"]) == (16_000, 32_000)  # resampled to 16 kHz: a third of the upload


def test_no_language_and_no_words_send_no_hints(fake):
    fake.engine("openai").transcribe(AUDIO, RATE)
    assert set(fake.requests[0]["fields"]) == {"model", "response_format", "file"}
    assert fake.requests[0]["fields"]["model"] == b"gpt-4o-mini-transcribe"


def test_gemini_gets_the_audio_inline_with_an_instruction(fake):
    engine = fake.engine("gemini", model="gemini-flash-latest", language="ta", path="/v1beta")
    engine.words = ["Karthi"]
    assert engine.transcribe(AUDIO, RATE) == "Hello from Gemini."  # its thinking is left out
    request = fake.requests[0]
    assert request["path"] == "/v1beta/models/gemini-flash-latest:generateContent"
    assert request["x-goog-api-key"] == "test-key" and request["auth"] is None
    body = request["json"]
    audio, instruction = body["contents"][0]["parts"]
    assert audio["inline_data"]["mime_type"] == "audio/wav"
    assert _wav(base64.b64decode(audio["inline_data"]["data"])) == (16_000, 32_000)
    assert "word for word" in instruction["text"] and "Tamil" in instruction["text"] and "Karthi" in instruction["text"]
    assert body["generationConfig"]["temperature"] == 0


def test_a_provider_error_is_typed_by_parakeet_and_said(fake):
    parakeet, loads = Parakeet(), []
    engine = fake.engine(fallback=lambda: loads.append(1) or parakeet)
    engine.words = ["Karthi"]
    fake.statuses = [401, 401]
    assert engine.transcribe(AUDIO, RATE) == "hello from parakeet"
    assert "HTTP 401 Invalid API key" in engine.last_error and engine.last_error.startswith("OpenAI")
    assert parakeet.words == ["Karthi"]  # Your words go along
    assert engine.transcribe(AUDIO, RATE) == "hello from parakeet" and loads == [1]  # loaded once
    assert engine.transcribe(AUDIO, RATE) == "Hello from OpenAI." and engine.last_error == ""  # the provider is back


def test_an_unreachable_provider_is_skipped_for_a_minute(monkeypatch):
    monkeypatch.setattr(cloud, "CONNECT_TIMEOUT", 0.3)  # Windows takes a while to refuse a connection
    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    parakeet = Parakeet()
    engine = CloudEngine("openai", "test-key", fallback=lambda: parakeet, url=f"http://127.0.0.1:{port}/v1")
    assert engine.transcribe(AUDIO, RATE) == "hello from parakeet" and "cannot reach 127.0.0.1" in engine.last_error
    connects = []
    monkeypatch.setattr(engine, "_connect", lambda: connects.append(1))
    t0 = time.perf_counter()
    assert engine.transcribe(AUDIO, RATE) == "hello from parakeet" and "a moment ago" in engine.last_error
    assert not connects and time.perf_counter() - t0 < 1.0  # no waiting on the network again


def test_a_slow_answer_gives_way_to_parakeet(fake, monkeypatch):
    monkeypatch.setattr(cloud, "ANSWER_TIMEOUT", 0.2)
    monkeypatch.setattr(cloud, "ANSWER_PER_SECOND", 0.0)
    fake.delay = 1.0
    engine = fake.engine(fallback=Parakeet)
    assert engine.transcribe(AUDIO, RATE) == "hello from parakeet" and "no answer in time" in engine.last_error


def test_without_a_fallback_the_failure_is_raised(fake):
    fake.statuses = [500]
    with pytest.raises(CloudError, match="OpenAI: HTTP 500"):
        fake.engine().transcribe(AUDIO, RATE)


def test_scoring_waits_out_a_rate_limit_but_dictation_does_not(fake, monkeypatch):
    monkeypatch.setattr(cloud, "RATE_LIMIT_WAITS", (0, 0))
    fake.statuses = [429, 429]
    assert fake.engine().transcribe(AUDIO, RATE) == "Hello from OpenAI." and len(fake.requests) == 3
    fake.statuses = [429]
    engine = fake.engine(fallback=Parakeet)
    assert engine.transcribe(AUDIO, RATE) == "hello from parakeet" and "429" in engine.last_error
    assert len(fake.requests) == 4  # not asked again: Parakeet typed at once


def test_the_connection_is_opened_while_the_user_speaks_and_kept(fake):
    engine = fake.engine()
    engine.prepare()
    for _ in range(100):
        if fake.connections:
            break
        time.sleep(0.01)
    engine.transcribe(AUDIO, RATE)
    engine.transcribe(AUDIO, RATE)
    assert fake.connections == 1 and len(fake.requests) == 2


def test_the_test_button_reports_the_time_and_never_falls_back(fake):
    engine = fake.engine(fallback=Parakeet)
    assert engine.check(AUDIO, RATE).startswith("gpt-4o-mini-transcribe answered in ")
    fake.statuses = [401]
    with pytest.raises(CloudError, match="Invalid API key"):
        engine.check(AUDIO, RATE)


def test_a_cloud_model_needs_a_key_which_never_shows():
    with pytest.raises(ValueError, match="needs an API key"):
        CloudEngine("openai", "")
    engine = CloudEngine("openai", "sk-secret")
    assert "sk-secret" not in repr(engine) and engine.title == "OpenAI gpt-4o-mini-transcribe"


def test_the_signature_follows_what_the_text_depends_on():
    engine = CloudEngine("openai", "sk-1")
    first = engine.signature
    engine.api_key = "sk-2"
    assert engine.signature == first  # the key doesn't change the text
    for change in (lambda: setattr(engine, "model", "whisper-1"), lambda: setattr(engine, "language", "ta"),
                   lambda: setattr(engine, "words", ["Karthi"])):
        before = engine.signature
        change()
        assert engine.signature != before


def test_the_catalog_uses_a_cloud_model_only_with_its_key():
    assert usable("openai") == "parakeet" and usable("groq", GatewayConfig()) == "parakeet"
    keys = GatewayConfig(provider="anthropic", api_key="ant", others={"groq": ("", "gsk")})
    assert usable("groq", keys) == "groq" and usable("openai", keys) == "parakeet"
    engine = load_engine("gemini", "ja", api_key="g-key")
    assert isinstance(engine, CloudEngine) and engine.model == "gemini-flash-lite-latest" and engine.language == "ja"


def test_an_own_server_needs_an_address_but_maybe_no_key(fake):
    with pytest.raises(ValueError, match="needs its address"):
        CloudEngine("server", "")
    engine = fake.engine("server", model="openai/whisper-large-v3-turbo", key="")
    assert engine.transcribe(AUDIO, RATE) == "Hello from OpenAI." and engine.title == "Your server openai/whisper-large-v3-turbo"
    request = fake.requests[0]
    assert request["path"] == "/v1/audio/transcriptions" and request["auth"] is None  # no key: no header
    assert request["fields"]["model"] == b"openai/whisper-large-v3-turbo"
    fake.engine("server", model="whisper-1", key="gw-key").transcribe(AUDIO, RATE)
    assert fake.requests[1]["auth"] == "Bearer gw-key"


def test_load_models_lists_the_speech_models_first(fake):
    assert fake.engine("server", key="gw-key").models() == ["gemini-3.5-transcribe", "whisper-1", "bge-m3",
                                                              "Qwen/Qwen3-30B"]
    assert fake.requests[0] == {"path": "/v1/models", "auth": "Bearer gw-key"}


def test_a_new_server_address_is_used_from_the_next_request(fake):
    other = FakeProvider()
    try:
        engine = fake.engine("server", model="whisper-1")
        engine.transcribe(AUDIO, RATE)
        before = engine.signature
        engine.set_url(other.address + "/v1/")
        engine.transcribe(AUDIO, RATE)
        assert len(fake.requests) == 1 and len(other.requests) == 1
        assert engine.signature != before  # another server may give other text
    finally:
        other.server.shutdown()


def test_the_catalog_uses_an_own_server_only_with_its_address():
    assert usable("server", GatewayConfig()) == "parakeet"
    keys = GatewayConfig().with_entry(SPEECH_SERVER, "http://10.0.0.5:8000/v1", "")
    assert usable("server", keys) == "server"
    engine = load_engine("server", "ta", model="whisper-1", url="http://10.0.0.5:8000/v1")
    assert engine.url == "http://10.0.0.5:8000/v1" and engine.language == "ta" and engine.api_key == ""
