"""Text cleanup against a fake gateway on this machine (no company server involved): the answer is used when it's good,
and the text is typed as heard, quickly, whenever the gateway can't help."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sst import gateway
from sst.gateway import GatewayConfig, GatewayError, Polisher, other_model, plausible

HEARD = "so we merge the five pr's today"


class FakeGateway:
    """Answers like the gateway; what it does depends on the model asked for."""

    def __init__(self):
        self.requests, self.connections = [], 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # keep-alive, like the real gateway

            def setup(self):
                fake.connections += 1
                super().setup()

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"auth": self.headers.get("Authorization"), **body})
                text = body["messages"][-1]["content"]
                model = body["model"]
                if model.startswith("error"):
                    return self._reply(500, {"error": {"message": "backend exploded"}})
                if model == "slow":
                    time.sleep(1.0)
                answer = {"long": text + " and then some more words " * 10,
                          "think": "<think>let me see</think> So we merge the five PRs today.",
                          "quoted": '"So we merge the five PRs today."'}.get(model, "So we merge the five PRs today.")
                self._reply(200, {"choices": [{"message": {"role": "assistant", "content": answer}}]})

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
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def polisher(self, model="good", fallback=None, vocabulary=()):
        return Polisher(GatewayConfig(self.url, "test-key"), model, list(vocabulary), fallback)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(gateway, "ANSWER_TIMEOUT", 0.4)
    monkeypatch.setattr(gateway, "ANSWER_PER_WORD", 0.0)
    monkeypatch.setattr(gateway, "CONNECT_TIMEOUT", 0.3)
    server = FakeGateway()
    yield server
    server.server.shutdown()


def test_a_good_answer_is_used(fake):
    p = fake.polisher(vocabulary=["PRs", "GitHub"])
    assert p.polish(HEARD) == "So we merge the five PRs today." and p.last_error == ""
    sent = fake.requests[0]
    assert sent["auth"] == "Bearer test-key" and sent["model"] == "good"
    assert "PRs, GitHub" in sent["messages"][0]["content"]  # the user's words reach the model
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}


def test_the_connection_is_kept_open_between_dictations(fake):
    p = fake.polisher()
    p.polish(HEARD)
    p.polish(HEARD)
    assert fake.connections == 1


def test_prepare_connects_ahead_of_time(fake):
    p = fake.polisher()
    p.prepare()
    time.sleep(0.3)
    assert fake.connections == 1
    p.polish(HEARD)
    assert fake.connections == 1  # the prepared connection was used


def test_the_other_model_is_tried_when_the_chosen_one_fails(fake):
    p = fake.polisher(model="error", fallback="good")
    assert p.polish(HEARD) == "So we merge the five PRs today."
    assert [r["model"] for r in fake.requests] == ["error", "good"]


def test_both_models_failing_gives_the_heard_text(fake):
    p = fake.polisher(model="error-1", fallback="error-2")
    assert p.polish(HEARD) == HEARD and "HTTP 500" in p.last_error
    assert [r["model"] for r in fake.requests] == ["error-1", "error-2"]


def test_a_slow_answer_gives_the_heard_text_in_time(fake):
    p = fake.polisher(model="slow", fallback="good")
    t0 = time.perf_counter()
    assert p.polish(HEARD) == HEARD
    assert time.perf_counter() - t0 < 0.9  # waited for the answer timeout (0.4 s), not the 1 s answer
    assert "took too long" in p.last_error
    assert [r["model"] for r in fake.requests] == ["slow"]  # no second wait on the other model


def test_an_unreachable_gateway_is_skipped_for_a_while(monkeypatch):
    monkeypatch.setattr(gateway, "CONNECT_TIMEOUT", 0.3)
    p = Polisher(GatewayConfig("http://127.0.0.1:9/v1", "k"), "good")  # nothing listens there
    assert p.polish(HEARD) == HEARD and "could not be reached" in p.last_error
    t0 = time.perf_counter()
    assert p.polish(HEARD) == HEARD  # skipped at once, no new connection attempts
    assert time.perf_counter() - t0 < 0.05


@pytest.mark.parametrize("model", ["long"])
def test_an_implausible_answer_is_not_used(fake, model):
    p = fake.polisher(model=model)
    assert p.polish(HEARD) == HEARD and "unusual" in p.last_error


@pytest.mark.parametrize("model", ["think", "quoted"])
def test_thinking_tags_and_quotes_are_removed(fake, model):
    assert fake.polisher(model=model).polish(HEARD) == "So we merge the five PRs today."


def test_without_a_key_nothing_is_sent(fake):
    p = Polisher(GatewayConfig(fake.url, ""), "good")
    assert p.polish(HEARD) == HEARD and fake.requests == []


def test_check_reports_the_answer_or_a_readable_reason(fake):
    assert "answered in" in fake.polisher().check()
    with pytest.raises(GatewayError, match="HTTP 500"):
        fake.polisher(model="error").check()


def test_the_key_never_shows_in_logs_and_config_round_trips(tmp_path):
    config = GatewayConfig("https://gw.example/v1", "secret-key-123")
    assert "secret-key-123" not in repr(config)
    config.save(tmp_path / "gateway.json")
    assert GatewayConfig.load(tmp_path / "gateway.json") == config
    assert GatewayConfig.load(tmp_path / "missing.json") == GatewayConfig()


def test_model_choices_fall_back_to_each_other():
    (first, _), (second, _) = gateway.MODELS
    assert other_model(first) == second and other_model(second) == first and other_model("unknown") is None


@pytest.mark.parametrize("cleaned, ok", [("So we merge the five PRs today.", True), ("", False), ("Yes.", False),
                                         (HEARD + " extra words " * 10, False)])
def test_plausible(cleaned, ok):
    assert plausible(HEARD, cleaned) is ok


def test_removing_fillers_from_a_short_dictation_is_plausible():
    assert plausible("um uh yes okay", "Yes, okay.")
