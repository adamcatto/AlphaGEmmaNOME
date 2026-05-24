import Plot from "react-plotly.js";
import type { VizSpec } from "../types/viz_spec";

interface Props {
  spec: VizSpec;
}

export default function ContactMapPlot({ spec }: Props) {
  const z = Array.from({ length: 64 }, () =>
    Array.from({ length: 64 }, () => Math.random()),
  );

  return (
    <div style={{ padding: 8, height: "100%" }}>
      <div style={{ fontSize: 11, color: "#666", marginBottom: 4 }}>
        contact map · prediction {spec.prediction_id} · placeholder data (backend wiring TODO)
      </div>
      <Plot
        data={[{ z, type: "heatmap", colorscale: "Viridis" }]}
        layout={{ title: `Contact map · ${spec.locus ?? ""}`, autosize: true }}
        style={{ width: "100%", height: "90%" }}
        useResizeHandler
      />
    </div>
  );
}
