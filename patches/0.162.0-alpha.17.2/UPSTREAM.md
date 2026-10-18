# Pinned upstream

- Repository: https://github.com/openai/codex
- Tag: `rust-v0.162.0-alpha.17.2`
- Commit: `740e5af33c71225640e0c1c1555c514c2c93ab74`
- Rust: `1.95.0`
- Target: Linux x86_64

This patch is opt-in. It does not depend on provider display names or change the
meaning of `ModelProviderInfo::is_openai()`. Without the environment variable
`CODEX_V2_PLAINTEXT_MESSAGES=1`, upstream message transport is unchanged.

The `Cargo.lock` workspace versions in this tag are normalized by Cargo at build
time. That packaging change is excluded from the source patch.
