# assistant-ui 本地演示前端 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有 FastAPI + LangChain 智能客服后端增加一个可在本地面试演示的 assistant-ui React 聊天界面。

**Architecture:** 前端使用 assistant-ui `LocalRuntime` 管理浏览器内消息状态，自定义 `ChatModelAdapter` 将当前后端 SSE 转换为累积消息快照。工具与引用事件进入只读活动面板；订单 `approval.required` 转换为 assistant-ui approval gate，用户决策仍提交给后端 `/decisions`，由 PostgreSQL 检查点和订单领域层决定结果。

**Tech Stack:** Node.js 24、npm 11、Vite、React、TypeScript、`@assistant-ui/react`、Vitest；现有 Python 3.13、FastAPI、LangChain/LangGraph、pytest 后端保持不变。执行 Task 1 时安装 npm 当前稳定版并提交 `package-lock.json`，之后不得无关升级。

**Spec:** `docs/superpowers/specs/2026-09-07-assistant-ui-local-frontend-design.md`

## Global Constraints

- 只新增独立 `frontend/`；不修改 Agent、工具、订单状态机和数据访问层。
- 后端固定使用 `http://127.0.0.1:8001`，前端固定使用 `http://127.0.0.1:5173`，由 Vite 代理 `/v1` 和 `/health`。
- 浏览器只接收后端公开 `AppEvent`；禁止展示 API Key、密码、原始 SQL、向量分数、图路径、完整工具参数、`ToolMessage.artifact` 或 chain-of-thought。
- 前端身份头固定为 `X-Demo-Customer`，只允许 `demo-customer-a` 和 `demo-customer-b`；这是本地演示机制，不得描述为生产认证。
- `ChatModelAdapter` 每次 yield 必须包含当前完整累积 content，不得只 yield delta。
- `abortSignal` 必须传给所有 fetch；AbortError 代表用户取消，不转换为后端故障。
- 客户切换必须创建新 `thread_id` 并重新挂载 Runtime，不能复用旧线程。
- 拒绝审批必须发送非空 `reason`；决策按钮在请求处理期间不可重复提交。
- 使用 assistant-ui approval gate 表示“后端执行前授权”；不得把订单操作改造成浏览器执行的工具。
- 单元测试不得连接网络或外部服务；真实 DeepSeek/Milvus/PostgreSQL/Neo4j 验收只在显式启动的本地环境运行。
- 保持设计中的少文件结构，不创建状态管理框架、组件库或额外后端协议。

---

### Task 1: 前端工程与流式 SSE 客户端

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/mvp-runtime.ts`
- Create: `frontend/src/mvp-runtime.test.ts`

**Interfaces:**
- Consumes: 后端 SSE 帧 `id: <sequence>\nevent: <event_type>\ndata: <AppEvent JSON>\n\n`。
- Produces: `MvpEvent`, `MvpSession`, `MvpEventSink`, `readMvpEvents(response)`, `postMvpEvents(path, body, session, signal, fetcher)`，供 Task 2 的 adapter 使用。

- [ ] **Step 1: 创建最小 npm 工程并锁定依赖**

在 `frontend/` 执行：

```bash
npm init -y
npm install @assistant-ui/react react react-dom
npm install --save-dev @types/react @types/react-dom @vitejs/plugin-react typescript vite vitest
```

将 `package.json` scripts 收敛为：

```json
{
  "scripts": {
    "dev": "vite --host 127.0.0.1 --port 5173",
    "test": "vitest run",
    "typecheck": "tsc --noEmit",
    "build": "tsc --noEmit && vite build"
  }
}
```

`vite.config.ts` 必须只配置 React、测试和代理：

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/v1": "http://127.0.0.1:8001",
      "/health": "http://127.0.0.1:8001",
    },
  },
  test: { environment: "node" },
});
```

- [ ] **Step 2: 先写 SSE 分片与 HTTP 错误的失败测试**

在 `mvp-runtime.test.ts` 创建 UTF-8 分片辅助函数，并覆盖：

