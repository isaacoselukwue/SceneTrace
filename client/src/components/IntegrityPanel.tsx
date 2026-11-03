import { useEffect, useState } from "react";
import { api, ApiError, type AnalysisOut, type ExportOut, type VerifyOut } from "../api/client";
import { useJobEvents } from "../lib/useJobEvents";
import { Icon } from "./Icons";
import { PanelHead } from "./PanelHead";

export function IntegrityPanel({ analysis, owner }: { analysis: AnalysisOut; owner: boolean }) {
  const finished = ["completed", "partial"].includes(analysis.status);
  const pending = ["queued", "running", "needs_selection"].includes(analysis.status);
  const canExport = owner && finished;
  const [exports, setExports] = useState<ExportOut[]>([]);
  const [job, setJob] = useState<string | null>(null);
  const [result, setResult] = useState<VerifyOut | null>(null);
  const [digest, setDigest] = useState("");
  const [error, setError] = useState<string | null>(null);
  const refresh = () => api.exports(analysis.analysis_id).then(setExports).catch(() => setExports([]));
  useJobEvents(job, () => { setJob(null); refresh(); });
  useEffect(() => { refresh(); }, [analysis.analysis_id]);
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
  const note = canExport ? "No sealed export yet."
    : owner && pending ? "Sealing opens when the analysis finishes."
      : owner ? "This analysis did not finish, so there is nothing to seal."
        : "This recorded run has no sealed export. Exports are created by the owner of an analysis.";
  return (
    <section className="panel integrity" aria-labelledby="integrity-title">
      <PanelHead icon="shield" titleId="integrity-title" title="Evidence integrity"
        description="Seal frames, records and answers into a content-addressed bundle whose head is kept outside it." />
      {latest ? (
        <div className="export">
          <div className="seal-row">
            <span className="badge tone-ok"><Icon name="lock" size={13} /> Sealed bundle, {latest.file_count} files</span>
            <span className="seal-links">
              <a href={latest.download_url ?? "#"} download>Download archive</a>
              {canExport && (
                <button type="button" className="link" onClick={create} disabled={Boolean(job)}>{job ? "Sealing…" : "Seal again"}</button>
              )}
            </span>
          </div>
          <div className="head-digest">
            <span className="digest-label" id="head-digest-label">Head digest</span>
            <code className="digest-box" aria-labelledby="head-digest-label">{latest.head_digest}</code>
          </div>
          <div className="export-actions">
            <button type="button" onClick={() => verify()}>Verify against the server registry</button>
          </div>
          <form className="verify-own" onSubmit={(e) => { e.preventDefault(); verify(digest.trim()); }}>
            <label htmlFor="expected">Or verify with a head you kept separately</label>
            <div className="input-row">
              <input id="expected" className="mono" value={digest} onChange={(e) => setDigest(e.target.value)} placeholder="64 hex characters"
                pattern="[a-f0-9]{64}" autoComplete="off" spellCheck={false} />
              <button type="submit" className="secondary" disabled={!/^[a-f0-9]{64}$/.test(digest.trim())}>Verify</button>
            </div>
          </form>
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
        </div>
      ) : (
        <>
          <p className="panel-note">{note}</p>
          {canExport && (
            <div className="panel-actions">
              <button type="button" onClick={create} disabled={Boolean(job)}>{job ? "Sealing…" : "Create sealed export"}</button>
            </div>
          )}
        </>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
