# Specification: `a2a_extended_send` streaming A2A client plugin

The separate [async v2 contract](#async-v2-contract) is below.

A stdlib-only Hermes "general" plugin that registers one tool, `a2a_extended_send`, in the `a2a`
toolset. It sends a task to a configured A2A peer over the **streaming** (SSE) path and returns
(a) retrospective `SUBMITTED`→`WORKING` timestamps plus `task_id`/`context_id`, and (b) a bounded
early return instead of a ~30-minute blind block.

## Design goal & honest scope

The win is an **early, bounded return** when a peer is stalled, plus **retrospective** dispatch
timestamps. It is *not* an execution oracle: on the public A2A streaming surface `WORKING` is set
after the handler is *scheduled* (not after execution begins), and keepalives flow for the entire
reply window regardless of worker progress. So the tool detects **transport death** and bounds
**total wall-clock**; it cannot prove the remote worker started or completed.

## Ground truth (read these files for exact shapes)

Public repo `NousResearch/hermes-agent`:

- `plugins/platforms/a2a/adapter.py` — `_rpc_message_stream`, `_SSE_KEEPALIVE`, `_reply_timeout`,
  `_sse_headers`, `_emit_terminal`.
- `plugins/platforms/a2a/protocol.py` — `PROTOCOL_VERSION = "1.0"`, task states, `sse_data`,
  `build_task`, `status_update`, `artifact_update`, `stream_task`, `extract_text`.
- `plugins/platforms/a2a/tools.py` — the bundled non-streaming client: `_resolve_peer`,
  `_auth_header`, `_select_jsonrpc_interface`, `_rpc_url`, `_fetch_card`.
- `plugins/platforms/a2a/security.py` — `redact_outbound` (egress-scrubbing pattern).
- `hermes_cli/urllib_security.py` — `open_credentialed_url`, `url_origin`.
- `hermes_cli/data/plugin_api.json` — the public import allowlist (only import these + stdlib).

## Wire contract (A2A v1.0)

**Discovery:** `GET {peer-url}/.well-known/agent-card.json` (404 → legacy `/.well-known/agent.json`).
Select the JSON-RPC interface from `supportedInterfaces[]` by `protocolBinding == "JSONRPC"` and
`protocolVersion`, *not* by array index. Require `capabilities.streaming` truthy. Echo `tenant` when
the interface/peer defines it.

**Request:** `POST {rpc-url}` with `Content-Type: application/json`, `Authorization: Bearer <token>`,
`A2A-Version: 1.0`.
```json
{"jsonrpc":"2.0","id":"<uuid>","method":"SendStreamingMessage","params":{
  "message":{"role":"ROLE_USER","parts":[{"text":"<brief>","mediaType":"text/plain"}],"messageId":"<uuid>"}}}
```
`message.contextId` (when continuing a context) lives inside `message`. Echo `params.tenant` when
required.

**SSE response:** headers `Content-Type: text/event-stream`, `Cache-Control: no-cache`; the server
sets `close_connection = True`, so stream closure is the terminal signal. Every `data:` line is a
full JSON-RPC envelope `{"jsonrpc":"2.0","id":<req_id>,"result":{...}}`. Happy path:

1. `result.task` with `status.state == "TASK_STATE_SUBMITTED"` (a *task* member).
2. `result.statusUpdate` with `status.state == "TASK_STATE_WORKING"` (a *statusUpdate* member —
   a different shape from frame 1).
3. `: keepalive` comments every 5s (`_SSE_KEEPALIVE`) for the entire wait.
4. Terminal: optional `result.artifactUpdate` (completed-with-reply only), then
   `result.statusUpdate` carrying a terminal `status.state`, then a `: done` comment.

**Edges the client MUST handle:** immediate terminal (frame-4-only, no SUBMITTED/WORKING — i.e.
rejected/empty/anti-loop); `TASK_STATE_INPUT_REQUIRED` terminal; a JSON-RPC `error` envelope or
HTTP error emitted *before* SSE headers (method-not-found, invalid params, rate-limit, untrusted
peer, 401/403/404); EOF/broken pipe before any terminal; `WORKING` set *after*
`run_coroutine_threadsafe(handle_message(...))` schedules the handler (scheduling, not execution).

**States:** `TASK_STATE_SUBMITTED`, `TASK_STATE_WORKING`, `TASK_STATE_INPUT_REQUIRED`,
`TASK_STATE_COMPLETED`, `TASK_STATE_FAILED`, `TASK_STATE_CANCELED`, `TASK_STATE_REJECTED`.
Terminal set = `{COMPLETED, FAILED, CANCELED, REJECTED}`. `INPUT_REQUIRED` is a clarification
outcome, not failure. Ids are `task-…` and `ctx-…`.

## Liveness spec (critical)

The remote emits `: keepalive` every 5s **for the full reply timeout regardless of worker
progress**. Keepalives therefore carry **no progress signal after WORKING**, and "no data for N
seconds" can never detect a wedged worker on a healthy transport. Implement three strictly
monotonic phases:

1. **Transport-dead:** no bytes at all (keepalives included) for ~3× the keepalive interval
   (15–20s) → `outcome="transport_error"`, close socket, return the last observed state.
2. **Acknowledgement:** connect/headers → first SUBMITTED/WORKING data must arrive within ~30s,
   else `local_timeout` at `timeout_phase="acknowledgement"`.
3. **Overall execution cap (the real win):** from `WORKING` (or from dispatch if no `WORKING`),
   `liveness_timeout_s` (default **120**) elapsed with no terminal → `outcome="local_timeout"`,
   `timeout_phase="overall"`, `last_remote_state` = last seen, `remote_outcome_unknown=true`.

Keepalives reset **nothing** (they may extend neither the acknowledgement nor the overall
deadline). Every bail tears down the socket + reader fully (bounded cleanup) and returns
promptly; a bail is a local decision and the remote task may still be running.

## Credential seam

Follow the public pattern the bundled client uses:

```python
from hermes_cli.config import load_config_readonly
# peer entry = load_config_readonly().get("a2a_agents", {}).get(peer)
# token = peer_entry["auth"]["token"]   # in-memory only
```

Build the `Authorization: Bearer <token>` header on a `urllib.request.Request`, then open via
`open_credentialed_url(request, timeout=…)`. Constraints:

- Never print/log/persist the token; never return it in tool output.
- Strip it from any error text via a redaction helper before surfacing.
- Forward it only to the peer's configured origin; refuse a cross-origin redirect unless the new
  origin is itself an approved alias.
- The mock server uses its own dummy token.

## Files

```
a2a-extended/
├── plugin.yaml            # name=a2a-extended, kind=general, stdlib only
├── __init__.py            # ctx.register_tool(name="a2a_extended_send", toolset="a2a", ...)
├── client.py              # card fetch + interface select + JSON-RPC/SSE framing (stdlib urllib)
├── liveness.py            # per-phase monotonic-deadline state machine (pure, unit-testable)
├── policy.py              # origin-trust + TLS/redirect rules + credential seam
└── tests/
    ├── test_protocol.py     # framing, request shape, interface selection, SSE parse
    ├── test_liveness.py     # per-phase deadline + transport-dead vs overall-cap logic
    ├── test_policy.py       # origin-trust, redirect refusal, credential non-exposure
    ├── test_redaction.py    # no token leaks in logs/messages
    └── mock_a2a_server.py   # stdlib http.server emitting exact + adversarial SSE frames
```

## Result schema

```json
{
  "outcome": "completed|failed|input_required|stream_closed_without_terminal|local_timeout|transport_error|refused",
  "task_id": "...", "context_id": "...", "message_id": "...",
  "received_at": "iso|null", "working_at": "iso|null",
  "timeout_phase": "acknowledgement|overall|null",
  "last_remote_state": "TASK_STATE_*|null",
  "remote_outcome_unknown": true,
  "elapsed_s": 12.3,
  "text": "reply/failure/clarification text"
}
```

No automatic resend after an ambiguous delivery.

## Acceptance criteria & verification

All tests exercise the in-repo `mock_a2a_server.py` (no live peer). Required cases:

- normal `SUBMITTED`→`WORKING`→terminal; immediate terminal (no SUBMITTED/WORKING);
  `INPUT_REQUIRED`; keepalive-only-to-300s stalled worker (MUST return at `liveness_timeout_s`,
  not 300s); delayed/missing ack; transport silence; HTTP/JSON-RPC error envelopes;
  EOF-before-terminal; malformed/multiline/split-UTF-8 SSE; cross-origin discovery/redirect
  refusal; credential non-exposure; socket/thread cleanup on every bail.

```bash
python3 -m pytest tests/ -v
```

Loader check (if a `hermes` binary is available in the environment): `ctx.register_tool` registers
`a2a_extended_send` and `hermes plugins list` loads it cleanly.

## Hygiene & security rules

- No private IPs/hostnames/peer names/tokens/`.well-known` URLs in the repo (loopback
  `127.0.0.1`/`localhost` and RFC-reserved documentation fixtures only).
- `.gitignore` covers `__pycache__/`, `.pytest_cache/`, `*.py[cod]`, `.env`, local config.
- A redaction unit test proving a token in a message/artifact is stripped must pass.
- Imports limited to Python stdlib + names in `plugin_api.json` + the `ctx` object; do not import
  bundled-plugin internals (`plugins.platforms.a2a.*`). `hermes plugins validate` warns on any
  other Hermes import.
- MIT license. Targets A2A `1.0`.

## Async v2 contract

`a2a-async` is a separate platform and client toolset, not a streaming-v1 fallback.
Use mTLS and `Authorization: Bearer ...` for discovery and all RPCs. Certificate
SHA-256 fingerprints and distinct directional bearer files jointly bind identity.
Request fields do not select the principal, route profile, or scope. The configured
HTTPS origin is the only outbound destination; the client never follows redirects.

Methods are JSON-RPC 2.0 `POST /`. The agent card advertises
`urn:hermes:a2a-async:tasks-send:v1`, no streaming, and no push. The extension accepts
`tasks/send` with `{"messageId":"caller-id", "message":"text"}` and returns
`{task_id, context_id, received_at}`. Each new task gets an isolated, server-issued
context; caller-supplied send contexts are refused. `tasks/get`, `tasks/cancel`, and
terminal `tasks/subscribe` accept `id`. Subscribe returns a single JSON snapshot;
poll nonterminal tasks instead. `tasks/list` is principal-scoped and filters by
`contextId` and/or `messageId`. Use `includeArtifacts: true` to retrieve the reply
and its single artifact. Lists page by nonnegative `offset` and return `nextOffset`
when more records remain (up to 32 records, or one when including artifacts).

The server commits receipt metadata to profile-local SQLite before acknowledging.
During a server generation one in-memory task owns the transition from `SUBMITTED`
to `WORKING` and once-only terminal settlement. `WORKING` means dispatch started,
not that the model has issued any tool call. The final notify send or processing
completion hook publishes the terminal state. Non-final sends cannot complete work.
The streamed-final field is a defensive compatibility fallback, not a streaming mode.

Timeout is an observation deadline, not a guarantee that a running side effect
stopped. The execution slot remains occupied until processing completes. Canceling
queued work prevents dispatch; running work is marked `CANCEL_REQUESTED` without
interruption. A late reply cannot replace a recorded terminal state. Defaults:
64 KiB request bound, 2 s socket idle timeout, 16 HTTP workers, 16 queued and 4 active
tasks, 120 s deadline from receipt, 64 terminal records and 2048 returned characters.

Disconnects do not cancel work. A duplicate `(principal, messageId)` with unchanged
payload returns the original receipt. The client journals identifiers and a digest
before sending, retries one transport failure with the same `messageId`, and can
recover the receipt using status by `submission_id`. Neither journal stores message
or reply bodies. Use a new explicit ID for intentionally repeated text. Restart and
eviction make prior task IDs unknown and end the server's idempotency guarantee;
do not retry blindly across either boundary. Old receipts are not automatically replayed.

Before enabling, require operator approval for each machine. The owning profile
must use `smart` or `manual`, the `builtin` approval transport, and launch without
`HERMES_YOLO_MODE`. Policy is checked on connect, admission and dispatch. Cross-profile
routes are refused and framed events have `allow_gateway_control=False`. The synchronous
deny hook resolves queued approvals in active inbound DM contexts before the notifier;
the transport discards approval and interim sends. This is defense in depth, not a
replacement for the trusted-peer boundary. Redaction covers recognized bearer strings
and configured token values; it is not general data-loss prevention. Audit messages
omit principals, addresses, IDs and content.

### Verification

Run the full test suite with Python 3.11 and Hermes source at
`8fc0374447313e276f6b9a2764bfc750190c966a` on `PYTHONPATH`. Set
`HERMES_DISABLE_LAZY_INSTALLS=1` and use a disposable `HERMES_HOME`. Without the
Hermes checkout, only the v1 suite runs. Test certificates are generated in pytest's
disposable directory, never in the repository.
