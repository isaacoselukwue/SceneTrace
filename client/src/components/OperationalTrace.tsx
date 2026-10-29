import type { JobEvent } from "../api/client";
import { duration } from "../lib/format";

const HIDDEN = new Set(["budget.updated"]);

// An operational log of steps and concise results. It never shows model reasoning.
export function OperationalTrace({ events, title = "Operations" }: { events: JobEvent[]; title?: string }) {
  const shown = events.filter((e) => !HIDDEN.has(e.type));
  const reserved = events.filter((e) => e.type === "budget.updated").length;
  return (
    <section className="trace" aria-label={title}>
      <h3>{title}</h3>
      <ol aria-live="polite" aria-relevant="additions">
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
