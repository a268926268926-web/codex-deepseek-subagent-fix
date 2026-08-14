<p align="center">
  <strong>Codex DeepSeek Subagent Fix</strong>
</p>
<p align="center">
  Make DeepSeek V4 Flash / Pro receive and execute subagent tasks correctly under Codex MultiAgentV2
</p>
<p align="center">
  <a href="https://github.com/CCanxue/codex-deepseek-subagent-fix/releases"><img src="https://img.shields.io/github/v/release/CCanxue/codex-deepseek-subagent-fix?label=release&color=2563eb" alt="release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/CCanxue/codex-deepseek-subagent-fix?color=16a34a" alt="license"></a>
  <a href="https://github.com/CCanxue/codex-deepseek-subagent-fix/releases"><img src="https://img.shields.io/badge/platform-Linux%20x86__64-6b7280" alt="platform"></a>
</p>

## What is this

Codex MultiAgentV2 sends subagent tasks inside the `encrypted_content` of an `agent_message` item. OpenAI decrypts it server-side, but DeepSeek's Responses implementation does not consume it, so subagents never see their tasks. This repository provides a patched Codex CLI that, for non-OpenAI providers, delivers those tasks as plain user messages, enabling `spawn_agent`, `followup_task`, and `send_message`.

## Features

- Supports `deepseek-v4-flash` and `deepseek-v4-pro` for both the main agent and subagents
- Ships a Linux x86_64 prebuilt binary with SHA256 verification
- Runs as a standalone `codex-ds` command without interfering with your existing Codex

## One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/CCanxue/codex-deepseek-subagent-fix/main/install.sh | bash
```

Installs the latest version to `~/.local/bin/codex-ds`. To pin a version or directory:

```bash
curl -fsSL https://raw.githubusercontent.com/CCanxue/codex-deepseek-subagent-fix/main/install.sh | bash -s -- --version 0.146.0 --install-dir ~/bin
```

## Quick start

```bash
# 1. Configure DeepSeek (pick one)
curl -fsSL https://cdn.deepseek.com/api-docs/codex-deepseek-setup-en.sh | bash
# or use the templates under config/

# 2. Launch
codex-ds            # TUI
codex-ds exec ...   # non-interactive
```

## Usage

`deepseek-v4-flash` and `deepseek-v4-pro` share the `deepseek` provider, so switching models does not require changing the provider:

- In the TUI, run `/model`
- CLI: `codex-ds -m deepseek-v4-flash` or `codex-ds -m deepseek-v4-pro`
- Change the default model in `~/.codex/config.toml` via `model`

## Releases

| Upstream version | Release tag | Status |
|---|---|---|
| 0.146.0 | `v0.146.0-deepseek.1` | Published |
| 0.147.0 | `v0.147.0-deepseek.1` | In progress |

`install.sh` installs the latest release by default; use `--version` to pin an upstream version. Run `codex-ds --version` to see which upstream version a binary corresponds to.

## FAQ

Does the official binary work? The main agent can use DeepSeek directly (officially supported), but subagents fail with `agent thread limit reached`. This release is the patched binary, so both paths work.

How do I uninstall? Remove `~/.local/bin/codex-ds`.

Why only Linux x86_64? That is the only platform validated and published so far. Other platforms can be rebuilt per upstream version by the maintainer.

How do I upgrade? When upstream Codex upgrades, the maintainer rebuilds and publishes a matching release; rerun the install command to upgrade.

## Configuration templates

- [config/config.toml.example](config/config.toml.example)
- [config/models.json.example](config/models.json.example)

The repository contains no keys or private configuration; fill in your own API key.

## References

- Upstream issue: [openai/codex#36586](https://github.com/openai/codex/issues/36586)
- Verification notes: [docs/qa-evidence.md](docs/qa-evidence.md)

## License

[Apache-2.0](LICENSE), based on openai/codex.
