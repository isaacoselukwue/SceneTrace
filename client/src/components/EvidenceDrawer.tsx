import { useEffect, useRef, useState } from "react";
import type { AnalysisOut, EvidenceOut } from "../api/client";
import { candidateActors, describeEvent } from "../lib/events";
import { whyNotSupported } from "../lib/explain";
import { clipTime, interval, shortHash } from "../lib/format";
import { StatusBadge } from "./Badges";
import { Icon } from "./Icons";

const ASSESSMENT_FIELDS: [string, string][] = [
  ["visible_before", "Visible before"],
  ["transition_visible", "Transition visible"],
  ["visible_after", "Visible after"],
  ["support_changed", "Support changed"],
  ["carried_motion_visible", "Carried motion visible"],
];
const ROLE_TEXT: Record<string, string> = { observation: "Observation", before: "Before", during: "During", after: "After" };
const ROLE_ORDER: Record<string, number> = { observation: 0, before: 1, during: 2, after: 3 };

function Digest({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button type="button" className="digest" title={`SHA-256 ${value}`} aria-label={`Copy SHA-256 digest ${shortHash(value)}`} onClick={() => {
      navigator.clipboard?.writeText(value).then(() => setCopied(true));
    }}>
      {shortHash(value)}{copied ? " copied" : ""}
    </button>
  );
}

