import { useEffect, useState } from "react";

const BACKEND = (import.meta as any).env.VITE_AGENT_BACKEND_URL ?? "http://localhost:8000";

interface Stats {
  dpo_count: number;
  sft_count: number;
}

interface FeedbackRecord {
  session_id: str;
  prompt: string;
  chosen?: string;
  rejected?: string;
  user_message?: string;
  original?: string;
  corrected?: string;
  timestamp: number;
}

export default function AlignmentDashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [records, setRecords] = useState<FeedbackRecord[]>([]);
  const [activeTab, setActiveTab] = useState<"dpo" | "sft">("dpo");
  const [selectedRecord, setSelectedRecord] = useState<FeedbackRecord | null>(null);

  const fetchStatsAndData = async () => {
    setLoading(true);
    try {
      const statsRes = await fetch(`${BACKEND}/feedback/stats`);
      const statsJson = await statsRes.json();
      setStats(statsJson);

      const recordsRes = await fetch(`${BACKEND}/feedback/export?type=${activeTab}`);
      const recordsJson = await recordsRes.json();
      setRecords(recordsJson);
    } catch (e) {
      console.error("Failed to fetch alignment stats/data:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStatsAndData();
  }, [activeTab]);

  const handleExport = (type: "dpo" | "sft") => {
    window.open(`${BACKEND}/feedback/export?type=${type}`, "_blank");
  };

  return (
    <div style={styles.container}>
      <header style={styles.header}>
        <h2 style={styles.title}>🛡️ Agent Alignment Portal</h2>
        <p style={styles.sub}>
          Monitor alignment metrics, inspect logged trajectories, and export standard fine-tuning datasets for local Gemma model training.
        </p>
      </header>

      {/* Stats Cards */}
      <div style={styles.statsRow}>
        <div style={styles.card}>
          <div style={styles.cardLabel}>DPO Preference Pairs</div>
          <div style={styles.cardVal}>{stats ? stats.dpo_count : "—"}</div>
          <button style={styles.exportBtn} onClick={() => handleExport("dpo")}>
            📥 Export DPO Dataset (.jsonl)
          </button>
        </div>
        <div style={styles.card}>
          <div style={styles.cardLabel}>SFT Expert Corrections</div>
          <div style={styles.cardVal}>{stats ? stats.sft_count : "—"}</div>
          <button style={{ ...styles.exportBtn, background: "#2d7a3f" }} onClick={() => handleExport("sft")}>
            📥 Export SFT Dataset (.jsonl)
          </button>
        </div>
      </div>

      {/* Main Panel tabs */}
      <div style={styles.tabsRow}>
        <button
          style={{ ...styles.tab, ...(activeTab === "dpo" ? styles.tabActive : {}) }}
          onClick={() => setActiveTab("dpo")}
        >
          View Pairwise Preferences (DPO)
        </button>
        <button
          style={{ ...styles.tab, ...(activeTab === "sft" ? styles.tabActive : {}) }}
          onClick={() => setActiveTab("sft")}
        >
          View Expert Corrections (SFT)
        </button>
        <button style={styles.refreshBtn} onClick={fetchStatsAndData}>
          🔄 Refresh
        </button>
      </div>

      <div style={styles.tableContainer}>
        {loading ? (
          <div style={styles.empty}>Loading datasets…</div>
        ) : records.length === 0 ? (
          <div style={styles.empty}>No records found for active alignment dataset.</div>
        ) : (
          <table style={styles.table}>
            <thead>
              <tr style={styles.thRow}>
                <th style={styles.th}>Timestamp</th>
                <th style={styles.th}>Session ID</th>
                <th style={styles.th}>{activeTab === "dpo" ? "Prompt context" : "User message"}</th>
                <th style={styles.th}>Action</th>
              </tr>
            </thead>
            <tbody>
              {records.map((r, i) => (
                <tr key={i} style={styles.tr}>
                  <td style={styles.td}>{new Date(r.timestamp * 1000).toLocaleString()}</td>
                  <td style={{ ...styles.td, fontFamily: "monospace", fontSize: 11 }}>{r.session_id.slice(0, 8)}…</td>
                  <td style={styles.td}>
                    {activeTab === "dpo" ? r.prompt : r.user_message || "—"}
                  </td>
                  <td style={styles.td}>
                    <button style={styles.inspectBtn} onClick={() => setSelectedRecord(r)}>
                      🔍 Inspect
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Inspect Modal */}
      {selectedRecord && (
        <div style={styles.modalOverlay} onClick={() => setSelectedRecord(null)}>
          <div style={styles.modal} onClick={(e) => e.stopPropagation()}>
            <header style={styles.modalHeader}>
              <h3 style={styles.modalTitle}>Trajectory Inspection</h3>
              <button style={styles.modalClose} onClick={() => setSelectedRecord(null)}>
                ✕
              </button>
            </header>
            <div style={styles.modalBody}>
              <div style={styles.section}>
                <h4 style={styles.sectionTitle}>Prompt Context</h4>
                <pre style={styles.code}>{selectedRecord.prompt}</pre>
              </div>

              {activeTab === "dpo" ? (
                <div style={styles.flexSplit}>
                  <div style={styles.splitCol}>
                    <h4 style={{ ...styles.sectionTitle, color: "#2d7a3f" }}>Chosen Trajectory (Preferred)</h4>
                    <pre style={styles.code}>{selectedRecord.chosen || "—"}</pre>
                  </div>
                  <div style={styles.splitCol}>
                    <h4 style={{ ...styles.sectionTitle, color: "#a12020" }}>Rejected Trajectory (Alternative)</h4>
                    <pre style={styles.code}>{selectedRecord.rejected || "—"}</pre>
                  </div>
                </div>
              ) : (
                <div style={styles.flexSplit}>
                  <div style={styles.splitCol}>
                    <h4 style={{ ...styles.sectionTitle, color: "#e0803a" }}>Original Attempt (Faulty)</h4>
                    <pre style={styles.code}>{selectedRecord.original || "—"}</pre>
                  </div>
                  <div style={styles.splitCol}>
                    <h4 style={{ ...styles.sectionTitle, color: "#2d7a3f" }}>Corrected Response (Expert)</h4>
                    <pre style={styles.code}>{selectedRecord.corrected || "—"}</pre>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, any> = {
  container: {
    padding: "20px 24px",
    background: "#fff",
    height: "100%",
    overflowY: "auto",
    display: "flex",
    flexDirection: "column",
  },
  header: {
    marginBottom: 20,
    borderBottom: "1px solid #eee",
    paddingBottom: 15,
  },
  title: {
    margin: "0 0 6px 0",
    fontSize: 20,
    color: "#111",
  },
  sub: {
    margin: 0,
    fontSize: 13,
    color: "#666",
    lineHeight: 1.4,
  },
  statsRow: {
    display: "flex",
    gap: 16,
    marginBottom: 24,
  },
  card: {
    flex: 1,
    background: "#fafafa",
    border: "1px solid #eaeaea",
    borderRadius: 8,
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 6,
  },
  cardLabel: {
    fontSize: 11,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: "#666",
    fontWeight: 600,
  },
  cardVal: {
    fontSize: 32,
    fontWeight: "bold",
    color: "#111",
  },
  exportBtn: {
    marginTop: 8,
    background: "#1f6feb",
    color: "#fff",
    border: "none",
    borderRadius: 4,
    padding: "8px 12px",
    fontSize: 12,
    fontWeight: 600,
    cursor: "pointer",
    alignSelf: "flex-start",
  },
  tabsRow: {
    display: "flex",
    gap: 8,
    borderBottom: "1px solid #ddd",
    paddingBottom: 1,
    marginBottom: 16,
    alignItems: "center",
  },
  tab: {
    padding: "8px 16px",
    fontSize: 13,
    background: "#f5f5f5",
    border: "1px solid #ddd",
    borderBottom: "none",
    borderTopLeftRadius: 6,
    borderTopRightRadius: 6,
    cursor: "pointer",
    color: "#555",
  },
  tabActive: {
    background: "#fff",
    borderColor: "#ddd",
    borderBottom: "2px solid #fff",
    color: "#1f6feb",
    fontWeight: 600,
    position: "relative",
    bottom: -1,
  },
  refreshBtn: {
    marginLeft: "auto",
    background: "none",
    border: "none",
    cursor: "pointer",
    fontSize: 12,
    color: "#666",
  },
  tableContainer: {
    flex: 1,
    border: "1px solid #eaeaea",
    borderRadius: 6,
    overflow: "auto",
    background: "#fff",
  },
  empty: {
    padding: 32,
    textAlign: "center",
    color: "#888",
    fontSize: 13,
  },
  table: {
    width: "100%",
    borderCollapse: "collapse",
    textAlign: "left",
    fontSize: 13,
  },
  thRow: {
    background: "#fcfcfc",
    borderBottom: "1px solid #eaeaea",
  },
  th: {
    padding: "10px 14px",
    fontWeight: 600,
    color: "#444",
    fontSize: 12,
  },
  tr: {
    borderBottom: "1px solid #f0f0f0",
  },
  td: {
    padding: "10px 14px",
    color: "#555",
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
    maxWidth: 240,
  },
  inspectBtn: {
    background: "#f0f0f0",
    border: "1px solid #ccc",
    borderRadius: 4,
    padding: "4px 8px",
    fontSize: 11,
    cursor: "pointer",
    color: "#333",
  },
  modalOverlay: {
    position: "fixed",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    background: "rgba(0,0,0,0.4)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 9999,
  },
  modal: {
    width: "80%",
    maxWidth: 900,
    background: "#fff",
    borderRadius: 8,
    overflow: "hidden",
    boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
    display: "flex",
    flexDirection: "column",
    maxHeight: "85vh",
  },
  modalHeader: {
    padding: "12px 16px",
    background: "#fafafa",
    borderBottom: "1px solid #eaeaea",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  modalTitle: {
    margin: 0,
    fontSize: 15,
    color: "#111",
  },
  modalClose: {
    background: "none",
    border: "none",
    fontSize: 16,
    cursor: "pointer",
    color: "#888",
  },
  modalBody: {
    padding: 16,
    overflowY: "auto",
    display: "flex",
    flexDirection: "column",
    gap: 16,
  },
  section: {
    display: "flex",
    flexDirection: "column",
    gap: 6,
  },
  sectionTitle: {
    margin: 0,
    fontSize: 12,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: "#555",
  },
  flexSplit: {
    display: "flex",
    gap: 16,
  },
  splitCol: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    gap: 6,
  },
  code: {
    margin: 0,
    padding: 10,
    background: "#fafafa",
    border: "1px solid #eaeaea",
    borderRadius: 4,
    fontSize: 11,
    fontFamily: "monospace",
    whiteSpace: "pre-wrap",
    wordBreak: "break-all",
    maxHeight: 280,
    overflow: "auto",
  },
};
