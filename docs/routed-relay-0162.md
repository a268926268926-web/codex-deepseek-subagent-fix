# Model-scoped V2 relay on 0.162

Use this mode for a session containing both routed and native models. It uses the
original packaged `codex-cli 0.162.0-alpha.17.2`, without a patched executable or
the process-wide plaintext environment switch. The provider can still be named
`OpenAI`: selection is by explicit model alias in each Responses request.

The local relay binds only to `127.0.0.1`. Requests for other models retain their
original body bytes and response stream. Requests for the configured routed alias
convert task `agent_message` items to readable user messages while retaining IDs,
sender/recipient context and all other history. Only the V2 message schema marker
and routed collaboration function-call metadata are adjusted. Codex's existing
direct plaintext V2 path handles delivery, completion, mailbox queues and follow-up.

For old ciphertext, a matching SHA-256 recovery entry is used. With automatic
recovery enabled, an uncached task is submitted to two independent ephemeral
native-model readbacks through the originally configured service. Matching user
task text is cached privately; failures stop that request without removing saved
history. Hidden reasoning is neither decoded nor requested. This is model-mediated
task recovery, not a local cryptographic decryptor or an official decryption API.
The five old task texts tested here were recovered consistently. Other ciphertext
may be unavailable if its service/backend/key scope changes.

Recovery handles the protocol's `NEW_TASK` and `MESSAGE` communications, including
queued follow-up messages. It uses the original runtime's built-in native model
catalog independently of the desktop's routed catalog. A controlled old-ciphertext
test failed with the routed catalog and succeeded with the original built-in one;
the final guarded helper recovered all five old communications twice, matching the
previously verified map. The recovery subprocess has an empty tool list, including
Responses-Lite additional tools, and `tool_choice=none`. A dedicated HTTP guard
blocks tool-call output before it reaches the CLI.

## Prepare

Requires Linux, Python 3.11+, the existing original CLI and Responses provider.
Do not place histories, credentials or recovery maps in this public repository.

```bash
mkdir -m 700 /absolute/private/bundle
cp scripts/{routed-v2-relay.py,recover-v2-tasks.py,launch-routed-codex.py,launch-codex-v2.sh} \
  /absolute/private/bundle/
python3 scripts/make-routed-catalog.py \
  --source /path/to/pinned-codex/codex-rs/models-manager/models.json \
  --output /absolute/private/bundle/routed-models.json --model gpt-5.6-sol
chmod +x /absolute/private/bundle/launch-codex-v2.sh
/absolute/private/bundle/launch-codex-v2.sh --check
```

An existing validated recovery map may be copied to
`/absolute/private/bundle/recovered-messages.local.json` with mode 0600. Without
one, the launcher enables on-demand recovery and writes a private map after two
matching reads. The map format is the same as `recover-v2-tasks.py`.
Keep `recover-v2-tasks.py` beside `routed-v2-relay.py`: the helper imports its HTTP
guard. Use the helper's default built-in catalog for native readback rather than
passing the desktop's routed catalog.

Normally exit the running desktop first, then run
`/absolute/private/bundle/launch-codex-v2.sh`. The launcher starts its local relay,
creates a temporary original-CLI wrapper with invocation-only provider/catalog
overrides, and sets `CODEX_CLI_PATH` for that launch. It refuses to launch another
desktop while the original main process is running. It does not edit config.toml,
replace the installed executable, clone the user's Codex home, or rewrite rollouts.
Continue the existing chat in the same home. On desktop exit, the owned relay and
temporary wrapper are removed. To roll back, exit normally and open Codex through
the usual launcher.

For isolated CLI validation:

```bash
/absolute/private/bundle/launch-codex-v2.sh --cli -- \
  exec --ephemeral --skip-git-repo-check -m gpt-5.6-sol 'Return a unique marker.'
```

## Latency and validation

Each uncached encrypted communication needs two native API calls and two ephemeral
CLI sessions before it reaches the routed child. Several misses in one request
are recovered sequentially; N misses need 2N native calls. This adds startup
latency, process startup, temporary memory and native-model token usage. Replaying
that communication uses the cache. Fresh plaintext routed tasks need no readback.

The standard-library HTTP transport opens a new upstream TCP/TLS connection for
each forwarded request. The handshake affects native and routed calls, and adds
latency across repeated tool rounds. Requests are parsed for model selection;
routed SSE events receive JSON processing and immediate flushing. Replies remain
streamed. Live TPS and peak load memory have not been benchmarked; CLI completion
times include model work, tools and recovery and are not output-rate measurements.
Routed direct tool descriptions can also increase input tokens compared with
native code-mode tool exposure. This relay is an additional local process and
failure point, and the launcher checks the exact verified runtime version.

Fourteen contract tests check native body/gzip preservation, HTTP mapped
input/stream conversion, attribution and IDs, unchanged reasoning, ciphertext
rejection, bounded decompression, disk-write failure and the recovery tool guard:

```bash
python3 scripts/test-routed-v2-relay.py
```

Live tests on the original CLI passed native parent → routed child with shell,
apply_patch, Node/GitHub MCP reads, automatic completion, same-child follow-up and
idle queued messages; routed parent → routed child; routed parent → native
`gpt-6-luna` / max with a formal tool; and paginated old main/child-history readback.
See [acceptance](acceptance-0162.md). These establish the tested capabilities;
every connector operation and desktop behavior after activation are separate
coverage boundaries.

Authentication headers are forwarded in memory to the configured HTTPS service.
Redirects are disabled. Access logs, task content, headers and raw responses are
not written by the relay. Recovery maps contain private task text and must stay
local. This small relay only implements the HTTP Responses path tested here;
it does not add WebSocket Responses or remote/cloud host support.
