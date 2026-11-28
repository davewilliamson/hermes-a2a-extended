# Contributing

Thanks for helping. This repo hosts two stdlib-only Hermes plugins; the bar for change is a green
test suite plus honest documentation.

## Ground rules

- Read [`SPEC.md`](SPEC.md) first — it is the canonical behaviour contract.
- Never commit secrets: no tokens, certs, keys, private hostnames, or `.well-known` URLs. Loopback
  (`127.0.0.1`/`localhost`) and RFC-reserved documentation fixtures only.
- Imports are limited to the Python stdlib + `hermes_cli` public names + the plugin `ctx` object.

## Before a PR

```bash
# see docs/TESTING.md for the pinned-checkout setup
HERMES_HOME="$(mktemp -d)" HERMES_DISABLE_LAZY_INSTALLS=1 \
  PYTHONPATH="$PWD/hermes-reference:$PWD" \
  hermes-reference/.venv/bin/python -m pytest tests/ \
    --cov=a2a-async --cov=a2a-extended --cov-fail-under=70 -q
```

- All tests exercise the in-repo mock server; no live peer is required.
- Keep coverage above the 70% floor; unit-test any new parent-process logic.
- Move the pinned-`hermes-agent` boundary ref **only** deliberately (see `test_boundary.py`).

## Pull requests

- Conventional commits: `fix:`, `feat:`, `docs:`, `test:`, `chore:`, `refactor:`.
- CI runs the suite on both Ubuntu and macOS plus a gitleaks scan with full history.
- A PR is not done until CI is green and the review confirms real behaviour, not just green boxes.

## License

MIT.
