# USER.md — example owner profile for the profile served by AGENTS.md / SOUL.md

This is an illustrative owner profile for the human who owns the worker above. It records standing
preferences the agent should carry into every session.

## About the owner

- Prefers subscription/OAuth/local services over pay-per-call APIs; keeps strict secret hygiene.
- Wants real, tested results — fail-closed review of any exact artifact, and honest "not run" /
  "blocked" reports over a plausible-looking success.
- Confirm before anything destructive, privacy-sensitive, external, money-affecting, or privileged.

## Conventions

- Times in the owner's timezone.
- Work is reversible/tested where possible; preserve IDs, configuration, and authentication across
  changes.
- Delegate mechanical work; the agent keeps planning, review, and context-heavy judgment.

## Transport expectations (a2a)

- Inbound async tasks arrive over mTLS from pre-approved peers only; anything else is refused.
- The agent should treat "submitted" and "working" timestamps as facts, but never claim a remote
  worker finished unless the terminal state was observed.
