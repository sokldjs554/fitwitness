"""Export from a real finished run to a receiver, and the rules around when it happens."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import pytest
from test_workflow import env, request  # noqa: F401
from fitwitness.runtime import otlp

proto = pytest.importorskip("opentelemetry.proto.collector.trace.v1.trace_service_pb2")
from google.protobuf import json_format  # noqa: E402


class Receiver:
    def __init__(self, status=200):
        self.bodies, self.headers = [], []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                outer.bodies.append(json.loads(self.rfile.read(length)))
                outer.headers.append(dict(self.headers))
                self.send_response(status)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/traces"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def test_a_finished_run_is_posted_once_as_a_valid_request(env, monkeypatch):
    from fitwitness.agents.graph import execute_run

    r, j, s = env
    run = j.enqueue(s, request(), "otlp-1")
    execute_run(r, s, run.id)
    receiver = Receiver()
    try:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-team=fitwitness")
        assert otlp.export_run(j, s, run.id, receiver.url) is True
    finally:
        receiver.close()
    assert len(receiver.bodies) == 1 and receiver.headers[0]["x-team"] == "fitwitness"
    parsed = json_format.Parse(json.dumps(receiver.bodies[0]), proto.ExportTraceServiceRequest())
    names = [sp.name for sp in parsed.resource_spans[0].scope_spans[0].spans]
    assert names[0] == "run" and "intent" in names and "retrieved" in names and "completed" in names


def test_a_paused_run_is_not_exported_until_it_ends(env):
    from fitwitness.agents.graph import execute_run

    r, j, s = env
    run = j.enqueue(s, request().model_copy(update={"review": "on_unknown"}), "otlp-2")
    execute_run(r, s, run.id)
    receiver = Receiver()
    try:
        if j.get(s, run.id).state == "waiting_input":
            assert otlp.export_run(j, s, run.id, receiver.url) is False and receiver.bodies == []
            assert otlp.export_run(j, s, run.id, receiver.url, include_open=True) is True
        else:
            pytest.skip("this corpus slice produced no unknown evidence")
    finally:
        receiver.close()


def test_an_unreachable_or_refusing_backend_never_fails_the_caller(env):
    from fitwitness.agents.graph import execute_run

    r, j, s = env
    run = j.enqueue(s, request(), "otlp-3")
    execute_run(r, s, run.id)
    assert otlp.export_run(j, s, run.id, "http://127.0.0.1:9/v1/traces") is False
    refusing = Receiver(status=503)
    try:
        assert otlp.export_run(j, s, run.id, refusing.url) is False
    finally:
        refusing.close()
    assert otlp.export_run(j, s, run.id, None) is False  # not configured: nothing happens
