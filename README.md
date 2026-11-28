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

## Async v2 (separate `a2a-async` plugin)

**REQUIRES DAVE APPROVAL** before enabling either half on any machine. Do not add a
listener to a live profile just to test this plugin. V1 `a2a_extended_send` is unchanged.
The async plugin adds `a2a_extended_dispatch` (admission only) and
`a2a_extended_status` (poll by task, context, or local submission ID). It does not stream.

Deploy `a2a-async/` as a separate plugin directory under the owning profile and enable it
only on that profile. A client-only profile can define `outbound` without enabling the
platform listener. Example with loopback and placeholder paths:

```yaml
approvals:
  mode: smart
security:
  approval:
    transport: builtin
platforms:
  a2a-async:
    enabled: true
    extra:
      host: 127.0.0.1
      port: 9443
      ca: /path/to/secrets/ca.crt
      cert: /path/to/secrets/server.crt
      key: /path/to/secrets/server.key
      peers:
        example-peer:
          sha256: <lowercase SHA-256 of the peer client certificate in DER format>
          token_file: /path/to/secrets/inbound.env
      outbound:
        example-peer:
          url: https://localhost:9443
          ca: /path/to/secrets/peer-ca.crt
          cert: /path/to/secrets/client.crt
          key: /path/to/secrets/client.key
          token_file: /path/to/secrets/outbound.env
```

Keep `HERMES_YOLO_MODE` unset for the entire process lifetime. `manual` is also
accepted. This is a **trusted-peer** interface: authorized peers can invoke the local
agent under its normal scope and tool policy. Do not grant credentials to untrusted
callers. The approval hook denies queued human-required approvals before the notifier;
it cannot change actions allowed by the operator's existing policy or make approvals
an isolation boundary. No approval prompt transport is exposed to callers.

`python3 scripts/gen_certs.py` generates short-lived loopback specimens under the
ignored `secrets/` directory (requires OpenSSL); establish independent per-peer certs
and directional opaque URL-safe bearer tokens at rollout. Store only paths in YAML.
Rotate or revoke a token by atomically replacing or withdrawing its file; cert/CA or
mapping changes require a restart. Do not copy a demo CA private key between peers.

Disconnects do not cancel admitted work. Submit again with the same `message_id` to
recover a lost acknowledgement while the server retains the receipt. Intentionally
repeating identical text requires a new explicit `message_id`. Cancellation stops
queued dispatch only; a running action may still finish. No peer can choose another
profile or use the inbound message as a gateway slash command.

To roll back, disable the `a2a-async` platform and plugin in its owning profile, then
restart that gateway. Optionally uninstall only `a2a-async`; do not touch v1. Running
work may already have taken effect. See [SPEC.md](SPEC.md) for limits and restart behavior.

## License

MIT. Targets A2A v1.0 (`PROTOCOL_VERSION = "1.0"`).
