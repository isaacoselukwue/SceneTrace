import type { AnalysisOut } from "../api/client";
import { duration, usd } from "../lib/format";
import { Icon } from "./Icons";

export function UsageSummary({ analysis }: { analysis: AnalysisOut }) {
  const { usage, timings_ms: t } = analysis;
  const paid = usage.calls.filter((c) => c.cost_status !== "free");
  const recorded = analysis.run_mode === "recorded_real";
  return (
    <section className="panel usage" aria-labelledby="usage-title">
      <div className="panel-head">
        <span className="panel-icon"><Icon name="clock" /></span>
        <div>
          <h2 id="usage-title">Cost and timing</h2>
          <p className="muted">{recorded ? "From the original live run." : "Every paid call is reserved against the caps before it is sent."}</p>
        </div>
      </div>
      <dl className="stats">
        <div><dt>{recorded ? "Original analysis cost" : "Cold analysis cost"}</dt><dd>{usd(usage.total_microusd)}</dd></div>
        <div><dt>Cost basis</dt><dd>{usage.cost_status.replaceAll("_", " ")}</dd></div>
        <div><dt>Provider calls</dt><dd>{usage.provider_calls}{usage.unknown_cost_calls ? `, ${usage.unknown_cost_calls} unknown` : ""}</dd></div>
        <div><dt>Normalise</dt><dd>{duration(t.normalise_ms)}</dd></div>
        <div><dt>Tracking</dt><dd>{duration(t.tracking_ms)}{t.tracking_reused_requests ? " (reused)" : ""}</dd></div>
        <div><dt>Assessment</dt><dd>{duration(t.assessment_ms)}</dd></div>
      </dl>
      {paid.length > 0 && (
        <details>
          <summary>Ledger ({paid.length} {recorded ? "replayed calls, no new charges" : "paid attempts"})</summary>
          <div className="table-scroll">
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
          </div>
        </details>
      )}
      {recorded && (
        <p className="fineprint">
          Cost and timings are from the original live run. This replay made no model calls
          {t.replay_total_ms !== undefined ? ` and took ${duration(t.replay_total_ms)}` : ""}.
        </p>
      )}
      <p className="fineprint">Figures are engineering estimates from provider-reported usage and published rates, not an invoice.</p>
    </section>
  );
}
