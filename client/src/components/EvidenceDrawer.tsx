import { useEffect, useRef, useState } from "react";
import type { AnalysisOut, EvidenceOut } from "../api/client";
import { whyNotSupported } from "../lib/explain";
import { clipTime, EVENT_LABELS, interval, shortHash } from "../lib/format";
import { StatusBadge } from "./Badges";

const ASSESSMENT_FIELDS: [string, string][] = [
  ["visible_before", "Visible before"],
  ["transition_visible", "Transition visible"],
  ["visible_after", "Visible after"],
  ["support_changed", "Support changed"],
  ["carried_motion_visible", "Carried motion visible"],
];

function Digest({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button type="button" className="digest" title={value} onClick={() => {
      navigator.clipboard?.writeText(value).then(() => setCopied(true));
    }}>
      {shortHash(value)}{copied ? " (copied)" : ""}
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
    return () => {
      window.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [onClose]);
  if (!event) return null;
  const r = event.current.record;
  const candidate = analysis.candidates.find((c) => c.candidate_id === event.candidate_id);
  const items = analysis.evidence.filter((e) => e.candidate_id === event.candidate_id);
  const raw = items.filter((e) => !e.derived);
  const annotated = new Map(items.filter((e) => e.derived).map((e) => [e.proxy_frame_index, e]));
  const assessments = analysis.assessments.filter((a) => a.candidate_id === event.candidate_id);
  const reasons = whyNotSupported(r.reason_codes);
  const frames: EvidenceOut[] = tab === "source" ? raw : raw.map((e) => annotated.get(e.proxy_frame_index) ?? e);
  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-head">
          <div>
            <h2 id="drawer-title">{EVENT_LABELS[r.event_type]} {r.event_id}, revision {r.revision}</h2>
            <p><StatusBadge status={r.status} /> {event.status_meaning}</p>
            <p className="mono">{interval(r.t_start_ms, r.t_end_ms)}{r.actor_id ? ` · actor ${r.actor_id}` : ""}{r.zone_id ? ` · zone ${r.zone_id}` : ""}</p>
          </div>
          <button ref={close} type="button" className="secondary" onClick={onClose}>Close</button>
        </header>
        {reasons.length > 0 && (
          <section>
            <h3>{r.status === "supported" ? "Why it is supported" : r.status === "rejected" ? "Why not supported?" : "What is missing"}</h3>
            <ul>{reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
          </section>
        )}
        <section>
          <div className="tabs" role="tablist" aria-label="Frame version">
            <button role="tab" aria-selected={tab === "source"} type="button" onClick={() => setTab("source")}>Original frames</button>
            <button role="tab" aria-selected={tab === "annotated"} type="button" onClick={() => setTab("annotated")}>Annotated copies</button>
          </div>
          <p className="fineprint">
            {tab === "source"
              ? "Original pixels decoded from the uploaded file at the mapped source frame. Nothing is drawn on them."
              : "Derived copies with neutral O1 and P labels. They are hashed separately from the originals."}
          </p>
          <div className="frames">
            {frames.map((e) => (
              <figure key={e.evidence_id}>
                <img src={e.image_url} alt={`${e.role} frame at ${clipTime(e.t_ms)}`} loading="lazy" width={e.width} height={e.height} />
                <figcaption>
                  <strong>{e.evidence_id}</strong> {e.role} · {clipTime(e.t_ms)}
                  <button type="button" className="link" onClick={() => onSeek(e.t_ms)}>Seek</button>
                  <br />
                  <span className="mono">source frame {e.source_frame_index} · PTS {clipTime(e.source_pts_ms)} · </span>
                  <Digest value={e.asset_sha256} />
                </figcaption>
              </figure>
            ))}
          </div>
        </section>
        {assessments.length > 0 && (
          <section>
            <h3>Structured assessments</h3>
            {assessments.map((a) => (
              <div key={a.assessment_id} className="assessment">
                <p className="mono">{a.model_id} · {a.purpose} · {a.status}</p>
                {a.result ? (
                  <table>
                    <tbody>
                      <tr><th scope="row">Verdict</th><td>{String(a.result.verdict)}</td></tr>
                      {ASSESSMENT_FIELDS.map(([key, label]) => (
                        <tr key={key}><th scope="row">{label}</th><td>{String(a.result?.[key])}</td></tr>
                      ))}
                      <tr><th scope="row">Actor named</th><td>{String(a.result.actor_assignment ?? "none")}</td></tr>
                      <tr><th scope="row">Reason codes</th><td>{(a.result.reason_codes as string[]).join(", ") || "none"}</td></tr>
                    </tbody>
                  </table>
                ) : <p className="muted">No valid structured result was returned, so this assessment cannot support the event.</p>}
              </div>
            ))}
          </section>
        )}
        {candidate && (
          <section>
            <h3>Geometric cues</h3>
            <p className="fineprint">Distances are in basis points of the object's size. Closeness is not contact.</p>
            <dl className="cues">
              {Object.entries(candidate.cues).map(([k, v]) => (
                <div key={k}><dt>{k.replaceAll("_", " ")}</dt><dd>{v === null ? "n/a" : String(v)}</dd></div>
              ))}
            </dl>
          </section>
        )}
        <section>
          <h3>Revision history</h3>
          <ol>
            {event.history.map((h) => (
              <li key={h.record.revision} className="mono">
                revision {h.record.revision}: {h.record.status} · <Digest value={h.record_sha256} />
              </li>
            ))}
          </ol>
          <p className="fineprint">Revisions are append-only. An answer always records which revision it used.</p>
        </section>
      </aside>
    </div>
  );
}
