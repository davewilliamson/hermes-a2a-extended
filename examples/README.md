# Examples

Worked examples you can adapt. **Every value below is a placeholder** — replace paths, hostnames,
fingerprints, and token files with your own; never commit real credentials.

| File | What it shows |
|------|---------------|
| [`config.yaml`](config.yaml) | A full profile config: v1 `a2a_agents` peers **and** the v2 `a2a-async` listener + outbound peers |
| [`AGENTS.md`](AGENTS.md) | Agent operating rules for a profile that hosts the async listener and dispatches to peers |
| [`SOUL.md`](SOUL.md) | A persona/red-lines file for that same profile |
| [`USER.md`](USER.md) | The owner profile that the above agent serves |

These three markdown files mirror the standard Hermes profile files
(`AGENTS.md` / `SOUL.md` / `USER.md` / `MEMORY.md`). A profile using `a2a-async` should record its
trusted-peer policy *in* `AGENTS.md` so every session on that profile inherits the rule.
