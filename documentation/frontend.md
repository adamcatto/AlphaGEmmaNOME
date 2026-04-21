# Frontend Developer Guide

Vite + React 18 + TypeScript. Single-page app. CSS grid splits the viewport into a fixed 1/3 chat left, 2/3 visualization right — well, actually, the current scaffold just renders `<ChatPane /><VizPane />` as siblings and relies on each component's `height: 100vh` + flex/grid internally (see [App.tsx](../frontend/src/App.tsx) and [main.tsx](../frontend/src/main.tsx)).

## File map

```
frontend/
├── index.html                  # one root div, imports main.tsx
├── vite.config.ts              # React plugin, :5173
├── tsconfig.json               # strict TS, noUnused*, bundler moduleResolution
├── package.json                # react 18, zustand, igv, plotly, react-plotly
└── src/
    ├── main.tsx                # ReactDOM bootstrap, StrictMode
    ├── App.tsx                 # ChatPane + VizPane
    ├── state/
    │   └── store.ts            # zustand: session, messages, vizSpecs, toggles
    ├── api/
    │   ├── chat.ts             # POST /chat SSE client + POST /upload
    │   └── tracks.ts           # GET /sessions/.../tracks + trackLabel helper
    ├── types/
    │   └── viz_spec.ts         # shared TS types (VizSpec, ChatMessage, ToolCall, ProgressEntry)
    └── components/
        ├── ChatPane.tsx        # messages + input + SSE handler
        ├── VizPane.tsx         # tab bar + panel switcher
        ├── TrackViewer.tsx     # canvas-based track renderer (default)
        ├── IgvTrackBrowser.tsx # IGV.js fallback (toggle)
        ├── ContactMapPlot.tsx  # Plotly heatmap (placeholder data)
        └── SequenceUpload.tsx  # file input wiring to POST /upload
```

## State (zustand)

[state/store.ts](../frontend/src/state/store.ts) exposes:

```ts
interface AppState {
  sessionId: string | null;
  setSessionId: (id: string | null) => void;

  messages: ChatMessage[];
  pushMessage: (m: ChatMessage) => void;
  updateLast: (updater: (m: ChatMessage) => ChatMessage) => void;

  vizSpecs: VizSpec[];
  activeVizIndex: number;
  pushViz: (spec: VizSpec) => void;       // appends and focuses the new tab
  setActiveViz: (i: number) => void;

  useIgvFallback: boolean;                // toggle in VizPane header
  setUseIgvFallback: (v: boolean) => void;
}
```

All state is in-memory — refreshing the page clears the session ID, dropping the session on the server's side (a new session is created on next message).

## Chat streaming

[components/ChatPane.tsx](../frontend/src/components/ChatPane.tsx) drives the main loop. On send:

1. Push a `{role: "user"}` message.
2. Push an empty `{role: "assistant", toolCalls: [], streaming: true}` placeholder.
3. Call `sendChat(message, sessionId, handlers)` from [api/chat.ts](../frontend/src/api/chat.ts).
4. For each SSE event, call `updateLast(fn)` to mutate the latest assistant message.

Event → mutation map:

| Event | UI effect |
|---|---|
| `token` | append to `content` |
| `thought` | append to `thoughts` (collapsible block) |
| `progress` | append to `progress` (colored dot list) |
| `tool_call_start` | append new `ToolCall{status:"pending"}` to `toolCalls` |
| `tool_call_result` | update last `ToolCall` to `done` with `observation` |
| `viz_spec` | `pushViz(spec)` — this focuses the new tab in VizPane |
| `error` | replace `content` with `Error: …`, clear `streaming` |
| `final` | update `sessionId`, fill `content` if empty, clear `streaming` |

## SSE wire parsing

Because `POST /chat` uses SSE but the built-in `EventSource` API only does GET, [api/chat.ts](../frontend/src/api/chat.ts) rolls its own:

```ts
const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = "";
const FRAME_SEP = /\r?\n\r?\n/;
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  while (true) {
    const match = FRAME_SEP.exec(buffer);
    if (!match) break;
    const rawFrame = buffer.slice(0, match.index);
    buffer = buffer.slice(match.index + match[0].length);
    // parse "event: X\ndata: Y" from rawFrame
    ...
  }
}
```

Gotchas:

- `sse-starlette` defaults to `\r\n\r\n` separators; the regex handles both.
- `decode(value, {stream: true})` preserves partial multi-byte characters across chunks.
- Any frame with no `event:` or `data:` line is silently skipped (logged to console). This lets the backend send comments/heartbeats without crashing the client.

## The track renderer

[TrackViewer.tsx](../frontend/src/components/TrackViewer.tsx) is the default panel for `igv_tracks`. It fetches per-position values from the agent-backend and draws each track as a filled area plus stroked outline on a `<canvas>`.

### Data flow

