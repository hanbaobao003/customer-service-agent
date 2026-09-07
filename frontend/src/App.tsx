import {
  AssistantRuntimeProvider,
  AuiIf,
  ComposerPrimitive,
  ErrorPrimitive,
  MessagePartPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useLocalRuntime,
  type EnrichedPartState,
} from "@assistant-ui/react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  APPROVAL_TOOL_NAME,
  checkBackendHealth,
  createMvpAdapter,
  createSession,
  emptyActivity,
  reduceActivity,
  type ActivityState,
  type BackendStatus,
  type MvpSession,
} from "./mvp-runtime";

const EXAMPLE_QUESTIONS = [
  "云端降噪耳机保修多久？",
  "已付款订单可以取消吗？",
  "查询订单 MVP-ORDER-1001",
  "请长期记住我偏好简洁中文回答",
];

const TOOL_LABELS: Record<string, string> = {
  web_search: "联网搜索",
  search_product_faq: "常规 RAG",
  search_policy_raptor: "RAPTOR 政策检索",
  search_commerce_graph: "GraphRAG 商品关系",
  query_business_data: "安全 SQL 查询",
  get_order: "订单查询",
  create_order: "创建订单",
  update_order_contact: "修改收货信息",
  cancel_order: "取消订单",
  request_return: "申请退货",
  remember_preference: "保存长期偏好",
  list_memories: "查看长期记忆",
  forget_memory: "删除长期记忆",
};

type ApprovalPart = Extract<EnrichedPartState, { type: "tool-call" }>;

export function App() {
  const [session, setSession] = useState(() => createSession("demo-customer-a"));
  const [backendStatus, setBackendStatus] = useState<BackendStatus>("offline");

  const refreshHealth = useCallback(() => {
    const controller = new AbortController();
    void checkBackendHealth(fetch, controller.signal).then((status) => {
      if (!controller.signal.aborted) setBackendStatus(status);
    });
    return () => controller.abort();
  }, []);

  useEffect(() => refreshHealth(), [refreshHealth]);

  const changeCustomer = (customerId: MvpSession["customerId"]) => {
    setSession(createSession(customerId));
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">PORTFOLIO MVP</p>
          <h1>智能客服 Agent</h1>
          <p className="subtitle">DeepSeek · LangChain · RAG · 安全工具调用</p>
        </div>
        <div className="topbar-controls">
          <button className={`health health-${backendStatus}`} onClick={refreshHealth} type="button">
            <span aria-hidden="true" />
            {backendStatus === "ready"
              ? "后端就绪"
              : backendStatus === "degraded"
                ? "依赖未就绪"
                : "后端未连接"}
          </button>
          <label className="customer-picker">
            演示身份
            <select
              value={session.customerId}
              onChange={(event) => changeCustomer(event.target.value as MvpSession["customerId"])}
            >
              <option value="demo-customer-a">客户 A</option>
              <option value="demo-customer-b">客户 B</option>
            </select>
          </label>
          <button
            className="new-thread"
            type="button"
            onClick={() => setSession(createSession(session.customerId))}
          >
            新对话
          </button>
        </div>
      </header>
      <ChatSession key={session.threadId} session={session} />
    </main>
  );
}

function ChatSession({ session }: { session: MvpSession }) {
  const [activity, setActivity] = useState(emptyActivity);
  const adapter = useMemo(
    () =>
      createMvpAdapter({
        session,
        onEvent: (event) => setActivity((current) => reduceActivity(current, event)),
      }),
    [session],
  );
  const runtime = useLocalRuntime(adapter, { maxSteps: 2 });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="workspace">
        <Thread />
        <ActivityPanel activity={activity} session={session} />
      </div>
    </AssistantRuntimeProvider>
  );
}

