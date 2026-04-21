# Tutorial 5 — Add a new tool

Walks through adding a JASPAR-motif-scan tool end-to-end: class, registration, config toggle, optional progress events. Same pattern applies to any new tool or macro.

## Anatomy of a tool

Every tool subclasses [`SessionAwareTool`](../../services/agent_backend/tools/_base.py), which itself extends `smolagents.Tool`. smolagents introspects four class attributes plus `forward()`:

| Attribute | Purpose |
|---|---|
| `name` | The identifier the LLM uses in a tool call. Must be unique and match the key in [tools.yaml](../../config/tools.yaml). |
| `description` | Free-text prompt the LLM reads to decide when to use this tool. Keep it to 1–3 sentences; mention *when* to use it, not just *what* it does. |
| `inputs` | JSON-schema-ish dict: `{"param": {"type": "...", "description": "...", "nullable": bool}}`. Optional params need `nullable: True`. |
| `output_type` | `"object"`, `"string"`, or `"number"`. Macros always return `"object"`. |
| `forward(self, **kwargs)` | Actual implementation. Return a dict; the agent sees a stringified version in its `observation`. |

`SessionAwareTool.__init__` receives `session_context`, the per-request `Session` dataclass (see [data-model.md](../data-model.md)). Use it to read uploads, cache predictions, or emit progress events.

## Worked example: `jaspar_motif_scan`

Goal: given a sequence and a TF name, return match positions and scores for that TF's JASPAR motif. Useful to sanity-check an AlphaGenome chip_tf prediction against the underlying motif.

### 1. Create the file

[services/agent_backend/tools/motifs.py](../../services/agent_backend/tools/motifs.py) (new):

```python
from __future__ import annotations
from typing import Any
import httpx

from ._base import SessionAwareTool


class JasparMotifScan(SessionAwareTool):
    name = "jaspar_motif_scan"
    description = (
        "Scan a DNA sequence for occurrences of a transcription factor's JASPAR motif. "
        "Use to sanity-check an AlphaGenome chip_tf prediction against the known "
        "binding motif, or to find candidate binding sites in an uploaded sequence."
    )
    inputs = {
        "sequence": {
            "type": "string",
            "description": "DNA sequence (ACGT only). Max 131072 bp.",
        },
        "tf": {
            "type": "string",
            "description": "TF name as it appears in JASPAR (e.g. 'CTCF', 'NEUROG1').",
        },
        "threshold": {
            "type": "number",
            "description": "Relative score cutoff in [0,1]. Default 0.85.",
            "nullable": True,
        },
    }
    output_type = "object"

    def forward(self, sequence: str, tf: str, threshold: float = 0.85) -> dict[str, Any]:
        if not sequence or any(c not in "ACGTN" for c in sequence.upper()):
            return {"error": "sequence must be ACGTN-only."}

        # 1. JASPAR matrix id for the TF (public API, no key required).
        r = httpx.get(
            f"https://jaspar.genereg.net/api/v1/matrix/?name={tf}&tax_group=vertebrates",
            timeout=10.0,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return {"error": f"No JASPAR matrix found for TF {tf!r}."}
        matrix_id = results[0]["matrix_id"]

        # 2. Fetch PWM and scan. (Left as an exercise — use `Bio.motifs` or a
        #    local PWM scanner to avoid shipping the full sequence to a third
        #    party.)
        hits = _scan_with_pwm(sequence, matrix_id, threshold)
        return {
            "tf": tf,
            "matrix_id": matrix_id,
            "threshold": threshold,
            "hit_count": len(hits),
            "hits": hits[:50],  # truncate — LLM context cost
        }


def _scan_with_pwm(sequence: str, matrix_id: str, threshold: float) -> list[dict]:
    ...  # implementation omitted
```

Notes on this skeleton:

- **Input validation at the boundary only.** We're running inside the trusted backend; the sequence might come from an LLM-parsed argument, so checking `ACGTN` is cheap insurance against hallucinated inputs. We don't re-validate things our own code produces.
- **Truncate results.** The agent stringifies the return dict into its observation, capped at 4000 chars by smolagents. 50 motif hits is plenty for the LLM to summarize; the full list can live in a side channel (session state, a follow-up endpoint) if the UI wants it.
- **No session side effects in this tool.** It's a pure transform. If you want the UI to render the hits, either (a) emit a `viz_spec` via `session_context.pending_viz_spec = {...}` or (b) emit a `progress` event — see below.

### 2. Register it

[services/agent_backend/tools/\_\_init\_\_.py](../../services/agent_backend/tools/__init__.py):

```python
from .motifs import JasparMotifScan

_ALL: dict[str, type[Tool]] = {
    # existing entries …
    "jaspar_motif_scan": JasparMotifScan,
}
```

`build_enabled_tools` iterates `_ALL` and instantiates every tool whose key is enabled in [tools.yaml](../../config/tools.yaml). Missing from `_ALL` ⇒ the tool is invisible to the agent regardless of config.

### 3. Toggle it on

[config/tools.yaml](../../config/tools.yaml):

```yaml
jaspar_motif_scan:
  enabled: true
```

The stub was already in `tools.yaml` under the "Planned stubs" section with `enabled: false`. Flipping it is a config change, no restart-on-failure risk.

