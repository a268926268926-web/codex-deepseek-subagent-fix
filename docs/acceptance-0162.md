# Local acceptance on 2026-10-11

Source: `rust-v0.162.0-alpha.17.2`, commit
`740e5af33c71225640e0c1c1555c514c2c93ab74`, Rust 1.95.0, Linux x86_64.
Tests used the configured Responses gateway. The routed alias was
`gpt-5.6-sol` with effort `max`. The provider display name was `OpenAI`.
The source-patch artifact is a development build; its CI script builds release.
The preferred mixed-model path uses the original packaged CLI plus the
[model-scoped relay](routed-relay-0162.md), without that compiled artifact.

## Final model-scoped relay acceptance

| Original CLI through relay | Observed result |
| --- | --- |
| Native parent → routed child | Passed, 32.1 s; new encrypted task automatically read back and cached |
| Native parent → routed child formal tools | Passed, 96.9 s; shell fixture, apply_patch proof independently read from disk, Node result 391, GitHub identity |
| Routed parent → routed child / same-child follow-up | Passed, 35.3 s |
| Native parent idle queue / later follow-up | Passed, 85.8 s; child read the queued note absent from the follow-up prompt |
| Routed parent → native gpt-6-luna / max | Passed, 45.5 s; native child used a formal tool and returned 391; verification follow-up passed |
| Old paginated child history | Completed, 12.2 s; task map used without changing the copied five ciphertexts |
| Old paginated main history | Completed, 11.4 s; original model/effort, fork, completed work and preservation goal read back |
| Relay/catalog/wrapper contracts | Seventeen passed; includes preserved Code Mode/native model metadata, launcher mode validation, real loopback HTTP preservation/conversion, recovery tool-call rejection, bounded gzip, failed-cache-write behavior and Desktop argument preservation |
| Installation/configuration before desktop activation | Packaged executable and config SHA-256 unchanged at that checkpoint |
| Delivered launcher CLI path | Passed, 20.7 s; native parent → routed child; owned relay and temporary wrapper cleaned up |
| Guarded fresh native parent → routed child | Passed, 51.0 s; this run did not need an uncached ciphertext readback |
| Final guarded old-communication recovery | Five communications, including three MESSAGE items, recovered twice with identical texts matching the earlier verified map; original built-in native catalog |
| Final old paginated child with one cache miss | Completed, 17.5 s; cache grew from 9 to 10 and exactly matched the verified map; copied history remained byte-equal |

Ten encrypted task payloads were cached locally after old/new native-origin
tests; each automatically accepted task needed two identical native readbacks.
The relay's access log remained empty. CLI completion durations include tool/model
work and first-use readback latency and do not measure generation TPS.

The original and guarded readback controls both failed when given the routed
external catalog. Both succeeded for the same old ciphertext using the original
built-in catalog (6.8 s and 4.3 s respectively). This isolates a catalog-dependent
compatibility condition; it does not prove a general service outage. Automatic
recovery now uses the built-in catalog independently of the desktop wrapper.
Recovery requests carry no tools, including Responses-Lite additional tools;
fault-injected upstream tool calls are rejected before CLI dispatch.

## Desktop tool-mode correction

The activated desktop's argv referenced the delivered wrapper and loopback relay;
later effective-config and socket checks showed those overrides were discarded.
Its existing mapped sidebar chat resumed prior history and completed a mapped-child
spawn/follow-up in 104.3 seconds, but could not complete formal `functions.exec`.
That run did not read its random fixture and is a partial failure, not full desktop
tool acceptance. Its model-reported registration diagnosis was not proof of the
live registry contents. Native Luna in the same desktop successfully used formal
exec. The direct-catalog control below is an isolated result, not evidence that
the actual Desktop had loaded that catalog.

An isolated request-metadata comparison established that the earlier `direct`
catalog registers direct `exec_command` but does not register Code Mode `exec`.
With `code_mode_only`, the original runtime advertised its freeform `exec` tool
and nested formal shell. A real routed-model control read the exact 44-byte random
fixture and returned shell arithmetic 391 in 20.5 seconds.

An ephemeral original app-server with a desktop client name then completed a
routed parent → routed child/max chain in 80.5 seconds: exact fixture read,
official `apply_patch` creation, same-child follow-up, shell arithmetic, and
independent parent verification of the proof. The parent initially tried a nested
collaboration invocation, which failed; its direct retry succeeded and created
exactly one child. Eight independent local outcome checks passed.

The generator preserves the original Code Mode and the wrapper enables its host.
After relaunch, the mapped sidebar and an ordinary in-session mapped child still
failed formal exec, returning `function_call functions__exec` JSON instead of the
expected freeform `custom_tool_call exec`. The ordinary native Luna/max control
passed formal fixture read, shell calculation, official patch and independent
proof verification. Follow-up and queued-message checks were not performed on
that failed mapped child.

