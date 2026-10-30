import { useEffect, useState } from "react";
import { api, ApiError, type AnalysisOut, type CapabilitiesOut, type ClipOut, type DemoOut, type SessionOut } from "../api/client";
import { candidateActors, describeEvent, sortedEvents } from "../lib/events";
import { dateTime, duration, shortInterval, usd } from "../lib/format";
import { StatusBadge } from "./Badges";
import { Icon } from "./Icons";
import { EmptyState, PanelHead } from "./PanelHead";

export function Home({ session, capabilities, onSession, navigate }: {
  session: SessionOut | null;
  capabilities: CapabilitiesOut | null;
  onSession: (s: SessionOut) => void;
  navigate: (hash: string) => void;
}) {
  const [demos, setDemos] = useState<DemoOut[]>([]);
  const [clips, setClips] = useState<ClipOut[]>([]);
  const owner = session?.scope === "owner";
  useEffect(() => { api.demos().then(setDemos).catch(() => setDemos([])); }, []);
  useEffect(() => { if (owner) api.clips().then(setClips).catch(() => setClips([])); }, [owner]);
  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  return (
    <main id="main" className="home">
      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow"><Icon name="spark" size={15} /> Built on Meta SAM 3.1, Muse Spark and Muse Wonder</p>
          <h1>Follow the object. <span className="accent">Check the evidence.</span></h1>
          <p className="lead">
            Pick one object in a short fixed-camera clip. SceneTrace tracks it, tests every candidate interaction against the
            original frames, and answers questions only with statements it can link to those frames. When the footage does not
            show what happened, it says so.
          </p>
          <div className="hero-actions">
            {demos.length > 0 && <button type="button" onClick={() => scrollTo("demos")}>Explore a recorded run</button>}
            <button type="button" className="secondary" onClick={() => scrollTo("upload")}>
              {owner ? "Analyse your own clip" : "Owner sign-in"}
            </button>
          </div>
          <ul className="hero-points">
            <li><Icon name="check" size={16} /> Every statement cites original frames</li>
            <li><Icon name="check" size={16} /> Closeness is never counted as contact</li>
            <li><Icon name="check" size={16} /> Unknown stays unknown</li>
          </ul>
        </div>
        {demos[0] && <FeaturedRun demo={demos[0]} onOpen={() => navigate(`#/analysis/${demos[0].analysis_id}`)} />}
      </section>

      <section className="steps" aria-label="How SceneTrace works">
        <Step number={1} title="Track" model="SAM 3.1"
          text="Segments the selected object in every frame and tracks the people near it. Frames that were not analysed stay unknown." />
        <Step number={2} title="Assess" model="Muse Spark 1.3"
          text="Each candidate moment is checked against original frames before, during and after. Deterministic rules decide the status." />
        <Step number={3} title="Answer" model="Validated claims"
          text="The investigator submits structured claims; the server checks each one against stored events and writes the sentence." />
      </section>

      {demos.length > 0 && (
        <section id="demos" className="section" aria-labelledby="demos-title">
          <div className="section-head">
            <h2 id="demos-title">Recorded investigations</h2>
            <p className="muted">Stored live runs, replayed through the same pipeline without new model calls.</p>
          </div>
          <div className="cards">
            {demos.map((d) => <DemoCard key={d.demo_id} demo={d} onOpen={() => navigate(`#/analysis/${d.analysis_id}`)} />)}
          </div>
        </section>
      )}

      <section id="upload" className="section upload-area" aria-label="Your footage">
        {!owner ? <AccessForm onSession={onSession} />
          : <UploadPanel capabilities={capabilities} onUploaded={(c) => navigate(`#/clip/${c.clip_id}`)} />}
        {owner && <ClipList clips={clips} navigate={navigate} />}
      </section>
    </main>
  );
}

function Step({ number, title, model, text }: { number: number; title: string; model: string; text: string }) {
  return (
    <article className="step">
      <div className="step-top">
        <span className="step-number" aria-hidden="true">{number}</span>
        <span className="step-model">{model}</span>
      </div>
      <h3>{title}</h3>
      <p>{text}</p>
    </article>
  );
}

