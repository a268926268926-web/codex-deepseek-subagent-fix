# Codex 第三方模型 Subagent 修复（DeepSeek Flash / Pro）

让 DeepSeek 等非 OpenAI 模型在 Codex MultiAgentV2 下真正收到并执行子代理任务（`spawn_agent` / `followup_task` / `send_message`）。仓库同时覆盖 DeepSeek V4-Pro 官方 GA（2026-08-13）之后的使用与测试情况。

## 本仓库内容

- [补丁](patches/codex-core-non-openai-subagent-payload.patch)：codex-cli 0.146.0 源码补丁，共 2 个核心文件改动
- [配置模板](config/config.toml.example)：DeepSeek flash / pro 脱敏配置（config.toml、models.json）
- [QA 证据](docs/qa-evidence.md)：flash / pro 真实会话测试记录

## 症状

codex-cli 0.146.0 + DeepSeek（`wire_api = "responses"`，models.json 中 `multi_agent_version = "v2"`）：

1. 主代理 `spawn_agent` 派生子代理，子代理正常初始化但回复“没有收到任务”，从不执行；
2. `followup_task` 同样无效。

## 根因

V2 的子代理任务通过 `InterAgentCommunication` 投递，请求中以 `agent_message` item 携带，任务文本放在 `encrypted_content`。OpenAI 端点在服务端解密，而 DeepSeek 的 Responses 实现不消费该内容，任务对模型不可见；改为普通 `user` 消息投递（V1 方式）后，DeepSeek 可稳定执行。

上游 issue：[#36586](https://github.com/openai/codex/issues/36586)（主报告）；同族问题 #36382、#37197、#36376（OpenAI 父 -> 非 OpenAI 子，需上游解决）。其他仓库中的同类报告（Zed、代理网关、路由器等）本质相同，不再逐一展开。

## 修复方案

非 OpenAI provider 下，V2 的 spawn / followup_task / send_message 直接把任务作为普通 user 消息（`UserInput::Text`）发送给子代理；OpenAI 路径保持 V2 加密通信不变。

- `spawn.rs`：改用 `spawn_agent_with_metadata` + `UserInput::Text`
- `message_tool.rs`：改用 `send_input` + `UserInput::Text`

补丁基于 openai/codex commit `2b5bdcf`（0.146.0）。

## 构建与安装

```bash
git clone https://github.com/openai/codex.git
cd codex
git checkout 2b5bdcf67547860f2e5c5a605009a70026796b2b
git apply /path/to/codex-core-non-openai-subagent-payload.patch
cd codex-rs
cargo build --release -p codex-cli
```

备份原二进制后替换（npm 安装布局：`@openai/codex/.../vendor/x86_64-unknown-linux-musl/bin/codex`）。源码构建的 `codex --version` 显示 `0.0.0` 属正常；回滚用备份二进制。

## 验证结果

**Flash（2026-08-03）**：`spawn_agent` -> `PONG`，`followup_task` -> `PONG2`，重复验证通过；`cargo test -p codex-core multi_agents` 88 项通过，fmt / clippy 干净。

**Pro（2026-08-14，官方 GA 后）**：

- Responses API：`deepseek-v4-pro` HTTP 200，正常返回
- 补丁版 `codex`：普通 exec 正常；spawn 子代理成功，子代理返回 `OK`
- 官方原版 `codex-official` 0.146.0：普通 exec 正常（需 `model_catalog_json` + provider 配置）；spawn 子代理失败，报 `collab spawn failed: agent thread limit reached`

## 与官方原版二进制的兼容

- 主代理：官方原版可直接使用 flash / pro（按官方一键脚本或本仓库配置模板）
- 子代理：官方原版仍不可用（spawn 报 thread limit），需要本补丁
- 不想编译的替代方案：models.json 设 `multi_agent_version = "v1"` 且 `supports_search_tool = false`，官方原版可暴露 V1 工具族；代价是失去 V2 工具族（`followup_task` / `send_message` / `list_agents` / `interrupt_agent`）
- DeepSeek 官方 setup 脚本生成的 models.json 仍是 v2：解决主代理配置，不解决子代理投递

## 配置模板

- `config.toml.example`：默认 flash，注释一行即可切 pro
- `models.json.example`：flash + pro 关键字段
- 本仓库不含任何密钥或私人配置，请替换为自己的 API key

## 踩坑记录

V1 / V2 工具族差异：

| 版本 | 命名空间 | 工具 |
|---|---|---|
| V1 | `multi_agent_v1` | `spawn_agent`、`send_input`、`resume_agent`、`wait_agent`、`close_agent` |
| V2 | `collaboration` | `spawn_agent`、`send_message`、`followup_task`、`wait_agent`、`interrupt_agent`、`list_agents` |

- 仅改 `multi_agent_version = "v1"` 时，V1 工具族注册为 Deferred，需 `tool_search` 发现；DeepSeek 无法调用 `tool_search`（与 #36382 同族），需同时设 `supports_search_tool = false`
- OpenAI 父 -> 非 OpenAI 子（#36376）：spawn message 是真密文，本地无法解密，本补丁不适用，需上游解决

## 许可

Apache-2.0
