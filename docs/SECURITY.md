# Security

`a2a-async` is a **trusted-peer** interface: an authorized peer can invoke the local agent under
the agent's normal scope and tool policy. The security posture is built around that premise.

## Threat model

| Boundary | Mechanism |
|----------|-----------|
| Peer identity | mTLS client certificate, pinned by SHA-256 fingerprint |
| Replay / forgery | directional opaque bearer token per peer (≥32 URL-safe chars) |
| Token leakage | token value read from a file referenced by path; never logged, returned, or passed to the model |
| Cross-origin leakage | the configured origin is the only outbound destination; redirects are refused (v1) or never followed (v2) |
| Scope/route escalation | request fields choose **no** principal, profile, or scope; cross-profile routes are refused; framed events carry `allow_gateway_control=False` |
| Approval bypass | the plugin denies queued human-required approvals before the notifier; it cannot *allow* what policy forbids |
| Abuse / oversized bodies | 64 KiB body cap; rejections drain a bounded prefix so Linux clients see the status, not a bare reset |

## Hard requirements (enforced at connect, admission, and dispatch)

- `approvals.mode` is `smart` or `manual` (never `off`/YOLO).
- `security.approval.transport` is `builtin`.
- The process launches **without** `HERMES_YOLO_MODE`.

The adapter refuses to start when these are not met.

## mTLS notes for operators

- `a2a-async` **must** use the real `ssl.SSLContext`, never a `truststore`-injected subclass. In
  the gateway, `truststore` replaces `ssl.SSLContext`; its `get_ca_certs()` raises
  `NotImplementedError` (an empty `✗ a2a-async error:` line) and its subclass cannot validate a
  private CA, which makes the server handshake die with `SSLError` and every inbound client see
  `ConnectionResetError`. The plugin bypasses the injection internally — do not "help" it by
  re-adding `get_ca_certs()` calls or re-setting `verify_mode` properties.
- Keep certs and CA per-peer; never reuse a demo CA private key across peers.

## Secrets hygiene

- No token, certificate, key, hostname, or peer name is committed to this repository. The config
  stores **paths** only; `*.env`, `*.crt`, `*.key`, `*.pem`, and `secrets/` are git-ignored.
- Rotate/revoke a token by atomically replacing or withdrawing its file. Cert/CA or `peers`-map
  changes require a gateway restart.
- `a2a_extended_send` (v1) reads its peer token in-memory only and forwards it solely to the
  peer's configured origin.

## Redaction

Redaction covers recognized bearer strings and configured token values; it is **not**
general-purpose data-loss prevention. Audit messages omit principals, addresses, IDs, and
content.

## What is *not* guaranteed

- A timeout/cancel is an observation deadline, not a kill switch: a running side effect may still
  finish.
- The approval hook is defense-in-depth, not a replacement for the trusted-peer boundary.
- Disconnects do not cancel admitted work.