function Thread() {
  return (
    <ThreadPrimitive.Root className="thread-root">
      <ThreadPrimitive.Viewport className="thread-viewport">
        <AuiIf condition={(state) => state.thread.isEmpty}>
          <section className="welcome">
            <div className="agent-mark" aria-hidden="true">AI</div>
            <h2>你好，我是智能客服</h2>
            <p>可以查询知识、订单和记忆，也可以演示需要人工审批的订单操作。</p>
            <div className="examples">
              {EXAMPLE_QUESTIONS.map((question) => <code key={question}>{question}</code>)}
            </div>
          </section>
        </AuiIf>

        <ThreadPrimitive.Messages>
          {({ message }) =>
            message.role === "user" ? <UserMessage /> : <AssistantMessage />
          }
        </ThreadPrimitive.Messages>

        <ThreadPrimitive.ViewportFooter className="composer-footer">
          <ComposerPrimitive.Root className="composer">
            <ComposerPrimitive.Input
              aria-label="向智能客服提问"
              className="composer-input"
              placeholder="输入问题，例如：查询订单 MVP-ORDER-1001"
              rows={2}
            />
            <div className="composer-actions">
              <span>Enter 发送 · Shift+Enter 换行</span>
              <AuiIf condition={(state) => state.thread.isRunning}>
                <ComposerPrimitive.Cancel className="send-button secondary">停止</ComposerPrimitive.Cancel>
              </AuiIf>
              <AuiIf condition={(state) => !state.thread.isRunning}>
                <ComposerPrimitive.Send className="send-button">发送</ComposerPrimitive.Send>
              </AuiIf>
            </div>
          </ComposerPrimitive.Root>
        </ThreadPrimitive.ViewportFooter>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="message-row user-row">
      <div className="message-bubble user-bubble">
        <MessagePrimitive.Parts />
      </div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="message-row assistant-row">
      <div className="message-avatar" aria-hidden="true">AI</div>
      <div className="assistant-content">
        <MessagePrimitive.Parts>
          {({ part }) => {
            if (part.type === "text") {
              return <MessagePartPrimitive.Text className="message-bubble assistant-bubble" component="p" />;
            }
            if (part.type === "tool-call" && part.toolName === APPROVAL_TOOL_NAME) {
              return <ApprovalCard part={part} />;
            }
            return null;
          }}
        </MessagePrimitive.Parts>
        <MessagePrimitive.Error>
          <div className="message-error" role="alert">
            <ErrorPrimitive.Message />
          </div>
        </MessagePrimitive.Error>
      </div>
    </MessagePrimitive.Root>
  );
}

function ApprovalCard({ part }: { part: ApprovalPart }) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string>();
  const approved = part.approval?.approved;
  const args = part.args as { toolName?: string; operationId?: string };

  const decide = async (allow: boolean) => {
    setSubmitting(true);
    setError(undefined);
    try {
      await part.respondToApproval({
        approved: allow,
        ...(!allow ? { reason: "用户拒绝本次操作" } : {}),
      });
    } catch {
      setError("提交失败，请重试。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="approval-card" aria-label="订单操作审批">
      <p className="approval-kicker">HUMAN IN THE LOOP</p>
      <h3>订单操作等待确认</h3>
      <p>工具：{TOOL_LABELS[args.toolName ?? ""] ?? args.toolName ?? "订单写操作"}</p>
      {args.operationId ? <small>操作编号：{args.operationId}</small> : null}
      {typeof approved === "boolean" ? (
        <p className={approved ? "decision approved" : "decision rejected"}>
          {approved ? "已批准，正在由后端安全执行" : "已拒绝，本次操作不会执行"}
        </p>
      ) : (
        <div className="approval-actions">
          <button disabled={submitting} onClick={() => void decide(true)} type="button">批准执行</button>
          <button disabled={submitting} onClick={() => void decide(false)} type="button">拒绝</button>
        </div>
      )}
      {error ? <p className="approval-error" role="alert">{error}</p> : null}
    </section>
  );
}

function ActivityPanel({ activity, session }: { activity: ActivityState; session: MvpSession }) {
  return (
    <aside className="activity-panel">
      <section>
        <p className="panel-label">当前会话</p>
        <strong>{session.customerId === "demo-customer-a" ? "客户 A" : "客户 B"}</strong>
        <code className="thread-id">{session.threadId}</code>
      </section>

      <section>
        <p className="panel-label">工具调用</p>
        {activity.tools.length ? (
          <ol className="activity-list">
            {activity.tools.map((tool) => (
              <li key={tool.id}>
                <span className={`tool-state tool-${tool.status}`} aria-hidden="true" />
                <div>
                  <strong>{TOOL_LABELS[tool.name] ?? tool.name}</strong>
                  <small>
                    {tool.status === "running" ? "执行中" : tool.status === "success" ? "已完成" : tool.status}
                    {tool.durationMs === undefined ? "" : ` · ${tool.durationMs} ms`}
                  </small>
                </div>
              </li>
            ))}
          </ol>
        ) : <p className="panel-empty">提问后可在这里观察 Agent 选择了哪个工具。</p>}
      </section>

      <section>
        <p className="panel-label">引用来源</p>
        {activity.citations.length ? (
          <ul className="citation-list">
            {activity.citations.map((citation, index) => (
              <li key={`${citation.sourceId}-${index}`}>
                <strong>{citation.title}</strong>
                <small>{citation.locator ?? citation.sourceId}</small>
              </li>
            ))}
          </ul>
        ) : <p className="panel-empty">知识类回答会显示经过后端校验的来源。</p>}
      </section>

      {activity.notice ? <p className="panel-notice" role="status">{activity.notice}</p> : null}
    </aside>
  );
}
