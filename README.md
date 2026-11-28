# hermes-a2a-extended

Two stdlib-only [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugins that add a
**bounded transport** for agent-to-agent (A2A) work on top of the
[A2A protocol (v1.0)](https://a2a-protocol.org), with mutually-authenticated TLS and an
origin-trust security model:

| Plugin | Kind | What it provides |
|--------|------|------------------|
| [`a2a-extended/`](a2a-extended/) | general | `a2a_extended_send` — a **streaming, liveness-aware A2A client** with a bounded early return |
| [`a2a-async/`](a2a-async/) | platform + client | an **mTLS JSON-RPC listener** plus `a2a_extended_dispatch` / `a2a_extended_status` for admitting and polling async tasks |

They are intentionally independent: `a2a-extended` works with any standard A2A peer today;
`a2a-async` is a hardened trusted-peer transport for a fleet where callers submit work,
receive a receipt, and poll for the result.

> **`a2a-async` requires operator approval before enabling it on any machine.** See
> [Security](docs/SECURITY.md) and the install steps below.

---

## The problem it solves

The bundled Hermes A2A tools (`a2a_send` / the non-streaming client) call a peer over the
**blocking** `SendMessage` path. If the peer wedges, the caller blocks for the peer's full reply
timeout (~30 minutes) with no signal until the reply arrives or the socket dies.

`a2a-extended` fixes that with a **streaming** client that treats the transport as the only
observable truth and returns promptly:

- **Bounded wall-clock** — three strictly-monotonic deadlines (transport-dead, acknowledgement,
  overall-cap) mean a wedged peer returns in **seconds to ~120 s** instead of blocking for half
  an hour.
- **Retrospective dispatch facts** — `SUBMITTED` → `WORKING` timestamps plus `task_id` /
  `context_id`, so you know *when* the peer acknowledged, even though you can't know *whether*
  it finished.

`a2a-async` solves the **opposite side** of the same problem: it is a dispatch surface on the
*server* side. Peers authenticate with client certificates **and** directional bearer tokens,
submit a task, receive a durable receipt, and poll — without the caller being able to route to a
different profile, pick scope, or drive the agent as a shell.

### Honest scope

Neither plugin is an *execution oracle*. On the public A2A streaming surface, `WORKING` is set
after the handler is *scheduled* (not when execution begins), and keepalives flow for the whole
reply window regardless of worker progress. So:

- `a2a_extended_send` detects **transport death** and bounds **total wall-clock**; it cannot prove
  the remote worker started or completed.
- `a2a-async`'s timeout is an **observation deadline**, not a guarantee that a running side effect
  stopped. A running action may still finish after cancellation or disconnect.

---

## Repository layout

```
├── a2a-extended/           # v1: streaming/liveness-aware A2A client
│   ├── plugin.yaml         #   kind=general, toolset=a2a, stdlib-only
│   ├── __init__.py         #   registers a2a_extended_send
│   ├── client.py           #   card fetch + interface select + JSON-RPC/SSE (stdlib urllib)
│   ├── liveness.py         #   per-phase monotonic-deadline state machine (pure)
│   └── policy.py           #   origin-trust + TLS/redirect rules + credential seam
├── a2a-async/              # v2: mTLS JSON-RPC platform + async client tools
│   ├── plugin.yaml         #   kind=platform, hook=pre_approval_request
│   ├── __init__.py         #   registers a2a_extended_dispatch / a2a_extended_status
│   ├── adapter.py          #   platform life-cycle, gate(), config loading
│   ├── wire.py             #   mTLS JSON-RPC server (tasks/send|get|list|cancel|subscribe)
│   └── journal.py          #   receipt / idempotency journal (SQLite)
├── scripts/gen_certs.py    #   generate short-lived loopback test certificates
├── tests/                  #   72 tests: unit + integration against a mock A2A server
├── docs/                   #   install, configuration, architecture, security, testing, troubleshooting
├── examples/               #   worked config + AGENTS.md / SOUL.md / USER.md examples
├── SPEC.md                 #   the canonical wire/behaviour specification
└── .github/workflows/ci.yml #  test matrix (ubuntu + macOS) + gitleaks
```

---

## Quick start

### v1 streaming client (drop-in for any A2A peer)

```yaml
# ~/.hermes/config.yaml
a2a_agents:
  some-peer:
    url: https://peer.example
    auth: {type: bearer, token: "..."}
    timeout: 120
    capabilities: [code]
```

Then, in a session, ask the agent to send work to `some-peer`; the `a2a_extended_send` tool is
used automatically when the toolset is enabled.

### v2 mTLS listener (trusted-peer fleet transport)

```bash
python3 scripts/gen_certs.py          # writes loopback CA + server + client certs to secrets/
```

```yaml
# ~/.hermes/config.yaml
approvals: {mode: smart}
security: {approval: {transport: builtin}}
platforms:
  a2a-async:
    enabled: true
    extra:
      host: 127.0.0.1
      port: 9443
      ca:   /path/to/secrets/ca.crt
      cert: /path/to/secrets/server.crt
      key:  /path/to/secrets/server.key
      peers:
        example-peer:
          sha256: "<sha256 of the peer's client cert, DER>"
          token_file: /path/to/secrets/inbound.env
      outbound:
        example-peer:
          url: https://localhost:9443
          ca:   /path/to/secrets/ca.crt
          cert: /path/to/secrets/client.crt
          key:  /path/to/secrets/client.key
          token_file: /path/to/secrets/outbound.env
```

Then restart that profile's gateway. Full steps, rollback, and client-only mode are in
[Installation](docs/INSTALL.md).

---

## Documentation

- [Installation](docs/INSTALL.md) — prerequisites, v1/v2 deployment, certificate generation,
  gateway restart, rollback.
- [Configuration](docs/CONFIGURATION.md) — every key for both plugins, defaults, and validation
  rules.
- [Architecture](docs/ARCHITECTURE.md) — data flow, the liveness state machine, the mTLS server,
  idempotency and the receipt journal.
- [Security](docs/SECURITY.md) — threat model, the trusted-peer boundary, approval gating,
  redaction.
- [Testing](docs/TESTING.md) — the suite, coverage, and the CI matrix.
- [Troubleshooting](docs/TROUBLESHOOTING.md) — the truststore pitfalls, reject-RST races, restart
  behaviour.
- [Examples](examples/) — a full worked config plus `AGENTS.md` / `SOUL.md` / `USER.md`.

The authoritative behaviour contract is [`SPEC.md`](SPEC.md).

---

## License

MIT. Targets A2A protocol `1.0` (`PROTOCOL_VERSION = "1.0"`).
