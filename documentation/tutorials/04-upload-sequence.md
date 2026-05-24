# Tutorial 4 — Upload a DNA sequence

**Endpoint**: `POST /upload` ([app.py:84-101](../../services/agent_backend/app.py#L84-L101))
**Tool**: `upload_sequence` ([uploads.py](../../services/agent_backend/tools/uploads.py))
**Frontend**: [SequenceUpload.tsx](../../frontend/src/components/SequenceUpload.tsx)

## Goal

Send a user-supplied DNA sequence (FASTA or plain text) to the backend, bind it to the session under an `upload_id`, and let the agent reference it by id in a follow-up question.

## Walkthrough

### Step 1 — upload

The left pane has a file picker at the bottom (`<input type="file" accept=".fasta,.fa,.txt">`). Pick a file; the frontend posts it as `multipart/form-data` to `/upload`:

```bash
curl -X POST "localhost:8000/upload?session_id=<optional>" -F file=@my_sequence.fasta
# {
#   "upload_id": "a7c5f8d2-…",
#   "length": 131072,
#   "session_id": "…"
# }
```

What the backend does ([app.py:84-101](../../services/agent_backend/app.py#L84-L101)):

1. Reads bytes, decodes UTF-8 (errors ignored).
2. Rejects with `413` if `len(bytes) > settings.limits.max_upload_bytes` (default 2 MB — see [config/settings.yaml](../../config/settings.yaml)).
3. Strips FASTA headers (lines starting with `>`) and joins the rest.
4. Uppercases and validates that every character is in `ACGTN`. Non-canonical → `400`.
5. Creates (or joins) a session, generates a uuid, stashes `{sequence, source: filename}` under `session.uploads[upload_id]`.

### Step 2 — ask a question about it

In the chat pane:

> I just uploaded a sequence as `upload_id=a7c5f8d2-…`. What is it?

The agent should pick `upload_sequence(upload_id="a7c5f8d2-…")`. This macro does NOT run AlphaGenome — it just echoes metadata back:

```json
{
  "upload_id": "a7c5f8d2-…",
  "length": 131072,
  "source": "my_sequence.fasta",
  "sequence_preview": "ACGTTACG…"
}
```

The preview is the first 60 bases with a trailing ellipsis. Useful as a sanity check ("yes the backend has my sequence, yes it's the right length").

### Step 3 — (want to predict on it?) see the gap below

## Gap: predicting on an uploaded sequence

The `upload_sequence` macro only *confirms* the upload. To actually feed the sequence into AlphaGenome through the agent, you need `predict_tracks` enabled:

```yaml
# config/tools.yaml
predict_tracks:
  enabled: true
```

Then rephrase:

> Predict ATAC and chip_tf tracks on upload_id=a7c5f8d2-…

The agent should chain `upload_sequence` → `predict_tracks(sequence=<returned sequence>, heads=[...])`. In practice Qwen3:4b is unreliable at this two-step chain — that's exactly why the primitives are disabled by default and macros are preferred. For reliable upload-then-predict you can call `/predict` directly (see [06-direct-api-usage.md](06-direct-api-usage.md)) or add a macro like `analyze_uploaded_sequence(upload_id, heads)` that bundles the chain into one tool.

## Sequence format notes

- **Exactly 131,072 bp** is required. AlphaGenome's input is fixed; shorter sequences are rejected with 400 at `/predict` time ("sequence length != 131072"). Longer sequences are currently also rejected — there is no auto-trim.
- **ACGTN only.** Lowercase is uppercased at upload time. Anything else (IUPAC ambiguity codes like `R`, `Y`, `W`) fails validation.
- **N is allowed** but produces zero signal in the predictions around that region (model treats `N` as no-information).

## Session binding

The upload is tied to the returned `session_id`. A subsequent `/upload` call without a `session_id` query-param creates a new session (losing access to previous uploads). The frontend always passes the current `sessionId` from zustand store on repeat uploads.

If the backend restarts, the session (and therefore the upload) is gone — `SessionStore` is in-memory.

## Direct invocation

No agent, just the HTTP endpoint:

```python
import requests

with open("my_sequence.fasta", "rb") as f:
    r = requests.post(
        "http://localhost:8000/upload",
        files={"file": ("my_sequence.fasta", f)},
    )
print(r.json())   # {"upload_id": "...", "length": 131072, "session_id": "..."}
```

To retrieve the sequence back for debugging, there's no GET endpoint — it lives only in-process under `session.uploads`. Add one if you need it (trivial; read `session.uploads[upload_id]["sequence"]`).

## Troubleshooting

| Symptom | Cause |
|---|---|
| `413 Upload too large` | File exceeds `limits.max_upload_bytes`. Raise in [config/settings.yaml](../../config/settings.yaml) or split the upload. |
| `400 File does not contain a valid DNA sequence` | File has characters outside `ACGTN` after FASTA-header stripping. Common culprits: lowercase IUPAC codes, Windows line endings producing stray bytes, BOM markers. |
| `upload_sequence` returns `Upload {id} not found in this session` | The `session_id` sent with the chat request doesn't match the one returned by `/upload`. Make sure the frontend's `setSessionId` call ran. |
| Agent calls `analyze_gene_tf_binding` instead of `upload_sequence` | Router is biased toward gene/locus macros. Rephrase with the explicit `upload_id=…` in the question. |
