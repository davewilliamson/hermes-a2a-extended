"""Hermes entry point for the bounded A2A client."""

import json

from .client import send


def _handle(args, **kwargs):
    return json.dumps(send(args.get("peer"), args.get("message"), args.get("context_id"),
                           args.get("liveness_timeout_s", 120)))


def register(ctx):
    name = "a2a_extended_send"
    ctx.register_tool(
        name=name,
        toolset="a2a",
        handler=_handle,
        schema={
            "name": name,
            "description": (
                "Send a task to a configured A2A peer. WORKING means scheduled, "
                "not running. A local timeout does not cancel the remote task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "peer": {"type": "string"},
                    "message": {"type": "string"},
                    "context_id": {"type": "string"},
                    "liveness_timeout_s": {"type": "number", "exclusiveMinimum": 0, "default": 120},
                },
                "required": ["peer", "message"],
                "additionalProperties": False,
            },
        },
    )
