# Troubleshooting

`a2a-async` loads inside the gateway, where Hermes injects the `truststore` package and caches
loaded plugin modules for the process lifetime. Most hard-to-diagnose failures trace back to one
of those two facts.

## Empty `✗ a2a-async error:` line in the logs

**Cause.** The gateway injects `truststore`, which replaces `ssl.SSLContext`; its
`get_ca_certs()` raises `NotImplementedError()` with an **empty** message. Any call to
`get_ca_certs()` in gateway-loaded code fails this way — and the empty message produces an empty
error line.

**Rule.** Never call `SSLContext.get_ca_certs()` in this plugin (or any gateway-loaded code).
Never evaluate side-effectful calls (e.g. `ctx.get_ca_certs()`) as *log-format arguments* — those
run at connect/init time even when the same call works in an isolated repro.

## Server handshake dies; every inbound client sees `ConnectionResetError`

**Cause.** `truststore` also breaks the *server-side* mTLS handshake: its `SSLContext` subclass
verifies peer certs against the OS trust store, which cannot validate the plugin's private CA.
The handshake fails (`SSLError`); a valid cert + token still gets a reset.

**Fix.** `wire.tls()` must instantiate from the pre-injection class
(`truststore._ssl_constants._original_SSLContext`) and set `verify_mode`/`minimum_version`/`options`
through the C descriptor, not the Python property (the injected subclass's setters recurse
infinitely → `RecursionError`). This works both with and without injection.

## `ConnectionResetError` instead of a clean 401/404/413 — but only on Linux

**Cause.** Rejecting a request without consuming its body leaves unread bytes in the socket; the
kernel turns the close into a TCP **RST**, which on Linux races the error response. macOS's
SSL-close timing masks it, so the macOS suite passes while ubuntu CI fails exactly those
mTLS-rejection tests.

**Fix.** Every rejection path must drain a bounded prefix of the body
(`min(Content-Length, 4 * MAX_BODY)`) before responding — the cap is deliberate, so oversized or
abusive bodies still reset.

## "Plugins never hot-reload" — my edit didn't take effect

A loaded plugin module is cached for the gateway's lifetime. Restart the gateway from a
**separate shell** (`launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway` on macOS;
`systemctl --user restart hermes-gateway.service` on Linux) — the gateway cannot restart itself
from inside its own session.

## Connect timeline (is it even reaching the server?)

- `Adapter.connect()` → `gate()` → `load_config_readonly()` → `Server.__init__` (TLS context +
  peer validation) → bind → `socket.getfqdn()`.
- A **~4 ms** reconnect failure means it died before `Server.__init__` completed (e.g. a
  `get_ca_certs()` call in `tls()`). A **~13 s** connect reached the `getfqdn()` stage and is
  healthy.

## Where to look

- Startup log is **`~/.hermes/logs/gateway.log`** (`gateway.error.log` holds stderr; `gateway.info.log`
  is not the startup log). Check `grep -E "✓ a2a-async connected|Reconnect a2a-async error" gateway.log`.
- An isolated `Adapter.connect()` returning `True` while the gateway fails just means `truststore`
  is only injected in the gateway process — capture the real exception by adding a broad
  `except BaseException` that writes `type/str/repr/traceback` to a file and re-raises.
- Config lives at `platforms.a2a-async.extra`; a listener refusing to start usually means the
  policy gate (`approvals.mode` / `security.approval.transport` / `HERMES_YOLO_MODE`) or a peer
  validation (distinct fingerprints/tokens, `[0-9a-f]{64}` fingerprint shape).
