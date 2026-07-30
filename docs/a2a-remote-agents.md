# Polynoia 远端 A2A Agent 使用与测试

## 结论

Polynoia 原有的本地多 Agent 协作不依赖 A2A：本地 Claude Code、Codex 和
OpenCode 仍通过 PAP（Polynoia Adapter Protocol）进入同一套对话、编排和
burst 生命周期。

当 Agent 运行在另一个进程、主机、云平台或不同框架中时，A2A 用来解决互操作：
Polynoia 读取远端 Agent Card，了解其身份、技能、输入输出模式和调用端点，再将
A2A 消息与任务事件翻译成 PAP。当前版本是 **A2A 客户端**，不会把 Polynoia
自身暴露为 A2A Server。

## 从哪里发现 Agent

A2A 标准化的是 Agent Card，不是一个全球统一的 Agent 搜索站。官方文档列出三种
常见发现方式：

1. Agent 所有者提供域名、基础 URL 或 Agent Card URL。公开 Agent 通常在
   `https://agent.example/.well-known/agent-card.json` 发布卡片。
2. 企业目录或受管 marketplace 按 skill、tag、provider 等条件返回 Agent Card
   URL。A2A 规范目前没有规定这类 registry 的统一查询 API。
3. 在开发或私有环境中，通过配置、环境变量或双方约定直接提供 URL。

参考：

