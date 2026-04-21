import { useRef, useState } from "react";
import { uploadSequence } from "../api/chat";
import { useStore } from "../state/store";

export default function SequenceUpload() {
  const { sessionId, setSessionId, pushMessage } = useStore();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);

  const handle = async (file: File) => {
    setBusy(true);
    try {
      const { upload_id, length, session_id } = await uploadSequence(file, sessionId);
      setSessionId(session_id);
      pushMessage({
        role: "user",
        content: `Uploaded ${file.name} (${length} bp) as upload_id=${upload_id}`,
      });
    } catch (err) {
      pushMessage({ role: "assistant", content: `Upload failed: ${String(err)}` });
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <div style={{ padding: "6px 10px", borderTop: "1px solid #eee", fontSize: 12 }}>
      <input
        ref={inputRef}
        type="file"
        accept=".fasta,.fa,.txt"
        disabled={busy}
        onChange={(e) => e.target.files?.[0] && handle(e.target.files[0])}
      />
    </div>
  );
}
