"""Server-owned, bounded task lifecycle for the asynchronous A2A platform."""

import asyncio
import contextvars
import hashlib
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import TimeoutError
from datetime import datetime, timezone

from gateway.config import Platform
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, ProcessingOutcome, SendResult
from hermes_cli.config import FailedConfigRead, load_config_readonly
from hermes_constants import get_hermes_home, profile_name_for_home
from tools.approval import resolve_gateway_approval

from .journal import new_receipts
from .wire import EXTENSION, Refusal, Server, redact, token

LOG = logging.getLogger(__name__)
PLATFORM = "a2a-async"
ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELED", "TIMED_OUT"})


def now():
    return datetime.now(timezone.utc).isoformat()


def gate():
    if "HERMES_YOLO_MODE" in os.environ:
        raise Refusal("HERMES_YOLO_MODE must be unset")
    try:
        config = load_config_readonly()
        if isinstance(config, FailedConfigRead):
            raise ValueError("unreadable config")
        mode = config.get("approvals", {}).get("mode", "manual")
        transport = config.get("security", {}).get("approval", {}).get("transport", "builtin")
    except Exception:
        raise Refusal("approval configuration unreadable") from None
    if mode not in ("smart", "manual"):
        raise Refusal("approvals.mode must be smart or manual")
    if transport != "builtin":
        raise Refusal("security.approval.transport must be builtin")


def require_id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise Refusal("invalid identifier", -32602)
    return value