Live production sampling collected 180 socket snapshots over 43.2 seconds:
none showed an app-server/relay connection, the relay's I/O syscall counters did
not change, and the new 504-byte encrypted task was not cached. The exact Desktop
CLI invocation then independently reproduced the argument-layering bug: adding
plugin `-c` flags after `app-server` drops the global provider URL, catalog and
Code Mode overrides placed before the subcommand.

The corrected wrapper places protected overrides in the app-server argument
scope. The original runtime's `config/read` reproduced the old failure and then
passed all nine fixed checks, including the retained plugin arguments. This is
tested by `scripts/verify-desktop-wrapper.py`; no model call, credentials or
private config content is logged.

Using the fixed wrapper with the same complete Desktop argument shape, a native
Luna/max parent spawned one ordinary gpt-5.6-sol/max child and completed in
252.3 seconds. The child read an exact random 75-byte UTF-8 fixture, ran formal
shell arithmetic (391), used official `apply_patch` to create a 30-byte proof,
then read the previously queued idle message and computed 551 in a follow-up on
the same child. Runtime command/file-change events identify that child's thread;
the parent and root independently verified the files. Fourteen outcome checks
passed. The test's private recovery cache gained exactly three communication
entries; only the queued-message entry contains its marker. Original executable,
user config and production recovery-cache hashes stayed unchanged during this
fix. This duration includes recovery and model/tool work, not a TPS benchmark.

The launcher now performs one tool-free `config/read` preflight before opening
Desktop and refuses to open it if its effective routing/catalog/V2 settings do
not match. This creates no model request or token usage. A smoke Desktop passed
this path and its owned temporary wrapper and relay were cleaned up.
The user's running instance still needs to load the corrected wrapper before
its actual ordinary mapped child can be accepted. These controls do not
establish every connector operation. The
installed CLI remained byte-identical; the user's config hash changed after
app/user activity and is not claimed unchanged after the earlier activation.

## Optional source patch and recovery controls

| Check | Observed result |
| --- | --- |
| Regression filter `plaintext` | 12 passed, 0 failed; 9 patch regressions and 3 existing history tests |
| Final CLI build/version | Completed; `codex-cli 0.162.0-alpha.17.2` |
| Patch forward/reverse check | Passed against pinned source and modified tree |
| Routed parent formal shell | Exact readable fixture returned |
| Routed child formal shell | Exact fixture returned |
| Routed child `apply_patch` | Created 16-byte proof, independently read from disk |
| Routed child Node MCP | Calculation returned 391 |
| Routed child GitHub MCP | Authenticated read-only identity returned |
| V2 spawn/completion/follow-up | Passed with the same child in two turns |
| Idle queued message | Did not wake the child; was read exactly on the next follow-up |
| Old native task recovery | Five task texts returned; two independent native reads matched |
| Recovery using task items alone | Same five texts; no other child history supplied |
| Luna recovery comparison | Four texts exact; first task had sentence-order differences |
| Packaged recovery helper | Generated the same five-task map with two matching reads |
| Routed old main-history readback | Reported original model/effort, fork and completed capabilities |
| Routed old child with request-only map | Reported original test and last requested calls; source kept five ciphertexts |
| Paginated main copy | Readback passed using an isolated Codex home and SQLite metadata |
| Paginated child copy | Request-only recovery passed; copied rollout byte-equal to original |
| Fresh V2 with recovery map enabled | New child read fixture and completed same-child follow-up |

Main-history test copies omitted one unfinished tool call at the snapshot boundary.
They retained ordinary messages, completed tool calls/results, encrypted reasoning
and the existing compaction. Original files were not changed. An earlier untrimmed
paginated test waited without completing and was terminated; it is not a pass.

An initial source-patch native-parent V2 request returned an upstream error before
dispatch. The original CLI and the same compiled CLI with its global switch unset
passed native-tool controls. A native-only direct catalog did not fix the enabled
switch. This isolated the process-wide adaptation and led to the per-alias relay;
the final relay established both cross-model directions above. A message sent immediately after spawn
was not reported in that child's first turn; the dedicated idle-queue test verified
the documented next-turn delivery behavior.

Recovery is a model-mediated readback of user task communications, not a local
decryptor or a byte-for-byte cryptographic export. It needs a compatible service
and may fail after backend/key-scope changes. Private ciphertext, histories,
recovered task maps, credentials and raw gateway logs are excluded from this repo.
CLI acceptance alone does not establish desktop behavior after relaunch; the
desktop failure and subsequent Code Mode controls are recorded separately above.