function FeaturedRun({ demo, onOpen }: { demo: DemoOut; onOpen: () => void }) {
  const [analysis, setAnalysis] = useState<AnalysisOut | null>(null);
  useEffect(() => { api.analysis(demo.analysis_id).then(setAnalysis).catch(() => setAnalysis(null)); }, [demo.analysis_id]);
  const events = analysis ? sortedEvents(analysis) : [];
  return (
    <aside className="featured" aria-label={`Recorded run: ${demo.title}`}>
      <div className="featured-media">
        {demo.poster_url && <img src={demo.poster_url} alt={`Frame from ${demo.title}`} />}
        <span className="featured-tag"><span className="badge-dot" aria-hidden="true" /> Recorded real run</span>
      </div>
      <div className="featured-body">
        <div className="featured-title">
          <h2>{demo.title}</h2>
          <span className="muted">O1 history</span>
        </div>
        <ol className="mini-events">
          {events.map((event) => {
            const record = event.current.record;
            return (
              <li key={record.event_id} className={`status-${record.status}`}>
                <span className="mini-time mono">{shortInterval(record.t_start_ms, record.t_end_ms)}</span>
                <span className="mini-label">{describeEvent(event, candidateActors(analysis!, event))}</span>
                <StatusBadge status={record.status} />
              </li>
            );
          })}
        </ol>
        <button type="button" className="block" onClick={onOpen}>Open this run <Icon name="arrow" size={16} /></button>
      </div>
    </aside>
  );
}

function DemoCard({ demo, onOpen }: { demo: DemoOut; onOpen: () => void }) {
  return (
    <article className="card demo-card">
      {demo.poster_url ? <img className="demo-poster" src={demo.poster_url} alt="" /> : <div className="demo-poster" />}
      <div className="demo-body">
        <div className="demo-tags">
          <span className="badge mode-recorded_real"><span className="badge-dot" aria-hidden="true" /> Recorded real run</span>
          {demo.media_kind === "synthetic_render" && <span className="badge tone-neutral">Synthetic test clip</span>}
        </div>
        <h3>{demo.title}</h3>
        <p className="demo-description">{demo.description}</p>
        <dl className="demo-meta">
          <div><dt>Recorded</dt><dd>{dateTime(demo.recorded_at)}</dd></div>
          <div><dt>Original cost</dt><dd>{usd(demo.original_processing_cost_microusd)}</dd></div>
          <div><dt>Questions</dt><dd>{demo.questions.length}</dd></div>
        </dl>
        <p className="fineprint">Models: {Object.values(demo.models).join(", ")}</p>
        <button type="button" onClick={onOpen}>Open recorded run</button>
      </div>
    </article>
  );
}

