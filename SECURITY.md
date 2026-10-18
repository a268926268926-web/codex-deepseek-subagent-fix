# Security

Do not commit real API keys, auth files, session logs, recovery maps, provider
databases, or routing files whose names end in `.local.json`.

The launcher reads private routing and recovery profiles from its own directory.
Treat the following as local-only:

- `recovered-messages.local.json`
- `routed-providers.local.json`
- `native-upstream.local.json`
- `runtime-compatibility.local.json`
- `~/.codex/`, `~/.cc-switch/`, and exported Codex sessions

Before publishing, run:

```bash
python3 scripts/check-public-tree.py .
```

Report security issues privately to the repository owner rather than opening a
public issue containing credentials or private history.