```ts
import { describe, expect, it, vi } from "vitest";
import { postMvpEvents, readMvpEvents } from "./mvp-runtime";

function responseFromChunks(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder();
  return new Response(new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
      controller.close();
    },
  }), { status, headers: { "content-type": "text/event-stream" } });
}

describe("readMvpEvents", () => {
  it("parses frames split across network chunks and flushes the tail", async () => {
    const response = responseFromChunks([
      "id: 1\nevent: message.del",
      "ta\ndata: {\"schema_version\":\"1.0\",\"event_type\":\"message.delta\",",
      "\"thread_id\":\"t-1\",\"request_id\":\"r-1\",\"sequence\":1,\"payload\":{\"text\":\"你\"}}\n\n",
    ]);
    const events = [];
    for await (const event of readMvpEvents(response)) events.push(event);
    expect(events).toEqual([
      expect.objectContaining({ event_type: "message.delta", payload: { text: "你" } }),
    ]);
  });
});

describe("postMvpEvents", () => {
  it("posts the trusted demo header and JSON body", async () => {
    const fetcher = vi.fn().mockResolvedValue(responseFromChunks([]));
    for await (const _ of postMvpEvents(
      "/v1/threads/t-1/messages:stream",
      { message: "你好" },
      { threadId: "t-1", customerId: "demo-customer-a" },
      new AbortController().signal,
      fetcher,
    )) { /* consume */ }
    expect(fetcher).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      method: "POST",
      headers: expect.objectContaining({ "X-Demo-Customer": "demo-customer-a" }),
      body: JSON.stringify({ message: "你好" }),
    }));
  });

  it("throws a safe status-only error for a failed response", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response("database password=secret", { status: 503 }));
    const consume = async () => {
      for await (const _ of postMvpEvents("/v1/x", {}, { threadId: "t", customerId: "demo-customer-a" }, new AbortController().signal, fetcher)) { /* consume */ }
    };
    await expect(consume()).rejects.toThrow("后端请求失败（503）");
  });
});
```

- [ ] **Step 3: 运行测试并确认有效 RED**

Run: `cd frontend && npm test -- src/mvp-runtime.test.ts`

Expected: FAIL，原因是 `./mvp-runtime` 或目标导出尚不存在；不是依赖安装或 TypeScript 配置错误。

- [ ] **Step 4: 实现最小事件类型、SSE 解析和 POST 客户端**

在 `mvp-runtime.ts` 定义：

```ts
export type MvpEventType =
  | "message.delta" | "tool.started" | "tool.completed" | "citation"
  | "approval.required" | "handoff.required" | "error" | "message.completed";

export type MvpEvent = {
  schema_version: "1.0";
  event_type: MvpEventType;
  thread_id: string;
  request_id: string;
  sequence: number;
  payload: Record<string, unknown>;
};

export type MvpSession = {
  threadId: string;
  customerId: "demo-customer-a" | "demo-customer-b";
};

export type MvpEventSink = (event: MvpEvent) => void;
export type Fetcher = typeof fetch;

export async function* readMvpEvents(response: Response): AsyncGenerator<MvpEvent> {
  if (!response.body) throw new Error("后端没有返回事件流");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const frames = buffer.replaceAll("\r\n", "\n").split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const eventLine = frame.split("\n").find((line) => line.startsWith("event: "));
      const dataLine = frame.split("\n").find((line) => line.startsWith("data: "));
      if (!eventLine || !dataLine) continue;
      const parsed = JSON.parse(dataLine.slice(6)) as MvpEvent;
      if (parsed.event_type === eventLine.slice(7)) yield parsed;
    }
    if (done) break;
  }
  if (buffer.trim()) {
    const dataLine = buffer.split("\n").find((line) => line.startsWith("data: "));
    if (dataLine) yield JSON.parse(dataLine.slice(6)) as MvpEvent;
  }
}

export async function* postMvpEvents(
  path: string,
  body: object,
  session: MvpSession,
  signal: AbortSignal,
  fetcher: Fetcher = fetch,
): AsyncGenerator<MvpEvent> {
  const response = await fetcher(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Demo-Customer": session.customerId },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw new Error(`后端请求失败（${response.status}）`);
  yield* readMvpEvents(response);
}
```

