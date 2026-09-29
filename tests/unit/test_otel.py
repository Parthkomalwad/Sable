"""OpenTelemetry export (Phase 9, H4): payload shape, nesting, redaction,
off by default, failures swallowed, batching."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from sable.core import otel
from sable.core.config.schema import ShellConfig


class _Capture(BaseHTTPRequestHandler):
    bodies: list = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers["Content-Length"])
        type(self).bodies.append((self.path, dict(self.headers), json.loads(self.rfile.read(length))))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Capture.bodies = []
    httpd = HTTPServer(("127.0.0.1", 0), _Capture)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", _Capture.bodies
    httpd.shutdown()
    otel.shutdown()


def _spans(bodies):
    return [s for _, _, b in bodies for rs in b["resourceSpans"]
            for ss in rs["scopeSpans"] for s in ss["spans"]]


def test_off_by_default_starts_nothing():
    assert ShellConfig.defaults().otel == {"endpoint": None, "headers": {}, "service_name": "sable"}
    before = threading.active_count()
    assert otel.configure(ShellConfig.defaults().otel) is False
    with otel.span("goal", goal="x") as attrs:
        attrs["y"] = 1
    otel.record("model.call", 1, 2)
    assert not otel.enabled()
    assert threading.active_count() == before


def test_payload_shape_nesting_and_redaction(server):
    url, bodies = server
    otel.configure({"endpoint": url, "headers": {"x-team": "a"}, "service_name": "svc"},
                   redact=lambda t: t.replace("hunter2", "[REDACTED]"))
    with otel.span("goal", agent="orchestrator"):
        with otel.span("command", command="echo hunter2 " + "x" * 2000) as attrs:
            attrs.update(exit_code=0, cost_usd=0.5, ok=True)
    otel.shutdown()
    path, headers, body = bodies[0]
    assert path == "/v1/traces" and headers["x-team"] == "a"
    resource = body["resourceSpans"][0]["resource"]["attributes"]
    assert resource == [{"key": "service.name", "value": {"stringValue": "svc"}}]
    cmd, goal = _spans(bodies)
    assert re.fullmatch(r"[0-9a-f]{32}", goal["traceId"])
    assert re.fullmatch(r"[0-9a-f]{16}", goal["spanId"])
    assert "parentSpanId" not in goal
    assert cmd["traceId"] == goal["traceId"] and cmd["parentSpanId"] == goal["spanId"]
    for s in (cmd, goal):
        assert isinstance(s["startTimeUnixNano"], str) and s["startTimeUnixNano"].isdigit()
        assert int(s["endTimeUnixNano"]) >= int(s["startTimeUnixNano"])
    attrs = {a["key"]: a["value"] for a in cmd["attributes"]}
    assert attrs["exit_code"] == {"intValue": "0"}
    assert attrs["cost_usd"] == {"doubleValue": 0.5}
    assert attrs["ok"] == {"boolValue": True}
    text = attrs["command"]["stringValue"]
    assert "hunter2" not in text and "[REDACTED]" in text and len(text) == otel.MAX_ATTR


def test_separate_goals_are_separate_traces_and_errors_marked(server):
    url, bodies = server
    otel.configure({"endpoint": url})
    with otel.span("goal"):
        pass
    with pytest.raises(KeyError), otel.span("goal"):
        raise KeyError("x")
    otel.shutdown()
    a, b = _spans(bodies)
    assert a["traceId"] != b["traceId"]
    assert a["status"] == {"code": 1} and b["status"]["code"] == 2


def test_failing_endpoint_does_not_raise():
    otel.configure({"endpoint": "http://127.0.0.1:9"})
    with otel.span("goal"):
        otel.record("model.call", 1, 2, model="m")
    otel.shutdown(0.2)  # the post fails inside the thread; nothing escapes


def test_batches_of_at_most_100(server):
    url, bodies = server
    otel.configure({"endpoint": url})
    for _ in range(150):
        otel.record("tool.call", 1, 2)
    otel.shutdown()
    sizes = [len(_spans([b])) for b in bodies]
    assert sum(sizes) == 150 and max(sizes) <= otel.BATCH_SIZE


def test_secret_headers_resolved_or_dropped():
    lookup = {"secret:tok": "abc"}.get
    assert otel.resolve_headers({"auth": "$SECRET:tok", "gone": "$SECRET:nope", "p": "1"}, lookup) == {
        "auth": "abc", "p": "1"}


def test_model_call_span_carries_tokens_and_cost(server):
    from types import SimpleNamespace

    from sable.agents import runtime

    class Backend:
        async def complete(self, messages, system):
            return SimpleNamespace(model="m1", prompt_tokens=7, completion_tokens=3, cost_usd=0.01)

    url, bodies = server
    otel.configure({"endpoint": url})
    with otel.span("turn", turn=1):
        runtime.call_llm(Backend(), [], "sys")
    otel.shutdown()
    model, turn = _spans(bodies)
    assert model["name"] == "model.call" and model["parentSpanId"] == turn["spanId"]
    attrs = {a["key"]: a["value"] for a in model["attributes"]}
    assert attrs["tokens_in"] == {"intValue": "7"} and attrs["model"] == {"stringValue": "m1"}


def test_config_round_trip_and_validation():
    raw = ShellConfig.defaults().to_dict()
    raw["otel"] = {"endpoint": "http://c:4318", "headers": {"a": "b"}}
    cfg = ShellConfig.from_dict(raw)
    assert cfg.otel["endpoint"] == "http://c:4318" and cfg.otel["service_name"] == "sable"
    assert ShellConfig.from_dict(cfg.to_dict()).otel == cfg.otel
    raw["otel"] = {"endpoint": 5}
    with pytest.raises(ValueError):
        ShellConfig.from_dict(raw)
