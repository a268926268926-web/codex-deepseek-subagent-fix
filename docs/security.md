# Security Model

The relay binds only to loopback and allows HTTPS upstreams or loopback HTTP.
It rejects URLs containing credentials, query strings, fragments, or whitespace.
Request bodies and decompressed bodies are bounded.

The launcher does not replace the installed Codex binary or edit the user's
runtime configuration. On exit it stops only child processes that it owns.

Encrypted old-task recovery sends a task to a separately configured native
provider twice, compares the text, and stores a local SHA-256 mapping. It is not
offline decryption. Hidden reasoning is neither requested nor stored.

Never place credentials, private recovery maps, Codex sessions, CC Switch
databases, or provider logs in this repository.
