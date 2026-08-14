<p align="center">
  <strong>Codex DeepSeek 子代理修复</strong>
</p>
<p align="center">
  让 DeepSeek V4 Flash / Pro 在 Codex MultiAgentV2 下正常收发子代理任务
</p>
<p align="center">
  <a href="https://github.com/CCanxue/codex-deepseek-subagent-fix/releases"><img src="https://img.shields.io/github/v/release/CCanxue/codex-deepseek-subagent-fix?label=release&color=2563eb" alt="release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/CCanxue/codex-deepseek-subagent-fix?color=16a34a" alt="license"></a>
  <a href="https://github.com/CCanxue/codex-deepseek-subagent-fix/releases"><img src="https://img.shields.io/badge/platform-Linux%20x86__64-6b7280" alt="platform"></a>
</p>

## 这是什么

Codex MultiAgentV2 会把子代理任务放进 `agent_message` 的 `encrypted_content`。OpenAI 在服务端解密这段内容，而 DeepSeek 的 Responses 实现不消费它，于是子代理总是「收不到任务」。本仓库提供打补丁后的 Codex CLI 二进制：在非 OpenAI provider 下，改为以普通 user 消息投递，让 DeepSeek 正常使用 `spawn_agent`、`followup_task`、`send_message`。

## 特性

- 支持 `deepseek-v4-flash` 与 `deepseek-v4-pro`，主代理和子代理均可用
- 提供 Linux x86_64 预编译二进制，安装时校验 SHA256
- 以独立命令 `codex-ds` 运行，不影响系统已有的 Codex

## 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/CCanxue/codex-deepseek-subagent-fix/main/install.sh | bash
```

默认安装最新版本到 `~/.local/bin/codex-ds`。如需指定版本或目录：

```bash
curl -fsSL https://raw.githubusercontent.com/CCanxue/codex-deepseek-subagent-fix/main/install.sh | bash -s -- --version 0.146.0 --install-dir ~/bin
```

## 快速开始

```bash
# 1. 配置 DeepSeek（二选一）
curl -fsSL https://cdn.deepseek.com/api-docs/codex-deepseek-setup-en.sh | bash
# 或参考本仓库 config/ 下的脱敏模板

# 2. 启动
codex-ds            # TUI
codex-ds exec ...   # 非交互
```

## 使用

`deepseek-v4-flash` 与 `deepseek-v4-pro` 同属 `deepseek` provider，切换模型无需更换 provider：

- TUI 内输入 `/model` 选择
- CLI 使用 `codex-ds -m deepseek-v4-flash` 或 `codex-ds -m deepseek-v4-pro`
- 修改默认模型：编辑 `~/.codex/config.toml` 中的 `model`

## 发布版本

| 上游版本 | Release tag | 状态 |
|---|---|---|
| 0.146.0 | `v0.146.0-deepseek.1` | 已发布 |
| 0.147.0 | `v0.147.0-deepseek.1` | 待发布 |

`install.sh` 默认安装 latest，可用 `--version` 指定上游版本。二进制自身对应哪个上游版本，可用 `codex-ds --version` 查看。

## 常见问题

官方原版二进制能用吗？主代理可以直接使用 DeepSeek（官方已适配），但子代理会报 `agent thread limit reached`；本 release 是打补丁后的二进制，两条路径都可用。

如何卸载？删除 `~/.local/bin/codex-ds` 即可。

为什么只有 Linux x86_64？目前只验证并发布了该平台；其他平台可按上游版本重新构建，由维护者发布。

如何升级？上游 Codex 升级后，由维护者重新打补丁构建并发布对应 release，重新运行安装命令即可升级。

## 配置模板

- [config/config.toml.example](config/config.toml.example)
- [config/models.json.example](config/models.json.example)

仓库不含任何密钥或私人配置，请自行替换 API key。

## 参考

- 上游问题：[openai/codex#36586](https://github.com/openai/codex/issues/36586)
- 验证记录：[docs/qa-evidence.md](docs/qa-evidence.md)

## 许可

[Apache-2.0](LICENSE)，基于 openai/codex 修改。
