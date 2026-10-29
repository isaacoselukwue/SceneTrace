import { useEffect, useState } from "react";
import { api, ApiError, type AnalysisOut, type ExportOut, type VerifyOut } from "../api/client";
import { useJobEvents } from "../lib/useJobEvents";
import { Icon } from "./Icons";

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
      <div className="panel-head">
        <span className="panel-icon"><Icon name="shield" /></span>
        <div>
          <h2 id="integrity-title">Evidence integrity</h2>
          <p className="muted">Seal frames, records and answers into a content-addressed bundle whose head is kept outside it.</p>
        </div>
      </div>
      {canExport && (
        <button type="button" className={latest ? "secondary" : ""} onClick={create} disabled={Boolean(job)}>
          {job ? "Sealing…" : "Create sealed export"}
        </button>
      )}
      {latest ? (
        <div className="export">
          <dl className="kv">
            <div><dt>Sealed bundle</dt><dd>{latest.file_count} files</dd></div>
            <div><dt>Head digest</dt><dd><code className="digest-box">{latest.head_digest}</code></dd></div>
          </dl>
          <div className="export-actions">
            <a className="button secondary" href={latest.download_url ?? "#"} download>Download archive</a>
          </div>
          <div className="verify">
            <button type="button" onClick={() => verify()}>Verify against the server registry</button>
            <form onSubmit={(e) => { e.preventDefault(); verify(digest.trim()); }}>
              <label htmlFor="expected">Or verify with a head you kept separately</label>
              <div className="input-row">
                <input id="expected" className="mono" value={digest} onChange={(e) => setDigest(e.target.value)} placeholder="64 hex characters"
                  pattern="[a-f0-9]{64}" autoComplete="off" spellCheck={false} />
                <button type="submit" className="secondary" disabled={!/^[a-f0-9]{64}$/.test(digest.trim())}>Verify</button>
              </div>
            </form>
          </div>
        </div>
      ) : <p className="muted empty">No sealed export yet.</p>}
      {result && (
        <div className={`verify-result ${result.integrity}`} role="status">
          <strong><Icon name={result.integrity === "valid" ? "check" : "x"} size={16} />
            {result.integrity === "valid" ? " Integrity valid" : " Integrity failed"}</strong>
          <span>
            {result.anchored ? `Anchored to the ${result.anchor_source === "supplied" ? "head you supplied" : "server registry"}.` : "Unanchored."}
            {result.errors.length > 0 && <span className="mono"> {result.errors.slice(0, 3).join(", ")}</span>}
          </span>
          <span className="fineprint">{result.note}</span>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
