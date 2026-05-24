import { create } from "zustand";
import type { ChatMessage, VizSpec } from "../types/viz_spec";

interface AppState {
  sessionId: string | null;
  setSessionId: (id: string | null) => void;
  messages: ChatMessage[];
  pushMessage: (m: ChatMessage) => void;
  updateLast: (updater: (m: ChatMessage) => ChatMessage) => void;
  vizSpecs: VizSpec[];
  activeVizIndex: number;
  pushViz: (spec: VizSpec) => void;
  setActiveViz: (i: number) => void;
  useIgvFallback: boolean;
  setUseIgvFallback: (v: boolean) => void;
}

export const useStore = create<AppState>((set) => ({
  sessionId: null,
  setSessionId: (id) => set({ sessionId: id }),

  messages: [],
  pushMessage: (m) => set((s) => ({ messages: [...s.messages, m] })),
  updateLast: (updater) =>
    set((s) => {
      if (s.messages.length === 0) return s;
      const copy = [...s.messages];
      copy[copy.length - 1] = updater(copy[copy.length - 1]);
      return { messages: copy };
    }),

  vizSpecs: [],
  activeVizIndex: 0,
  pushViz: (spec) =>
    set((s) => ({ vizSpecs: [...s.vizSpecs, spec], activeVizIndex: s.vizSpecs.length })),
  setActiveViz: (i) => set({ activeVizIndex: i }),

  useIgvFallback: false,
  setUseIgvFallback: (v) => set({ useIgvFallback: v }),
}));
