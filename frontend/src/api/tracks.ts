const BACKEND = import.meta.env.VITE_AGENT_BACKEND_URL ?? "http://localhost:8000";

export interface TrackMetadata {
  track_index: number;
  head?: string;
  assay_title?: string;
  target_label?: string;
  biosample_name?: string;
  biosample_type?: string;
  gtex_tissue?: string;
  gtex_tissue_group?: string;
  name?: string;
}

export interface TrackRow {
  track_index: number;
  values: number[];
  max: number;
  mean: number;
  metadata: TrackMetadata | null;
}

export interface TracksResponse {
  prediction_id: string;
  head: string;
  locus: string | null;
  resolution: string | null;
  positions: number;
  downsampled_to: number;
  tracks: TrackRow[];
}

export async function fetchTracks(
  sessionId: string,
  predictionId: string,
  head: string,
  indices: number[] | null,
  maxPoints = 2000,
): Promise<TracksResponse> {
  const url = new URL(
    `${BACKEND}/sessions/${sessionId}/predictions/${predictionId}/tracks`,
  );
  url.searchParams.set("head", head);
  url.searchParams.set("max_points", String(maxPoints));
  if (indices && indices.length > 0) {
    url.searchParams.set("indices", indices.join(","));
  }
  const r = await fetch(url.toString());
  if (!r.ok) throw new Error(`Tracks fetch failed: ${r.status} ${await r.text()}`);
  return r.json();
}

export function trackLabel(row: TrackRow): string {
  const m = row.metadata;
  if (!m) return `track ${row.track_index}`;
  const target = m.target_label || m.assay_title;
  const sample = m.biosample_name || m.gtex_tissue || m.gtex_tissue_group;
  if (target && sample) return `${target} · ${sample}`;
  return target || sample || m.name || `track ${row.track_index}`;
}
