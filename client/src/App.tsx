import { useEffect, useRef, useState } from "react";
import { api, type CapabilitiesOut, type SessionOut } from "./api/client";
import { Home } from "./components/Home";
import { Logo } from "./components/Icons";
import { AnalysisPage, ClipPage } from "./components/Workspace";

function useHash(): [string, (hash: string) => void] {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const onChange = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return [hash, (next: string) => { window.location.hash = next; }];
}

export function App() {
  const [hash, navigate] = useHash();
  const [session, setSession] = useState<SessionOut | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilitiesOut | null>(null);
  const [about, setAbout] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    api.session().then(setSession).catch(() => setSession(null)).finally(() => {
      setReady(true);
      api.capabilities().then(setCapabilities).catch(() => setCapabilities(null));
    });
  }, []);

  useEffect(() => { window.scrollTo(0, 0); }, [hash]);

  const clip = hash.match(/^#\/clip\/([a-z0-9_]+)$/);
  const analysis = hash.match(/^#\/analysis\/([a-z0-9_]+)$/);
  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <header className="site-head">
        <div className="site-head-inner">
          <button type="button" className="brand" onClick={() => navigate("#/")} aria-label="SceneTrace home">
            <Logo />
            <span className="brand-name">SceneTrace</span>
            <span className="brand-tag">Follow the object. Check the evidence.</span>
          </button>
          <nav className="site-nav" aria-label="Site">
            {capabilities && (
              <span className={`live-pill ${capabilities.live_available ? "on" : "off"}`}
                title={capabilities.live_available ? "Meta Model API is configured" : capabilities.live_block_reason ?? "Live analysis is off"}>
                <span className="dot" aria-hidden="true" />
                {capabilities.live_available ? "Live models on" : "Live models off"}
              </span>
            )}
            <button type="button" className="ghost" onClick={() => setAbout(true)}>About</button>
          </nav>
        </div>
      </header>
      {!ready ? <main id="main"><p className="muted">Loading…</p></main>
        : analysis ? <AnalysisPage key={analysis[1]} analysisId={analysis[1]} session={session} navigate={navigate} />
        : clip && capabilities ? <ClipPage clipId={clip[1]} capabilities={capabilities} navigate={navigate} />
          : <Home session={session} capabilities={capabilities} onSession={setSession} navigate={navigate} />}
      <footer className="site-foot">
        <span>SceneTrace, created by Isaac Oselukwue.</span>
        <span>P identifiers are clip-local tracks, not identities. SceneTrace does not recognise faces or judge intent.</span>
      </footer>
      {about && <About capabilities={capabilities} onClose={() => setAbout(false)} />}
    </>
  );
}

function About({ capabilities, onClose }: { capabilities: CapabilitiesOut | null; onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); previous?.focus(); };
  }, [onClose]);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="about-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="about-title">About SceneTrace</h2>
        <p>SceneTrace was created by Isaac Oselukwue. It reconstructs the observed history of one selected object in a short video and links every factual statement to the original frames.</p>
        <h3>Models</h3>
        <ul className="model-list">
          {(capabilities?.models ?? []).map((m) => (
            <li key={m.role}>
              <span className="mono">{m.model_id}</span>
              <span className="muted">{m.role}, capability check {m.status.replace("_", " ")}</span>
            </li>
          ))}
        </ul>
        <p>Segmentation and tracking: SAM 3.1. Visual assessment and investigation: Muse Spark. Optional secondary review: Muse Wonder. All through Meta Model API in Standard mode.</p>
        <h3>What the statuses mean</h3>
        <p>Supported means supported by the available footage and checks, not proven. A rejected candidate is shown so you can see why it was not counted. Missing detections are treated as unknown, not as absence.</p>
        <p className="fineprint">Build {capabilities?.build.commit ?? "unknown"}. Engineering prototype; not a surveillance or identification tool.</p>
        <button ref={close} type="button" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}
