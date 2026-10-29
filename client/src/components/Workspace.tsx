import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type AnalysisOut, type CapabilitiesOut, type ClipOut, type SessionOut } from "../api/client";
import { useJobEvents } from "../lib/useJobEvents";
import { AnswerPanel } from "./AnswerPanel";
import { ModeBanner, RunModeBadge } from "./Badges";
import { EvidenceDrawer } from "./EvidenceDrawer";
import { IntegrityPanel } from "./IntegrityPanel";
import { ObjectHistory } from "./ObjectHistory";
import { OperationalTrace } from "./OperationalTrace";
import { TargetSelector } from "./TargetSelector";
import { UsageSummary } from "./UsageSummary";
import { VideoCanvas, type VideoHandle } from "./VideoCanvas";

export function ClipPage({ clipId, capabilities, navigate }: { clipId: string; capabilities: CapabilitiesOut; navigate: (h: string) => void }) {
  const [clip, setClip] = useState<ClipOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => api.clip(clipId).then(setClip).catch((e) => setError(e instanceof ApiError ? e.message : "Not found.")), [clipId]);
  useEffect(() => { load(); }, [load]);
  const { events } = useJobEvents(clip && clip.status !== "ready" && clip.status !== "rejected" ? clip.normalise_job_id : null, () => load());
  if (error) return <main id="main"><p className="error">{error}</p></main>;
  if (!clip) return <main id="main"><p className="muted">Loading…</p></main>;
  return (
    <main id="main" className="clip-page">
      <h1>{clip.display_name}</h1>
      {clip.media_kind === "synthetic_render" && <div className="banner banner-synthetic">Synthetic test animation, not real footage.</div>}
      {clip.status === "rejected" && <p className="error" role="alert">The file was rejected ({clip.reject_code}). Upload an MP4 or MOV within the limits.</p>}
      {clip.status !== "ready" && clip.status !== "rejected" && <OperationalTrace events={events} title="Preparing the clip" />}
      {clip.status === "ready" && (
        <>
          {clip.analyses.length > 0 && (
            <p>Previous runs: {clip.analyses.map((id, i) => (
              <button key={id} type="button" className="link" onClick={() => navigate(`#/analysis/${id}`)}>run {i + 1}</button>
            ))}</p>
          )}
          <TargetSelector clip={clip} capabilities={capabilities} onStarted={(id) => navigate(`#/analysis/${id}`)} />
        </>
      )}
    </main>
  );
}

export function AnalysisPage({ analysisId, session }: { analysisId: string; session: SessionOut | null }) {
  const [analysis, setAnalysis] = useState<AnalysisOut | null>(null);
  const [clip, setClip] = useState<ClipOut | null>(null);
  const [openEvent, setOpenEvent] = useState<string | null>(null);
  const [tab, setTab] = useState<"history" | "answer" | "details">("history");
  const [error, setError] = useState<string | null>(null);
  const video = useRef<VideoHandle>(null);
  const load = useCallback(async () => {
    try {
      const a = await api.analysis(analysisId);
      setAnalysis(a);
      setClip(await api.clip(a.clip_id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Not found.");
    }
  }, [analysisId]);
  useEffect(() => { load(); }, [load]);
  const running = analysis && ["queued", "running"].includes(analysis.status);
  const { events } = useJobEvents(running ? analysis.job_id : null, () => load());
  if (error) return <main id="main"><p className="error">{error}</p></main>;
  if (!analysis || !clip) return <main id="main"><p className="muted">Loading…</p></main>;
  const owner = session?.scope === "owner" && !analysis.demo;
  const seek = (ms: number) => video.current?.seek(ms);
  const cite = (eventId: string, ms: number) => { seek(ms); setOpenEvent(eventId); };
  return (
    <main id="main" className="workspace">
      <div className="workspace-head">
        <h1>{clip.display_name}</h1>
        <RunModeBadge mode={analysis.run_mode} />
        {analysis.status === "partial" && <span className="badge tone-warn">Partial: {analysis.partial_reasons.join(", ").toLowerCase().replaceAll("_", " ")}</span>}
        {analysis.status === "failed" && <span className="badge tone-bad">Failed</span>}
        {analysis.status === "cancelled" && <span className="badge tone-warn">Cancelled</span>}
      </div>
      <ModeBanner analysis={analysis} />
      {running && (
        <div className="panel">
          <OperationalTrace events={events} title="Analysis in progress" />
          {owner && analysis.job_id && <button type="button" className="secondary" onClick={() => api.cancel(analysis.job_id!).then(load)}>Cancel</button>}
        </div>
      )}
      {analysis.status === "needs_selection" && <SelectionStep analysis={analysis} onBound={load} />}
      <div className="grid">
        <div className="area-video"><VideoCanvas ref={video} clip={clip} analysis={analysis} /></div>
        <div className="tabs mobile-only area-tabs" role="tablist" aria-label="Panels">
          {(["history", "answer", "details"] as const).map((t) => (
            <button key={t} role="tab" type="button" aria-selected={tab === t} onClick={() => setTab(t)}>
              {t === "history" ? "History" : t === "answer" ? "Answer" : "Evidence"}
            </button>
          ))}
        </div>
        <div className={`tab-panel area-history ${tab === "history" ? "active" : ""}`}>
          <ObjectHistory analysis={analysis} onOpen={setOpenEvent} onSeek={seek} />
        </div>
        <div className={`tab-panel area-answer ${tab === "answer" ? "active" : ""}`}>
          {["completed", "partial"].includes(analysis.status) && <AnswerPanel analysis={analysis} canAsk={owner} onCite={cite} />}
        </div>
        <div className={`tab-panel area-details ${tab === "details" ? "active" : ""}`}>
          <IntegrityPanel analysis={analysis} canExport={owner && ["completed", "partial"].includes(analysis.status)} />
          <UsageSummary analysis={analysis} />
        </div>
      </div>
      {openEvent && <EvidenceDrawer analysis={analysis} eventId={openEvent} onClose={() => setOpenEvent(null)} onSeek={seek} />}
    </main>
  );
}

function SelectionStep({ analysis, onBound }: { analysis: AnalysisOut; onBound: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const reason = analysis.partial_reasons.includes("TARGET_NOT_FOUND")
    ? "No match on that frame. Go back and choose another frame or draw a box."
    : "More than one object matches. Choose the one to follow; SceneTrace will not guess.";
  return (
    <section className="panel" aria-labelledby="select-title">
      <h2 id="select-title">Select the intended object</h2>
      <p>{reason}</p>
      <div className="selections">
        {analysis.selections.map((s) => (
          <button key={s.selection_id} type="button" className="selection" onClick={() =>
            api.bindSelection(analysis.analysis_id, s.selection_id).then(onBound).catch((e) => setError(e.message))}>
            {s.thumbnail_url && <img src={s.thumbnail_url} alt={`Candidate at ${s.box.join(", ")}`} />}
            <span>Box {s.box.join(", ")}</span>
          </button>
        ))}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
