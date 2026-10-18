# Operations

## Inspect

```bash
python3 scripts/launch-routed-codex.py --check
```

## Update an installed runtime

When the bundled Codex CLI changes, the launcher runs `update-runtime.py` once.
The updater checks the local app-server configuration without making a model
request. A failed check leaves the new runtime blocked.

## Switch CCS aggregation default

Unprefixed model names use the CCS aggregation default. Prefixed IDs such as
`ccs-workbuddy/<model>` remain tied to their member provider.

Keep the direct DeepSeek alias explicitly routed when a GPT provider is the
default:

```json
"cn:deepseek-v4.1-flash": "ccs-workbuddy/cn:deepseek-v4.1-flash"
```

## Diagnose desktop launch

Failed desktop launches write a bounded, private message to:

```text
~/.local/state/codex-v2-launcher.log
```

The launcher displays a desktop notification when a graphical session is active.