### 4. Make the router aware

[services/agent_backend/router.py](../../services/agent_backend/router.py) classifies chat turns into a domain. JASPAR scanning is a `PREDICT`-adjacent capability. If your tool is a new *category* (not PREDICT/ANSWER), extend the router. For motif scanning, piggyback on `PREDICT` — no router change needed.

Separately, [services/agent_backend/prompts/system.md](../../services/agent_backend/prompts/system.md) has a tool-picking table. Add one row:

```
- jaspar_motif_scan     : user asks about a TF's binding motif on a specific sequence.
```

And add a few-shot example to [prompts/few_shot.json](../../services/agent_backend/prompts/few_shot.json):

```json
{
  "q": "Are there CTCF motif hits in the uploaded sequence abc123?",
  "thought": "Fetch the sequence via upload_sequence, then scan for CTCF motif.",
  "tool": "upload_sequence",
  "args": {"upload_id": "abc123"}
}
```

The sharper your few-shot examples, the more reliably a small LLM routes to your tool. Two to three examples is enough; more bloats the prompt and hurts the other tools.

### 5. Restart the agent-backend

```bash
# terminal 3
# Ctrl-C then
uv run uvicorn services.agent_backend.app:app --port 8000 --reload
```

`--reload` picks up Python changes on save; YAML changes (like flipping `enabled:`) also require a restart because settings are cached at module load.

### 6. Try it

```
> Are there CTCF motif hits near the start of upload abc123?
```

Expected trace in the chat pane:

```
● upload_sequence({"upload_id": "abc123"})       done
● jaspar_motif_scan({"sequence": "ACGT…", "tf": "CTCF"})   done
```

Followed by a `final_answer` summarizing the hit count.

## Emitting progress events

For tools that take >1 s, push a `progress` event so the UI shows something:

```python
def forward(self, ...):
    _emit(self.session_context, "progress",
          {"stage": "jaspar_fetch", "text": f"Fetching JASPAR PWM for {tf}…"})
    # fetch
    _emit(self.session_context, "progress",
          {"stage": "jaspar_scan", "text": "Scanning sequence…"})
    # scan
    _emit(self.session_context, "progress",
          {"stage": "jaspar_done", "text": f"Found {len(hits)} hits"})
    return {...}
```

`_emit` is the helper from [macros.py](../../services/agent_backend/tools/macros.py) (look at `AnalyzeGeneTfBinding` for the canonical pattern). It writes via `session_context.bus.publish(...)` if the bus is set, no-ops otherwise. Stage suffixes (`_done`, `_error`, else amber) are interpreted by the frontend's `ProgressEntry` color-coding.

## Emitting a viz spec

If your tool produces something the right-pane should render, stash a viz spec on the session:

```python
def forward(self, ...):
    ...
    if self.session_context is not None:
        self.session_context.pending_viz_spec = {
            "type": "motif_hits",
            "prediction_id": None,
            "locus": None,
            "head": None,
            "track_indices": None,
            # custom payload fields are fine — frontend falls back to JSON viewer
            # for unknown `type`.
            "tf": tf,
            "hits": hits,
        }
    return {...}
```

The `step_callback` in [agent.py](../../services/agent_backend/agent.py) drains `pending_viz_spec` after each step and emits a `viz_spec` SSE event. Add a matching panel type to [frontend/src/types/viz_spec.ts](../../frontend/src/types/viz_spec.ts) and a new component in [frontend/src/components/](../../frontend/src/components/) to render it — or ship without a custom panel and rely on the JSON fallback.

## Macro vs. primitive: which is this?

Rule of thumb:
- **Primitive** — single focused operation, no orchestration across services. `GeneToLocus`, `PredictTracks`. Disabled by default because chaining them reliably requires a stronger LLM.
- **Macro** — bundles a full user question into one tool call. `AnalyzeGeneTfBinding` internally does lookup → predict → rank → annotate. Enabled by default.

`JasparMotifScan` as written is a primitive — the agent must first fetch the sequence via `upload_sequence`. If most of your users will ask "does TF X bind my upload?", promote it to a macro that takes `upload_id` and `tf` directly and does both steps internally — you'll see much higher tool-picking reliability with Qwen3:4b.

## Checklist

When adding a tool, hit all of these:

- [ ] Class subclasses `SessionAwareTool` with `name`, `description`, `inputs`, `output_type`, `forward`.
- [ ] Imported and added to `_ALL` in [tools/\_\_init\_\_.py](../../services/agent_backend/tools/__init__.py).
- [ ] Entry in [config/tools.yaml](../../config/tools.yaml) with `enabled: true`.
- [ ] Row in the routing table of [prompts/system.md](../../services/agent_backend/prompts/system.md).
- [ ] 1–2 few-shot examples in [prompts/few_shot.json](../../services/agent_backend/prompts/few_shot.json).
- [ ] Truncate large return payloads (observations max 4000 chars).
- [ ] Emit `progress` events if the tool is slow (>1 s).
- [ ] If it renders something, stash a `viz_spec` and add a frontend panel (or rely on JSON fallback).
- [ ] Add a row to [documentation/agent-tools.md](../agent-tools.md) so developers can find it.
