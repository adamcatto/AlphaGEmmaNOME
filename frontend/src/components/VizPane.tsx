import { useStore } from "../state/store";
import TrackViewer from "./TrackViewer";
import IgvTrackBrowser from "./IgvTrackBrowser";
import ContactMapPlot from "./ContactMapPlot";

export default function VizPane() {
  const { vizSpecs, activeVizIndex, setActiveViz, useIgvFallback, setUseIgvFallback } = useStore();
  const active = vizSpecs[activeVizIndex];

  return (
    <div style={styles.pane}>
      <div style={styles.tabRow}>
        <div style={styles.tabs}>
          {vizSpecs.length === 0 && <div style={styles.empty}>No visualizations yet.</div>}
          {vizSpecs.map((s, i) => (
            <button
              key={i}
              onClick={() => setActiveViz(i)}
              style={{ ...styles.tab, ...(i === activeVizIndex ? styles.tabActive : {}) }}
            >
              {s.type} · {s.head ?? "—"}
            </button>
          ))}
        </div>
        <label style={styles.toggle}>
          <input
            type="checkbox"
            checked={useIgvFallback}
            onChange={(e) => setUseIgvFallback(e.target.checked)}
          />
          <span>use IGV</span>
        </label>
      </div>
      <div style={styles.panel}>
        {active && active.type === "igv_tracks" && (
          useIgvFallback ? <IgvTrackBrowser spec={active} /> : <TrackViewer spec={active} />
        )}
        {active && active.type === "contact_map" && <ContactMapPlot spec={active} />}
        {active &&
          !["igv_tracks", "contact_map"].includes(active.type) && (
            <pre style={styles.fallback}>{JSON.stringify(active, null, 2)}</pre>
          )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  pane: { display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden" },
  tabRow: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: 6,
    borderBottom: "1px solid #ddd",
    minHeight: 36,
  },
  tabs: { display: "flex", gap: 4, overflowX: "auto", flex: 1 },
  tab: {
    fontSize: 12,
    padding: "4px 10px",
    background: "#f0f0f0",
    border: "1px solid #ccc",
    borderRadius: 4,
    cursor: "pointer",
  },
  tabActive: { background: "#1f6feb", color: "#fff", borderColor: "#1f6feb" },
  toggle: {
    display: "flex",
    alignItems: "center",
    gap: 4,
    fontSize: 11,
    color: "#666",
    padding: "0 8px",
    whiteSpace: "nowrap",
  },
  panel: { flex: 1, overflow: "hidden", position: "relative" },
  empty: { fontSize: 12, color: "#888", padding: 8 },
  fallback: { padding: 12, fontSize: 12, overflow: "auto" },
};