实现时只增加必要的运行时字段检查，使 malformed JSON 得到统一“事件流格式错误”，不得把原始帧内容拼入错误消息。

- [ ] **Step 5: 运行测试、类型检查并提交**

Run:

```bash
cd frontend
npm test -- src/mvp-runtime.test.ts
npm run typecheck
```

Expected: 全部 PASS，TypeScript 退出码 0。

Commit:

```bash
git add frontend
git commit -m "feat: add frontend SSE client"
```

---

### Task 2: LocalRuntime 适配与订单审批恢复

**Files:**
- Modify: `frontend/src/mvp-runtime.ts`
- Modify: `frontend/src/mvp-runtime.test.ts`

**Interfaces:**
- Consumes: Task 1 的 `postMvpEvents`；assistant-ui `ChatModelAdapter.run({ messages, abortSignal, unstable_getMessage })`。
- Produces: `APPROVAL_TOOL_NAME`, `createMvpAdapter({ session, onEvent, fetcher })`。adapter 将文本转换为 assistant-ui 累积 content，将审批转换为 tool-call approval gate。

- [ ] **Step 1: 先写文本流和非文本事件映射的失败测试**

使用两帧合成响应测试 adapter：

```ts
it("yields cumulative text while forwarding safe side events", async () => {
  const seen: string[] = [];
  const fetcher = vi.fn().mockResolvedValue(responseFromEvents([
    event("tool.started", { tool_call_id: "c-1", tool_name: "search_product_faq" }),
    event("citation", { source_id: "faq-1", title: "保修说明", locator: "第 1 节" }),
    event("message.delta", { text: "保修" }),
    event("message.delta", { text: "期为 12 个月" }),
    event("message.completed", { message: "保修期为 12 个月" }),
  ]));
  const adapter = createMvpAdapter({
    session: { threadId: "t-1", customerId: "demo-customer-a" },
    fetcher,
    onEvent: (item) => seen.push(item.event_type),
  });
  const output = [];
  for await (const chunk of adapter.run(runOptions("商品保修多久"))) output.push(chunk);
  expect(output.at(-1)?.content).toEqual([{ type: "text", text: "保修期为 12 个月" }]);
  expect(seen).toEqual(["tool.started", "citation", "message.delta", "message.delta", "message.completed"]);
});
```

辅助函数 `responseFromEvents` 必须生成真实的 `event:` + `data:` SSE 帧；`runOptions` 必须提供一个 user `ThreadMessage` 和 AbortSignal，不绕开公开 adapter 接口。

- [ ] **Step 2: 先写 approval gate 和同线程恢复的失败测试**

```ts
it("turns approval.required into a pending approval tool call", async () => {
  const fetcher = vi.fn().mockResolvedValue(responseFromEvents([
    event("approval.required", {
      interrupt_id: "interrupt-1",
      operation_id: "operation-1",
      tool_name: "cancel_order",
      preview: {},
      allowed_decisions: ["approve", "reject"],
    }),
  ]));
  const adapter = createMvpAdapter({ session: SESSION_A, fetcher, onEvent: vi.fn() });
  const chunks = [];
  for await (const chunk of adapter.run(runOptions("取消订单"))) chunks.push(chunk);
  expect(chunks.at(-1)).toEqual(expect.objectContaining({
    status: { type: "requires-action", reason: "tool-calls" },
    content: [expect.objectContaining({
      type: "tool-call",
      toolName: APPROVAL_TOOL_NAME,
      toolCallId: "interrupt-1",
      approval: { id: "interrupt-1" },
    })],
  }));
});

it.each([
  [true, "approve", undefined],
  [false, "reject", "用户拒绝本次操作"],
])("posts a decided approval to the same thread", async (approved, decision, reason) => {
  const fetcher = vi.fn().mockResolvedValue(responseFromEvents([
    event("message.completed", { message: "订单操作已处理" }),
  ]));
  const adapter = createMvpAdapter({ session: SESSION_A, fetcher, onEvent: vi.fn() });
  const currentAssistant = assistantWithDecidedApproval("interrupt-1", approved);
  const chunks = [];
  for await (const chunk of adapter.run(runOptions("取消订单", currentAssistant))) chunks.push(chunk);
  expect(fetcher).toHaveBeenCalledWith(
    "/v1/threads/t-1/decisions",
    expect.objectContaining({ body: JSON.stringify({ interrupt_id: "interrupt-1", decision, ...(reason ? { reason } : {}) }) }),
  );
  expect(chunks.at(-1)?.content).toContainEqual({ type: "text", text: "订单操作已处理" });
});
```

