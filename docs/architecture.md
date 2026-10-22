# Architecture

## Request Path

```text
Codex desktop
  -> installed codex-cli
  -> invocation-scoped wrapper
  -> model-scoped relay on loopback
  -> CC Switch aggregation proxy
  -> selected provider
```

The wrapper changes only the current launch. It does not replace the installed
binary or rewrite `config.toml`. The relay keeps native models and responses
byte-equivalent while adapting explicitly configured routed aliases.

## Components

### Launcher

`launch-routed-codex.py` validates the installed runtime, provider, model catalog,
routes, recovery profile, and private file permissions. It starts the relay,
creates an invocation-scoped wrapper, verifies the effective app-server
configuration, and launches the desktop. On exit it stops only processes it owns.

### Relay

`routed-v2-relay.py` binds only to loopback. For a mapped model it:

- converts V2 child task `agent_message` content to readable input;
- preserves message IDs, sender/recipient roles, and ordinary history;
- rewrites the model only when an explicit route target exists;
- streams upstream responses without buffering normal native traffic.

The direct `cn:deepseek-v4.1-flash` alias maps explicitly to
`ccs-workbuddy/cn:deepseek-v4.1-flash`. This keeps DeepSeek independent of the CCS
aggregation default.

With `portable_plaintext` enabled, the relay removes the `message.encrypted`
schema marker for the routed request and marks message calls with
`encrypted_function_args: []`. It retains Codex's registered `collaboration`
namespace so direct tool dispatch continues to match the host registry. This
preserves the V2 agent graph while using standard Responses messages at the
external provider boundary.

### Recovery

An old encrypted task is looked up by SHA-256. On a miss, automatic recovery reads
the task through an independent native-compatible provider twice and accepts only
matching text. Recovery requests contain no tools and reject tool-call output.
This is model-mediated task recovery, not offline decryption.

### Runtime Updates

`update-runtime.py` starts an owned app-server and checks provider URL, catalog,
Code Mode, Multi-Agent V2, model overrides, wait support, metadata visibility, and
desktop plugin arguments. Successful checks are bound to runtime and source hashes
in a private `.local.json` record. A failed check keeps an unknown runtime blocked.

## Model Routing

```text
native model name     -> unchanged request through CCS default
routed alias + target -> adapted request, target model name
routed alias only     -> adapted request, original model name
```

CCS aggregation member prefixes are parsed before default routing. Therefore an
explicit target such as `ccs-workbuddy/<model>` remains bound to WorkBuddy even
when the aggregation default changes.

## Trust Boundaries

- The relay listens only on `127.0.0.1`.
- Remote upstreams require HTTPS.
- Redirects are disabled.
- Request and decompressed sizes are bounded.
- Credentials remain in the client process or a private recovery profile.
- Sessions, recovery maps, provider databases, and generated catalogs are not
  public repository content.

## Validation

- Local unit tests cover request adaptation, routing, recovery guards, config
  validation, update checks, and public-tree hygiene.
- Desktop effective configuration is checked without a model request.
- Provider tests cover text, shell tools, file changes, parent/child agents,
  follow-up, and queued messages.
- The verified desktop runtime for this branch is
  `codex-cli 0.163.0-alpha.10.2`; `0.162.0-alpha.17.2` remains a compatibility
  baseline.

See [runtime compatibility](runtime-compatibility.md) and the
[historical acceptance record](history/acceptance-0162.md).