export function EvidenceDrawer({ analysis, eventId, onClose, onSeek }: {
  analysis: AnalysisOut;
  eventId: string;
  onClose: () => void;
  onSeek: (ms: number) => void;
}) {
  const [tab, setTab] = useState<"source" | "annotated">("source");
  const close = useRef<HTMLButtonElement>(null);
  const event = analysis.events.find((e) => e.current.record.event_id === eventId);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, [onClose]);
  if (!event) return null;
  const r = event.current.record;
  const candidate = analysis.candidates.find((c) => c.candidate_id === event.candidate_id);
  const items = analysis.evidence.filter((e) => e.candidate_id === event.candidate_id);
  const sameFrames = new Map<string, EvidenceOut[]>();
  for (const e of items.filter((item) => !item.derived).sort((a, b) => a.t_ms - b.t_ms || ROLE_ORDER[a.role] - ROLE_ORDER[b.role])) {
    sameFrames.set(e.asset_sha256, [...(sameFrames.get(e.asset_sha256) ?? []), e]);
  }
  const raw = [...sameFrames.values()];
  const annotated = new Map(items.filter((e) => e.derived).map((e) => [e.proxy_frame_index, e]));
  const assessments = analysis.assessments.filter((a) => a.candidate_id === event.candidate_id);
  const reasons = whyNotSupported(r.reason_codes);
  const frames = raw.map((group) => ({ group, shown: tab === "source" ? group[0] : annotated.get(group[0].proxy_frame_index) ?? group[0] }));
  const tone = r.status === "supported" ? "ok" : r.status === "rejected" ? "bad" : "warn";
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-head">
          <div className="drawer-title">
            <p className="eyebrow-text">Evidence {r.event_id} · revision {r.revision}</p>
            <h2 id="drawer-title">{describeEvent(event, candidateActors(analysis, event))}</h2>
            <div className="drawer-badges">
              <StatusBadge status={r.status} />
              <span className="badge tone-neutral mono">{interval(r.t_start_ms, r.t_end_ms)}</span>
              {r.actor_id && <span className="badge tone-neutral">Actor {r.actor_id}</span>}
              {r.zone_id && <span className="badge tone-neutral">Zone {r.zone_id}</span>}
            </div>
          </div>
          <button ref={close} type="button" className="icon-button" onClick={onClose} aria-label="Close evidence">
            <Icon name="x" size={20} />
          </button>
        </header>
        <div className="drawer-body">
          <p className="status-meaning">{event.status_meaning}</p>
          {reasons.length > 0 && (
            <section className={`callout tone-${tone}`}>
              <h3>{r.status === "supported" ? "Why it is supported" : r.status === "rejected" ? "Why not supported?" : "What is missing"}</h3>
              <ul>{reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
            </section>
          )}
          <section className="drawer-section" aria-labelledby="frames-title">
            <div className="section-row">
              <h3 id="frames-title">Frames</h3>
              <div className="tabs compact-tabs" role="tablist" aria-label="Frame version">
                <button role="tab" aria-selected={tab === "source"} type="button" onClick={() => setTab("source")}>Original frames</button>
                <button role="tab" aria-selected={tab === "annotated"} type="button" onClick={() => setTab("annotated")}>Annotated copies</button>
              </div>
            </div>
            <p className="fineprint">
              {tab === "source"
                ? "Original pixels decoded from the uploaded file at the mapped source frame. Nothing is drawn on them."
                : "Derived copies with neutral O1 and P labels. They are hashed separately from the originals."}
            </p>
            {frames.length === 0 && <p className="muted">No frames were extracted for this event.</p>}
            <div className="frames">
              {frames.map(({ group, shown: e }) => (
                <figure key={e.evidence_id}>
                  <img src={e.image_url} alt={`${ROLE_TEXT[e.role] ?? e.role} frame at ${clipTime(e.t_ms)}`} loading="lazy" width={e.width} height={e.height} />
                  <figcaption>
                    <span className="frame-role">
                      <span className="frame-name">
                        <strong>{group.map((item) => item.evidence_id).join(", ")}</strong>
                        <span className="frame-role-text">{[...new Set(group.map((item) => ROLE_TEXT[item.role] ?? item.role))].join(" / ")}</span>
                      </span>
                      <button type="button" className="time-chip" onClick={() => onSeek(e.t_ms)} aria-label={`Seek video to ${clipTime(e.t_ms)}`}>
                        <Icon name="play" size={10} /> {clipTime(e.t_ms)}
                      </button>
                    </span>
                    <span className="frame-facts">Source frame {e.source_frame_index} · PTS {clipTime(e.source_pts_ms)}</span>
                    <span className="frame-facts">SHA-256 <Digest value={e.asset_sha256} /></span>
                  </figcaption>
                </figure>
              ))}
            </div>
          </section>
          {assessments.length > 0 && (
            <section className="drawer-section">
              <h3>Structured assessments</h3>
              {assessments.map((a) => (
                <div key={a.assessment_id} className="assessment">
                  <p className="assessment-head"><span className="mono">{a.model_id}</span> <span className="muted">{a.purpose} · {a.status}</span></p>
                  {a.result ? (
                    <table>
                      <tbody>
                        <tr><th scope="row">Verdict</th><td>{String(a.result.verdict)}</td></tr>
                        {ASSESSMENT_FIELDS.map(([key, label]) => (
                          <tr key={key}><th scope="row">{label}</th><td>{String(a.result?.[key])}</td></tr>
                        ))}
                        <tr><th scope="row">Actor named</th><td>{String(a.result.actor_assignment ?? "none")}</td></tr>
                        <tr><th scope="row">Reason codes</th><td className="codes">{(a.result.reason_codes as string[]).join(", ") || "none"}</td></tr>
                      </tbody>
                    </table>
                  ) : <p className="muted">No valid structured result was returned, so this assessment cannot support the event.</p>}
                </div>
              ))}
            </section>
          )}
          {candidate && (
            <section className="drawer-section">
              <h3>Geometric cues</h3>
              <p className="fineprint">Distances are in basis points of the object's size. Closeness is not contact.</p>
              <details className="cues">
                <summary>Show {Object.keys(candidate.cues).length} measurements</summary>
                <table>
                  <tbody>
                    {Object.entries(candidate.cues).map(([k, v]) => (
                      <tr key={k}><th scope="row">{k.replaceAll("_", " ")}</th><td>{v === null ? "n/a" : String(v)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </details>
            </section>
          )}
          <section className="drawer-section">
            <h3>Revision history</h3>
            <ol className="revisions">
              {event.history.map((h) => (
                <li key={h.record.revision}>
                  <span>Revision {h.record.revision}</span> <StatusBadge status={h.record.status} /> <Digest value={h.record_sha256} />
                </li>
              ))}
            </ol>
            <p className="fineprint">Revisions are append-only. An answer always records which revision it used.</p>
          </section>
        </div>
      </aside>
    </div>
  );
}