- [ ] **Step 3: 运行测试并确认有效 RED**

Run: `cd frontend && npm test -- src/mvp-runtime.test.ts`

Expected: FAIL，原因是 `createMvpAdapter`、审批映射或恢复分支尚不存在。

- [ ] **Step 4: 实现 adapter 的最小控制流**

核心签名和分支固定为：

```ts
import type { ChatModelAdapter, ChatModelRunResult, ThreadMessage } from "@assistant-ui/react";

export const APPROVAL_TOOL_NAME = "confirm_order_operation";

type AdapterOptions = {
  session: MvpSession;
  onEvent: MvpEventSink;
  fetcher?: Fetcher;
};

export function createMvpAdapter(options: AdapterOptions): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal, unstable_getMessage }) {
      const current = unstable_getMessage?.();
      const decided = findDecidedApproval(current);
      const path = decided
        ? `/v1/threads/${encodeURIComponent(options.session.threadId)}/decisions`
        : `/v1/threads/${encodeURIComponent(options.session.threadId)}/messages:stream`;
      const body = decided
        ? {
            interrupt_id: decided.interruptId,
            decision: decided.approved ? "approve" : "reject",
            ...(!decided.approved ? { reason: "用户拒绝本次操作" } : {}),
          }
        : { message: latestUserText(messages) };

      let text = "";
      const preserved = decided ? preserveResolvedApprovalParts(current) : [];
      for await (const event of postMvpEvents(path, body, options.session, abortSignal, options.fetcher)) {
        options.onEvent(event);
        if (event.event_type === "message.delta") text += requiredString(event.payload.text);
        if (event.event_type === "message.completed" && !text) text = requiredString(event.payload.message);
        if (event.event_type === "error") throw new Error(publicEventError(event));
        if (event.event_type === "handoff.required" && !text) text = "需要人工客服继续处理本次请求。";
        if (event.event_type === "approval.required") {
          yield approvalResult(event, text);
          return;
        }
        if (text) yield { content: [...preserved, { type: "text", text }] } satisfies ChatModelRunResult;
      }
    },
  };
}
```

`approvalResult` 只把 `interrupt_id`、`operation_id`、`tool_name` 和安全展示文案放进 args；不复制未知 preview 字段。`findDecidedApproval` 只接受 `APPROVAL_TOOL_NAME` 且 `approval.approved` 为 boolean 的 tool-call。工具、citation 和终止事件均转发给 `onEvent`，但不能重复拼入聊天文本。

- [ ] **Step 5: 验证 adapter 并提交**

Run:

```bash
cd frontend
npm test -- src/mvp-runtime.test.ts
npm run typecheck
```

Expected: adapter 与 Task 1 测试全部 PASS。

Commit:

```bash
git add frontend/src/mvp-runtime.ts frontend/src/mvp-runtime.test.ts
git commit -m "feat: adapt MVP events to assistant-ui"
```

---

### Task 3: assistant-ui 聊天页面与可见工具状态

