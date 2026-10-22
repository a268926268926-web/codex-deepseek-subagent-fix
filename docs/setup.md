# Setup

1. Install Codex from the supported desktop or CLI package.
2. Create a private bundle directory with mode `0700`.
3. Copy the launcher and relay scripts into it.
4. Generate `routed-models.json` from the installed runtime's model catalog,
   then replace the temporary WorkBuddy source alias with the native slug:

   ```bash
   python3 scripts/add-native-deepseek-alias.py --catalog /private/routed-models.json
   ```
5. Copy `config/routes.example.json` to `routed-providers.local.json` and adjust
   provider keys and model names. Set `portable_plaintext` to `true` only when
   routed V2 task text may be stored outside ciphertext.
6. Keep credentials only in ignored `.local.json` files with mode `0600`.
7. Run:

```bash
python3 /absolute/private/bundle/launch-routed-codex.py --check
```

The result must be `READY`. Fully quit any running Codex desktop before launching
the V2 entry. The launcher refuses to create a second desktop instance.
