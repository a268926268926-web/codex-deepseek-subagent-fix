# Codex V2 Model-Scoped Relay

Run Codex Multi-Agent V2 with a mix of native OpenAI-family models and third-party
Responses models. The installed Codex runtime stays in place. A loopback relay
changes only the explicitly routed model aliases that cannot consume Codex's
encrypted V2 agent-message items.

The current launcher workflow is verified with:

- `codex-cli 0.163.0-alpha.10.2`
- the previous `0.162.0-alpha.17.2` baseline
- native GPT routing through CC Switch aggregation
- Direct `cn:deepseek-v4.1-flash` through an explicit WorkBuddy aggregation member
- old encrypted task recovery through a separately configured compatible provider

## Architecture

```text
Codex desktop
  -> original installed codex-cli through an invocation-only wrapper
  -> local model-scoped relay on 127.0.0.1
  -> CC Switch aggregation proxy
  -> selected native or third-party provider
```

Native models are forwarded without a model-name rewrite. Routed aliases receive
the compatibility transformation, old-task recovery lookup, and optional target
mapping. The launcher enables Code Mode and Multi-Agent V2 in the app-server
argument scope, where current desktop releases preserve the overrides.

Read [the architecture document](docs/architecture.md) for the request and
recovery paths.

## What It Solves

Codex Multi-Agent V2 places a child task in an `encrypted_content` item of an
`agent_message`. An OpenAI-compatible third-party endpoint may implement the
Responses shape without consuming that encrypted item. The relay converts the
task to readable input only for aliases configured as routed.

For old encrypted tasks, the relay looks up a local SHA-256 tombstone and, on a
miss, reads the task twice through an independent native-compatible provider.
Matching task text is cached privately after two identical reads. This is
model-mediated recovery, not offline decryption, and hidden reasoning is not
requested.

## CCS Aggregation

CC Switch aggregation has one default provider and zero or more prefixed members:

- Unprefixed model names use the aggregation default.
- `ccs-<member-key>/<model>` names use that member.
- Changing the default does not change explicitly prefixed members.

The supplied routes example keeps WorkBuddy stable while the default may be a GPT
provider:

```json
{
  "version": 1,
  "upstream": "http://127.0.0.1:15721/v1",
  "model_routes": {
    "cn:deepseek-v4.1-flash": "ccs-workbuddy/cn:deepseek-v4.1-flash"
  },
  "native_profile": "native-upstream.local.json"
}
```

The direct DeepSeek alias uses the same route so that changing the CCS default GPT
provider does not send the DeepSeek request to the wrong upstream.

## Quick Start

1. Create a private bundle with mode `0700`.
2. Copy the runtime files from `scripts/`.
3. Generate a routed model catalog from the installed Codex catalog.
4. Copy the examples from `config/` and replace placeholder values.
5. Create `native-upstream.local.json` with mode `0600`.
6. Validate:

```bash
python3 /absolute/private/bundle/launch-routed-codex.py --check
```

The result must contain `"status": "READY"`.

Fully quit any running Codex desktop, then start through the V2 desktop entry or:

```bash
/absolute/private/bundle/launch-codex-v2.sh
```

The launcher refuses to create a second desktop instance and writes a private
diagnostic to `~/.local/state/codex-v2-launcher.log` when a GUI launch fails.

## Runtime Updates

`update-runtime.py` starts an isolated app-server, checks nine provider,
catalog, Code Mode, Multi-Agent V2, model-override and desktop-plugin settings,
and stores a private compatibility record bound to the relevant file hashes.
No model request is made. An unknown runtime remains blocked when validation
fails.

See [runtime compatibility](docs/runtime-compatibility.md).

## Tests

```bash
python3 tests/test-routed-v2-relay.py
python3 tests/test-workbuddy-routes.py
python3 tests/test-native-recovery-profile.py
python3 tests/test-prepare-workbuddy-ccs.py
python3 tests/test-ensure-ccs-proxy.py
python3 tests/test-runtime-updater.py
```

The suite uses temporary fixtures and never needs production credentials.

## Repository Layout

```text
config/     sanitized configuration examples
docs/       current architecture, setup, operations and security
scripts/    runtime launcher, relay, recovery, CCS and update helpers
tests/      hermetic regression tests
legacy/     archived 0.147 binary release and 0.162 source-patch flow
patches/    pinned upstream patch sources retained for audit
```

## Private Data

Never commit:

- API keys, auth files, CC Switch databases or provider credentials
- Codex sessions, recovered task maps or private routing files
- generated model catalogs copied from a user installation
- runtime logs, screenshots or acceptance outputs containing local data

See [SECURITY.md](SECURITY.md).

## Legacy Paths

The standalone patched-binary release and the process-wide 0.162 source patch are
not the current desktop flow. They remain under [legacy](legacy/README.md) and in
the pinned [patch directory](patches/0.162.0-alpha.17.2/) for historical
audit.

## License

Apache-2.0. See [LICENSE](LICENSE).
