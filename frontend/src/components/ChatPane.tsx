import { useState } from "react";
import { sendChat } from "../api/chat";
import { useStore } from "../state/store";
import type { ProgressEntry, ToolCall, VizSpec } from "../types/viz_spec";
import SequenceUpload from "./SequenceUpload";

const BACKEND = (import.meta as any).env.VITE_AGENT_BACKEND_URL ?? "http://localhost:8000";

export default function ChatPane() {
  const { messages, pushMessage, updateLast, pushViz, sessionId, setSessionId } = useStore();
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [ratings, setRatings] = useState<Record<number, "up" | "down">>({});
  const [rejectedContents, setRejectedContents] = useState<Record<number, string>>({});

  const send = async () => {
    if (!input.trim() || busy) return;
    const userMessage = input;
    setInput("");
    pushMessage({ role: "user", content: userMessage });
    pushMessage({ role: "assistant", content: "", toolCalls: [], streaming: true });
    setBusy(true);

    try {
      await streamChatResponse(userMessage, sessionId);
    } catch (err) {
      updateLast((m) => ({ ...m, content: `Transport error: ${String(err)}`, streaming: false }));
    } finally {
      setBusy(false);
    }
  };

  const streamChatResponse = async (userMessage: string, currentSessionId: string | null) => {
    await sendChat(userMessage, currentSessionId, {
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
          updateLast((m) => {
            const progress = [...(m.progress ?? [])];
            const last = progress[progress.length - 1];
            if (
              last &&
              last.stage === entry.stage &&
              entry.stage === "optimize_edits" &&
              entry.current != null
            ) {
              progress[progress.length - 1] = entry;
            } else {
              progress.push(entry);
            }
            return { ...m, progress };
          });
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
  };

  const handleRating = async (index: number, rating: "up" | "down") => {
    if (!sessionId) return;
    setRatings((prev) => ({ ...prev, [index]: rating }));

    const message = messages[index];
    const prevUserMessage = messages[index - 1]?.content ?? "";
    const promptContext = `User: ${prevUserMessage}`;

    try {
      await fetch(`${BACKEND}/feedback/preference`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          prompt: promptContext,
          chosen: rating === "up" ? message.content : rejectedContents[index] || "",
          rejected: rating === "down" ? message.content : "",
        }),
      });
    } catch (e) {
      console.error("Failed to log rating feedback:", e);
    }
  };

  const handleRegenerate = async (index: number) => {
    if (!sessionId || busy) return;
    const prevUserMessage = messages[index - 1]?.content;
    if (!prevUserMessage) return;

    // Cache current content as rejected
    setRejectedContents((prev) => ({ ...prev, [index]: messages[index].content }));

    setBusy(true);
    // Reset message state
    messages[index] = { role: "assistant", content: "", toolCalls: [], streaming: true };

    try {
      // Pop the last user/assistant turn from the backend history
      await fetch(`${BACKEND}/sessions/${sessionId}/history/pop`, { method: "POST" });
      // Stream new response
      await streamChatResponse(prevUserMessage, sessionId);
    } catch (err) {
      console.error("Regeneration failed:", err);
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
            {m.thoughts && <ThoughtBlock text={m.thoughts} sessionId={sessionId} userMessage={messages[i-1]?.content} />}
            {m.progress && m.progress.length > 0 && <ProgressList entries={m.progress} />}
            {m.toolCalls && m.toolCalls.length > 0 && (
              <div style={styles.toolList}>
                {m.toolCalls.map((tc, j) => (
                  <ToolCallRow key={j} call={tc} sessionId={sessionId} userMessage={messages[i-1]?.content} />
                ))}
              </div>
            )}
            <div style={styles.content}>
              {m.content}
              {m.streaming && <span style={styles.cursor}>▋</span>}
            </div>

            {m.role === "assistant" && !m.streaming && (
              <div style={styles.feedbackRow}>
                <button
                  style={{ ...styles.feedbackBtn, color: ratings[i] === "up" ? "#2d7a3f" : "#666" }}
                  onClick={() => handleRating(i, "up")}
                  title="Thumbs up"
                >
                  👍 {ratings[i] === "up" ? "Liked" : ""}
                </button>
                <button
                  style={{ ...styles.feedbackBtn, color: ratings[i] === "down" ? "#a12020" : "#666" }}
                  onClick={() => handleRating(i, "down")}
                  title="Thumbs down"
                >
                  👎 {ratings[i] === "down" ? "Disliked" : ""}
                </button>
                {i === messages.length - 1 && (
                  <button
                    style={styles.regenerateBtn}
                    onClick={() => handleRegenerate(i)}
                    disabled={busy}
                    title="Regenerate alternative response"
                  >
                    🔄 Regenerate Alternative
                  </button>
                )}
              </div>
            )}
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
        const hasBar =
          e.current != null && e.total != null && e.total > 0;
        const pct = hasBar
          ? Math.min(100, Math.round((e.current! / e.total!) * 100))
          : 0;
        return (
          <div key={i} style={styles.progressRow}>
            <span style={{ ...styles.progressDot, background: dotColor }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <span>{e.text}</span>
              {hasBar && (
                <div style={styles.progressBarTrack} aria-label={`${e.current} of ${e.total}`}>
                  <div style={{ ...styles.progressBarFill, width: `${pct}%` }} />
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ThoughtBlock({ text, sessionId, userMessage }: { text: string; sessionId: string | null; userMessage?: string }) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [correctedText, setCorrectedText] = useState(text);
  const [submitted, setSubmitted] = useState(false);

  const applyCorrection = async () => {
    if (!sessionId) return;
    try {
      await fetch(`${BACKEND}/feedback/correction`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          prompt: `User: ${userMessage || ""}`,
          user_message: userMessage || "",
          original: text,
          corrected: correctedText,
        }),
      });
      setSubmitted(true);
      setEditing(false);
    } catch (e) {
      console.error("Failed to log correction:", e);
    }
  };

  return (
    <div style={styles.collapsible}>
      <div style={styles.collapsibleHeaderRow}>
        <button style={styles.collapsibleHeader} onClick={() => setOpen(!open)}>
          <span>{open ? "▼" : "▶"}</span> thinking
        </button>
        {open && !submitted && (
          <button style={styles.editBtn} onClick={() => setEditing(!editing)}>
            {editing ? "Cancel" : "✏️ Correct Step"}
          </button>
        )}
        {submitted && <span style={styles.savedBadge}>✓ Correction Saved</span>}
      </div>
      {open && (
        <div style={styles.collapsibleBodyContainer}>
          {editing ? (
            <div style={styles.correctionForm}>
              <textarea
                style={styles.correctionArea}
                value={correctedText}
                onChange={(e) => setCorrectedText(e.target.value)}
              />
              <button style={styles.saveCorrectionBtn} onClick={applyCorrection}>
                Save Expert Correction
              </button>
            </div>
          ) : (
            <pre style={styles.collapsibleBody}>{correctedText}</pre>
          )}
        </div>
      )}
    </div>
  );
}

function ToolCallRow({ call, sessionId, userMessage }: { call: ToolCall; sessionId: string | null; userMessage?: string }) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [correctedArgs, setCorrectedArgs] = useState(JSON.stringify(call.arguments || {}, null, 2));
  const [submitted, setSubmitted] = useState(false);

  const hasDetail = Boolean(call.arguments || call.observation);
  const statusColor =
    call.status === "done" ? "#2d7a3f" : call.status === "error" ? "#a12020" : "#8a6d00";

  const applyCorrection = async () => {
    if (!sessionId) return;
    try {
      await fetch(`${BACKEND}/feedback/correction`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          prompt: `User: ${userMessage || ""}`,
          user_message: userMessage || "",
          original: JSON.stringify(call.arguments || {}),
          corrected: correctedArgs,
        }),
      });
      setSubmitted(true);
      setEditing(false);
    } catch (e) {
      console.error("Failed to log tool correction:", e);
    }
  };

  return (
    <div style={styles.toolCall}>
      <div style={styles.toolHeaderRow}>
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
        {open && !submitted && call.arguments !== undefined && (
          <button style={styles.editBtn} onClick={() => setEditing(!editing)}>
            {editing ? "Cancel" : "✏️ Correct Args"}
          </button>
        )}
        {submitted && <span style={styles.savedBadge}>✓ Args Correction Saved</span>}
      </div>
      {open && (
        <div style={styles.toolBody}>
          {call.arguments !== undefined && (
            <>
              <div style={styles.toolLabel}>arguments</div>
              {editing ? (
                <div style={styles.correctionForm}>
                  <textarea
                    style={styles.correctionArea}
                    value={correctedArgs}
                    onChange={(e) => setCorrectedArgs(e.target.value)}
                  />
                  <button style={styles.saveCorrectionBtn} onClick={applyCorrection}>
                    Save Args Correction
                  </button>
                </div>
              ) : (
                <pre style={styles.pre}>{correctedArgs}</pre>
              )}
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

const styles: Record<string, any> = {
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
  message: { padding: "12px 14px", borderRadius: 6, marginBottom: 12 },
  userMsg: { background: "#f0f4ff" },
  assistantMsg: { background: "#fafafa", border: "1px solid #eee" },
  role: { fontSize: 11, opacity: 0.6, marginBottom: 6 },
  content: { whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.5 },
  cursor: { opacity: 0.4, marginLeft: 2, animation: "blink 1s steps(1) infinite" },
  collapsible: { marginBottom: 6, border: "1px solid #eee", borderRadius: 4, background: "#fff" },
  collapsibleHeaderRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    borderBottom: "1px solid #eee",
    background: "#fdfdfd",
  },
  collapsibleHeader: {
    padding: "6px 10px",
    fontSize: 11,
    fontFamily: "monospace",
    background: "transparent",
    border: "none",
    cursor: "pointer",
    color: "#666",
    flex: 1,
    textAlign: "left",
  },
  collapsibleBodyContainer: {
    padding: "6px 10px",
    borderTop: "1px solid #eee",
  },
  collapsibleBody: {
    margin: 0,
    fontSize: 11,
    fontFamily: "monospace",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    color: "#444",
    maxHeight: 280,
    overflow: "auto",
    border: "none",
    background: "transparent",
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
    flexShrink: 0,
  },
  progressBarTrack: {
    marginTop: 4,
    height: 4,
    background: "#d8e2f8",
    borderRadius: 2,
    overflow: "hidden",
  },
  progressBarFill: {
    height: "100%",
    background: "#1f6feb",
    borderRadius: 2,
    transition: "width 0.2s ease",
  },
  toolList: { display: "flex", flexDirection: "column", gap: 3, marginBottom: 6 },
  toolCall: { border: "1px solid #eee", borderRadius: 4, background: "#fff" },
  toolHeaderRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    background: "#fdfdfd",
    borderBottom: "1px solid #eee",
  },
  toolHeader: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    padding: "6px 10px",
    fontSize: 11,
    fontFamily: "monospace",
    background: "transparent",
    border: "none",
    textAlign: "left",
    flex: 1,
  },
  toolName: { fontWeight: 600 },
  toolStatus: { opacity: 0.6, marginLeft: "auto" },
  caret: { marginLeft: 4, fontSize: 9, opacity: 0.5 },
  toolBody: { padding: "6px 10px" },
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
  feedbackRow: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    marginTop: 8,
    paddingTop: 8,
    borderTop: "1px dashed #eee",
  },
  feedbackBtn: {
    background: "none",
    border: "none",
    cursor: "pointer",
    fontSize: 11,
    display: "flex",
    alignItems: "center",
    gap: 4,
  },
  regenerateBtn: {
    marginLeft: "auto",
    background: "#f0f0f0",
    border: "1px solid #ccc",
    borderRadius: 4,
    padding: "3px 8px",
    fontSize: 11,
    cursor: "pointer",
    color: "#333",
  },
  editBtn: {
    background: "none",
    border: "none",
    cursor: "pointer",
    fontSize: 10,
    color: "#1f6feb",
    padding: "0 8px",
  },
  savedBadge: {
    fontSize: 10,
    color: "#2d7a3f",
    fontWeight: "bold",
    paddingRight: 8,
  },
  correctionForm: {
    display: "flex",
    flexDirection: "column",
    gap: 6,
    marginTop: 4,
  },
  correctionArea: {
    width: "100%",
    minHeight: 80,
    fontSize: 11,
    fontFamily: "monospace",
    padding: 6,
    border: "1px solid #ccc",
    borderRadius: 4,
  },
  saveCorrectionBtn: {
    background: "#2d7a3f",
    color: "#fff",
    border: "none",
    borderRadius: 4,
    padding: "4px 8px",
    fontSize: 10,
    cursor: "pointer",
    alignSelf: "flex-end",
  },
};
