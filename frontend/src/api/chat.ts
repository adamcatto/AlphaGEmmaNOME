import type { ChatEventType } from "../types/viz_spec";

const BACKEND = import.meta.env.VITE_AGENT_BACKEND_URL ?? "http://localhost:8000";

export interface ChatHandlers {
  onEvent: (type: ChatEventType, data: unknown) => void;
  onClose?: () => void;
}

export async function sendChat(
  message: string,
  sessionId: string | null,
  handlers: ChatHandlers,
): Promise<void> {
  const response = await fetch(`${BACKEND}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  if (!response.ok || !response.body) throw new Error(`Chat failed: ${response.status}`);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  // Frame boundary is a blank line. sse-starlette defaults to CRLF separators,
  // so events are terminated by \r\n\r\n; handle both to be safe.
  const FRAME_SEP = /\r?\n\r?\n/;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    while (true) {
      const match = FRAME_SEP.exec(buffer);
      if (!match) break;
      const rawFrame = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      const normalized = rawFrame.replace(/\r\n/g, "\n");
      const eventMatch = normalized.match(/^event: (.+)$/m);
      const dataMatch = normalized.match(/^data: (.+)$/m);
      if (!eventMatch || !dataMatch) continue;
      try {
        const type = eventMatch[1].trim() as ChatEventType;
        const data = JSON.parse(dataMatch[1]);
        handlers.onEvent(type, data);
      } catch (err) {
        console.warn("SSE parse failed", err, rawFrame);
      }
    }
  }
  handlers.onClose?.();
}

export async function uploadSequence(
  file: File,
  sessionId: string | null,
): Promise<{ upload_id: string; length: number; session_id: string }> {
  const form = new FormData();
  form.append("file", file);
  const url = new URL(`${BACKEND}/upload`);
  if (sessionId) url.searchParams.set("session_id", sessionId);
  const r = await fetch(url.toString(), { method: "POST", body: form });
  if (!r.ok) throw new Error(`Upload failed: ${r.status}`);
  return r.json();
}
