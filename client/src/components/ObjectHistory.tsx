import type { AnalysisOut } from "../api/client";
import { candidateActors, describeEvent, sortedEvents } from "../lib/events";
import { whyNotSupported } from "../lib/explain";
import { interval, shortInterval } from "../lib/format";
import { STATUS_ICON, StatusBadge } from "./Badges";
import { Icon } from "./Icons";

export function ObjectHistory({ analysis, onOpen, onSeek }: {
  analysis: AnalysisOut;
  onOpen: (eventId: string) => void;
  onSeek: (ms: number) => void;
}) {
  const target = analysis.entities.find((e) => e.entity_id === "O1");
  const events = sortedEvents(analysis);
  const people = analysis.entities.filter((e) => e.kind === "person");
  return (
    <section className="panel history" aria-labelledby="history-title">
      <div className="panel-title">
        <h2 id="history-title">Object history</h2>
        <span className="muted count">{events.length} event{events.length === 1 ? "" : "s"}</span>
      </div>
      <p className="entity-line">
        <span className="entity entity-o">O1</span> <span className="entity-label">{target?.label ?? "Selected object"}</span>
        {people.length > 0 && <span className="muted">near</span>}
        {people.map((p) => (
          <span key={p.entity_id} className="entity entity-p" title="Clip-local person track, not an identity">
            {p.entity_id}{p.identity_warning ? " (track interrupted)" : ""}
          </span>
        ))}
      </p>
      {events.length === 0 && <p className="muted empty">No events yet. The history appears as the analysis completes.</p>}
      <ol className="event-list">
        {events.map((event) => {
          const record = event.current.record;
          const reasons = record.status === "rejected" || record.status === "plausible" ? whyNotSupported(record.reason_codes) : [];
          return (
            <li key={record.event_id} className={`event status-${record.status}`}>
              <span className="event-marker" aria-hidden="true"><Icon name={STATUS_ICON[record.status] ?? "info"} size={14} /></span>
              <div className="event-body">
                <div className="event-head">
                  <p className="event-text">{describeEvent(event, candidateActors(analysis, event))}</p>
                  <StatusBadge status={record.status} />
                </div>
                <div className="event-actions">
                  <button type="button" className="time-chip" onClick={() => onSeek(record.t_start_ms)}
                    aria-label={`Seek to ${interval(record.t_start_ms, record.t_end_ms)}`}>
                    <Icon name="play" size={11} /> {shortInterval(record.t_start_ms, record.t_end_ms)}
                  </button>
                  <button type="button" className="evidence-link" onClick={() => onOpen(record.event_id)}>
                    Evidence {record.event_id}{event.history.length > 1 ? ` (revision ${record.revision})` : ""}
                    <Icon name="arrow" size={14} />
                  </button>
                </div>
                {reasons.length > 0 && (
                  <details className="why">
                    <summary>{record.status === "rejected" ? "Why not supported?" : "What is missing?"}</summary>
                    <ul>{reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                  </details>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      <p className="fineprint">Supported means supported by the available footage and checks, not independently proven fact.</p>
    </section>
  );
}
