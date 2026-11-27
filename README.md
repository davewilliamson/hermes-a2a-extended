# hermes-a2a-extended

A stdlib-only [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that adds a
bounded, liveness-aware streaming A2A client tool.

## What it does

`a2a_extended_send` sends a task to a configured A2A peer over the **streaming** path
(`SendStreamingMessage` / SSE) rather than the blocking `SendMessage` path. It returns:

- retrospective dispatch timestamps (`SUBMITTED` → `WORKING`) plus `task_id` / `context_id`;
- a bounded early return (per-phase deadlines) instead of the ~30-minute blind block that a
  wedged peer otherwise causes.

## Honest scope

This tool detects **transport death** and bounds **total wall-clock**. It does **not** prove that
the remote worker actually started or completed — that is unprovable from the public A2A streaming
surface, where keepalives flow for the full reply window regardless of worker progress.

## Config

Peers are read from the standard Hermes `a2a_agents` config map (no values live in this repo):

```yaml
a2a_agents:
  some-peer:
    url: https://peer.example
    auth: {type: bearer, token: "..."}
    timeout: 120
    capabilities: [code]
```

## Security model

- The peer token is read in-memory only (`a2a_agents.<peer>.auth.token`), used to build an
  `Authorization` header, and forwarded **only** to that peer's configured origin.
- Requests go through `hermes_cli.urllib_security.open_credentialed_url`, which strips credential
  headers across cross-origin redirects.
- Tokens are never logged, persisted, passed to the model, or returned in tool output.

## License

MIT. Targets A2A v1.0 (`PROTOCOL_VERSION = "1.0"`).
