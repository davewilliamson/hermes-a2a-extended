"""Bounded JSON-RPC over mutually authenticated TLS; no redirect handling."""

import hashlib
import hmac
import http.client
import json
import os
import re
import socket
import ssl
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

MAX_BODY = 65536
TOKEN = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")
EXTENSION = "urn:hermes:a2a-async:tasks-send:v1"


class Refusal(Exception):
    def __init__(self, message, code=-32000):
        super().__init__(message)
        self.code = code


def token(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("token file must be regular")
        data = handle.read(258)
        if len(data) == 258:
            raise ValueError("invalid token file")
        value = data.decode("ascii").strip()
    if not TOKEN.fullmatch(value):
        raise ValueError("invalid token file")
    return value


def redact(text, credentials=()):
    for value in credentials:
        text = text.replace(value, "[redacted]")
    return re.sub(r"(?i)\bbearer[ \t]+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", text)


def tls(config, server=False):
    if server:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(config["ca"])
    else:
        ctx = ssl.create_default_context(cafile=config["ca"])
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(config["cert"], config["key"])
    return ctx


def authenticate(sock, headers, peers):
    cert = sock.getpeercert(binary_form=True)
    values = headers.get_all("Authorization", [])
    if not cert or len(values) != 1 or not values[0].startswith("Bearer "):
        return None
    value = values[0][7:]
    if not TOKEN.fullmatch(value):
        return None
    fingerprint = hashlib.sha256(cert).hexdigest()
    matches = []
    for principal, entry in peers.items():
        try:
            if hmac.compare_digest(value, token(entry["token_file"])):
                matches.append((principal, entry["sha256"]))
        except (OSError, ValueError):
            continue
    if len(matches) == 1 and hmac.compare_digest(fingerprint, matches[0][1]):
        return matches[0][0]
    return None


class Server(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self, address, adapter, config):
        self.adapter = adapter
        self.peers = config["peers"]
        self.tls = tls(config, server=True)
        self.slots = threading.BoundedSemaphore(16)
        self.connections = set()
        self.connection_lock = threading.Lock()
        self.closing = False
        if not 1 <= len(self.peers) <= 64 or len({p["sha256"] for p in self.peers.values()}) != len(self.peers):
            raise ValueError("distinct per-peer certificate fingerprints are required")
        if any(not re.fullmatch(r"[0-9a-f]{64}", p["sha256"]) for p in self.peers.values()):
            raise ValueError("invalid peer certificate fingerprint")
        if len({token(p["token_file"]) for p in self.peers.values()}) != len(self.peers):
            raise ValueError("distinct per-peer tokens are required")
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        secured = None
        try:
            if not isinstance(request, socket.socket):
                return
            request.settimeout(2)
            with self.tls.wrap_socket(request, server_side=True, do_handshake_on_connect=False) as secured:
                with self.connection_lock:
                    if self.closing:
                        return
                    self.connections.add(secured)
                secured.do_handshake()
                self.finish_request(secured, client_address)
        except (OSError, ValueError):
            pass
        finally:
            with self.connection_lock:
                if secured is not None:
                    self.connections.discard(secured)
            request.close()
            self.slots.release()

    def close_connections(self):
        with self.connection_lock:
            self.closing = True
            for connection in self.connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


class Handler(BaseHTTPRequestHandler):
    # Keep the short error body and its headers together when the client has outstanding input.
    wbufsize = 8192
    server: Server

    def log_message(self, format, *args):
        pass

    def respond(self, status, payload):
        body = json.dumps(payload, ensure_ascii=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/.well-known/agent-card.json":
            return self.respond(404, {"error": "not found"})
        if authenticate(self.connection, self.headers, self.server.peers) is None:
            return self.respond(401, {"error": "unauthorized"})
        self.respond(200, self.server.adapter.card())

    def do_POST(self):
        if self.path != "/":
            return self.respond(404, {"error": "not found"})
        principal = authenticate(self.connection, self.headers, self.server.peers)
        if principal is None:
            return self.respond(401, {"error": "unauthorized"})
        response = {"jsonrpc": "2.0", "id": None}
        status = 200
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") is not None or len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,9}", lengths[0]):
                raise Refusal("invalid content length", -32600)
            size = int(lengths[0])
            if size > MAX_BODY:
                status = 413
                raise Refusal("body too large", -32600)
            try:
                req = json.loads(self.rfile.read(size))
            except (ValueError, RecursionError):
                raise Refusal("invalid JSON", -32700) from None
            if (not isinstance(req, dict) or req.get("jsonrpc") != "2.0"
                    or type(req.get("id")) not in (str, int) or not isinstance(req.get("method"), str)):
                raise Refusal("invalid request", -32600)
            response["id"] = req["id"]
            params = req.get("params", {})
            if not isinstance(params, dict):
                raise Refusal("invalid params", -32602)
            response["result"] = self.server.adapter.rpc(principal, req["method"], params)
        except Refusal as exc:
            response["error"] = {"code": exc.code, "message": str(exc)}
        self.respond(status, response)


def rpc(config, method, params):
    target = urlsplit(config["url"])
    if (target.scheme != "https" or not target.hostname or target.username is not None
            or target.query or target.fragment or target.path not in ("", "/")):
        raise ValueError("expected a configured https origin")
    conn = http.client.HTTPSConnection(target.hostname, target.port or 443, timeout=2, context=tls(config))
    try:
        bearer = token(config["token_file"])
        data = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        if len(data) > MAX_BODY:
            raise ValueError("body too large")
        conn.request("POST", "/", data, {"Authorization": "Bearer " + bearer, "Content-Type": "application/json"})
        response = conn.getresponse()
        if response.status in (401, 403, 413):
            raise Refusal("peer refused request")
        if response.status != 200:
            raise OSError("unexpected HTTP status")
        data = response.read(MAX_BODY + 1)
        if len(data) > MAX_BODY:
            raise OSError("oversized response")
        obj = json.loads(data)
        if not isinstance(obj, dict) or obj.get("jsonrpc") != "2.0" or obj.get("id") != 1:
            raise ValueError("invalid RPC response")
        if "error" in obj:
            if isinstance(obj["error"], dict) and obj["error"].get("code") == -32001:
                raise OSError("peer admission outcome unknown")
            raise Refusal("peer refused request")
        if not isinstance(obj.get("result"), dict):
            raise ValueError("invalid RPC result")
        return obj["result"]
    finally:
        conn.close()
