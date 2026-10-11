# Local acceptance on 2026-10-11

Source: `rust-v0.162.0-alpha.17.2`, commit
`740e5af33c71225640e0c1c1555c514c2c93ab74`, Rust 1.95.0, Linux x86_64.
Tests used the configured Responses gateway. The routed alias was
`gpt-5.6-sol` with effort `max`. The provider display name was `OpenAI`.
The local runnable artifact is a development build; the CI script builds release.

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

An initial native-parent V2 request returned an upstream error before dispatch.
A packaged native-model baseline later passed; a native-parent cross-model chain
has not been established by those results. A message sent immediately after spawn
was not reported in that child's first turn; the dedicated idle-queue test verified
the documented next-turn delivery behavior.

Recovery is a model-mediated readback of user task communications, not a local
decryptor or a byte-for-byte cryptographic export. It needs a compatible service
and may fail after backend/key-scope changes. Private ciphertext, histories,
recovered task maps, credentials and raw gateway logs are excluded from this repo.
Desktop activation and behavior after relaunch are not covered by CLI acceptance.