- [A2A 官方 Agent Discovery 指南](https://a2a-protocol.org/latest/topics/agent-discovery/)
- [A2A 1.0 规范](https://a2a-protocol.org/latest/specification/)
- [官方示例 Agent 仓库](https://github.com/a2aproject/a2a-samples)

Polynoia v1 实现第 1、3 种方式：

- “发现”接受域名、基础 URL 或完整卡片 URL。
- 输入基础 URL 时，自动请求 `/.well-known/agent-card.json`。
- “安装后的联系人”是本机可信目录；只有安装并加入对话的远端 Agent 才能收到
  对话任务。
- 暂不提供公共 registry 搜索。后续 registry connector 只需返回 Agent Card URL，
  后面的预览、安装和调用链路可以复用。

不要把互联网上随机找到的 Agent 当作可信执行方。Agent Card、回复、artifact 和
任务状态都属于不可信输入。

## 在 UI 中发现和调用

1. 打开侧栏的“新建联系人”。
2. 切换到 `Remote A2A`。
3. 输入 Agent 的域名、基础 URL 或 Agent Card URL，点击“发现 Agent”。
4. 检查名称、描述、skills、输入输出模式、协议版本、endpoint 和签名状态。
5. 如果卡片要求 Bearer Token，只填写 Polynoia 服务端已有的环境变量名。
6. 点击“安装联系人”，再把该联系人加入 1:1 对话或群聊。
7. 直接发消息，或让群聊协调者像调用本地联系人一样把任务 dispatch 给它。

安装时会重新拉取卡片并比对 hash。如果预览后卡片发生变化，安装返回
`card_changed`，必须重新检查并确认。联系人详情页可以手动刷新卡片；能力或安全
配置变化会列出变更项，并使旧会话失效。

## 通过 API 验证发现与安装

假设 Polynoia 后端运行在 `http://127.0.0.1:7780`，远端 Agent 运行在
`http://127.0.0.1:9999`：

```bash
curl -sS http://127.0.0.1:7780/api/a2a/discover \
  -H 'content-type: application/json' \
  -d '{"locator":"http://127.0.0.1:9999"}'
```

响应中的 `agent.card_hash` 是安装确认值：

```bash
curl -sS http://127.0.0.1:7780/api/a2a/install \
  -H 'content-type: application/json' \
  -d '{
    "locator":"http://127.0.0.1:9999",
    "expected_card_hash":"sha256:<discover 返回的 64 位 hash>"
  }'
```

这两个接口只负责发现和安装。实际调用继续走 Polynoia 的正常对话/WebSocket
路径，不需要另建一套 A2A 编排 API。

## 本地确定性测试

仓库内的回环测试会启动一个真实的本地 FastAPI A2A Server，并同时使用官方
Python SDK 的服务端和客户端。它不依赖外网、LLM 或密钥，覆盖：

- Agent Card 发现与安装；
- 数据库中的联系人和会话池解析；
- JSON-RPC 流式 artifact 到 PAP 事件的转换；
- 多轮 `context_id` 复用；
- A2A cancel 请求传播；
- 本地 worker 与远端 worker 混合 burst；
- 远端失败时所有 lane 正确结束，协调者仍执行汇总；
- 远端 adapter 不接收本地 `workspace_id` 或 `agent_id`。

从仓库根目录运行：

```bash
apps/server/.venv/bin/pytest \
  apps/server/tests/integration/test_a2a_loopback.py -q
```

## 使用官方 Hello World 示例测试

官方 Python SDK 当前实现 A2A 1.0，并提供运行在 `127.0.0.1:9999` 的 Hello
World 示例。先在另一个目录启动它：

```bash
git clone https://github.com/a2aproject/a2a-samples.git
cd a2a-samples/samples/python/agents/helloworld
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python __main__.py
```

启动后可在 Polynoia 的 `Remote A2A` 页面输入：

```text
http://127.0.0.1:9999
```

也可以执行仓库内的可选冒烟测试：

```bash
POLYNOIA_A2A_SAMPLE_URL=http://127.0.0.1:9999 \
  apps/server/.venv/bin/pytest \
  apps/server/tests/integration/test_a2a_official_sample.py \
  -m a2a_live -q
```

可用 `POLYNOIA_A2A_SAMPLE_PROMPT` 覆盖测试消息。如果样例使用 Bearer
认证，先把 token 放在另一个环境变量中，再让测试只引用变量名：

```bash
export REMOTE_AGENT_TOKEN='<token>'
export POLYNOIA_A2A_SAMPLE_BEARER_ENV_VAR=REMOTE_AGENT_TOKEN
```

不要把 token 写进 Agent URL、Agent Card、安装 JSON 或数据库。

## 安全与运行配置

所有变量都使用 `POLYNOIA_` 前缀：

| 环境变量 | 默认值 | 用途 |
| --- | ---: | --- |
| `POLYNOIA_A2A_ENABLED` | `true` | 关闭时隐藏管理接口并禁止运行时调用 |
| `POLYNOIA_A2A_ALLOW_PRIVATE_NETWORKS` | `false` | 是否信任 RFC1918 等私网地址 |
| `POLYNOIA_A2A_CONNECT_TIMEOUT_S` | `5` | 连接超时 |
| `POLYNOIA_A2A_READ_TIMEOUT_S` | `30` | HTTP 读取/写入超时 |
| `POLYNOIA_A2A_STREAM_IDLE_TIMEOUT_S` | `45` | 流式响应相邻字节块的最大空闲时间 |
| `POLYNOIA_A2A_TASK_TIMEOUT_S` | `600` | 单个远端任务总时限 |
| `POLYNOIA_A2A_CARD_MAX_BYTES` | `262144` | Agent Card/JWKS 最大字节数 |
| `POLYNOIA_A2A_RESPONSE_MAX_BYTES` | `8388608` | 单个远端 A2A HTTP 响应的最大传输字节数 |
| `POLYNOIA_A2A_MAX_REDIRECTS` | `3` | Agent Card 最大重定向次数 |

网络策略：

- 生产目标必须使用 HTTPS。
- 仅 loopback 开发目标允许明文 HTTP。
- 私网默认拒绝；允许私网也不会放宽“非 loopback HTTP”限制，因此 LAN Agent 应
  配置 HTTPS。
- 云 metadata、link-local、multicast、unspecified 和 reserved 地址始终拒绝。
- 每次重定向、DNS 解析和实际连接 peer 都重新校验，降低 SSRF 和 DNS rebinding
  风险。
- 运行时请求只接受 identity 编码，逐块限制响应大小和空闲时间，避免无界流与压缩
  放大。
- 有签名的 Agent Card 必须验证成功；未签名卡片会明确标记，仍需人工确认。

当前支持 A2A 主版本 1 的 `JSONRPC` 与 `HTTP+JSON`；只声明 gRPC 的卡片会被
拒绝。认证支持无需认证和 HTTP Bearer。OAuth/OpenID Connect、mTLS 等认证方案
尚未接入。

远端 Agent 只能返回消息和 artifact 引用。Polynoia 不为它挂载本地工作区，不
自动下载远端文件，不把远端 patch 合并进 Git，也不会把本地 MCP/工具权限交给它。

## 手动检查与协议一致性

[A2A Inspector](https://github.com/a2aproject/a2a-inspector) 可用于查看 Agent
Card、发送测试消息并检查任务/流式事件。先用 Inspector 验证远端 Server，再用
Polynoia 导入同一 URL，能快速区分“远端协议实现问题”和“Polynoia 集成问题”。

[A2A TCK](https://github.com/a2aproject/a2a-tck) 主要验证 A2A Server 的协议一致性。
当前 Polynoia 只实现客户端，因此 TCK 不是此功能的发布门禁；将来如果 Polynoia
对外暴露 A2A Server，再把 TCK 纳入 CI。

## 故障排查

| category | 含义与处理 |
| --- | --- |
| `invalid_locator` | URL 格式、凭证、查询参数或主机名不合法；改用干净的域名/卡片 URL |
| `unsafe_target` | 命中 SSRF/网络策略；改用 HTTPS，或仅在可信部署中开启私网 |
| `card_not_found` | 标准或显式卡片路径返回 404；向 Agent 所有者确认 URL |
| `card_too_large` | 卡片/JWKS 超过限制；缩小卡片或调整受控部署的上限 |
| `invalid_card` | JSON 或 A2A 1.x schema 不合法；先用 Inspector 检查 |
| `invalid_signature` | 声明的 JWS/JWKS 无法验证；不要绕过，联系发布者修复 |
| `unsupported_version` | 不是 A2A 主版本 1；升级远端或增加兼容适配 |
| `unsupported_binding` | 没有 JSON-RPC/HTTP+JSON endpoint；当前不支持 gRPC-only |
| `unsupported_auth` | OAuth、mTLS 等尚未支持，或 Bearer 环境变量未配置 |
| `card_changed` | 预览和安装之间卡片 hash 变化；重新发现并人工确认 |
| `remote_unavailable` | DNS、连接、HTTP 5xx 或功能关闭；检查网络和远端日志 |
| `remote_timeout` | 连接、读取或任务超过时限；检查远端，再谨慎调大超时 |
| `remote_unauthorized` | Bearer 变量缺失、token 失效或远端返回 401/403 |
| `remote_protocol_error` | 返回内容或任务生命周期不符合协议；用 Inspector 复现 |
| `remote_task_failed` | 远端任务进入 failed；查看状态消息和远端日志 |
| `remote_task_rejected` | 远端拒绝任务；检查输入、权限和 skill 范围 |
| `remote_task_canceled` | 任务被用户或远端取消；按需重新发起 |
