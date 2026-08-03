# Codex 第三方模型 Subagent 修复（DeepSeek）

让 DeepSeek 等非 OpenAI 模型在 Codex MultiAgentV2 下真正收到并执行子代理任务（`spawn_agent` / `followup_task` / `send_message`）。

关联上游 issue：
- [openai/codex#36586](https://github.com/openai/codex/issues/36586)：DeepSeek 子代理收不到 V2 任务（encrypted_content 被丢弃）
- [openai/codex#36376](https://github.com/openai/codex/issues/36376)：OpenAI 父代理 -> 非 OpenAI 子代理（真密文，需上游解决，见下文）
- [openai/codex#36382](https://github.com/openai/codex/issues/36382)：DeepSeek 官方配置下工具静默不可用（同族问题）

## 症状

codex-cli 0.146.0 + DeepSeek（`deepseek-v4-flash`，`wire_api = "responses"`，models.json 中 `multi_agent_version = "v2"`）：

1. 主代理调用 `collaboration__spawn_agent` 派生子代理；
2. 子代理正常初始化，但回复“没有收到任务”，从不执行任务；
3. `followup_task` 同样无效。

## 根因

V2 的子代理任务通过 `InterAgentCommunication` 投递，模型请求中以 `agent_message` item 携带（任务文本放在 `encrypted_content` 里）。OpenAI 端点会在服务端解密，而 DeepSeek 的 Responses 实现不能消费该内容，任务对模型不可见。

进一步实测（见 docs/qa-evidence.md）：
- 即使把 `encrypted_content` 折叠成明文 `input_text`，DeepSeek 仍可能忽略整个 `agent_message` item；
- 任务以普通 `user` 消息投递时（V1 方式），DeepSeek 能稳定执行。

## 修复方案（本地补丁）

对齐 V1 的投递方式：**非 OpenAI provider 下，V2 的 spawn / followup_task / send_message 直接把任务作为普通 user 消息（`UserInput`）发送给子代理；OpenAI 路径保持 V2 加密通信不变。**

改动（基于 openai/codex commit `2b5bdcf`，即 0.146.0 源码）：

- `codex-rs/core/src/tools/handlers/multi_agents_v2/spawn.rs`：非 OpenAI 时改用 `spawn_agent_with_metadata` + `UserInput::Text`
- `codex-rs/core/src/tools/handlers/multi_agents_v2/message_tool.rs`：非 OpenAI 时 `send_message` / `followup_task` 改用 `send_input` + `UserInput::Text`

补丁文件：[patches/codex-core-non-openai-subagent-payload.patch](patches/codex-core-non-openai-subagent-payload.patch)

## 构建与安装

```bash
# 1. 准备源码（或直接 git apply 补丁到 openai/codex 对应 commit）
git clone https://github.com/openai/codex.git
cd codex
git checkout 2b5bdcf67547860f2e5c5a605009a70026796b2b
git apply /path/to/codex-core-non-openai-subagent-payload.patch

# 2. 构建（需要 Rust stable；首次约 10-15 分钟）
cd codex-rs
cargo build --release -p codex-cli

# 3. 备份并替换本机二进制（npm 安装布局示例）
cp <npm>/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex codex-vendored-backup.bin
install -m 755 target/release/codex <npm>/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex

# 4. 验证
codex --version
```

回滚：用备份二进制替换回去即可。源码构建的 `codex --version` 显示 `0.0.0` 属正常（正式发布流程才注入版本号），功能等同对应源码版本。

## 验证结果

- `cargo test -p codex-core multi_agents`：88 项通过；`cargo fmt --check` / `cargo clippy -p codex-core --tests` 干净
- 真实 DeepSeek 会话（deepseek-v4-flash）：
  - `spawn_agent` 任务 `Reply with exactly the single word PONG` -> 子代理回复 `PONG`
  - `followup_task` 追加 `Reply with exactly the single word PONG2` -> 回复 `PONG2`

详见 [docs/qa-evidence.md](docs/qa-evidence.md)。

## 踩坑记录

### V1 与 V2 的工具族差异

| 版本 | 命名空间 | 工具 |
|---|---|---|
| V1 | `multi_agent_v1` | `spawn_agent`、`send_input`、`resume_agent`、`wait_agent`、`close_agent` |
| V2 | `collaboration` | `spawn_agent`、`send_message`、`followup_task`、`wait_agent`、`interrupt_agent`、`list_agents` |

### models.json 改 v1 不可行（对 DeepSeek）

把 models.json 中 deepseek 条目改为 `multi_agent_version = "v1"` 后：

- V1 工具族被注册为 **Deferred**，必须先调用 `tool_search` 才能载入；
- `tool_search` 虽然在请求的 tools 数组中，但 deepseek-v4-flash 无法识别/调用它（与 #36382 同族问题）；
- 结论：**保持 `multi_agent_version = "v2"` + 本补丁**才是可用组合。

### OpenAI 父 -> 非 OpenAI 子（#36376）

OpenAI 父代理返回的 spawn `message` 是**真密文**（`encrypted_function_args = null`），本地无法解密，本补丁不适用。需要上游在 OpenAI 侧解决明文投递契约。

## 配置模板

- [config/config.toml.example](config/config.toml.example)：DeepSeek provider 配置（密钥用占位符）
- [config/models.json.example](config/models.json.example)：models.json 中 deepseek 条目的关键字段示例

本仓库不含任何密钥或私人配置；请参考模板自行填入自己的 API key。