1. `spec.head`, `spec.prediction_id`, `spec.track_indices` come from the SSE `viz_spec`.
2. `useEffect` calls `fetchTracks(sessionId, prediction_id, head, indices)` from [api/tracks.ts](../frontend/src/api/tracks.ts).
3. Agent-backend returns a downsampled `TracksResponse` with one `TrackRow` per index, already block-meaned down to 2000 points per track (tunable via `max_points` query param).

### Rendering per row

Each `TrackRowView` does:

- Size canvas to `(width - sidePad - 16) × rowHeight` at `devicePixelRatio`.
- Build a filled polygon: baseline → every `(x, y)` where `x = i/(n-1) * plotW` and `y = baselineY - values[i]/maxV * plotH` → back to baseline.
- Stroke a 1.2px line on top of the fill.
- Draw a subtle baseline.
- Mouse-move → show a vertical hover line + a small tooltip with the value and window fraction.

### Color scheme

[HEAD_COLORS](../frontend/src/components/TrackViewer.tsx) maps each AlphaGenome head to a `[stroke, fill]` pair. Unknown heads fall back to blue. This is what drives the pill color in the header and the track-row accent.

### Track labels

`trackLabel(row)` from [api/tracks.ts](../frontend/src/api/tracks.ts):

1. `target_label || assay_title` — the biological target/assay.
2. `biosample_name || gtex_tissue || gtex_tissue_group` — the sample context.
3. If both present: `"CTCF · GM12878"`; otherwise whichever is set; fallback `"track {index}"`.

When metadata is missing (no TSV, or metadata fetch failed in the backend), every row shows `"track {index}"` — this is one of the ways you can tell the placeholder `track_metadata.tsv` is still in use.

### Axis row

The last row is `<AxisRow>` — 5 evenly-spaced tick marks, labels formatted by `fmtBp` (`1Mb`, `65kb`, `123bp`).

## The IGV.js fallback

[IgvTrackBrowser.tsx](../frontend/src/components/IgvTrackBrowser.tsx) wraps the `igv` npm package. Toggle via the "use IGV" checkbox in the VizPane header. Shows hg38 reference + whatever tracks you wire in — it does NOT render AlphaGenome prediction arrays currently; it's a genome-context view only, useful for cross-referencing.

Enable with `setUseIgvFallback(true)` from the checkbox. Effect re-runs on `spec.prediction_id`/`spec.locus` change and calls `igv.removeBrowser()` on cleanup.

## ContactMapPlot

[ContactMapPlot.tsx](../frontend/src/components/ContactMapPlot.tsx) renders a 64×64 heatmap via `react-plotly.js`. **The data is `Math.random()`** — the component is a placeholder until the backend wires up contact-map retrieval (analogous to the `tracks` endpoint but 2D). TODO in the component.

## Backend URL

Both API modules read:

```ts
const BACKEND = import.meta.env.VITE_AGENT_BACKEND_URL ?? "http://localhost:8000";
```

Override by exporting before `npm run dev`:

```bash
VITE_AGENT_BACKEND_URL=https://agent.staging.example.com npm run dev
```

In docker-compose, the frontend service gets `VITE_AGENT_BACKEND_URL=http://localhost:8000` because it's the browser — not the container — making the request.

## Dev commands

```bash
npm run dev       # vite, :5173, HMR
npm run build     # tsc -b && vite build — emits to dist/
npm run preview   # vite preview — serve dist/ as a smoke test
```

## Adding a new viz panel type

1. Add the new type to [types/viz_spec.ts](../frontend/src/types/viz_spec.ts) `VizPanelType`.
2. Create a component under `components/` that takes `{ spec: VizSpec }`.
3. Wire it in [VizPane.tsx](../frontend/src/components/VizPane.tsx):

```tsx
{active && active.type === "my_panel" && <MyPanel spec={active} />}
```

4. On the backend, have a tool or macro return `{"viz_spec": {"type": "my_panel", ...}}` and it'll round-trip through the SSE and appear as a new tab.

Unknown panel types already fall through to the JSON fallback viewer in VizPane, so you can ship new types server-first without breaking the frontend.

## Layout caveats

- `<App>` puts `ChatPane` and `VizPane` as siblings without wrapping them in a grid. Each sets `height: 100vh` internally. If you add more panels side-by-side, convert `App.tsx` to a proper CSS grid (`grid-template-columns: 1fr 2fr` is the documented intent).
- Both panes have `overflow: hidden` at the top level; inner scroll containers handle overflow. Don't move overflow up without reconsidering the child layout.

## Known gaps

- No error boundary around panels — a thrown render error in a panel blanks the right half.
- `ContactMapPlot` shows random data.
- `splice_arcs` and `variant_delta` viz types aren't rendered — they fall through to the JSON fallback.
- Session persistence across page reload is not implemented.
