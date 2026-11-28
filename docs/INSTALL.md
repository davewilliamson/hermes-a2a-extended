# Installation

Deploying `a2a-extended` is a drop-in config change. Deploying `a2a-async` is deliberately
multi-step: it opens an authenticated inbound listener, so the install below assumes operator
approval and ends with a gateway restart.

## Prerequisites

- A working Hermes Agent install (the plugin is stdlib-only; no extra Python deps).
- Python 3.11+ to run the test suite (the pinned-test recipe resolves a Python ≥ 3.14).
- **OpenSSL** (only to generate certificates for `a2a-async`; `a2a-extended` needs none).

## 1. Install the plugin directory

Copy each plugin directory you want under the owning profile's `plugins/` directory:

```bash
# the profile that will host the tool (v1) and/or listener (v2)
PROFILE_HOME="$HOME/.hermes"                       # default profile
# PROFILE_HOME="$HOME/.hermes/profiles/<name>"     # a named profile

cp -R a2a-extended "$PROFILE_HOME/plugins/"
cp -R a2a-async     "$PROFILE_HOME/plugins/"       # v2 only, if approved
```

No `pip install` is involved: plugins are loaded from source by the gateway.

## 2a. v1 — configure the streaming client

v1 uses the standard Hermes `a2a_agents` map. No secret lives in this repo.

```yaml
a2a_agents:
  some-peer:
    url: https://peer.example
    auth: {type: bearer, token: "..."}   # in-memory only; never logged
    timeout: 120
    capabilities: [code]
```

Restart the profile's gateway, and `a2a_extended_send` is available in the `a2a` toolset.

## 2b. v2 — generate certificates and configure the listener

`a2a-async` requires **mTLS** (client **and** server certificates) plus per-peer directional
bearer tokens. Generate short-lived **loopback only** specimens for testing:

```bash
python3 scripts/gen_certs.py
# -> secrets/ca.crt, secrets/server.crt, secrets/server.key,
#    secrets/client.crt, secrets/client.key
```

> These are loopback demo certs with a private CA. At rollout, issue **independent per-peer
> certificates** from your own CA and **never copy a CA private key between peers**. The
> `secrets/` directory and `*.crt`/`*.key`/`*.pem`/`*.env` files are git-ignored.

Compute the fingerprint of a peer's **client** certificate (lowercase SHA-256 of the DER):

```bash
openssl x509 -in secrets/client.crt -outform DER | shasum -a 256   # macOS
# or: openssl x509 -in secrets/client.crt -outform DER | sha256sum  # Linux
```

Write each directional bearer token to its own file — an opaque `[A-Za-z0-9_-]{32,256}` string
(only the path is stored in config, never the value):

```bash
mkdir -p secrets
printf '%s' 'change-me-32+-chars-of-url-safe-random' > secrets/inbound.env   # token this server accepts
chmod 600 secrets/inbound.env secrets/outbound.env 2>/dev/null || true
```

### Server (listener) profile

```yaml
approvals: {mode: smart}          # or manual
security: {approval: {transport: builtin}}
platforms:
  a2a-async:
    enabled: true
    extra:
      host: 127.0.0.1
      port: 9443
      ca:   /abs/path/secrets/ca.crt
      cert: /abs/path/secrets/server.crt
      key:  /abs/path/secrets/server.key
      peers:
        example-peer:
          sha256:     "<64-hex fingerprint of that peer's client cert>"
          token_file: /abs/path/secrets/inbound.env
```

### Client-only profile

A peer that only *sends* work needs no listener — define `outbound` without `enabled`:

```yaml
approvals: {mode: smart}
security: {approval: {transport: builtin}}
platforms:
  a2a-async:
    enabled: false
    extra:
      outbound:
        example-peer:
          url:        https://host:9443
          ca:         /abs/path/secrets/ca.crt
          cert:       /abs/path/secrets/client.crt
          key:        /abs/path/secrets/client.key
          token_file: /abs/path/secrets/outbound.env
```

### Policy requirements (enforced on connect, admission, and dispatch)

The owning profile **must**:

- use `approvals.mode: smart` **or** `manual`;
- use `security.approval.transport: builtin`;
- launch **without** `HERMES_YOLO_MODE` set.

The adapter refuses to start otherwise. See [Configuration](CONFIGURATION.md) for exact types and
defaults.

## 3. Restart the gateway

Plugins never hot-reload. Restart from a **separate** shell (the gateway cannot restart itself):

```bash
# macOS (launchd):
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway

# Linux (systemd user service):
systemctl --user restart hermes-gateway.service
```

Confirm the listener is up:

```bash
grep -E "✓ a2a-async connected" ~/.hermes/logs/gateway.log
```

## 4. Rollback

Disable the `a2a-async` platform and plugin in its owning profile, then restart that gateway:

```yaml
platforms: {a2a-async: {enabled: false}}
```

Optionally uninstall **only** `a2a-async` (leave `a2a-extended` untouched). Work that was already
admitted may still have taken effect — disabling the listener stops new submissions, not in-flight
work.
