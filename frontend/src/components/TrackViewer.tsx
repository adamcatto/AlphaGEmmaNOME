import { useEffect, useMemo, useRef, useState } from "react";
import type { VizSpec } from "../types/viz_spec";
import { useStore } from "../state/store";
import { fetchTracks, trackLabel, type TracksResponse, type TrackRow } from "../api/tracks";

interface Props {
  spec: VizSpec;
}

// Per-head accent. Keeps the UI readable when several predictions are open in
// tabs: color at a glance tells you what kind of signal you're looking at.
const HEAD_COLORS: Record<string, [string, string]> = {
  chip_tf: ["#7c5cff", "#bfb2ff"],
  chip_histone: ["#2fa8c8", "#a3e0ef"],
  atac: ["#e0803a", "#f3c59a"],
  dnase: ["#d65ea0", "#f3bcd9"],
  cage: ["#4aa86a", "#bde4c8"],
  rna_seq: ["#c94b4b", "#eeb0b0"],
  procap: ["#5a8db0", "#bcd4e4"],
  splice_sites: ["#8a7bcb", "#cfc7ea"],
  splice_junctions: ["#8a7bcb", "#cfc7ea"],
  splice_site_usage: ["#8a7bcb", "#cfc7ea"],
  contact_maps: ["#3d3d3d", "#bababa"],
};

function colorFor(head: string): [string, string] {
  return HEAD_COLORS[head] ?? ["#1f6feb", "#b5d0f6"];
}

interface LocusRange {
  chrom: string;
  start: number;
  end: number;
}

function parseLocus(locus: string | null): LocusRange | null {
  if (!locus) return null;
  const m = locus.match(/^(chr[\w]+):(\d+)-(\d+)$/);
  if (!m) return null;
  return { chrom: m[1], start: parseInt(m[2], 10), end: parseInt(m[3], 10) };
}

function fmtBp(bp: number): string {
  if (bp >= 1e6) return `${(bp / 1e6).toFixed(bp % 1e6 === 0 ? 0 : 2)}Mb`;
  if (bp >= 1e3) return `${(bp / 1e3).toFixed(bp % 1e3 === 0 ? 0 : 1)}kb`;
  return `${bp}bp`;
}

