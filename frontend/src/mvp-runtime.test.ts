import type {
  ChatModelAdapter,
  ChatModelRunOptions,
  ChatModelRunResult,
  ThreadAssistantMessage,
  ThreadUserMessage,
} from "@assistant-ui/react";
import { describe, expect, it, vi } from "vitest";

import {
  APPROVAL_TOOL_NAME,
  createMvpAdapter,
  postMvpEvents,
  readMvpEvents,
  type MvpEvent,
  type MvpEventType,
} from "./mvp-runtime";

function responseFromChunks(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)));
        controller.close();
      },
    }),
    { status, headers: { "content-type": "text/event-stream" } },
  );
}

function event(eventType: MvpEventType, payload: Record<string, unknown>, sequence = 1): MvpEvent {
  return {
    schema_version: "1.0",
    event_type: eventType,
    thread_id: "t-1",
    request_id: "r-1",
    sequence,
    payload,
  };
}

function responseFromEvents(events: MvpEvent[]): Response {
  return responseFromChunks(
    events.map((item) => `id: ${item.sequence}\nevent: ${item.event_type}\ndata: ${JSON.stringify(item)}\n\n`),
  );
}

function userMessage(text: string): ThreadUserMessage {
  return {
    id: "user-1",
    role: "user",
    createdAt: new Date("2026-09-07T00:00:00Z"),
    content: [{ type: "text", text }],
    attachments: [],
    metadata: { custom: {} },
  };
}

function assistantMessageWithApproval(approved: boolean): ThreadAssistantMessage {
  return {
    id: "assistant-1",
    role: "assistant",
    createdAt: new Date("2026-09-07T00:00:01Z"),
    status: { type: "requires-action", reason: "tool-calls" },
    content: [
      {
        type: "tool-call",
        toolCallId: "interrupt-1",
        toolName: APPROVAL_TOOL_NAME,
        args: { interruptId: "interrupt-1", toolName: "cancel_order" },
        argsText: '{"interruptId":"interrupt-1","toolName":"cancel_order"}',
        approval: { id: "interrupt-1", approved },
      },
    ],
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: {},
    },
  };
}

function runOptions(text: string, current?: ThreadAssistantMessage): ChatModelRunOptions {
  const fallback: ThreadAssistantMessage = {
    ...assistantMessageWithApproval(true),
    content: [],
    status: { type: "running" },
  };
  return {
    messages: [userMessage(text)],
    runConfig: {},
    abortSignal: new AbortController().signal,
    context: {},
    unstable_getMessage: () => current ?? fallback,
  };
}

async function collectRun(
  adapter: ChatModelAdapter,
  options: ChatModelRunOptions,
): Promise<ChatModelRunResult[]> {
  const run = adapter.run(options);
  if (!(Symbol.asyncIterator in Object(run))) {
    return [await (run as Promise<ChatModelRunResult>)];
  }
  const chunks: ChatModelRunResult[] = [];
  for await (const chunk of run as AsyncGenerator<ChatModelRunResult>) chunks.push(chunk);
  return chunks;
}

describe("readMvpEvents", () => {
  it("parses an SSE frame split across network chunks", async () => {
    const response = responseFromChunks([
      "id: 1\nevent: message.del",
      'ta\ndata: {"schema_version":"1.0","event_type":"message.delta",',
      '"thread_id":"t-1","request_id":"r-1","sequence":1,"payload":{"text":"你"}}\n\n',
    ]);

    const events = [];
    for await (const event of readMvpEvents(response)) events.push(event);

    expect(events).toEqual([
      expect.objectContaining({
        event_type: "message.delta",
        payload: { text: "你" },
      }),
    ]);
  });

  it("flushes a final frame without a blank-line terminator", async () => {
    const response = responseFromChunks([
      'event: message.completed\ndata: {"schema_version":"1.0","event_type":"message.completed","thread_id":"t-1","request_id":"r-1","sequence":1,"payload":{"message":"完成"}}',
    ]);

    const events = [];
    for await (const event of readMvpEvents(response)) events.push(event);

    expect(events[0]?.payload).toEqual({ message: "完成" });
  });
});

