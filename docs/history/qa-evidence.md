# QA 证据（脱敏摘要）

环境：codex-cli 0.146.0 源码（commit 2b5bdcf），DeepSeek `deepseek-v4-flash`，`wire_api = "responses"`，models.json `multi_agent_version = "v2"`。日期：2026-08-03。

## 修复前（RED）

官方未打补丁二进制：

- 主代理 `collaboration__spawn_agent` 派生子代理，任务为 `reply with the single word PONG`；
- 会话记录显示子代理收到的 `agent_message` 内容为：
  - `input_text`: `Message Type: NEW_TASK ... Payload:`（空）
  - `encrypted_content`: 任务文本（DeepSeek 不消费该块）
- 子代理最终回复：“I'm ready to help with your Go2 RL MoE CTS project. What would you like me to work on?”，未执行任务。

## 中间实验：请求层折叠（已废弃）

在 `build_responses_request` 非 OpenAI 分支把 `encrypted_content` 折叠为明文 `input_text`：

- 代理抓包确认 wire 上 `agent_message` 已携带完整明文 payload；
- 但 deepseek-v4-flash 仍频繁回复“没有任务”或递归 spawn（模型对 `agent_message` item 的消费不可靠）；
- 结论：请求层转换不够，故最终改为 V1 式 `UserInput` 直投（见 README）。

## 修复后（GREEN）

安装补丁二进制 + v2 元数据，真实 DeepSeek 会话：

1. `spawn_agent`，任务 `Reply with exactly the single word PONG and nothing else.`
   -> 子代理最终回答 `PONG`
2. `followup_task` 追加 `Reply with exactly the single word PONG2 and nothing else.`
   -> 子代理最终回答 `PONG2`
3. 重复验证（spawn + followup）：`PONG` / `PONG2`

## DeepSeek V4-Pro（2026-08-14，官方 GA 后）

环境：`deepseek-v4-pro`，`wire_api = "responses"`，models.json `multi_agent_version = "v2"`（配置含 `model_catalog_json`）。

1. Responses API：`deepseek-v4-pro` HTTP 200，正常返回；
2. 补丁版 `codex` 普通 exec：返回 `OK`；
3. 补丁版 `codex` spawn 子代理（任务 `Reply with exactly: OK`）：子代理最终返回 `OK`，主代理正确收到结果；
4. 官方原版 `codex-official` 0.146.0（临时 CODEX_HOME + `env_key`，未改动官方配置）普通 exec：返回 `OK`；
5. 官方原版 spawn 子代理：失败，日志报 `collab spawn failed: agent thread limit reached`，随后 wait 报 `an agent cannot interrupt itself`。

结论：DeepSeek 官方已支持 v4-pro 主代理；第三方子代理仍需补丁版二进制。

## 自动化测试

- `cargo test -p codex-core multi_agents`：88 passed
- `cargo fmt --check`：通过
- `cargo clippy -p codex-core --tests`：无新增告警

## models.json v1 实验

按上游 workaround 将 deepseek 条目改为 `multi_agent_version = "v1"`：

- 请求 wire 的 tools 数组中不出现 `multi_agent_v1__*`，只有 `tool_search`；
- deepseek-v4-flash 声称工具列表中不存在 `tool_search`（实际存在），无法发现 V1 工具族；
- 结论：v1 + DeepSeek 当前不可用；已恢复 v2。

## 说明

- 本文件为脱敏摘要，原始会话日志、密钥、本地路径均不公开。
- 二进制备份（约 300MB）不随仓库分发；需要时从本机构建或自行编译。