**Files:**
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/styles.css`
- Modify: `frontend/index.html`

**Interfaces:**
- Consumes: Task 2 的 `createMvpAdapter`、`MvpSession`、`MvpEvent`、`APPROVAL_TOOL_NAME`。
- Produces: 可交互的 `App`、每客户独立的 `ChatSession`、assistant-ui `Thread`、订单 `ApprovalCard` 和只读 `ActivityPanel`。

- [ ] **Step 1: 先写可独立测试的会话与活动归约测试**

在 `mvp-runtime.test.ts` 增加纯函数测试：

```ts
it("creates a new customer-scoped thread id", () => {
  expect(createSession("demo-customer-b", () => "uuid-2")).toEqual({
    customerId: "demo-customer-b",
    threadId: "demo-customer-b-uuid-2",
  });
});

it("reduces tools and citations without retaining unknown payload fields", () => {
  const started = reduceActivity(emptyActivity(), event("tool.started", {
    tool_call_id: "c-1", tool_name: "get_order", raw_sql: "SECRET",
  }));
  const cited = reduceActivity(started, event("citation", {
    source_id: "faq-1", title: "保修说明", locator: "第 1 节", score: 0.99,
  }));
  expect(cited.tools).toEqual([{ id: "c-1", name: "get_order", status: "running" }]);
  expect(cited.citations).toEqual([{ sourceId: "faq-1", title: "保修说明", locator: "第 1 节" }]);
  expect(JSON.stringify(cited)).not.toContain("SECRET");
  expect(JSON.stringify(cited)).not.toContain("0.99");
});

it.each([
  [new Response('{"status":"ready"}', { status: 200 }), "ready"],
  [new Response('{"status":"not_ready"}', { status: 503 }), "degraded"],
])("maps backend readiness without exposing dependency details", async (response, expected) => {
  const fetcher = vi.fn().mockResolvedValue(response);
  await expect(checkBackendHealth(fetcher)).resolves.toBe(expected);
});
```

- [ ] **Step 2: 运行测试并确认有效 RED，然后实现纯函数**

Run: `cd frontend && npm test -- src/mvp-runtime.test.ts`

Expected: FAIL，原因是 `createSession`、`emptyActivity`、`reduceActivity`、`checkBackendHealth` 未定义。

在 `mvp-runtime.ts` 添加精确返回类型；工具完成按 `tool_call_id` 更新原项，citation 只保留 `source_id/title/locator` 字符串，`error` 只保留后端公开 message 或固定中文提示。`checkBackendHealth(fetcher = fetch)` 请求 `/health/ready`，只返回 `ready | degraded | offline`，不得把依赖连接参数带入 UI。

- [ ] **Step 3: 用 assistant-ui primitives 实现最小聊天组件**

`main.tsx`：

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
```

`App.tsx` 必须采用以下结构，不引入路由或全局 store：

```tsx
import { useMemo, useState } from "react";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePartPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useLocalRuntime,
} from "@assistant-ui/react";
import {
  APPROVAL_TOOL_NAME,
  checkBackendHealth,
  createMvpAdapter,
  createSession,
  emptyActivity,
  reduceActivity,
  type MvpSession,
} from "./mvp-runtime";

export function App() {
  const [session, setSession] = useState(() => createSession("demo-customer-a"));
  return (
    <main className="app-shell">
      <header className="topbar">
        <div><h1>智能客服 Agent</h1><p>DeepSeek · LangChain · 安全工具调用</p></div>
        <label>演示客户
          <select value={session.customerId} onChange={(event) =>
            setSession(createSession(event.target.value as MvpSession["customerId"]))}>
            <option value="demo-customer-a">客户 A</option>
            <option value="demo-customer-b">客户 B</option>
          </select>
        </label>
      </header>
      <ChatSession key={session.threadId} session={session} />
    </main>
  );
}

function ChatSession({ session }: { session: MvpSession }) {
  const [activity, setActivity] = useState(emptyActivity);
  const adapter = useMemo(() => createMvpAdapter({
    session,
    onEvent: (event) => setActivity((current) => reduceActivity(current, event)),
  }), [session]);
  const runtime = useLocalRuntime(adapter, { maxSteps: 2 });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="workspace"><Thread /><ActivityPanel activity={activity} session={session} /></div>
    </AssistantRuntimeProvider>
  );
}
```