class Adapter(BasePlatformAdapter):
    draft_stream_is_message = False
    @property
    def authorization_is_upstream(self):
        return True

    deadline_s = 120
    queue_limit = 16
    concurrency = 4
    retain = 64

    def __init__(self, config, owned_sessions):
        super().__init__(config, Platform(PLATFORM))
        home = get_hermes_home()
        self.home = home.resolve()
        self.profile = profile_name_for_home(home) or "default"
        self.scope = contextvars.copy_context()
        self.server = self.thread = self.loop = None
        self.receipts = None
        self.sessions = owned_sessions
        self.rows = {}
        self.keys = {}
        self.active = set()
        self.open = False


    async def connect(self, *, is_reconnect=False):
        server = None
        try:
            self.scope.copy().run(gate)
            if self.open or self.active or self.scope.copy().run(get_hermes_home).resolve() != self.home:
                raise Refusal("adapter already open or owning profile changed")
            if not callable(getattr(self, "_event_session_key", None)):
                raise Refusal("Hermes session-key boundary unavailable")
            config = self.config.extra
            server = Server((config.get("host", "127.0.0.1"), config.get("port", 9443)), self, config)
            self.receipts = new_receipts(self.home / "a2a-async-receipts.sqlite3")
        except Refusal as exc:
            LOG.warning("A2A async startup refused: %s", exc)
            return False
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            if server is not None:
                server.server_close()
            LOG.warning("A2A async startup refused: invalid TLS, peer configuration or receipt journal")
            return False
        self.rows.clear()
        self.keys.clear()
        self.server = server
        self.loop = asyncio.get_running_loop()
        self.open = True
        self.thread = threading.Thread(target=server.serve_forever, daemon=True, name="a2a-async-http")
        self.thread.start()
        self.scope.copy().run(self._mark_connected)
        return True

    async def disconnect(self):
        self.open = False
        self.scope.copy().run(self._mark_disconnected)
        server, thread = self.server, self.thread
        if server is not None and thread is not None:
            await asyncio.to_thread(server.shutdown)
            server.close_connections()
            await asyncio.to_thread(server.server_close)
            await asyncio.to_thread(thread.join)
            self.server = self.thread = None
        for row in tuple(self.rows.values()):
            self.finish(row, "FAILED", reason="adapter disconnected")
        if self.receipts is not None:
            self.receipts.close()
            self.receipts = None

    def card(self):
        assert self.server is not None
        address = self.config.extra.get("public_url") or f"https://{self.server.server_address[0]}:{self.server.server_port}/"
        return {"name": "Hermes async", "description": "Asynchronous task admission", "version": "1.0.0",
                "supportedInterfaces": [{"url": address, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}],
                "capabilities": {"streaming": False, "pushNotifications": False,
                                 "extensions": [{"uri": EXTENSION, "required": False}]},
                "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
                "security": [{"bearer": []}],
                "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"], "skills": []}

    def rpc(self, principal, method, params):
        # Immutable snapshots cross the HTTP/loop boundary, never live task records.
        assert self.loop is not None
        future = self.scope.copy().run(asyncio.run_coroutine_threadsafe, self.request(principal, method, params), self.loop)
        try:
            return future.result(timeout=1.5)
        except TimeoutError:
            # Do not cancel: the caller may have lost an acknowledgement, not admission.
            raise Refusal("admission outcome unknown", -32001) from None

    async def request(self, principal, method, params):
        if not self.open or get_hermes_home().resolve() != self.home:
            raise Refusal("adapter not serving this profile")
        if method == "tasks/send":
            gate()
            return self.admit(principal, params)
        if method == "tasks/list":
            context = require_id(params["contextId"]) if "contextId" in params else None
            message = require_id(params["messageId"]) if "messageId" in params else None
            matches = [row for row in reversed(tuple(self.rows.values())) if row["principal"] == principal
                       and (context is None or row["context_id"] == context)
                       and (message is None or row["message_id"] == message)]
            offset = params.get("offset", 0)
            if type(offset) is not int or offset < 0:
                raise Refusal("invalid list offset", -32602)
            artifacts = params.get("includeArtifacts") is True
            limit = 1 if artifacts else 32
            return {"tasks": [self.view(row, artifacts) for row in matches[offset:offset + limit]],
                    **({"nextOffset": offset + limit} if offset + limit < len(matches) else {})}
        if method not in {"tasks/get", "tasks/subscribe", "tasks/cancel"}:
            raise Refusal("unknown method", -32601)
        row = self.rows.get(require_id(params.get("id")))
        if row is None or row["principal"] != principal:
            return {"state": "unknown"}
        if method == "tasks/cancel":
            if row["state"] in TERMINAL:
                raise Refusal("not-cancelable")
            if row["state"] == "SUBMITTED":
                self.finish(row, "CANCELED", reason="canceled before dispatch")
                self.pump()
            else:
                row["state"] = "CANCEL_REQUESTED"
            return {"cancel": "observation_only"}
        if method == "tasks/subscribe" and row["state"] not in TERMINAL:
            raise Refusal("task is not terminal; poll tasks/get")
        return self.view(row, params.get("includeArtifacts", method == "tasks/subscribe") is True)

    def secrets(self):
        entries = (*self.config.extra.get("peers", {}).values(), *self.config.extra.get("outbound", {}).values())
        values: set[str] = set()
        for entry in entries:
            try:
                values.add(token(entry["token_file"]))
            except (OSError, ValueError, KeyError):
                pass
        return values

    def admit(self, principal, params):
        if "contextId" in params:
            raise Refusal("tasks/send only admits a new context", -32602)
        message_id = require_id(params.get("messageId"))
        text = params.get("message")
        if not isinstance(text, str) or not text.strip() or len(text) > 8192:
            raise Refusal("invalid message", -32602)
        try:
            digest = hashlib.sha256(text.encode()).digest()
        except UnicodeError:
            raise Refusal("invalid message encoding", -32602) from None
        key = (principal, message_id)
        if (existing := self.keys.get(key)) is not None:
            if existing["digest"] != digest:
                raise Refusal("messageId already used with different payload")
            return self.ack(existing)
        if sum(r["state"] == "SUBMITTED" for r in self.rows.values()) >= self.queue_limit:
            raise Refusal("admission queue full")
        task_id = uuid.uuid4().hex
        context_id = uuid.uuid4().hex
        source = self.own_source(principal, context_id, task_id)
        event = MessageEvent(text="[Authenticated A2A task]\n" + redact(text, self.secrets()),
                             message_type=MessageType.TEXT, source=source, message_id=task_id,
                             allow_gateway_control=False)
        row = {"task_id": task_id, "context_id": context_id, "received_at": now(), "principal": principal,
               "message_id": message_id, "digest": digest, "event": event, "state": "SUBMITTED",
               "working_at": None, "completed_at": None, "started": time.monotonic()}
        try:
            assert self.receipts is not None
            with self.receipts:
                self.receipts.execute("INSERT INTO receipt VALUES (?,?,?)", (task_id, context_id, row["received_at"]))
        except sqlite3.Error:
            raise Refusal("receipt journal unavailable") from None
        self.rows[task_id] = self.keys[key] = row
        assert self.loop is not None
        row["timer"] = self.loop.call_later(self.deadline_s, self.expire, row)
        self.loop.call_soon(self.pump)
        LOG.info("A2A async admitted task")
        return self.ack(row)

    def own_source(self, principal, context_id, task_id):
        source = self.build_source(chat_id=context_id, chat_type="dm", user_id=principal, message_id=task_id)
        if getattr(source, "profile_route_rejected", True) or (source.profile or self.profile) != self.profile:
            raise Refusal("cross-profile route refused")
        return source

    @staticmethod
    def ack(row):
        return {k: row[k] for k in ("task_id", "context_id", "received_at")}

    def view(self, row, artifacts):
        state = row["state"]
        wire_state = {"TIMED_OUT": "FAILED", "CANCEL_REQUESTED": "WORKING"}.get(state, state)
        result = {**self.ack(row), **{k: row[k] for k in ("state", "working_at", "completed_at")},
                  "id": row["task_id"], "contextId": row["context_id"],
                  "status": {"state": f"TASK_STATE_{wire_state}",
                             "timestamp": row["completed_at"] or row["working_at"] or row["received_at"]},
                  "elapsed_s": row.get("elapsed_s", time.monotonic() - row["started"])}
        if "reason" in row:
            result["reason"] = row["reason"]
        if artifacts and "reply" in row:
            result["reply"] = row["reply"]
            result["artifacts"] = [{"artifactId": row["task_id"], "parts": [{"text": row["reply"]}]}]
        return result

    def pump(self):
        if not self.open:
            return
        assert self.loop is not None
        for row in tuple(self.rows.values()):
            if len(self.active) >= self.concurrency:
                break
            if row["state"] == "SUBMITTED":
                self.active.add(row["task_id"])
                row["state"] = "WORKING"
                row["working_at"] = now()
                self.loop.create_task(self.dispatch(row))

    async def dispatch(self, row):
        event = row.get("event")
        try:
            gate()
            if not self.open or get_hermes_home().resolve() != self.home:
                raise Refusal("adapter not serving this profile")
            if event is None or row["state"] != "WORKING":
                self.finish(row, "CANCELED", reason="canceled before dispatch")
            else:
                event.source = self.own_source(row["principal"], row["context_id"], row["task_id"])
                # Core calls the same method at admission. Do not infer ownership from a key suffix.
                session_key = self._event_session_key(event)
                if (getattr(event.source, "profile_route_rejected", True)
                        or (event.source.profile or self.profile) != self.profile):
                    raise Refusal("cross-profile route refused")
                row["session_key"] = session_key
                with self.sessions[0]:
                    self.sessions[1].add(session_key)
                await self.handle_message(event)
                if getattr(event, "_gateway_accepted", False):
                    return
                self.finish(row, "FAILED", reason="gateway admission refused")
        except asyncio.CancelledError:
            self.finish(row, "FAILED", reason="dispatch interrupted")
            self.release(row)
            raise
        except Exception:
            self.finish(row, "FAILED", reason="dispatch failed")
        self.release(row)

    def expire(self, row):
        self.finish(row, "TIMED_OUT", reason="execution deadline elapsed; running work may continue")
        self.pump()

    def release(self, row):
        self.active.discard(row["task_id"])
        with self.sessions[0]:
            self.sessions[1].discard(row.get("session_key"))
        self.trim()
        self.pump()

    def trim(self):
        completed = [r for r in self.rows.values() if r["state"] in TERMINAL and r["task_id"] not in self.active]
        for old in completed[:-self.retain]:
            if self.receipts is not None:
                try:
                    with self.receipts:
                        self.receipts.execute("DELETE FROM receipt WHERE task=?", (old["task_id"],))
                except sqlite3.Error:
                    LOG.warning("A2A async receipt journal eviction failed")
            self.rows.pop(old["task_id"], None)
            self.keys.pop((old["principal"], old["message_id"]), None)

    def finish(self, row, state, text=None, reason=None):
        if row["state"] in TERMINAL:
            return
        if text:
            row["reply"] = redact(text, self.secrets())[:2048]
        if reason:
            row["reason"] = reason
        row.update(state=state, completed_at=now(), elapsed_s=time.monotonic() - row["started"])
        row["timer"].cancel()
        row.pop("event", None)
        self.trim()
        LOG.info("A2A async task finished state=%s", state)

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        if (metadata or {}).get("notify") and not (metadata or {}).get("is_approval_prompt"):
            # The chat id is a fresh server-issued correlation key for exactly one task.
            for row in self.rows.values():
                if row["context_id"] == chat_id and row["task_id"] in self.active:
                    self.finish(row, "COMPLETED", content, reason=None if content else "empty reply")
                    break
        return SendResult(success=True)

    async def on_processing_complete(self, event, outcome):
        task_id = str(getattr(event, "message_id", "") or "")
        row = self.rows.get(task_id)
        if row is None:
            return
        streamed = getattr(event, "_streamed_final_response", "")
        if outcome == ProcessingOutcome.SUCCESS:
            text = streamed if isinstance(streamed, str) else ""
            self.finish(row, "COMPLETED", text, reason=None if text else "empty reply")
        elif outcome == ProcessingOutcome.CANCELLED:
            self.finish(row, "CANCELED", reason="gateway canceled processing")
        else:
            self.finish(row, "FAILED", reason="gateway processing failed")
        self.release(row)

    async def get_chat_info(self, chat_id):
        return {"name": "A2A async", "type": "dm"}


def approval_hook():
    sessions = (threading.Lock(), set())

    def deny(session_key="", **kwargs):
        if not isinstance(session_key, str):
            return
        with sessions[0]:
            if session_key in sessions[1]:
                resolve_gateway_approval(session_key, "deny", resolve_all=True)

    return sessions, deny
