import { describe, expect, it, vi } from "vitest";

import { postMvpEvents, readMvpEvents } from "./mvp-runtime";

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
