import { useState } from "react";
import { sendChat } from "../api/chat";
import { useStore } from "../state/store";
import type { ProgressEntry, ToolCall, VizSpec } from "../types/viz_spec";
import SequenceUpload from "./SequenceUpload";

export default function ChatPane() {
  const { messages, pushMessage, updateLast, pushViz, sessionId, setSessionId } = useStore();
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  const send = async () => {
    if (!input.trim() || busy) return;
    const userMessage = input;
    setInput("");
    pushMessage({ role: "user", content: userMessage });
    pushMessage({ role: "assistant", content: "", toolCalls: [], streaming: true });
    setBusy(true);

    try {
      await sendChat(userMessage, sessionId, {
        onEvent: (type, data) => {
          if (type === "token") {
            const text = (data as { text: string }).text;
            updateLast((m) => ({ ...m, content: (m.content ?? "") + text }));
          } else if (type === "thought") {
            const text = (data as { text: string }).text;
            updateLast((m) => ({
              ...m,
              thoughts: (m.thoughts ? m.thoughts + "\n\n" : "") + text,
            }));
          } else if (type === "progress") {
            const entry = data as ProgressEntry;
            updateLast((m) => ({
              ...m,
              progress: [...(m.progress ?? []), entry],
            }));
          } else if (type === "tool_call_start") {
            const { tool, arguments: args } = data as { tool: string; arguments?: unknown };
            updateLast((m) => ({
              ...m,
              toolCalls: [...(m.toolCalls ?? []), { tool, status: "pending", arguments: args }],
            }));
          } else if (type === "tool_call_result") {
            const observation = (data as { observation: string }).observation;
            updateLast((m) => {
              const calls = [...(m.toolCalls ?? [])];
              if (calls.length > 0) {
                calls[calls.length - 1] = {
                  ...calls[calls.length - 1],
                  status: "done",
                  observation,
                };
              }
              return { ...m, toolCalls: calls };
            });
          } else if (type === "viz_spec") {
            pushViz(data as VizSpec);
          } else if (type === "final") {
            const payload = data as { session_id: string; answer: string };
            setSessionId(payload.session_id);
            updateLast((m) => ({
              ...m,
              content: m.content && m.content.length > 0 ? m.content : payload.answer,
              streaming: false,
            }));
          } else if (type === "error") {
            const payload = data as { error: string };
            updateLast((m) => ({ ...m, content: `Error: ${payload.error}`, streaming: false }));
          }
        },
        onClose: () => {
          updateLast((m) => ({ ...m, streaming: false }));
        },
      });
    } catch (err) {
      updateLast((m) => ({ ...m, content: `Transport error: ${String(err)}`, streaming: false }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={styles.pane}>
      <header style={styles.header}>OmniGemmaNome</header>
      <div style={styles.messages}>
        {messages.map((m, i) => (
          <div key={i} style={{ ...styles.message, ...(m.role === "user" ? styles.userMsg : styles.assistantMsg) }}>
            <div style={styles.role}>{m.role}</div>
            {m.thoughts && <ThoughtBlock text={m.thoughts} />}
            {m.progress && m.progress.length > 0 && <ProgressList entries={m.progress} />}
            {m.toolCalls && m.toolCalls.length > 0 && (
              <div style={styles.toolList}>
                {m.toolCalls.map((tc, j) => (
                  <ToolCallRow key={j} call={tc} />
                ))}
              </div>
            )}
            <div style={styles.content}>
              {m.content}
              {m.streaming && <span style={styles.cursor}>▋</span>}
            </div>
          </div>
        ))}
      </div>
      <SequenceUpload />
      <div style={styles.inputRow}>
        <input
          style={styles.input}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && send()}
          placeholder="Ask about a gene, locus, or variant…"
          disabled={busy}
        />
        <button style={styles.button} onClick={send} disabled={busy}>
          {busy ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}

function ProgressList({ entries }: { entries: ProgressEntry[] }) {
  return (
    <div style={styles.progressList}>
      {entries.map((e, i) => {
        const dotColor =
          e.stage.endsWith("_error")
            ? "#a12020"
            : e.stage.endsWith("_done")
              ? "#2d7a3f"
              : "#8a6d00";
        return (
          <div key={i} style={styles.progressRow}>
            <span style={{ ...styles.progressDot, background: dotColor }} />
            <span>{e.text}</span>
          </div>
        );
      })}
    </div>
  );
}

function ThoughtBlock({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={styles.collapsible}>
      <button style={styles.collapsibleHeader} onClick={() => setOpen(!open)}>
        <span>{open ? "▼" : "▶"}</span> thinking
      </button>
      {open && <pre style={styles.collapsibleBody}>{text}</pre>}
    </div>
  );
}

function ToolCallRow({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false);
  const hasDetail = Boolean(call.arguments || call.observation);
  const statusColor =
    call.status === "done" ? "#2d7a3f" : call.status === "error" ? "#a12020" : "#8a6d00";
  return (
    <div style={styles.toolCall}>
      <button
        style={{ ...styles.toolHeader, cursor: hasDetail ? "pointer" : "default" }}
        onClick={() => hasDetail && setOpen(!open)}
        disabled={!hasDetail}
      >
        <span style={{ color: statusColor, fontWeight: 600 }}>●</span>
        <span style={styles.toolName}>{call.tool}</span>
        <span style={styles.toolStatus}>{call.status}</span>
        {hasDetail && <span style={styles.caret}>{open ? "▼" : "▶"}</span>}
      </button>
      {open && (
        <div style={styles.toolBody}>
          {call.arguments !== undefined && (
            <>
              <div style={styles.toolLabel}>arguments</div>
              <pre style={styles.pre}>{JSON.stringify(call.arguments, null, 2)}</pre>
            </>
          )}
          {call.observation && (
            <>
              <div style={styles.toolLabel}>observation</div>
              <pre style={styles.pre}>{call.observation}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  pane: {
    display: "flex",
    flexDirection: "column",
    borderRight: "1px solid #ddd",
    height: "100vh",
    overflow: "hidden",
  },
  header: {
    padding: "12px 16px",
    borderBottom: "1px solid #ddd",
    fontWeight: 600,
    fontSize: 14,
  },
  messages: { flex: 1, overflowY: "auto", padding: "8px 12px" },
  message: { padding: "8px 10px", borderRadius: 6, marginBottom: 8 },
  userMsg: { background: "#f0f4ff" },
  assistantMsg: { background: "#fafafa", border: "1px solid #eee" },
  role: { fontSize: 11, opacity: 0.6, marginBottom: 4 },
  content: { whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.5 },
  cursor: { opacity: 0.4, marginLeft: 2, animation: "blink 1s steps(1) infinite" },
  collapsible: { marginBottom: 6, border: "1px solid #eee", borderRadius: 4, background: "#fff" },
  collapsibleHeader: {
    width: "100%",
    textAlign: "left",
    padding: "4px 8px",
    fontSize: 11,
    fontFamily: "monospace",
    background: "transparent",
    border: "none",
    cursor: "pointer",
    color: "#666",
  },
  collapsibleBody: {
    margin: 0,
    padding: "6px 10px",
    fontSize: 11,
    fontFamily: "monospace",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    borderTop: "1px solid #eee",
    color: "#444",
    maxHeight: 280,
    overflow: "auto",
  },
  progressList: {
    display: "flex",
    flexDirection: "column",
    gap: 3,
    marginBottom: 6,
    padding: "4px 6px",
    borderLeft: "2px solid #1f6feb",
    background: "#f5f8ff",
    borderRadius: 3,
  },
  progressRow: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    fontSize: 11,
    fontFamily: "monospace",
    color: "#333",
  },
  progressDot: {
    width: 6,
    height: 6,
    borderRadius: "50%",
    display: "inline-block",
  },
  toolList: { display: "flex", flexDirection: "column", gap: 3, marginBottom: 6 },
  toolCall: { border: "1px solid #eee", borderRadius: 4, background: "#fff" },
  toolHeader: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    width: "100%",
    padding: "4px 8px",
    fontSize: 11,
    fontFamily: "monospace",
    background: "transparent",
    border: "none",
    textAlign: "left",
  },
  toolName: { fontWeight: 600 },
  toolStatus: { opacity: 0.6, marginLeft: "auto" },
  caret: { marginLeft: 4, fontSize: 9, opacity: 0.5 },
  toolBody: { borderTop: "1px solid #eee", padding: "6px 10px" },
  toolLabel: { fontSize: 10, textTransform: "uppercase", opacity: 0.5, marginTop: 4 },
  pre: {
    margin: "2px 0 6px 0",
    fontSize: 11,
    fontFamily: "monospace",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    maxHeight: 240,
    overflow: "auto",
  },
  inputRow: {
    display: "flex",
    gap: 6,
    padding: 10,
    borderTop: "1px solid #ddd",
    background: "#fff",
  },
  input: { flex: 1, padding: 8, fontSize: 13, border: "1px solid #ccc", borderRadius: 4 },
  button: {
    padding: "8px 16px",
    fontSize: 13,
    background: "#1f6feb",
    color: "#fff",
    border: "none",
    borderRadius: 4,
    cursor: "pointer",
  },
};