`App` 挂载时调用 `checkBackendHealth`，顶部只显示“后端就绪 / 依赖未就绪 / 后端未连接”；组件卸载时用 AbortController 取消请求。该状态用于提示，不绕过后端 readiness，也不阻止用户在依赖恢复后重试。

`Thread` 使用 `ThreadPrimitive.Root/Viewport/Messages`、`MessagePrimitive.Root/Parts`、`MessagePartPrimitive.Text` 和 `ComposerPrimitive.Root/Input/Send/Cancel`。`MessagePrimitive.Parts` 的 render function 必须：

- 文本 part 渲染成消息气泡；
- `toolName === APPROVAL_TOOL_NAME` 时渲染 `ApprovalCard`；
- 其他 tool/data/reasoning part 返回 `null`，防止内部信息进入页面；
- `ApprovalCard` 用 `respondToApproval({ approved: true })` 和 `respondToApproval({ approved: false, reason: "用户拒绝本次操作" })`；pending 时显示两个按钮，decided 时显示“已批准/已拒绝”，请求进行中由 part status 禁用按钮。

空状态显示四个可复制的示例问题；不实现点击即发送，避免为了建议卡片增加额外 Runtime API。

- [ ] **Step 4: 添加清晰但有限的响应式样式**

`styles.css` 只覆盖：页面背景、顶部身份栏、消息 viewport、用户/助手气泡、composer、工具/引用侧栏、审批卡片、focus-visible 和 820px 单列断点。不得加入 Tailwind、动画库、图标库或主题框架。

`index.html` 只保留 `lang="zh-CN"`、viewport、标题、`#root` 和 `/src/main.tsx` module script。

- [ ] **Step 5: 运行单元、类型与生产构建验证**

Run:

```bash
cd frontend
npm test
npm run typecheck
npm run build
```

Expected: Vitest 全部 PASS；TypeScript 无错误；Vite 在 `frontend/dist/` 生成构建产物。

手工静态检查：构建产物与源码不得包含 `Deepseek_API_KEY`、`postgresql://`、`NEO4J_AUTH` 或 `.env` 内容。

Commit:

```bash
git add frontend
git commit -m "feat: add assistant-ui demo experience"
```

---

### Task 4: 启动说明、回归与本地真实体验

**Files:**
- Modify: `README.md`
- Modify: `docs/demo.md`
- Create: `docs/tdd/records/m7-assistant-ui-frontend.md`
- Modify only if an API incompatibility is proven by tests: `frontend/src/mvp-runtime.ts`
- Modify only if an API incompatibility is proven by tests: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: Task 1–3 的前端与现有 `create_mvp_app_from_environment`。
- Produces: 一组可复制的双终端启动命令、验收问题、RED/GREEN 记录和当前验证边界。

- [ ] **Step 1: 更新本地启动与故障排查说明**

README 和 `docs/demo.md` 增加：

```bash
# 终端 1：后端
cd /Users/danny/Documents/wang-agent/.worktrees/mvp-complete
PYTHONPATH=src uv run --env-file /Users/danny/Documents/wang-agent/.env \
  python -m uvicorn customer_service_agent.mvp.app:create_mvp_app_from_environment \
  --factory --host 127.0.0.1 --port 8001

# 终端 2：assistant-ui 前端
cd /Users/danny/Documents/wang-agent/.worktrees/mvp-complete/frontend
npm run dev
```

浏览器地址写为 `http://127.0.0.1:5173/`。保留旧的 `http://127.0.0.1:8001/` 作为无 Node 依赖的备用页。

故障排查只列三个最常见问题：

