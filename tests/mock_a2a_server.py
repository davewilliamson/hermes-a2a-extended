"""A2A v1.0 mock and adversarial SSE fixtures."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Event, Thread
from time import monotonic

DUMMY = "fixture-auth-marker-only"
STAMP = "2026-01-01T00:00:00+00:00"


def task(state="SUBMITTED"):
    return {"task": {"id": "task-fixture", "contextId": "ctx-fixture",
                     "status": {"state": "TASK_STATE_" + state, "timestamp": STAMP}}}


def status(state, text=None):
    value = {"state": "TASK_STATE_" + state, "timestamp": STAMP}
    if text:
        value["message"] = {"role": "ROLE_AGENT", "messageId": "fixture", "contextId": "ctx-fixture",
                            "parts": [{"text": text, "mediaType": "text/plain"}]}
    return {"statusUpdate": {"taskId": "task-fixture", "contextId": "ctx-fixture", "status": value}}


def artifact(text):
    return {"artifactUpdate": {"taskId": "task-fixture", "contextId": "ctx-fixture",
                               "artifact": {"artifactId": "fixture", "parts": [{"text": text, "mediaType": "text/plain"}]}}}


@contextmanager
def server(stream=None, *, endpoint=None, redirect=None, legacy=False, error=None, streaming=True):
    stop, disconnected = Event(), Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def json(self, payload, code=200):
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            requests.append(("GET", self.path, dict(self.headers), None))
            if redirect and self.path != "/card":
                self.send_response(302)
                self.send_header("Location", redirect)
                self.end_headers()
                return
            if legacy and self.path.endswith("agent-card.json"):
                self.json({}, 404)
                return
            self.json({"capabilities": {"streaming": streaming}, "supportedInterfaces": [
                {"protocolBinding": "HTTP+JSON", "protocolVersion": "1.0", "url": base + "/wrong"},
                {"protocolBinding": "JSONRPC", "protocolVersion": "0.3", "url": base + "/wrong"},
                {"protocolBinding": "JSONRPC", "protocolVersion": "1.0", "url": endpoint or base + "/rpc",
                 "tenant": "fixture-tenant"},
            ]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(("POST", self.path, dict(self.headers), body))
            req_id = body["id"]
            if error:
                if isinstance(error, int):
                    self.json({}, error)
                else:
                    self.json({"jsonrpc": "2.0", "id": req_id, "error": error})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.close_connection = True

            def raw(data):
                self.wfile.write(data)
                self.wfile.flush()

            def frame(payload):
                raw(("data: " + json.dumps({"jsonrpc": "2.0", "id": req_id, "result": payload},
                                           ensure_ascii=False) + "\n\n").encode())

            try:
                if stream:
                    stream(frame, raw, req_id, stop)
                else:
                    frame(task())
                    frame(status("WORKING"))
                    raw(b": keepalive\n\n")
                    frame(artifact("reply"))
                    frame(status("COMPLETED"))
                    raw(b": done\n\n")
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    base = f"http://127.0.0.1:{httpd.server_port}"
    thread = Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield base, requests, disconnected
    finally:
        stop.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def keepalive(raw, stop):
    end = monotonic() + 300
    while monotonic() < end and not stop.wait(0.02):
        raw(b": keepalive\n\n")
