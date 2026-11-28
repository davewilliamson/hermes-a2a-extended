# a2a-async

Mutually authenticated, asynchronous agent-to-agent (A2A) task admission and
status for [Hermes Agent](https://github.com/NousResearch/hermes-agent), built on
the [A2A protocol v1.0](https://a2a-protocol.org).

- **`kind: platform`** — an mTLS JSON-RPC listener that admits tasks from trusted
  peers, records a durable receipt, and lets callers poll for results.
- **Tools** — `a2a_extended_dispatch` and `a2a_extended_status`.

## Security & disclosure

This plugin **opens an inbound TLS listener** (a `ThreadingHTTPServer` that
requires mutually-authenticated TLS) and reads its **own CA certificate, server
certificate, private key, and per-peer client-certificate SHA-256 fingerprints
plus directional bearer-token files** from paths declared in `config.yaml`. It
makes **no outbound network calls except to the configured A2A peer**, and it
ships **no telemetry or analytics**.

**`a2a-async` requires operator approval before enabling it on any machine.** A
pre-approval hook (`pre_approval_request`) sits in front of every admitted task,
so callers cannot drive the agent as a shell, route to another profile, or pick
scope.

See [../docs/SECURITY.md](../docs/SECURITY.md) for the threat model,
[../docs/INSTALL.md](../docs/INSTALL.md) for deployment, and the
[project README](../README.md) for overview and configuration.
