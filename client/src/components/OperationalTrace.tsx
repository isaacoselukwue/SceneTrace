import { useEffect, useRef, useState } from "react";
import type { JobEvent } from "../api/client";
import { elapsed } from "../lib/format";

const HIDDEN = new Set(["budget.updated"]);

export function OperationalTrace({ events, title = "Operations", running = false }: { events: JobEvent[]; title?: string; running?: boolean }) {
  const shown = events.filter((e) => !HIDDEN.has(e.type));
  const reserved = events.filter((e) => e.type === "budget.updated").length;
  const list = useRef<HTMLOListElement>(null);
  const [overflowing, setOverflowing] = useState(false);
  useEffect(() => {
    const element = list.current;
    if (!element) return;
    element.scrollTop = element.scrollHeight;
    setOverflowing(element.scrollHeight > element.clientHeight);
  }, [shown.length]);
  return (
    <section className="trace" aria-label={title}>
      <div className="trace-head">
        {running && <span className="spinner" aria-hidden="true" />}
        <h3>{title}</h3>
      </div>
      {shown.length === 0 && <p className="muted">Waiting for the first step…</p>}
      <ol ref={list} className={overflowing ? "overflowing" : undefined} aria-live="polite" aria-relevant="additions">
        {shown.map((e) => (
          <li key={e.seq} className={`trace-${e.type.replace(".", "-")}`}>
            <span className="trace-time">{elapsed(e.elapsed_ms)}</span>
            <span className="trace-text">{e.summary}</span>
          </li>
        ))}
      </ol>
      {reserved > 0 && <p className="fineprint">{reserved} provider call(s) reserved in the cost ledger before dispatch.</p>}
    </section>
  );
}
