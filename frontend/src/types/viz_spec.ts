export type VizPanelType =
  | "igv_tracks"
  | "contact_map"
  | "splice_arcs"
  | "variant_delta";

export interface VizSpec {
  type: VizPanelType;
  prediction_id: string;
  compare_prediction_id?: string;
  locus: string | null;
  head: string | null;
  track_indices: number[] | null;
}

export type ChatEventType =
  | "token"
  | "thought"
  | "progress"
  | "tool_call_start"
  | "tool_call_result"
  | "viz_spec"
  | "error"
  | "final";

export interface ToolCall {
  tool: string;
  status: "pending" | "done" | "error";
  arguments?: unknown;
  observation?: string;
}

export interface ProgressEntry {
  stage: string;
  text: string;
  current?: number;
  total?: number;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  thoughts?: string;
  toolCalls?: ToolCall[];
  progress?: ProgressEntry[];
  streaming?: boolean;
}
