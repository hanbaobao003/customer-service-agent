import type {
  ChatModelAdapter,
  ChatModelRunResult,
  ThreadAssistantMessagePart,
  ThreadMessage,
  ToolCallMessagePart,
} from "@assistant-ui/react";

export type MvpEventType =
  | "message.delta"
  | "tool.started"
  | "tool.completed"
  | "citation"
  | "approval.required"
  | "handoff.required"
  | "error"
  | "message.completed";

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

export const APPROVAL_TOOL_NAME = "confirm_order_operation";

type AdapterOptions = {
  session: MvpSession;
  onEvent: MvpEventSink;
  fetcher?: Fetcher;
};

function parseFrame(frame: string): MvpEvent | undefined {
  const lines = frame.split("\n");
  const eventLine = lines.find((line) => line.startsWith("event: "));
  const dataLine = lines.find((line) => line.startsWith("data: "));
  if (!eventLine || !dataLine) return undefined;

  let parsed: unknown;
  try {
    parsed = JSON.parse(dataLine.slice(6));
  } catch {
    throw new Error("事件流格式错误");
  }
  if (!parsed || typeof parsed !== "object") throw new Error("事件流格式错误");

  const event = parsed as Partial<MvpEvent>;
  if (
    event.schema_version !== "1.0" ||
    event.event_type !== eventLine.slice(7) ||
    typeof event.thread_id !== "string" ||
    typeof event.request_id !== "string" ||
    typeof event.sequence !== "number" ||
    !event.payload ||
    typeof event.payload !== "object"
  ) {
    throw new Error("事件流格式错误");
  }
  return event as MvpEvent;
}

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
      const event = parseFrame(frame);
      if (event) yield event;
    }
    if (done) break;
  }

  if (buffer.trim()) {
    const event = parseFrame(buffer);
    if (event) yield event;
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
    headers: {
      "Content-Type": "application/json",
      "X-Demo-Customer": session.customerId,
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw new Error(`后端请求失败（${response.status}）`);
  yield* readMvpEvents(response);
}

function requiredString(value: unknown): string {
  if (typeof value !== "string" || !value) throw new Error("事件流格式错误");
  return value;
}

function latestUserText(messages: readonly ThreadMessage[]): string {
  const message = [...messages].reverse().find((item) => item.role === "user");
  if (!message) throw new Error("没有可发送的用户消息");
  const text = message.content
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n")
    .trim();
  if (!text) throw new Error("用户消息不能为空");
  return text;
}

type DecidedApproval = {
  interruptId: string;
  approved: boolean;
  part: ToolCallMessagePart;
};

function findDecidedApproval(message: ThreadMessage): DecidedApproval | undefined {
  if (message.role !== "assistant") return undefined;
  for (const part of message.content) {
    if (
      part.type === "tool-call" &&
      part.toolName === APPROVAL_TOOL_NAME &&
      typeof part.approval?.approved === "boolean"
    ) {
      return {
        interruptId: part.approval.id,
        approved: part.approval.approved,
        part,
      };
    }
  }
  return undefined;
}

function approvalResult(event: MvpEvent, text: string): ChatModelRunResult {
  const interruptId = requiredString(event.payload.interrupt_id);
  const args = {
    interruptId,
    operationId: requiredString(event.payload.operation_id),
    toolName: requiredString(event.payload.tool_name),
  };
  const content: ThreadAssistantMessagePart[] = [];
  if (text) content.push({ type: "text", text });
  content.push({
    type: "tool-call",
    toolCallId: interruptId,
    toolName: APPROVAL_TOOL_NAME,
    args,
    argsText: JSON.stringify(args),
    approval: { id: interruptId, prompt: "订单操作需要你的确认" },
  });
  return {
    content,
    status: { type: "requires-action", reason: "tool-calls" },
  };
}

function publicEventError(event: MvpEvent): string {
  const message = event.payload.message;
  if (typeof message === "string" && message.trim()) return message;
  const code = event.payload.code;
  return typeof code === "string" && code ? `请求失败（${code}）` : "请求处理失败";
}

export function createMvpAdapter(options: AdapterOptions): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal, unstable_getMessage }) {
      const current = unstable_getMessage();
      const decided = findDecidedApproval(current);
      const threadId = encodeURIComponent(options.session.threadId);
      const path = decided
        ? `/v1/threads/${threadId}/decisions`
        : `/v1/threads/${threadId}/messages:stream`;
      const body = decided
        ? {
            interrupt_id: decided.interruptId,
            decision: decided.approved ? "approve" : "reject",
            ...(!decided.approved ? { reason: "用户拒绝本次操作" } : {}),
          }
        : { message: latestUserText(messages) };
      const preserved: ThreadAssistantMessagePart[] = decided ? [decided.part] : [];
      let text = "";

      for await (const event of postMvpEvents(
        path,
        body,
        options.session,
        abortSignal,
        options.fetcher,
      )) {
        options.onEvent(event);
        if (event.event_type === "message.delta") {
          text += requiredString(event.payload.text);
        } else if (event.event_type === "message.completed" && !text) {
          text = requiredString(event.payload.message);
        } else if (event.event_type === "handoff.required" && !text) {
          text = "需要人工客服继续处理本次请求。";
        } else if (event.event_type === "error") {
          throw new Error(publicEventError(event));
        } else if (event.event_type === "approval.required") {
          yield approvalResult(event, text);
          return;
        }

        if (text) {
          yield {
            content: [...preserved, { type: "text", text }],
          } satisfies ChatModelRunResult;
        }
      }
    },
  };
}
