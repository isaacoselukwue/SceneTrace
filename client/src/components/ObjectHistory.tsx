import type { AnalysisOut, EventOut } from "../api/client";
import { whyNotSupported } from "../lib/explain";
import { EVENT_LABELS, interval } from "../lib/format";
import { StatusBadge } from "./Badges";

function describe(event: EventOut, candidateActors: string[]): string {
  const r = event.current.record;
  if (r.event_type === "first_seen" || r.event_type === "last_seen") return EVENT_LABELS[r.event_type];
  if (r.status === "rejected") return `${EVENT_LABELS[r.event_type]} candidate${candidateActors.length ? ` near ${candidateActors.join(", ")}` : ""}`;
  const actor = r.actor_id ? ` by ${r.actor_id}` : ", actor not established";
  const zone = r.zone_id ? ` in ${r.zone_id}` : "";
  return `${EVENT_LABELS[r.event_type]}${zone}${actor}`;
}

export function ObjectHistory({ analysis, onOpen, onSeek }: {
  analysis: AnalysisOut;
  onOpen: (eventId: string) => void;
  onSeek: (ms: number) => void;
}) {
  const target = analysis.entities.find((e) => e.entity_id === "O1");
  const candidates = new Map(analysis.candidates.map((c) => [c.candidate_id, c]));
  const events = [...analysis.events].sort((a, b) => a.current.record.t_start_ms - b.current.record.t_start_ms);
  return (
    <section className="panel history" aria-labelledby="history-title">
      <h2 id="history-title">Object history</h2>
      <p className="entity-line">
        <strong>O1</strong> {target?.label ?? "Selected object"}
        {analysis.entities.filter((e) => e.kind === "person").map((p) => (
          <span key={p.entity_id} className="chip" title="Clip-local person track, not an identity">
            {p.entity_id}{p.identity_warning ? " (track interrupted)" : ""}
          </span>
        ))}
      </p>
      {events.length === 0 && <p className="muted">No events yet. The history appears as the analysis completes.</p>}
      <ol className="event-list">
        {events.map((event) => {
          const r = event.current.record;
          const candidate = event.candidate_id ? candidates.get(event.candidate_id) : undefined;
          const reasons = r.status === "rejected" || r.status === "plausible" ? whyNotSupported(r.reason_codes) : [];
          return (
            <li key={r.event_id} className={`event status-${r.status}`}>
              <div className="event-head">
                <StatusBadge status={r.status} />
                <button type="button" className="link time" onClick={() => onSeek(r.t_start_ms)} aria-label={`Seek to ${interval(r.t_start_ms, r.t_end_ms)}`}>
                  {interval(r.t_start_ms, r.t_end_ms)}
                </button>
              </div>
              <p className="event-text">{describe(event, candidate?.actor_candidates ?? [])}</p>
              {reasons.length > 0 && (
                <details>
                  <summary>{r.status === "rejected" ? "Why not supported?" : "What is missing?"}</summary>
                  <ul>{reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                </details>
              )}
              <div className="event-actions">
                <button type="button" className="secondary" onClick={() => onOpen(r.event_id)}>
                  Evidence {r.event_id}{event.history.length > 1 ? ` (revision ${r.revision})` : ""}
                </button>
              </div>
            </li>
          );
        })}
      </ol>
      <p className="fineprint">Supported means supported by the available footage and checks, not independently proven fact.</p>
    </section>
  );
}
