import { useEffect, useRef } from "react";
import type { JobEvent } from "../api/client";
import { duration } from "../lib/format";

const HIDDEN = new Set(["budget.updated"]);

export function OperationalTrace({ events, title = "Operations", running = false }: { events: JobEvent[]; title?: string; running?: boolean }) {
  const shown = events.filter((e) => !HIDDEN.has(e.type));
  const reserved = events.filter((e) => e.type === "budget.updated").length;
  const list = useRef<HTMLOListElement>(null);
  useEffect(() => {
    if (list.current) list.current.scrollTop = list.current.scrollHeight;
  }, [shown.length]);
  return (
    <section className="trace" aria-label={title}>
      <div className="trace-head">
        {running && <span className="spinner" aria-hidden="true" />}
        <h3>{title}</h3>
      </div>
      {shown.length === 0 && <p className="muted">Waiting for the first step…</p>}
      <ol ref={list} aria-live="polite" aria-relevant="additions">
        {shown.map((e) => (
          <li key={e.seq} className={`trace-${e.type.replace(".", "-")}`}>
            <span className="trace-time">{duration(e.elapsed_ms)}</span>
            <span className="trace-text">{e.summary}</span>
          </li>
        ))}
      </ol>
      {reserved > 0 && <p className="fineprint">{reserved} provider call(s) reserved in the cost ledger before dispatch.</p>}
    </section>
  );
}