function AccessForm({ onSession }: { onSession: (s: SessionOut) => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <section className="panel access" aria-labelledby="access-title">
      <PanelHead icon="lock" titleId="access-title" title="Owner access"
        description="Uploads and paid analysis need the owner access code. The recorded investigations are open to everyone." />
      <form className="input-row" onSubmit={async (e) => {
        e.preventDefault();
        try { onSession(await api.session(code)); } catch (err) { setError(err instanceof ApiError ? err.message : "Sign-in failed."); }
      }}>
        <label htmlFor="code" className="sr-only">Access code</label>
        <input id="code" type="password" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="current-password"
          placeholder="Access code" />
        <button type="submit">Sign in</button>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

function UploadPanel({ capabilities, onUploaded }: { capabilities: CapabilitiesOut | null; onUploaded: (c: ClipOut) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const maxMb = Math.round((capabilities?.limits.max_upload_bytes ?? 150_000_000) / 1_000_000);
  const maxS = Math.round((capabilities?.limits.max_clip_duration_ms ?? 90_000) / 1000);
  const percent = progress === null ? 0 : Math.round(progress * 100);
  return (
    <section className="panel upload" aria-labelledby="upload-title">
      <PanelHead icon="upload" titleId="upload-title" title="Upload a clip"
        description={`MP4 or MOV, up to ${maxMb} MB and ${maxS} s, from a fixed camera with at most ${capabilities?.limits.max_supported_people ?? 6} people.`} />
      <form onSubmit={async (e) => {
        e.preventDefault();
        if (!file) return;
        setError(null);
        setProgress(0);
        try {
          onUploaded(await api.upload(file, file.name.replace(/\.[^.]+$/, ""), setProgress));
        } catch (err) {
          setError(err instanceof ApiError ? err.message : "Upload failed.");
          setProgress(null);
        }
      }}>
        <label className={`dropzone${dragging ? " dragging" : ""}${file ? " has-file" : ""}`}
          onDragEnter={() => setDragging(true)} onDragLeave={() => setDragging(false)} onDrop={() => setDragging(false)}>
          <input className="file-input" type="file" aria-label="Video file" accept="video/mp4,video/quicktime,.mp4,.mov"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <span className="dropzone-icon"><Icon name="film" size={26} /></span>
          <span className="dropzone-title">{file ? file.name : "Choose a video or drop it here"}</span>
          <span className="dropzone-hint">{file ? `${(file.size / 1_000_000).toFixed(1)} MB, ready to upload` : "Tap to browse your files"}</span>
        </label>
        <label className="check">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          <span>I own this footage and everyone shown agreed to its analysis. SceneTrace does not identify people.</span>
        </label>
        {progress !== null && (
          <div className="progress" role="progressbar" aria-label="Upload progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
            <span style={{ width: `${percent}%` }} />
          </div>
        )}
        <button type="submit" className="block" disabled={!file || !consent || progress !== null}>
          {progress === null ? "Upload" : `Uploading ${percent}%`}
        </button>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
      <p className="fineprint">
        Uploads are private to this owner and expire after 24 hours. Live analysis sends the normalised clip to Meta Model API in
        Standard mode; every model request asks the provider not to store the response, and the uploaded copy is deleted after the run.
      </p>
    </section>
  );
}

const CLIP_STATUS: Record<string, string> = { ready: "Ready", rejected: "Rejected", expired: "Expired" };

function ClipList({ clips, navigate }: { clips: ClipOut[]; navigate: (hash: string) => void }) {
  return (
    <section className="panel clips" aria-labelledby="clips-title">
      <PanelHead icon="layers" titleId="clips-title" title="Your clips" description="Private uploads and their analysis runs."
        aside={clips.length > 0 ? `${clips.length} clip${clips.length === 1 ? "" : "s"}` : undefined} />
      {clips.length === 0 ? (
        <EmptyState icon="film" title="No clips yet">Uploaded clips and their runs appear here. Uploads expire after 24 hours.</EmptyState>
      ) : (
        <ul className="clip-list">
          {clips.map((c) => (
            <li key={c.clip_id}>
              <button type="button" className="clip-row" onClick={() => navigate(`#/clip/${c.clip_id}`)}>
                <ClipThumbnail clip={c} />
                <span className="clip-text">
                  <span className="clip-name" title={c.display_name}>{c.display_name}</span>
                  <span className="clip-meta">
                    {duration(c.duration_ms)} · {c.analyses.length} run{c.analyses.length === 1 ? "" : "s"}
                    {c.media_kind === "synthetic_render" ? " · synthetic test clip" : ""} · <span className="nowrap">expires {dateTime(c.expires_at)}</span>
                  </span>
                </span>
                <span className={`badge ${c.status === "ready" ? "tone-ok" : c.status === "rejected" ? "tone-bad" : "tone-neutral"}`}>
                  {CLIP_STATUS[c.status] ?? "Preparing"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {clips.length > 0 && (
        <p className="fineprint panel-foot">Open a clip to follow another object in it or to revisit its runs. Clips expire 24 hours after upload.</p>
      )}
    </section>
  );
}

function ClipThumbnail({ clip }: { clip: ClipOut }) {
  const [image, setImage] = useState<string | null>(null);
  const ready = clip.status === "ready" && Boolean(clip.duration_ms);
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    api.frame(clip.clip_id, (clip.duration_ms ?? 0) / 2).then((f) => !cancelled && setImage(f.image_url)).catch(() => setImage(null));
    return () => { cancelled = true; };
  }, [clip.clip_id, clip.duration_ms, ready]);
  return (
    <span className="clip-thumb" aria-hidden="true">
      {image ? <img src={image} alt="" /> : <Icon name="film" size={18} />}
    </span>
  );
}
