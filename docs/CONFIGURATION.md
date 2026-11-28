# Configuration reference

Both plugins read Hermes config. No secret value is ever stored in this repository — the config
holds **paths** to token files and certificate files; the secret material lives elsewhere.

---

## v1 — `a2a_extended_send` (`a2a-extended`)

Peers come from the standard Hermes **`a2a_agents`** map under `~/.hermes/config.yaml`.

```yaml
a2a_agents:
  <peer-name>:
    url:          https://peer.example      # required; https or http, hostname, optional port
    auth:         {type: bearer, token: "..."}   # required bearer token (in-memory only)
    timeout:      120                       # seconds; informational
    capabilities: [code]                    # optional
    tenant:       ""                        # optional; echoed to the peer when it defines one
```

Validation applied by the client policy (`policy.py`):

- `url` must be `http`/`https`, contain a hostname, no `username`, and **no query or fragment**.
- `auth.token` must be printable ASCII (`33..126` inclusive) — non-ASCII/control bytes are
  refused up front as "invalid bearer credential configuration".
- `a2a_agents.<peer>` must be a mapping; a missing or non-mapping entry is refused.

---

## v2 — `a2a-async` platform

All `a2a-async` config lives at **`platforms.a2a-async.extra`**, with `enabled` toggling the
listener.

### Required surrounding policy

```yaml
approvals:
  mode: smart              # or manual — "off"/YOLO is refused
security:
  approval:
    transport: builtin
```

The process must also launch **without** `HERMES_YOLO_MODE` set.

### `platforms.a2a-async.extra`

| Key | Type | Default | Required | Meaning |
|-----|------|---------|----------|---------|
| `host` | string | `127.0.0.1` | no | listener bind address |
| `port` | int | `9443` | no | listener port |
| `public_url` | string | — | no | advertised origin in the agent card (opt) |
| `ca` | path | — | server | CA bundle that signed the server + peer certs |
| `cert` | path | — | server | server certificate (PEM) |
| `key` | path | — | server | server private key (PEM) |
| `peers` | map | — | server | authorised inbound principals (see below) |
| `outbound` | map | — | no | peers this profile can *dispatch* to (see below) |

### `peers` (who may talk to the listener)

Each key is a principal name; each value:

```yaml
peers:
  <name>:
    sha256:     "<64 lowercase hex chars>"
    token_file: "/abs/path/token.env"
```

- **`sha256`** — the lowercase SHA-256 of the peer's **client certificate in DER** (the cert
  fingerprint), used to bind the mTLS identity.
- **`token_file`** — path to a file containing that peer's opaque bearer token
  (`[A-Za-z0-9_-]{32,256}`). Only the path is stored in config.

Constraints enforced at listener start:

- 1–64 peers, and every peer must have a **distinct** fingerprint and **distinct** token.
- Fingerprints must match `[0-9a-f]{64}`.

### `outbound` (who this profile can dispatch to)

```yaml
outbound:
  <name>:
    url:        "https://host:9443"      # required; the ONLY allowed destination
    ca:         "/abs/path/peer-ca.crt"  # trust store for the peer's server cert
    cert:       "/abs/path/client.crt"   # our client certificate
    key:        "/abs/path/client.key"   # our private key
    token_file: "/abs/path/outbound.env" # bearer token we present
```

The configured HTTPS origin is the **only** outbound destination; the client never follows
redirects.

---

## Secrets on disk

- Token values live in files referenced by `token_file`; the files carry `chmod 600` and are
  git-ignored (`*.env`, `secrets/`, `*.crt`, `*.key`, `*.pem`).
- Certificates/keys are PEM on disk; only paths appear in config.
- Bake the token files at deploy time; rotate/revoke by **atomically replacing or withdrawing**
  the referenced file. Cert/CA or `peers`-mapping changes require a gateway restart (the config
  is read once at connect).

## Tool parameters

### `a2a_extended_send`

| Param | Required | Meaning |
|-------|----------|---------|
| `peer` | yes | a key in `a2a_agents` |
| `message` | yes | the task brief |
| `context_id` | no | resume an existing A2A context |
| `liveness_timeout_s` | no | overall execution cap (default **120**) |

### `a2a_extended_dispatch`

| Param | Required | Meaning |
|-------|----------|---------|
| `peer` | yes | a key in `outbound` |
| `message` | yes | the task text |
| `message_id` | no | caller-chosen idempotency key (reuse to recover a lost receipt) |

### `a2a_extended_status`

Exactly one of `submission_id` / `task_id` / `context_id` must be provided.

---

## Server defaults (from `wire.py` / `adapter.py`)

| Limit | Value |
|-------|-------|
| Request body bound (`MAX_BODY`) | 64 KiB |
| Socket idle timeout | 2 s |
| HTTP workers | 16 |
| Queued tasks | 16 |
| Active tasks | 4 |
| Deadline from receipt | 120 s |
| Terminal-record retention | 64 |
| Max returned chars | 2048 |
