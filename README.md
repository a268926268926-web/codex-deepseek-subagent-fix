<p align="center">
  <strong>🤖 Codex DeepSeek Subagent Fix</strong>
</p>
<p align="center">
  Make DeepSeek V4 Flash / Pro receive and execute subagent tasks correctly under Codex MultiAgentV2
</p>
<p align="center">
  <a href="https://github.com/CCanxue/codex-deepseek-subagent-fix/releases"><img src="https://img.shields.io/github/v/release/CCanxue/codex-deepseek-subagent-fix?label=release&color=2563eb" alt="release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/CCanxue/codex-deepseek-subagent-fix?color=16a34a" alt="license"></a>
  <a href="https://github.com/CCanxue/codex-deepseek-subagent-fix/releases"><img src="https://img.shields.io/badge/platform-Linux%20x86__64-6b7280" alt="platform"></a>
</p>

## 🔍 What is this

### 0.162 adaptation and old-task recovery

The `codex-0.162-v2-plaintext` branch adds an opt-in adaptation pinned to
`rust-v0.162.0-alpha.17.2`, including routed providers whose display name remains
`OpenAI`. For sessions mixing native and routed models, use the
[model-scoped relay and launcher](docs/routed-relay-0162.md): the packaged CLI
stays unchanged, and only the explicit routed alias receives compatibility changes.
Live tests passed native parent → routed child, routed parent → native Luna/max,
formal shell/file/MCP tools, queued follow-up and old paginated-history recovery.
See the [acceptance record](docs/acceptance-0162.md).

The optional [source patch](docs/0.162-plaintext.md) has a process-wide switch and
is intended for wholly routed invocations. It is not the mixed-model desktop path.
Both modes can replay old tasks through a private local map produced by native
compatible-service task readback; saved ciphertext and visible history stay intact.
Native recovery uses the original built-in model catalog, with tools removed and
tool-call output blocked before CLI dispatch. Fourteen relay contracts and two
matching reads of five old task/queued communications passed locally.
The old upstream `install.sh` still selects 0.146/0.147 releases and does not
install this adaptation. No private history, map or credential is included here.

Codex MultiAgentV2 sends subagent tasks inside the `encrypted_content` of an `agent_message` item. OpenAI decrypts it server-side, but DeepSeek's Responses implementation does not consume it, so subagents never see their tasks. This repository provides a patched Codex CLI that, for non-OpenAI providers, delivers those tasks as plain user messages, enabling `spawn_agent`, `followup_task`, and `send_message`.

## ✨ Features

- ✅ Supports `deepseek-v4-flash` and `deepseek-v4-pro` for both the main agent and subagents
- 🛡️ Publishes SHA256 checksums, GitHub build provenance, and keyless `cosign` signatures
- 🔌 Runs as a standalone `codex-ds` command without interfering with your existing Codex

## 🚀 One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/CCanxue/codex-deepseek-subagent-fix/main/install.sh | bash
```

Installs the latest version to `~/.local/bin/codex-ds`. To pin a version or directory:

```bash
curl -fsSL https://raw.githubusercontent.com/CCanxue/codex-deepseek-subagent-fix/main/install.sh | bash -s -- --version 0.146.0 --install-dir ~/bin
```

## ⚡ Quick start

```bash
# 1. Configure DeepSeek (pick one)
curl -fsSL https://cdn.deepseek.com/api-docs/codex-deepseek-setup-en.sh | bash
# or use the templates under config/

# 2. Launch
codex-ds            # TUI
codex-ds exec ...   # non-interactive
```

## 🧰 Usage

`deepseek-v4-flash` and `deepseek-v4-pro` share the `deepseek` provider, so switching models does not require changing the provider:

- In the TUI, run `/model`
- CLI: `codex-ds -m deepseek-v4-flash` or `codex-ds -m deepseek-v4-pro`
- Change the default model in `~/.codex/config.toml` via `model`

## 📦 Releases

| Upstream version | Release tag | Status |
|---|---|---|
| 0.146.0 | `v0.146.0-deepseek.1` | Published |
| 0.147.0 | `v0.147.0-deepseek.1` | Published |

`install.sh` installs the latest release by default; use `--version` to pin an upstream version. Run `codex-ds --version` to see which upstream version a binary corresponds to.

## Source and verification

The release binary is built from an exact OpenAI Codex tag and a public patch.
For 0.147.0, the source base is [`rust-v0.147.0`](patches/0.147.0/UPSTREAM.md)
at commit `be6e8eac029b183056b7e4402879f15d2c85f61b`; the complete change is
[`0001-deliver-non-openai-subagent-tasks-as-user-input.patch`](patches/0.147.0/0001-deliver-non-openai-subagent-tasks-as-user-input.patch).
The build strips upstream's packaging-only debug symbols before publishing the
Linux executable.

To build it yourself on Linux x86_64 with Rust and Git installed:

```bash
git clone https://github.com/CCanxue/codex-deepseek-subagent-fix.git
cd codex-deepseek-subagent-fix
scripts/build-release.sh
```

Every CI-built release includes `SHA256SUMS`, a keyless Sigstore signature
(`.sig`) and certificate (`.pem`). Verify a downloaded release before using it:

```bash
sha256sum -c SHA256SUMS --ignore-missing
cosign verify-blob \
  --certificate codex-deepseek-subagent-fix-linux-x86_64.pem \
  --signature codex-deepseek-subagent-fix-linux-x86_64.sig \
  --certificate-identity-regexp 'https://github.com/CCanxue/codex-deepseek-subagent-fix/.github/workflows/release.yml@refs/tags/.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  codex-deepseek-subagent-fix-linux-x86_64
```

GitHub also stores a build provenance attestation for the binary. With the GitHub
CLI installed, verify it using:

```bash
gh attestation verify codex-deepseek-subagent-fix-linux-x86_64 \
  --repo CCanxue/codex-deepseek-subagent-fix
```

The current published releases predate this workflow and therefore do not have
the signature or attestation assets. The workflow currently audits only
`v0.147.0-deepseek.*`; add and validate a new pinned patch before publishing a
different upstream version.

## ❓ FAQ

Does the official binary work? The main agent can use DeepSeek directly (officially supported), but subagents fail with `agent thread limit reached`. This release is the patched binary, so both paths work.

How do I uninstall? Remove `~/.local/bin/codex-ds`.

Why only Linux x86_64? That is the only platform validated and published so far. Other platforms can be rebuilt per upstream version by the maintainer.

How do I upgrade? When upstream Codex upgrades, the maintainer rebuilds and publishes a matching release; rerun the install command to upgrade.

## 📁 Configuration templates

- [config/config.toml.example](config/config.toml.example)
- [config/models.json.example](config/models.json.example)

The repository contains no keys or private configuration; fill in your own API key.

## 🔗 References

- Upstream issue: [openai/codex#36586](https://github.com/openai/codex/issues/36586)
- Verification notes: [docs/qa-evidence.md](docs/qa-evidence.md)

## 📄 License

[Apache-2.0](LICENSE), based on openai/codex.
