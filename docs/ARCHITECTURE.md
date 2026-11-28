# Architecture

`hermes-a2a-extended` ships **two independent plugins** that attack the same reliability problem
from opposite directions. They share no code and are installed/deployed separately.

```
┌─────────────────────────────┐          ┌─────────────────────────────┐
│  a2a-extended (v1, client)  │          │  a2a-async (v2, server)     │
│  -------------------------- │          │  -------------------------- │
│  a2a_extended_send          │          │  mTLS JSON-RPC listener      │
│  streaming SSE + liveness   │          │  + dispatch / status tools   │
│  ~30-min block → bounded    │          │  reliable admission + polling│
└──────────────┬──────────────┘          └──────────────┬──────────────┘
               │  outbound to any standard A2A peer     │  inbound from trusted peers
               └────────────────────────────────────────┘
```

---

## v1 — `a2a-extended` (streaming client)

### Modules

| File | Role |
|------|------|
| `__init__.py` | registers `a2a_extended_send` in the `a2a` toolset |
| `client.py` | discovery + JSON-RPC/SSE framing over stdlib `urllib` |
| `liveness.py` | pure per-phase monotonic-deadline state machine |
| `policy.py` | origin-trust, TLS/redirect rules, credential seam |

### Data flow

1. `send(peer, message, …)` resolves the peer's `url`/`token`/`tenant` via `policy.resolve()`.
2. It spawns a **subprocess** (`_worker`) that performs the network I/O so the caller never blocks
   on a wedged peer's socket from its own thread; the parent communicates the result back.
3. The worker fetches the A2A **agent card** (`/.well-known/agent-card.json`, falling back to
   `/.well-known/agent.json`), selects the JSON-RPC interface whose `protocolBinding == "JSONRPC"`
   and requires `capabilities.streaming`.
4. It POSTs `SendStreamingMessage` and drives an **SSE** stream to completion.

### Liveness state machine (the core value)

The peer emits `: keepalive` comments every ~5 s *for the entire reply timeout regardless of
worker progress*, so "no data for N seconds" cannot detect a wedged worker. Three strictly
monotonic deadlines handle it instead:

| Phase | Trigger | Result |
|-------|---------|--------|
| **transport-dead** | no bytes at all (~3× keepalive interval, 15–20 s) | `outcome=transport_error` |
| **acknowledgement** | first `SUBMITTED`/`WORKING` not seen within ~30 s | `outcome=local_timeout`, `timeout_phase=acknowledgement` |
| **overall** | no terminal within `liveness_timeout_s` (default 120 s) | `outcome=local_timeout`, `timeout_phase=overall`, `remote_outcome_unknown=true` |

Keepalives reset **nothing**. Every bail tears down the socket + reader and returns promptly.

### Credential seam

The bearer token is read in-memory only and forwarded **only** to the peer's configured origin.
`policy.SameOriginRedirect` refuses cross-origin redirects (and any POST redirect that would
change the method), so the `Authorization` header can never leak to a third origin. `redact()`
strips the token from any text that might be surfaced to the model or logs.

---

## v2 — `a2a-async` (mTLS async transport)

### Modules

| File | Role |
|------|------|
| `__init__.py` | registers `a2a_extended_dispatch` / `a2a_extended_status`; loads `outbound` config |
| `adapter.py` | platform life-cycle: `connect()` → `gate()` → `load_config_readonly()` → start server |
| `wire.py` | the mTLS `HTTPServer`: JSON-RPC `tasks/send|get|list|cancel|subscribe` + peer auth + drain |
| `journal.py` | SQLite receipt/idempotency journal |

### Identity, in two layers

A caller is authenticated by **both**:

1. **mTLS** — the client certificate, pinned by its SHA-256 fingerprint in `peers.<name>.sha256`.
2. **Directional bearer token** — an opaque `[A-Za-z0-9_-]{32,256}` value read from
   `peers.<name>.token_file`.

The `(principal, messageId)` pair is the idempotency key.

### Request lifecycle

1. `tasks/send {messageId, message}` → server commits receipt metadata to a profile-local SQLite
   journal **before** acknowledging, then returns `{task_id, context_id, received_at}`.
2. One in-memory task owns the `SUBMITTED → WORKING` transition; `WORKING` means *dispatch
   started*, not that the model issued any tool call.
3. The final notify/processing-completion hook publishes the terminal state exactly once.
4. Callers poll `tasks/get` (or `tasks/list`, principal-scoped, filterable by `contextId` /
   `messageId`); `includeArtifacts: true` returns the reply.

### Idempotency & recovery

- A duplicate `(principal, messageId)` with an unchanged payload returns the original receipt.
- The client journals identifiers + a digest **before** sending, retries one transport failure
  with the same `messageId`, and can recover a lost receipt via `a2a_extended_status` using the
  local `submission_id`.
- Neither journal stores message or reply bodies.
- Restart and eviction invalidate the server's idempotency guarantee — do not retry blindly
  across those boundaries.

### Bounds (`wire.py` / `adapter.py`)

64 KiB request body, 2 s socket idle, 16 HTTP workers, 16 queued / 4 active tasks, 120 s deadline
from receipt, 64 retained terminal records, 2048 max returned chars.

### Approval hook

The plugin registers `pre_approval_request`. It is **defense in depth**, not an isolation
boundary: it denies queued human-required approvals in active inbound contexts before the
notifier, and the transport discards approval and interim sends. It cannot change what the
operator's existing policy already allows.
