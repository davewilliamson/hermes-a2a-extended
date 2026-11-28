# Testing

The suite is designed so it never touches a real `~/.hermes` and never needs a live peer; every
transport path is exercised against an in-repo mock A2A server.

## Run the suite

Hermes can't be `pip install`ed (its `setup.py` refuses wheel/sdist builds) and pins its
dependencies to Python ≥ 3.14. The working recipe resolves a pinned checkout with `uv`:

```bash
# 1. clone hermes-agent at the pinned ref
git clone https://github.com/NousResearch/hermes-agent hermes-reference
git -C hermes-reference checkout 8fc0374447313e276f6b9a2764bfc750190c966a

# 2. resolve runtime deps without building the project
(cd hermes-reference && uv sync --no-install-project --no-dev)
uv pip install --python hermes-reference/.venv/bin/python pytest pytest-cov

# 3. run (off the real home, no lazy installs)
export HERMES_HOME="$(mktemp -d)" HERMES_DISABLE_LAZY_INSTALLS=1
PYTHONPATH="$PWD/hermes-reference:$PWD" \
  hermes-reference/.venv/bin/python -m pytest tests/ -q
```

Without the hermes checkout on `PYTHONPATH`, **only the v1 suite runs** (the `a2a-async` tests
import Hermes internals and self-skip). CI always provides the checkout.

## What the tests cover

| File | Coverage |
|------|----------|
| `test_protocol.py` | request framing, agent-card/interface selection, SSE parsing (multi-line, split-UTF-8) |
| `test_liveness.py` | per-phase deadlines; transport-dead vs overall-cap; keepalive non-reset |
| `test_client.py` | end-to-end `send()` against the mock server (SUBMITTED→WORKING→terminal, stalls, EOF) |
| `test_policy.py` / `test_policy_unit.py` | origin-trust rules, redirect refusal matrix, credential non-exposure |
| `test_redaction.py` | no token leaks into logs/messages/artifacts |
| `test_boundary.py` | asserts the hermes-agent checkout is **pinned** to the expected ref |
| `test_admission.py` / `test_async_policy.py` | listener admission + policy gating (yolo/approval-mode refusals) |
| `test_wire.py` | mTLS server: auth, oversized-body 413, drain-to-avoid-RST behaviour |
| `test_lifecycle.py` / `test_disconnect.py` | listener life-cycle, disconnect ≠ cancel |
| `test_gateway.py` | gateway-facing integration |

## Coverage

Coverage is measured with `pytest-cov` in CI and gated at **70%** (`--cov-fail-under=70`); the
current total is ~76% with branch coverage.

Two numbers under-state real coverage by construction:

- The v1 client (`a2a-extended`) runs its network worker in a **spawned subprocess**
  (`client.subprocess.Popen`). That code — `_network`, `_read`, the SSE state machine,
  `open_peer`/`_activity_handlers` in `policy.py` — is exercised by the integration tests and its
  workers' cleanup is asserted in the `send` fixture, but coverage.py cannot trace across the
  process boundary, so `client.py`/`policy.py` report lower than reality.
- The inbound `a2a-async` modules (78–85%) and `liveness.py` (100%) are measured directly and
  are the accurate head-line numbers.

To close a *real* gap, always write a unit test for parent-process validation (as
`test_policy_unit.py` does for `origin`/`check`/`redact`/`resolve`/redirect logic) rather than
chasing subprocess coverage.

## CI

`.github/workflows/ci.yml` runs the full suite on **both** `ubuntu-latest` and `macos-latest`
(the inbound transport's socket-close/RST semantics differ per platform — a fix that is valid on
one can fail on the other) plus a **gitleaks** scan with full git history.

## Conventions

- Never introduce a private IP/hostname/token/`.well-known` URL into the repo; use loopback
  (`127.0.0.1`/`localhost`) and RFC-reserved documentation fixtures only.
- Imports are limited to Python stdlib + `hermes_cli` public names + the plugin `ctx` object.
- Conventional commit messages (`fix:`, `feat:`, `docs:`, `test:`, `chore:`, `refactor:`).
