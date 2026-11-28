"""Hermes async A2A plugin registration and durable client correlation."""

import hashlib
import http.client
import json
import sqlite3
from contextlib import closing

from hermes_cli.config import load_config_readonly
from hermes_constants import get_hermes_home

from .adapter import Adapter, PLATFORM, approval_hook, require_id
from .journal import private_db
from .wire import Refusal, redact, rpc, token


def outbound():
    return load_config_readonly().get("platforms", {}).get(PLATFORM, {}).get("extra", {}).get("outbound", {})


def journal():
    path = get_hermes_home() / "a2a-async.sqlite3"
    db = private_db(path)
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS submission (sid TEXT PRIMARY KEY, peer TEXT, mid TEXT, digest TEXT, "
                   "task TEXT, context TEXT, received TEXT)")
    return db


def remember(db, sid, ack):
    task = require_id(ack.get("task_id"))
    context = require_id(ack.get("context_id"))
    received = ack.get("received_at")
    if not isinstance(received, str) or len(received) > 64:
        raise ValueError("invalid receipt timestamp")
    with db:
        old = db.execute("SELECT task, context, received FROM submission WHERE sid=?", (sid,)).fetchone()
        if old is None or (old[0] is not None and old != (task, context, received)):
            raise Refusal("peer changed admission receipt")
        db.execute("UPDATE submission SET task=?, context=?, received=? WHERE sid=?", (task, context, received, sid))
    return {"task_id": task, "context_id": context, "received_at": received}


def dispatch(args, **kwargs):
    sid = None
    try:
        peer = require_id(args.get("peer"))
        config = outbound().get(peer)
        if config is None:
            raise Refusal("peer not configured")
        raw = args.get("message")
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 8192:
            raise Refusal("invalid message")
        credentials = {token(entry["token_file"]) for entry in outbound().values()}
        message = redact(raw, credentials)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        mid = require_id(args["message_id"]) if "message_id" in args else digest
        sid = hashlib.sha256((peer + ":" + mid).encode()).hexdigest()
        with closing(journal()) as db:
            with db:
                db.execute("INSERT OR IGNORE INTO submission (sid,peer,mid,digest) VALUES (?,?,?,?)",
                           (sid, peer, mid, digest))
            saved, task, context, received = db.execute(
                "SELECT digest,task,context,received FROM submission WHERE sid=?", (sid,)).fetchone()
            if saved != digest:
                raise Refusal("message_id already used with different payload")
            if task:
                return json.dumps(dict(outcome="admitted", task_id=task, context_id=context,
                                       received_at=received, submission_id=sid))
            for attempt in range(2):
                try:
                    ack = rpc(config, "tasks/send", {"messageId": mid, "message": message})
                    return json.dumps(dict(outcome="admitted", submission_id=sid, **remember(db, sid, ack)))
                except (OSError, ValueError, http.client.HTTPException):
                    if attempt:
                        return json.dumps(dict(outcome="pending", submission_id=sid))
    except Refusal:
        return json.dumps(dict(outcome="refused", submission_id=sid))
    except (OSError, ValueError, KeyError, sqlite3.Error):
        return json.dumps(dict(outcome="transport_error", submission_id=sid))


def status(args, **kwargs):
    try:
        selectors = [(key, require_id(args[key])) for key in ("submission_id", "task_id", "context_id") if key in args]
        if len(selectors) != 1:
            raise Refusal("provide exactly one selector")
        key, value = selectors[0]
        column = {"submission_id": "sid", "task_id": "task", "context_id": "context"}[key]
        with closing(journal()) as db:
            rows = db.execute(f"SELECT sid,peer,mid,task FROM submission WHERE {column}=? LIMIT 2", (value,)).fetchall()
            if len(rows) != 1:
                return json.dumps({"state": "unknown"})
            sid, peer, mid, task = rows[0]
            config = outbound().get(peer)
            if config is None:
                return json.dumps({"state": "unknown"})
            if task:
                result = rpc(config, "tasks/get", {"id": task, "includeArtifacts": True})
            else:
                matches = rpc(config, "tasks/list", {"messageId": mid, "includeArtifacts": True}).get("tasks", [])
                if not matches:
                    return json.dumps({"state": "pending", "submission_id": sid})
                result = matches[0]
                remember(db, sid, result)
            # Never echo arbitrary RPC fields or unredacted peer errors into the tool output.
            output = {k: result[k] for k in ("state", "received_at", "working_at", "completed_at", "elapsed_s") if k in result}
            if isinstance(result.get("reply"), str):
                output["reply"] = redact(result["reply"], [token(config["token_file"])])
            return json.dumps(output)
    except (Refusal, OSError, ValueError, KeyError, TypeError, sqlite3.Error, http.client.HTTPException):
        return json.dumps({"state": "unavailable"})


def register(ctx):
    sessions, deny = approval_hook()
    ctx.register_hook("pre_approval_request", deny)
    ctx.register_platform(PLATFORM, "A2A async", lambda config: Adapter(config, sessions), lambda: True)
    for name, desc, handler, args, required in (
        ("a2a_extended_dispatch", "Submit to a configured A2A peer without waiting for execution.",
         dispatch, ("peer", "message", "message_id"), ("peer", "message")),
        ("a2a_extended_status", "Read an async A2A task by exactly one local selector.",
         status, ("submission_id", "task_id", "context_id"), ()),
    ):
        ctx.register_tool(name=name, toolset=PLATFORM, handler=handler,
                          schema={"name": name, "description": desc, "parameters": {"type": "object",
                                  "properties": {k: {"type": "string"} for k in args},
                                  "required": list(required), "additionalProperties": False}})
