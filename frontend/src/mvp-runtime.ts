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
