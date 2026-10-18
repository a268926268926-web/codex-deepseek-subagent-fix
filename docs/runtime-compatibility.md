# Runtime Compatibility

The model-scoped relay is verified against `codex-cli 0.163.0-alpha.10.2` and
retains the previously verified `0.162.0-alpha.17.2` baseline.

Compatibility is checked in two layers:

1. Local app-server configuration: provider URL, model catalog, Code Mode,
   multi-agent flags, model overrides, wait support, metadata, and desktop plugin
   arguments.
2. Real provider checks: text, shell tools, file changes, child agents, child
   follow-up, and queued V2 messages.

The local checks do not prove that a future provider or model supports the same
wire protocol. Re-run end-to-end tests after a runtime, provider, or principal
model change.
