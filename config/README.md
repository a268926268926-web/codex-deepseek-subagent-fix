# Configuration

All files ending in `.local.json` are private runtime inputs and are ignored by
Git. Start from the examples:

- `config.toml.example`: Codex provider and feature flags.
- `routes.example.json`: direct and WorkBuddy model routes.
- `native-upstream.example.json`: independent upstream used only for encrypted
  old-task recovery.

Keep credentials out of `routes.example.json`. The recovery credential belongs
only in a private `native-upstream.local.json` with mode `0600`.