- 8001 被占用：复用健康进程或先正常停止旧进程；不盲目启动第二个实例。
- `/health/ready` 为 503：根据 `dependencies` 启动缺失服务；Milvus 必须监听 19530。
- 页面能打开但对话失败：检查 Vite 与后端是否分别监听 5173/8001，不在前端配置任何密钥。

- [ ] **Step 2: 运行前端和后端确定性回归**

Run:

```bash
cd frontend && npm test && npm run build
cd ..
UV_CACHE_DIR=.uv-cache uv run pytest tests/contract/test_message_api.py tests/contract/test_decision_api.py tests/contract/test_demo_api.py tests/contract/test_mvp_page.py -q
UV_CACHE_DIR=.uv-cache uv run pytest -m 'unit or contract' -q
git diff --check
```

Expected: 全部测试通过；`git diff --check` 无输出。若历史总数与旧报告不同，记录本次实际数量，不复制旧数字。

- [ ] **Step 3: 启动本地服务并执行真实浏览器验收**

先运行：

```bash
curl -s http://127.0.0.1:8001/health/live
curl -s http://127.0.0.1:8001/health/ready
```

Expected: live 为 `{"status":"ok"}`；ready 为 HTTP 200 且 PostgreSQL、Milvus 均为 true。若 ready 失败，只报告未满足依赖，不把前端构建成功描述成完整真实验收。

在 `http://127.0.0.1:5173/` 依次输入：

1. `云端降噪耳机保修多久？`：出现 `search_product_faq`、引用和流式回答。
2. `查询订单 MVP-ORDER-1001`：出现 `get_order`，只返回客户 A 的订单。
3. `取消订单 MVP-ORDER-1001，原因是不想要了`：先出现审批卡；选择拒绝，订单不修改。
4. 重新 seed 或换可操作订单后再次发起写操作：选择批准，恢复同线程并展示后端结果。
5. 切换客户 B：对话清空、thread id 改变，不能看到客户 A 的上下文。

额外执行真实模型测试：

```bash
RUN_LIVE_MODEL_TESTS=1 PYTHONPATH=src \
  uv run --env-file /Users/danny/Documents/wang-agent/.env \
  pytest tests/live/test_mvp_deepseek.py -q -rs
```

不得输出 `.env` 内容或密钥值。

- [ ] **Step 4: 写入 TDD 证据和验证边界**

`docs/tdd/records/m7-assistant-ui-frontend.md` 必须记录：

- 每个 Task 的 RED 命令、具体失败原因、GREEN 命令和实际结果；
- 前端版本来自 `package-lock.json`；
- 真实浏览器验收中实际成功的场景；
- 未启动的 Docker 服务、被跳过的真实调用或未验证功能；
- assistant-ui approval gate 只是 UI 决策桥，实际权限、幂等和执行仍在后端；
- `langchain-dev-guide` 的约束：只消费公开事件，artifact 不进入 UI，HITL 恢复沿用同一可信线程。

- [ ] **Step 5: 最终检查并提交**

Run:

```bash
rg -n "[T]BD|[T]ODO|implement[ ]later|填充|稍后实现" frontend README.md docs/demo.md docs/tdd/records/m7-assistant-ui-frontend.md
git diff --check
git status --short
```

Expected: placeholder 搜索无结果；diff check 无输出；status 只包含本 Task 文档改动和任何由失败测试证明必要的前端修正。

Commit:

```bash
git add README.md docs/demo.md docs/tdd/records/m7-assistant-ui-frontend.md frontend
git commit -m "docs: add assistant-ui demo workflow"
```

## 最终完成门

以下条件全部满足才可声明“assistant-ui 本地 MVP 可体验”：

1. 前端测试、类型检查、生产构建全部通过。
2. 后端 unit/contract 回归通过，未改变现有 API 契约。
3. 真实后端 readiness 为 200，或明确列出阻塞真实体验的依赖。
4. 浏览器完成流式问答、工具活动、引用、批准/拒绝和客户切换验收。
5. 源码、构建产物、日志和文档均未包含密钥或连接密码。
6. 旧 FastAPI 单页仍可作为备用入口。
