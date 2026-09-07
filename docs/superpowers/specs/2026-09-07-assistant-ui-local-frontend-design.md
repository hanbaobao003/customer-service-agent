# assistant-ui 本地演示前端设计

日期：2026-09-07  
状态：已批准
对应目标：为现有智能客服 Agent 增加可在面试现场使用的本地聊天界面。

## 1. 目标

在不改写现有 Agent、工具和数据层的前提下，增加一个基于 assistant-ui 的本地 React 前端，使用户可以像使用常见聊天产品一样直接提问，并观察真实 DeepSeek 流式回答、工具调用、知识引用和人工审批流程。

成功标准：

1. 浏览器打开一个地址后即可连续对话，不需要手写 `curl`。
2. 前端消费现有 FastAPI SSE 接口，并实时显示回复。
3. 工具开始、工具完成、引用、审批请求和错误均有清晰展示。
4. 可切换演示客户；切换客户时自动创建新会话，避免身份串用。
5. 可在审批卡片中批准或拒绝订单写操作。
6. 不向浏览器暴露 API Key、连接密码、原始 SQL、内部检索分数或模型思维过程。

## 2. 范围

本次包含：

- Vite + React + TypeScript 前端。
- `@assistant-ui/react` 聊天组件和本地 Runtime 适配器。
- 现有 SSE 事件到前端状态的映射。
- 工具活动、引用和审批的最小可演示 UI。
- 客户 A/B 切换、会话重置、连接状态和错误提示。
- 前端单元测试、生产构建检查和一条真实 DeepSeek 手工验收路径。

本次不包含：

- Assistant UI Cloud、登录系统、线上部署或公网访问。
- 重写后端为 assistant-ui 专用协议。
- WebSocket、复杂会话列表、文件上传、语音、多主题或移动端专项适配。
- 在前端展示 `ToolMessage.artifact`、审计明细或 chain-of-thought。
- 将前端构建产物嵌入 FastAPI。开发和演示阶段由 Vite 单独提供页面。

## 3. 方案选择

采用 assistant-ui 的 `LocalRuntime` 自定义适配器，而不是 `AssistantTransport`。

原因：当前后端已经提供稳定的“发送消息并返回 SSE”接口。`LocalRuntime` 只需负责一次消息请求和流式增量映射，改动最小；`AssistantTransport` 更适合由后端完整管理消息快照、分支和双向命令的系统，会引入当前 MVP 不需要的协议与状态同步复杂度。

官方参考：