describe("postMvpEvents", () => {
  it("posts the trusted demo header and JSON body", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(responseFromChunks([]));

    for await (const _event of postMvpEvents(
      "/v1/threads/t-1/messages:stream",
      { message: "你好" },
      { threadId: "t-1", customerId: "demo-customer-a" },
      new AbortController().signal,
      fetcher,
    )) {
      // Consume the response to exercise the complete request boundary.
    }

    expect(fetcher).toHaveBeenCalledWith(
      "/v1/threads/t-1/messages:stream",
      expect.objectContaining({
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Demo-Customer": "demo-customer-a",
        },
        body: JSON.stringify({ message: "你好" }),
      }),
    );
  });

  it("does not expose a failed response body", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("database password=secret", { status: 503 }));

    const consume = async () => {
      for await (const _event of postMvpEvents(
        "/v1/x",
        {},
        { threadId: "t", customerId: "demo-customer-a" },
        new AbortController().signal,
        fetcher,
      )) {
        // Consume the response to surface generator errors.
      }
    };

    await expect(consume()).rejects.toThrow("后端请求失败（503）");
    await expect(consume()).rejects.not.toThrow("secret");
  });
});

describe("createMvpAdapter", () => {
  it("yields cumulative text while forwarding public side events", async () => {
    const seen: MvpEventType[] = [];
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      responseFromEvents([
        event("tool.started", { tool_call_id: "c-1", tool_name: "search_product_faq" }, 1),
        event("citation", { source_id: "faq-1", title: "保修说明", locator: "第 1 节" }, 2),
        event("message.delta", { text: "保修" }, 3),
        event("message.delta", { text: "期为 12 个月" }, 4),
        event("message.completed", { message: "保修期为 12 个月" }, 5),
      ]),
    );
    const adapter = createMvpAdapter({
      session: { threadId: "t-1", customerId: "demo-customer-a" },
      fetcher,
      onEvent: (item) => seen.push(item.event_type),
    });

    const output = await collectRun(adapter, runOptions("商品保修多久"));

    expect(output.map((item) => item.content)).toEqual([
      [{ type: "text", text: "保修" }],
      [{ type: "text", text: "保修期为 12 个月" }],
      [{ type: "text", text: "保修期为 12 个月" }],
    ]);
    expect(seen).toEqual([
      "tool.started",
      "citation",
      "message.delta",
      "message.delta",
      "message.completed",
    ]);
  });

  it("turns approval.required into a pending approval tool call", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      responseFromEvents([
        event("approval.required", {
          interrupt_id: "interrupt-1",
          operation_id: "operation-1",
          tool_name: "cancel_order",
          preview: { raw_sql: "must-not-leak" },
          allowed_decisions: ["approve", "reject"],
        }),
      ]),
    );
    const adapter = createMvpAdapter({
      session: { threadId: "t-1", customerId: "demo-customer-a" },
      fetcher,
      onEvent: vi.fn(),
    });

    const output = await collectRun(adapter, runOptions("取消订单"));

    expect(output.at(-1)).toEqual({
      status: { type: "requires-action", reason: "tool-calls" },
      content: [
        {
          type: "tool-call",
          toolCallId: "interrupt-1",
          toolName: APPROVAL_TOOL_NAME,
          args: {
            interruptId: "interrupt-1",
            operationId: "operation-1",
            toolName: "cancel_order",
          },
          argsText: JSON.stringify({
            interruptId: "interrupt-1",
            operationId: "operation-1",
            toolName: "cancel_order",
          }),
          approval: { id: "interrupt-1", prompt: "订单操作需要你的确认" },
        },
      ],
    });
    expect(JSON.stringify(output)).not.toContain("must-not-leak");
  });

  it.each([
    [true, { interrupt_id: "interrupt-1", decision: "approve" }],
    [
      false,
      {
        interrupt_id: "interrupt-1",
        decision: "reject",
        reason: "用户拒绝本次操作",
      },
    ],
  ])("posts a decided approval to the same thread", async (approved, expectedBody) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      responseFromEvents([
        event("message.completed", { message: "订单操作已处理" }),
      ]),
    );
    const adapter = createMvpAdapter({
      session: { threadId: "t-1", customerId: "demo-customer-a" },
      fetcher,
      onEvent: vi.fn(),
    });

    const output = await collectRun(
      adapter,
      runOptions("取消订单", assistantMessageWithApproval(approved)),
    );

    expect(fetcher).toHaveBeenCalledWith(
      "/v1/threads/t-1/decisions",
      expect.objectContaining({ body: JSON.stringify(expectedBody) }),
    );
    expect(output.at(-1)?.content).toContainEqual({
      type: "text",
      text: "订单操作已处理",
    });
  });
});
