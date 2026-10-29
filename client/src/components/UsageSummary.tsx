import type { AnalysisOut } from "../api/client";
import { duration, usd } from "../lib/format";

export function UsageSummary({ analysis }: { analysis: AnalysisOut }) {
  const { usage, timings_ms: t } = analysis;
  const paid = usage.calls.filter((c) => c.cost_status !== "free");
  return (
    <section className="panel usage" aria-labelledby="usage-title">
      <h2 id="usage-title">Cost and timing</h2>
      <dl className="stats">
        <div><dt>Cold analysis cost</dt><dd>{usd(usage.total_microusd)}</dd></div>
        <div><dt>Cost basis</dt><dd>{usage.cost_status.replaceAll("_", " ")}</dd></div>
        <div><dt>Provider calls</dt><dd>{usage.provider_calls}{usage.unknown_cost_calls ? `, ${usage.unknown_cost_calls} unknown` : ""}</dd></div>
        <div><dt>Normalise</dt><dd>{duration(t.normalise_ms)}</dd></div>
        <div><dt>Tracking</dt><dd>{duration(t.tracking_ms)}{t.tracking_reused_requests ? " (reused)" : ""}</dd></div>
        <div><dt>Assessment</dt><dd>{duration(t.assessment_ms)}</dd></div>
      </dl>
      {paid.length > 0 && (
        <details>
          <summary>Ledger ({paid.length} paid attempts)</summary>
          <table className="ledger">
            <thead><tr><th>Operation</th><th>Model</th><th>Reserved</th><th>Charged</th><th>Status</th></tr></thead>
            <tbody>
              {paid.map((c) => (
                <tr key={c.provider_call_id}>
                  <td>{c.operation}{c.attempt > 1 ? ` (attempt ${c.attempt})` : ""}</td>
                  <td className="mono">{c.model_id}</td>
                  <td>{usd(c.reserved_microusd)}</td>
                  <td>{usd(c.actual_microusd)}</td>
                  <td>{c.cost_status.replaceAll("_", " ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      <p className="fineprint">Reservations are made before every provider call. Figures are engineering estimates from usage and published rates, not an invoice.</p>
    </section>
  );
}