- [Custom Backend Runtime](https://www.assistant-ui.com/docs/runtimes/custom/overview)
- [Pick a Runtime](https://www.assistant-ui.com/docs/runtimes/pick-a-runtime)

## 4. 总体架构

```text
浏览器 :5173
  └─ assistant-ui Thread
       └─ LocalRuntime 自定义适配器
            ├─ POST /v1/threads/{thread_id}/messages:stream
            │    └─ Vite proxy → FastAPI :8001 → LangChain Agent → Tools
            └─ POST /v1/threads/{thread_id}/decisions
                 └─ Vite proxy → FastAPI :8001 → PostgreSQL checkpoint 恢复
```

前端不直接连接 DeepSeek、PostgreSQL、Milvus 或 Neo4j。所有模型调用、客户身份约束、工具权限和审批执行仍由后端负责。

## 5. 文件结构

为保持简洁，前端控制在少量文件内：

```text
frontend/
├── package.json
├── package-lock.json
├── tsconfig.json
├── vite.config.ts
├── index.html
└── src/
    ├── main.tsx
    ├── App.tsx
    ├── mvp-runtime.ts
    ├── mvp-runtime.test.ts
    └── styles.css
```

- `App.tsx`：页面布局、客户切换、Thread、工具活动、引用和审批卡片。
- `mvp-runtime.ts`：请求、SSE 解析、assistant-ui Runtime 适配和事件归一化。
- `mvp-runtime.test.ts`：SSE 分片、事件映射、错误与审批状态的确定性测试。
- `styles.css`：只保留演示所需样式，不建立额外设计系统。

只有当单个文件在实施中明显失去可读性时才拆分；不得预先创建空目录或推测性抽象。

## 6. 数据与控制流

### 6.1 普通对话

1. 页面初始化生成唯一 `thread_id`，格式为 `demo-<customer>-<random>`。
2. 用户输入消息后，适配器调用：
   `POST /v1/threads/{thread_id}/messages:stream`。
3. 请求头携带后端当前约定的可信演示客户标识，请求体沿用现有消息契约。
4. 适配器增量读取 SSE，不等待完整响应。
5. `message.delta` 追加到 assistant 消息；`message.completed` 结束本轮。
6. 非文本事件写入当前页面的活动状态，不伪装成模型自然语言。

### 6.2 SSE 事件映射

| 后端事件 | 前端行为 |
|---|---|
| `message.delta` | 增量显示助手文本 |
| `tool.started` | 新增运行中的工具活动 |
| `tool.completed` | 将对应工具标记为成功并显示安全摘要 |
| `citation` | 在回答下方显示可读来源 |
| `approval.required` | 显示批准/拒绝卡片并保存 `interrupt_id` |
| `handoff.required` | 显示需要人工客服接管的提示 |
| `error` | 结束本轮并显示可理解的错误信息 |
| `message.completed` | 完成本轮流式状态 |

未知事件只在开发控制台记录事件名，不中断对话，也不显示其原始载荷。

### 6.3 审批恢复

1. 收到 `approval.required` 后，卡片展示操作摘要，而非内部参数全集。
2. 用户选择批准或拒绝。
3. 前端调用 `POST /v1/threads/{thread_id}/decisions`，传入相同 `interrupt_id` 和 `approve | reject`。
4. 按现有后端响应契约继续消费恢复后的 SSE；按钮在请求期间禁用，避免重复提交。
5. 后端仍负责幂等、权限、检查点和订单状态机，前端不自行推断执行结果。

### 6.4 客户切换

客户切换会清空当前可见消息并生成新 `thread_id`。旧线程不复用于新客户，从交互层避免触发 `THREAD_CUSTOMER_MISMATCH`，也避免面试演示中产生跨客户上下文错觉。

## 7. UI 设计

页面采用单屏双区域：

- 主区域：assistant-ui 对话线程、输入框、停止/重试等框架原生能力。
- 辅助区域：当前客户、后端连接状态、本轮工具活动与引用。

审批卡片放在对话流附近，以便清楚表达“Agent 已暂停，等待人类决策”。窄屏时辅助区域移到对话下方。界面重点是可讲解性，不追求电商产品级视觉细节。

工具使用后显示固定工具名和简短中文状态，例如“正在查询订单”“已检索政策知识”；不得展示原始 SQL、向量分数、图路径、连接信息或完整工具参数。

## 8. 本地运行

- 后端继续运行在 `127.0.0.1:8001`。
- 前端运行在 `127.0.0.1:5173`。
- Vite 将 `/v1` 和 `/health` 代理到后端，因此不需要修改 FastAPI CORS 配置。
- `npm run dev` 用于开发体验；`npm run build` 验证可构建；演示时优先使用同一开发启动命令以减少部署步骤。
- 现有后端首页保留，作为依赖无关的备用演示入口。

启动前必须确认：

1. 8001 端口没有被旧进程重复占用，或复用已运行且健康的后端。
2. PostgreSQL、Neo4j 和 Milvus 按演示功能需求启动；Milvus 未监听 19530 时，RAG 初始化不能宣称成功。
3. `.env` 只由后端加载，前端环境中不复制 DeepSeek 或其他服务密钥。

## 9. 错误处理

- 后端不可达：在页面顶部显示“后端未连接”，保留用户输入以便重试。
- SSE 中断：结束加载状态并提示可重试，不拼接虚假的完成事件。
- 后端业务错误：优先显示安全的中文 `message`；没有安全消息时映射为通用提示和错误码。
- 审批失败：恢复按钮并保留卡片，允许用户再次提交；前端不假设订单已修改。
- 客户身份冲突：创建新线程并提示已重置会话，不尝试覆盖后端身份绑定。

## 10. TDD 与验证

实施遵循先失败测试、后最小实现：

1. 先测试 SSE 解析能处理跨网络分片、连续事件和流尾缓冲。
2. 先测试八类事件到 UI 状态的映射，再实现适配器。
3. 先测试审批只提交一次且沿用原线程，再实现审批交互。
4. 先测试客户切换生成新线程并清空状态，再实现切换控件。

验证分层：

- 前端单元测试：不连接外部服务，使用合成 SSE 流。
- 前端静态验证：TypeScript 检查和 `npm run build`。
- 后端回归：运行现有相关 pytest，确认新增前端没有改变 API 行为。
- 本地集成：真实 FastAPI + DeepSeek，至少演示 FAQ/RAG 工具调用和一个订单审批流程。
- 人工验收：在浏览器中完成连续提问、工具状态观察、来源查看、批准/拒绝和客户切换。

## 11. 验收场景

1. **普通问答**：输入“你好”，可看到真实 DeepSeek 流式回复。
2. **知识检索**：询问商品保修期限，可看到知识工具活动、回答和来源。
3. **外部搜索**：询问需要时效信息的问题，可看到 `web_search` 调用。
4. **订单查询**：查询当前客户的模拟订单，只返回该客户数据。
5. **订单写操作**：请求取消可取消订单，界面先显示审批卡片；批准后继续执行，拒绝后不修改。
6. **长期记忆**：明确要求记住偏好，再查询记忆，可观察记忆工具调用。
7. **客户隔离**：切换客户后自动进入新线程，不能沿用上一客户上下文。
8. **依赖异常**：关闭或误配后端时，页面给出明确错误且不泄露敏感配置。

## 12. 实施边界与完成定义

首版完成定义是“本地面试演示稳定、关键能力可见”，不是生产前端完备度。满足上述验收场景、测试通过、构建成功并补充一页启动说明后即停止扩展。

设计获用户批准后，下一步才编写逐步 TDD 实施计划；实施计划再次获批后才修改业务代码和安装前端依赖。
