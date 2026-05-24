import { useEffect, useRef } from "react";
import igv from "igv";
import type { VizSpec } from "../types/viz_spec";

interface Props {
  spec: VizSpec;
}

// Legacy IGV.js browser. Kept as a fallback behind the "use IGV" toggle in
// VizPane; the default renderer is TrackViewer. Loads hg38 and shows genome
// context only — does not display AlphaGenome prediction arrays.
export default function IgvTrackBrowser({ spec }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const browserRef = useRef<unknown>(null);

  useEffect(() => {
    let disposed = false;
    if (!containerRef.current) return;

    const options = {
      genome: "hg38",
      locus: spec.locus ?? "chr17:43044295-43125483",
      tracks: [],
    };

    (igv as { createBrowser: (el: HTMLElement, opts: unknown) => Promise<unknown> })
      .createBrowser(containerRef.current, options)
      .then((browser) => {
        if (disposed) return;
        browserRef.current = browser;
      })
      .catch((err) => console.error("IGV init failed", err));

    return () => {
      disposed = true;
      if (browserRef.current && containerRef.current) {
        (igv as { removeBrowser: (b: unknown) => void }).removeBrowser(browserRef.current);
        browserRef.current = null;
      }
    };
  }, [spec.prediction_id, spec.locus]);

  return (
    <div style={{ height: "100%", overflow: "auto" }}>
      <div style={{ padding: "4px 8px", fontSize: 11, color: "#666", borderBottom: "1px solid #eee" }}>
        locus: {spec.locus ?? "—"} · head: {spec.head ?? "—"} · prediction: {spec.prediction_id}
      </div>
      <div ref={containerRef} />
    </div>
  );
}
