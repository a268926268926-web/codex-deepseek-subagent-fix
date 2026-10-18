# Model-scoped V2 relay on 0.162 and 0.163

Use this mode for a session containing both routed and native models. It uses the
original packaged `codex-cli 0.162.0-alpha.17.2` or `0.163.0-alpha.10.2`, without a patched executable or
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

### Runtime updates

The launcher accepts both explicitly verified runtimes above. After a later
installed Codex update, it automatically runs `update-runtime.py` once before
launching. The updater starts an owned app-server using a temporary wrapper and
checks all nine route, catalog, Code Mode, V2 and desktop plugin settings. It makes
no model requests, invokes no subagents, installs no software, and leaves the
running desktop alone. A failed check keeps the new runtime blocked.

Successful checks are saved atomically in `runtime-compatibility.local.json`, bound
to SHA-256 hashes of the installed CLI, catalog, launcher, verifier and updater.
Changes to those files require revalidation. This validates local startup
configuration only; a future protocol change can still require a relay update and
an actual model test. To rerun the check manually in the private bundle:

```bash
python3 /absolute/private/bundle/update-runtime.py
```

Include `update-runtime.py` and `verify-desktop-wrapper.py` when copying the bundle.

Requires Linux, Python 3.11+, the existing original CLI and Responses provider.
Do not place histories, credentials or recovery maps in this public repository.

Preserve the routed model's original `code_mode` or `code_mode_only` tool mode.
The catalog generator changes the transport/search compatibility fields, not that
mode. Forcing `direct` can pass CLI shell tests while removing the desktop's
`functions.exec` entry point. The launcher rejects such a catalog and explicitly
enables `features.code_mode_host` for both desktop and CLI invocations.

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
The routed model retains Code Mode. Tool search is disabled for compatibility,
so configured tools are described inside `exec` instead of being discovered
lazily; this can increase input tokens compared with native deferred tools.
This relay is an additional local process and
failure point, and the launcher checks the exact verified runtime version.

Seventeen contract tests check preserved Code Mode and native catalog metadata,
launcher mode validation, native body/gzip preservation, HTTP mapped
input/stream conversion, attribution and IDs, unchanged reasoning, ciphertext
rejection, bounded decompression, disk-write failure, the recovery tool guard and
preserved Desktop arguments in the app-server configuration scope:

```bash
python3 tests/test-routed-v2-relay.py
```

Live tests on the original CLI passed native parent → routed child with shell,
apply_patch, Node/GitHub MCP reads, automatic completion, same-child follow-up and
idle queued messages; routed parent → routed child; routed parent → native
`gpt-6-luna` / max with a formal tool; and paginated old main/child-history readback.
See [acceptance](acceptance-0162.md). These establish the tested capabilities;
every connector operation and desktop behavior after activation are separate
coverage boundaries.

After desktop activation, the original mapped sidebar chat continued its history
and delivered/followed up with a mapped child, but formal `exec` failed. An
isolated `direct`-catalog control did not register that tool; this does not prove
the actual Desktop loaded that catalog. A Code Mode control passed file reading and
shell arithmetic, and an ephemeral original app-server passed mapped parent/child
file reading, `apply_patch`, same-child follow-up and independent parent checks.
The revised catalog fixes that registration condition. A later ordinary
in-session routed child still received an unreadable task and returned flattened
JSON `functions__exec` calls. Live socket and relay I/O sampling showed that the
Desktop process was not using the relay at all. The exact Desktop invocation
reproduced the cause: its trailing plugin `-c` options discard global routing and
catalog overrides placed before `app-server` on this runtime.

The wrapper now puts its protected overrides after the Desktop app-server
arguments. Check effective configuration, rather than relying on process argv:

```bash
python3 scripts/verify-desktop-wrapper.py --catalog /absolute/path/to/routed-models.json
```

This makes no model request and retains only nonsecret boolean checks. It
reproduces the old failure and checks nine fixed configuration values, including
preserved Desktop plugin options. A running app-server retains its old settings;
normally relaunch with the updated bundle before claiming that its actual
ordinary in-session routed child has passed. If that path passes, no further
sidebar-chat investigation is needed.

The revised launcher checks effective app-server configuration once before
opening Desktop, without making a model request. A fixed-wrapper control using
the complete Desktop arguments passed native Luna/max parent → ordinary routed
child/max formal file reading, patching, queued idle-message readback, same-child
follow-up and independent file checks. This is separate from acceptance of the
user's currently running app-server, which still has its old startup settings.

Authentication headers are forwarded in memory to the configured HTTPS service.
Redirects are disabled. Access logs, task content, headers and raw responses are
not written by the relay. Recovery maps contain private task text and must stay
local. This small relay only implements the HTTP Responses path tested here;
it does not add WebSocket Responses or remote/cloud host support.
