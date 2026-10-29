import { useEffect, useState } from "react";
import { api, ApiError, type CapabilitiesOut, type ClipOut, type DemoOut, type SessionOut } from "../api/client";
import { dateTime, duration, usd } from "../lib/format";

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
  return (
    <main id="main" className="home">
      <section className="intro">
        <h1>Follow the object. Check the evidence.</h1>
        <p>
          Select one object in a short, fixed-camera clip. SceneTrace tracks it with SAM 3.1, checks candidate interactions
          against the original frames with Muse Spark, and answers questions only with statements it can link to those frames.
          When the footage does not show what happened, it says so.
        </p>
      </section>
      {demos.length > 0 && (
        <section aria-labelledby="demos-title">
          <h2 id="demos-title">Recorded investigations</h2>
          <div className="cards">
            {demos.map((d) => (
              <article key={d.demo_id} className="card">
                <h3>{d.title}</h3>
                <p>{d.description}</p>
                <p className="fineprint">
                  Recorded {dateTime(d.recorded_at)} · {Object.values(d.models).join(", ")} · original cost {usd(d.original_processing_cost_microusd)}
                  {d.media_kind === "synthetic_render" ? " · synthetic test animation" : ""}
                </p>
                <button type="button" onClick={() => navigate(`#/analysis/${d.analysis_id}`)}>Open recorded run</button>
              </article>
            ))}
          </div>
        </section>
      )}
      {!owner ? <AccessForm onSession={onSession} /> : <UploadPanel capabilities={capabilities} onUploaded={(c) => navigate(`#/clip/${c.clip_id}`)} />}
      {owner && clips.length > 0 && (
        <section aria-labelledby="clips-title">
          <h2 id="clips-title">Your clips</h2>
          <ul className="clip-list">
            {clips.map((c) => (
              <li key={c.clip_id}>
                <button type="button" className="link" onClick={() => navigate(`#/clip/${c.clip_id}`)}>{c.display_name}</button>
                <span className="fineprint"> {c.status} · {duration(c.duration_ms)} · {c.analyses.length} analysis run(s)
                  {c.media_kind === "synthetic_render" ? " · synthetic test animation" : ""} · expires {dateTime(c.expires_at)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}

function AccessForm({ onSession }: { onSession: (s: SessionOut) => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <section className="panel" aria-labelledby="access-title">
      <h2 id="access-title">Owner access</h2>
      <p>Uploads and paid analysis need the owner access code. Recorded investigations above are open to everyone.</p>
      <form className="row" onSubmit={async (e) => {
        e.preventDefault();
        try { onSession(await api.session(code)); } catch (err) { setError(err instanceof ApiError ? err.message : "Sign-in failed."); }
      }}>
        <label htmlFor="code" className="sr-only">Access code</label>
        <input id="code" type="password" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="current-password" />
        <button type="submit">Sign in</button>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

function UploadPanel({ capabilities, onUploaded }: { capabilities: CapabilitiesOut | null; onUploaded: (c: ClipOut) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const maxMb = Math.round((capabilities?.limits.max_upload_bytes ?? 150_000_000) / 1_000_000);
  const maxS = Math.round((capabilities?.limits.max_clip_duration_ms ?? 90_000) / 1000);
  return (
    <section className="panel upload" aria-labelledby="upload-title">
      <h2 id="upload-title">Upload a clip</h2>
      <p>MP4 or MOV, up to {maxMb} MB and {maxS} seconds, from a fixed camera with at most {capabilities?.limits.max_supported_people ?? 6} people.</p>
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
        <label className="field">Video file
          <input type="file" accept="video/mp4,video/quicktime,.mp4,.mov" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <label className="check">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          I own this footage and everyone shown agreed to its analysis. SceneTrace does not identify people.
        </label>
        <button type="submit" disabled={!file || !consent || progress !== null}>
          {progress === null ? "Upload" : `Uploading ${Math.round(progress * 100)}%`}
        </button>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
      <p className="fineprint">Uploads are private to this owner and expire after 24 hours. Live analysis sends the normalised clip to Meta Model API in Standard mode; remote copies are deleted after the run.</p>
    </section>
  );
}
