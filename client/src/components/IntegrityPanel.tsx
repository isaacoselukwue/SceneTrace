import { useEffect, useState } from "react";
import { api, ApiError, type AnalysisOut, type ExportOut, type VerifyOut } from "../api/client";
import { shortHash } from "../lib/format";
import { useJobEvents } from "../lib/useJobEvents";

export function IntegrityPanel({ analysis, canExport }: { analysis: AnalysisOut; canExport: boolean }) {
  const [exports, setExports] = useState<ExportOut[]>([]);
  const [job, setJob] = useState<string | null>(null);
  const [result, setResult] = useState<VerifyOut | null>(null);
  const [digest, setDigest] = useState("");
  const [error, setError] = useState<string | null>(null);
  const refresh = () => api.exports(analysis.analysis_id).then(setExports).catch(() => setExports([]));
  useJobEvents(job, () => { setJob(null); refresh(); });
  useEffect(() => { refresh(); }, [analysis.analysis_id]); // eslint-disable-line react-hooks/exhaustive-deps
  const latest = exports.find((e) => e.status === "sealed");

  async function create() {
    setError(null);
    try {
      setJob((await api.createExport(analysis.analysis_id)).job_id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Export failed.");
    }
  }
  async function verify(expected?: string) {
    if (!latest) return;
    setError(null);
    try {
      setResult(await api.verify(latest.export_id, expected));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Verification failed.");
    }
  }
  return (
    <section className="panel integrity" aria-labelledby="integrity-title">
      <h2 id="integrity-title">Evidence integrity</h2>
      {canExport && <button type="button" onClick={create} disabled={Boolean(job)}>{job ? "Sealing…" : "Create sealed export"}</button>}
      {latest ? (
        <div className="export">
          <p>
            Sealed bundle, {latest.file_count} files. Head <span className="mono" title={latest.head_digest ?? ""}>{shortHash(latest.head_digest, 16)}</span>
            {" "}<a href={latest.download_url ?? "#"} download>Download archive</a>
          </p>
          <div className="verify">
            <button type="button" className="secondary" onClick={() => verify()}>Verify against the server registry</button>
            <form onSubmit={(e) => { e.preventDefault(); verify(digest.trim()); }}>
              <label htmlFor="expected">Or verify with a head you kept separately</label>
              <input id="expected" className="mono" value={digest} onChange={(e) => setDigest(e.target.value)} placeholder="64 hex characters"
                pattern="[a-f0-9]{64}" />
              <button type="submit" className="secondary" disabled={!/^[a-f0-9]{64}$/.test(digest.trim())}>Verify</button>
            </form>
          </div>
        </div>
      ) : <p className="muted">No sealed export yet.</p>}
      {result && (
        <p className={`verify-result ${result.integrity}`} role="status">
          <strong>{result.integrity === "valid" ? "✓ Integrity valid" : "✕ Integrity failed"}</strong>,{" "}
          {result.anchored ? `anchored to the ${result.anchor_source === "supplied" ? "head you supplied" : "server registry"}` : "unanchored"}.
          {result.errors.length > 0 && <span className="mono"> {result.errors.slice(0, 3).join(", ")}</span>}
          <br /><span className="fineprint">{result.note}</span>
        </p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