export default function TrackViewer({ spec }: Props) {
  const sessionId = useStore((s) => s.sessionId);
  const [data, setData] = useState<TracksResponse | null>(null);
  const [compareData, setCompareData] = useState<TracksResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Zoom range states (bin indices)
  const [zoomStart, setZoomStart] = useState<number | null>(null);
  const [zoomEnd, setZoomEnd] = useState<number | null>(null);
  const [inputStart, setInputStart] = useState("");
  const [inputEnd, setInputEnd] = useState("");

  useEffect(() => {
    if (!sessionId || !spec.head) {
      setData(null);
      setCompareData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);

    const primaryP = fetchTracks(sessionId, spec.prediction_id, spec.head, spec.track_indices);
    const compareP = spec.compare_prediction_id
      ? fetchTracks(sessionId, spec.compare_prediction_id, spec.head, spec.track_indices)
      : Promise.resolve(null);

    Promise.all([primaryP, compareP])
      .then(([r, rc]) => {
        if (!cancelled) {
          setData(r);
          setCompareData(rc);
          // Reset zoom range if the underlying sequence changes length
          setZoomStart(null);
          setZoomEnd(null);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [sessionId, spec.prediction_id, spec.compare_prediction_id, spec.head, spec.track_indices]);

  const locus = useMemo(() => parseLocus(data?.locus ?? spec.locus ?? null), [data?.locus, spec.locus]);
  const width = 860;
  const rowHeight = 46;
  const axisHeight = 28;
  const sidePad = 140;

  const N = useMemo(() => {
    if (data && data.tracks.length > 0) {
      return data.tracks[0].values.length;
    }
    return 0;
  }, [data]);

  const activeStart = zoomStart !== null ? zoomStart : 0;
  const activeEnd = zoomEnd !== null ? zoomEnd : (N > 0 ? N - 1 : 0);

  const zoomedLocus = useMemo(() => {
    if (!locus || N === 0) return locus;
    const span = locus.end - locus.start;
    return {
      chrom: locus.chrom,
      start: Math.round(locus.start + (activeStart / N) * span),
      end: Math.round(locus.start + ((activeEnd + 1) / N) * span),
    };
  }, [locus, activeStart, activeEnd, N]);

  // Symmetrical Zoom In
  const zoomIn = () => {
    if (N <= 3) return;
    const len = activeEnd - activeStart + 1;
    if (len <= 3) return;
    const shrink = Math.max(1, Math.round(len * 0.15)); // shrink 15% on each side
    const nextStart = Math.min(activeEnd - 2, activeStart + shrink);
    const nextEnd = Math.max(activeStart + 2, activeEnd - shrink);
    setZoomStart(nextStart);
    setZoomEnd(nextEnd);
  };

  // Symmetrical Zoom Out
  const zoomOut = () => {
    if (N === 0) return;
    const len = activeEnd - activeStart + 1;
    const expand = Math.max(1, Math.round(len * 0.25)); // expand 25% on each side
    const nextStart = Math.max(0, activeStart - expand);
    const nextEnd = Math.min(N - 1, activeEnd + expand);
    if (nextStart === 0 && nextEnd === N - 1) {
      setZoomStart(null);
      setZoomEnd(null);
    } else {
      setZoomStart(nextStart);
      setZoomEnd(nextEnd);
    }
  };

  const resetZoom = () => {
    setZoomStart(null);
    setZoomEnd(null);
  };

  // Synchronize inputs with actual active zoom indices
  useEffect(() => {
    if (N > 0) {
      setInputStart(String(activeStart));
      setInputEnd(String(activeEnd));
    }
  }, [activeStart, activeEnd, N]);

  const handleRangeSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const start = parseInt(inputStart, 10);
    const end = parseInt(inputEnd, 10);
    if (!isNaN(start) && !isNaN(end) && start >= 0 && end < N && end - start >= 2) {
      setZoomStart(start);
      setZoomEnd(end);
    } else {
      setInputStart(String(activeStart));
      setInputEnd(String(activeEnd));
    }
  };

  // Handle hotkeys (CMD/CTRL + "=" for zoom in, CMD/CTRL + "-" for zoom out)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const isMac = navigator.platform.toUpperCase().indexOf("MAC") >= 0;
      const hasModifier = isMac ? e.metaKey : e.ctrlKey;
      if (hasModifier) {
        if (e.key === "=" || e.key === "+") {
          e.preventDefault();
          zoomIn();
        } else if (e.key === "-") {
          e.preventDefault();
          zoomOut();
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activeStart, activeEnd, N]);

  const handleZoomFromRow = (start: number, end: number) => {
    setZoomStart(start);
    setZoomEnd(end);
  };

  const isZoomed = zoomStart !== null || zoomEnd !== null;

  return (
    <div style={styles.root}>
      <header style={styles.header}>
        <div style={styles.headerLeft}>
          <div style={styles.headerTitle}>
            <span style={styles.headBadge(colorFor(spec.head ?? "")[0])}>{spec.head ?? "—"}</span>
            <span style={styles.locusText}>{data?.locus ?? spec.locus ?? "—"}</span>
          </div>
          <div style={styles.meta}>
            {data
              ? `${data.tracks.length} tracks · ${data.downsampled_to}/${data.positions} pts · ${data.resolution ?? "—"}`
              : loading
                ? "loading…"
                : error
                  ? "error"
                  : ""}
          </div>
        </div>

        {N > 0 && (
          <div style={styles.zoomPanel}>
            <form onSubmit={handleRangeSubmit} style={styles.rangeForm}>
              <span style={styles.formLabel}>Bins:</span>
              <input
                type="number"
                value={inputStart}
                onChange={(e) => setInputStart(e.target.value)}
                style={styles.rangeInput}
                min={0}
                max={N - 1}
              />
              <span style={styles.formLabel}>to</span>
              <input
                type="number"
                value={inputEnd}
                onChange={(e) => setInputEnd(e.target.value)}
                style={styles.rangeInput}
                min={0}
                max={N - 1}
              />
              <button type="submit" style={styles.goButton}>Go</button>
            </form>

            <div style={styles.buttonGroup}>
              <button
                onClick={zoomOut}
                title="Zoom Out (Ctrl/Cmd + -)"
                style={styles.zoomButton}
              >
                Zoom −
              </button>
              <button
                onClick={zoomIn}
                title="Zoom In (Ctrl/Cmd + =)"
                style={styles.zoomButton}
              >
                Zoom +
              </button>
              <button
                onClick={resetZoom}
                disabled={!isZoomed}
                style={{
                  ...styles.zoomButton,
                  ...(isZoomed ? styles.resetActive : styles.resetDisabled),
                }}
              >
                Reset
              </button>
            </div>
          </div>
        )}
      </header>

      <div style={styles.scroll}>
        {error && <div style={styles.errorBox}>Could not load tracks: {error}</div>}
        {!error && data && data.tracks.length === 0 && (
          <div style={styles.emptyBox}>No track indices requested for this panel.</div>
        )}
        {!error &&
          data &&
          data.tracks.map((row, i) => {
            const compRow = compareData?.tracks.find((r) => r.track_index === row.track_index);
            return (
              <TrackRowView
                key={row.track_index}
                row={row}
                compareRow={compRow}
                head={spec.head ?? ""}
                width={width}
                height={rowHeight}
                sidePad={sidePad}
                isLast={i === data.tracks.length - 1}
                activeStart={activeStart}
                activeEnd={activeEnd}
                onZoom={handleZoomFromRow}
              />
            );
          })}
        {data && locus && zoomedLocus && (
          <AxisRow
            width={width}
            height={axisHeight}
            sidePad={sidePad}
            locus={zoomedLocus}
          />
        )}
      </div>
    </div>
  );
}

function TrackLabelView({ row, sidePad }: { row: TrackRow; sidePad: number }) {
  const [hovered, setHovered] = useState(false);
  const label = trackLabel(row);

  return (
    <div
      style={{
        ...styles.rowLabel,
        width: sidePad - 8,
        position: "relative",
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div
        title={label}
        style={{
          ...styles.labelMain,
          ...(hovered ? {
            position: "absolute",
            zIndex: 100,
            background: "#ffffff",
            padding: "4px 8px",
            border: "1px solid #d0d7de",
            borderRadius: "6px",
            boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
            whiteSpace: "normal",
            wordBreak: "break-word",
            width: "max-content",
            maxWidth: "320px",
            left: 0,
            top: "50%",
            transform: "translateY(-50%)",
            color: "#1f2328",
            fontSize: "12px",
            fontWeight: 500,
          } : {})
        }}
      >
        {label}
      </div>
      {/* Invisible placeholder to reserve height when absolute positioned */}
      <div style={{ ...styles.labelMain, visibility: "hidden" }}>{label}</div>
      <div style={styles.labelSub}>idx {row.track_index} · max {row.max.toFixed(2)}</div>
    </div>
  );
}

interface RowProps {
  row: TrackRow;
  compareRow?: TrackRow;
  head: string;
  width: number;
  height: number;
  sidePad: number;
  isLast: boolean;
  activeStart: number;
  activeEnd: number;
  onZoom: (start: number, end: number) => void;
}

function TrackRowView({ row, compareRow, head, width, height, sidePad, isLast, activeStart, activeEnd, onZoom }: RowProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [hover, setHover] = useState<{ x: number; v: number; cv?: number; frac: number } | null>(null);

  // Click & drag selection states
  const [dragStart, setDragStart] = useState<number | null>(null);
  const [dragCurrent, setDragCurrent] = useState<number | null>(null);

  const [stroke, fill] = colorFor(head);

  // Slice the data vectors to the visible bin indices
  const visibleValues = useMemo(() => {
    return row.values.slice(activeStart, activeEnd + 1);
  }, [row.values, activeStart, activeEnd]);

  const visibleCompareValues = useMemo(() => {
    if (compareRow && compareRow.values && compareRow.values.length > 0) {
      return compareRow.values.slice(activeStart, activeEnd + 1);
    }
    return undefined;
  }, [compareRow, activeStart, activeEnd]);

  const visibleCount = visibleValues.length;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const plotW = width - sidePad - 16;
    canvas.width = plotW * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${plotW}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, plotW, height);

    const values = visibleValues;
    if (values.length === 0) return;

    // Local autoscale over the active visible range
    const maxV = Math.max(
      Math.max(...values),
      visibleCompareValues ? Math.max(...visibleCompareValues) : 0,
      1e-9
    );

    const n = values.length;
    const padTop = 4;
    const padBot = 4;
    const plotH = height - padTop - padBot;

    const baselineY = height - padBot;
    ctx.fillStyle = fill;
    ctx.beginPath();
    ctx.moveTo(0, baselineY);
    for (let i = 0; i < n; i++) {
      const x = n > 1 ? (i / (n - 1)) * plotW : 0;
      const y = baselineY - (values[i] / maxV) * plotH;
      ctx.lineTo(x, y);
    }
    if (n > 1) {
      ctx.lineTo(plotW, baselineY);
    }
    ctx.closePath();
    ctx.fill();

    ctx.strokeStyle = stroke;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
      const x = n > 1 ? (i / (n - 1)) * plotW : 0;
      const y = baselineY - (values[i] / maxV) * plotH;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // Draw compareRow if present (as a dashed bright red line)
    if (visibleCompareValues && visibleCompareValues.length > 0) {
      const cValues = visibleCompareValues;
      const cN = cValues.length;
      ctx.strokeStyle = "#ff3b30";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      for (let i = 0; i < cN; i++) {
        const x = cN > 1 ? (i / (cN - 1)) * plotW : 0;
        const y = baselineY - (cValues[i] / maxV) * plotH;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.setLineDash([]); // Reset
    }

    // Subtle baseline.
    ctx.strokeStyle = "rgba(0,0,0,0.08)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, baselineY + 0.5);
    ctx.lineTo(plotW, baselineY + 0.5);
    ctx.stroke();
  }, [visibleValues, visibleCompareValues, stroke, fill, width, height, sidePad]);

  const plotW = width - sidePad - 16;

  const onMouseDown = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.button !== 0) return; // Left click only
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const frac = Math.max(0, Math.min(1, x / plotW));
    const localIdx = Math.round(frac * (visibleCount - 1));
    const globalIdx = activeStart + localIdx;
    setDragStart(globalIdx);
    setDragCurrent(globalIdx);
  };

  const onMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left;
    if (x < 0 || x > plotW) {
      setHover(null);
      return;
    }
    const frac = Math.max(0, Math.min(1, x / plotW));
    const localIdx = Math.round(frac * (visibleCount - 1));
    const globalIdx = activeStart + localIdx;

    if (dragStart !== null) {
      setDragCurrent(globalIdx);
    }

    setHover({
      x,
      v: row.values[globalIdx],
      cv: compareRow && compareRow.values ? compareRow.values[globalIdx] : undefined,
      frac: globalIdx / (row.values.length - 1),
    });
  };

  const onMouseUp = () => {
    if (dragStart !== null && dragCurrent !== null) {
      const start = Math.min(dragStart, dragCurrent);
      const end = Math.max(dragStart, dragCurrent);
      if (end - start >= 2) {
        onZoom(start, end);
      }
    }
    setDragStart(null);
    setDragCurrent(null);
  };

  const onMouseLeave = () => {
    setHover(null);
    setDragStart(null);
    setDragCurrent(null);
  };

  // Convert global bin index back to horizontal pixel coordinate on screen
  const getXForGlobalIdx = (globalIdx: number) => {
    if (visibleCount <= 1) return 0;
    const localIdx = globalIdx - activeStart;
    const frac = localIdx / (visibleCount - 1);
    return frac * plotW;
  };

  const dragLeft = dragStart !== null && dragCurrent !== null ? Math.min(getXForGlobalIdx(dragStart), getXForGlobalIdx(dragCurrent)) : 0;
  const dragRight = dragStart !== null && dragCurrent !== null ? Math.max(getXForGlobalIdx(dragStart), getXForGlobalIdx(dragCurrent)) : 0;
  const dragWidth = dragRight - dragLeft;

  // Percentage of position inside the current zoom window
  const hoverLocalPct = hover && visibleCount > 1
    ? (Math.round((hover.frac * (row.values.length - 1) - activeStart)) / (visibleCount - 1)) * 100
    : 0;

  return (
    <div
      style={{
        ...styles.row,
        borderBottom: isLast ? "none" : "1px solid #eef0f4",
      }}
    >
      <TrackLabelView row={row} sidePad={sidePad} />
      <div
        style={{ position: "relative", width: plotW, height, cursor: "crosshair", userSelect: "none" }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseLeave}
      >
        <canvas ref={canvasRef} />
        {dragStart !== null && dragCurrent !== null && dragWidth > 0 && (
          <div
            style={{
              position: "absolute",
              top: 0,
              bottom: 0,
              left: dragLeft,
              width: dragWidth,
              background: "rgba(31, 111, 235, 0.15)",
              borderLeft: "1px solid #1f6feb",
              borderRight: "1px solid #1f6feb",
              pointerEvents: "none",
              zIndex: 5,
            }}
          />
        )}
        {hover && (
          <>
            <div style={{ ...styles.hoverLine, left: hover.x }} />
            <div
              style={{
                ...styles.hoverBox,
                left: Math.min(hover.x + 8, plotW - 160),
                zIndex: 10,
              }}
            >
              <div style={{ fontWeight: 600 }}>Bin {activeStart + Math.round((hover.frac * (row.values.length - 1) - activeStart))}</div>
              <div>ref: {hover.v.toFixed(3)}</div>
              {hover.cv !== undefined && <div style={{ color: "#ff8b80" }}>edit: {hover.cv.toFixed(3)}</div>}
              <div style={{ borderTop: "1px solid rgba(255,255,255,0.2)", marginTop: 3, paddingTop: 3 }}>
                zoom: {hoverLocalPct.toFixed(1)}%
              </div>
              <div style={styles.hoverFrac}>full: {(hover.frac * 100).toFixed(1)}%</div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function AxisRow({
  width,
  height,
  sidePad,
  locus,
}: {
  width: number;
  height: number;
  sidePad: number;
  locus: LocusRange;
}) {
  const plotW = width - sidePad - 16;
  const span = locus.end - locus.start;
  const ticks = 5;
  return (
    <div style={{ ...styles.axis, height }}>
      <div style={{ width: sidePad - 8 }} />
      <div style={{ position: "relative", width: plotW, height }}>
        {Array.from({ length: ticks }).map((_, i) => {
          const frac = i / (ticks - 1);
          const bp = locus.start + frac * span;
          return (
            <div
              key={i}
              style={{
                ...styles.tick,
                left: `${frac * 100}%`,
                transform: i === 0 ? "translateX(0)" : i === ticks - 1 ? "translateX(-100%)" : "translateX(-50%)",
              }}
            >
              <div style={styles.tickMark} />
              <div style={styles.tickLabel}>{fmtBp(Math.round(bp))}</div>
            </div>
          );
        })}
        <div style={styles.axisLine} />
        <div style={styles.chromLabel}>{locus.chrom}</div>
      </div>
    </div>
  );
}

const styles: {
  root: React.CSSProperties;
  header: React.CSSProperties;
  headerLeft: React.CSSProperties;
  headerTitle: React.CSSProperties;
  headBadge: (c: string) => React.CSSProperties;
  locusText: React.CSSProperties;
  meta: React.CSSProperties;
  zoomPanel: React.CSSProperties;
  rangeForm: React.CSSProperties;
  formLabel: React.CSSProperties;
  rangeInput: React.CSSProperties;
  goButton: React.CSSProperties;
  buttonGroup: React.CSSProperties;
  zoomButton: React.CSSProperties;
  resetActive: React.CSSProperties;
  resetDisabled: React.CSSProperties;
  scroll: React.CSSProperties;
  row: React.CSSProperties;
  rowLabel: React.CSSProperties;
  labelMain: React.CSSProperties;
  labelSub: React.CSSProperties;
  hoverLine: React.CSSProperties;
  hoverBox: React.CSSProperties;
  hoverFrac: React.CSSProperties;
  axis: React.CSSProperties;
  tick: React.CSSProperties;
  tickMark: React.CSSProperties;
  tickLabel: React.CSSProperties;
  axisLine: React.CSSProperties;
  chromLabel: React.CSSProperties;
  errorBox: React.CSSProperties;
  emptyBox: React.CSSProperties;
} = {
  root: {
    display: "flex",
    flexDirection: "column",
    height: "100%",
    background: "linear-gradient(180deg, #fafbfd 0%, #f3f5f9 100%)",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "8px 14px",
    borderBottom: "1px solid #e5e8ee",
    background: "#fff",
    flexWrap: "wrap",
    gap: 8,
  },
  headerLeft: {
    display: "flex",
    flexDirection: "column",
    gap: 2,
  },
  headerTitle: { display: "flex", alignItems: "center", gap: 10 },
  headBadge: (c: string) => ({
    padding: "2px 8px",
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: 0.3,
    color: "#fff",
    background: c,
    borderRadius: 10,
    textTransform: "uppercase" as const,
  }),
  locusText: { fontSize: 13, fontFamily: "monospace", color: "#333" },
  meta: { fontSize: 11, color: "#888", fontFamily: "monospace" },
  zoomPanel: {
    display: "flex",
    alignItems: "center",
    gap: 12,
  },
  rangeForm: {
    display: "flex",
    alignItems: "center",
    gap: 4,
    fontSize: 11,
    color: "#444",
  },
  formLabel: {
    fontWeight: 500,
  },
  rangeInput: {
    width: 54,
    fontSize: 11,
    padding: "2px 4px",
    border: "1px solid #ccc",
    borderRadius: 3,
    textAlign: "center",
    fontFamily: "monospace",
  },
  goButton: {
    fontSize: 11,
    padding: "2px 8px",
    background: "#24292f",
    color: "#fff",
    border: "1px solid #24292f",
    borderRadius: 3,
    cursor: "pointer",
    fontWeight: 600,
  },
  buttonGroup: {
    display: "flex",
    gap: 4,
  },
  zoomButton: {
    fontSize: 11,
    padding: "3px 8px",
    background: "#f6f8fa",
    border: "1px solid #d0d7de",
    borderRadius: 4,
    cursor: "pointer",
    color: "#24292f",
    fontWeight: 500,
  },
  resetActive: {
    background: "#fef2f2",
    borderColor: "#fecaca",
    color: "#991b1b",
    fontWeight: 600,
  },
  resetDisabled: {
    opacity: 0.5,
    cursor: "not-allowed",
  },
  scroll: { flex: 1, overflow: "auto", padding: "8px 12px" },
  row: {
    display: "flex",
    alignItems: "stretch",
    padding: "6px 0",
  },
  rowLabel: {
    flexShrink: 0,
    paddingRight: 10,
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
  },
  labelMain: {
    fontSize: 12,
    fontWeight: 500,
    color: "#222",
    whiteSpace: "nowrap" as const,
    overflow: "hidden" as const,
    textOverflow: "ellipsis" as const,
  },
  labelSub: { fontSize: 10, color: "#888", fontFamily: "monospace", marginTop: 2 },
  hoverLine: {
    position: "absolute",
    top: 0,
    bottom: 0,
    width: 1,
    background: "rgba(0,0,0,0.25)",
    pointerEvents: "none",
  },
  hoverBox: {
    position: "absolute",
    top: 0,
    padding: "3px 6px",
    background: "rgba(20,20,30,0.9)",
    color: "#fff",
    fontSize: 10,
    fontFamily: "monospace",
    borderRadius: 3,
    pointerEvents: "none",
    whiteSpace: "nowrap" as const,
  },
  hoverFrac: { opacity: 0.7, marginTop: 1 },
  axis: {
    display: "flex",
    alignItems: "stretch",
    padding: "2px 0 6px 0",
    borderTop: "1px solid #e5e8ee",
  },
  tick: {
    position: "absolute",
    top: 0,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
  },
  tickMark: { width: 1, height: 5, background: "#aaa" },
  tickLabel: { fontSize: 10, fontFamily: "monospace", color: "#666", marginTop: 2 },
  axisLine: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    height: 1,
    background: "#ccc",
  },
  chromLabel: {
    position: "absolute",
    bottom: 2,
    left: 0,
    fontSize: 10,
    fontFamily: "monospace",
    color: "#888",
  },
  errorBox: {
    margin: 12,
    padding: 10,
    background: "#fef2f2",
    border: "1px solid #fecaca",
    borderRadius: 4,
    color: "#991b1b",
    fontSize: 12,
  },
  emptyBox: {
    margin: 12,
    padding: 10,
    background: "#fff",
    border: "1px dashed #ddd",
    borderRadius: 4,
    color: "#888",
    fontSize: 12,
    textAlign: "center" as const,
  },
};
