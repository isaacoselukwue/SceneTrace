import type { AnalysisOut } from "../api/client";
import { duration, usd } from "../lib/format";
import { PanelHead } from "./PanelHead";

const COST_BASIS: Record<string, string> = {
  provider_usage_based: "Provider-reported usage",
  estimated: "Estimated from published rates",
  replayed: "Recorded calls, replayed",
  unknown: "Unknown",
  none: "No paid calls",
};
const STAGES = [
  { key: "normalise_ms", label: "Normalise" },
  { key: "tracking_ms", label: "Tracking" },
  { key: "assessment_ms", label: "Assessment" },
] as const;

export function UsageSummary({ analysis }: { analysis: AnalysisOut }) {
  const { usage, timings_ms: timings } = analysis;
  const paid = usage.calls.filter((c) => c.cost_status !== "free");
  const recorded = analysis.run_mode === "recorded_real";
  const finished = ["completed", "partial", "failed", "cancelled"].includes(analysis.status);
  const processing = finished ? duration(timings.total_ms) : "In progress";
  return (
    <section className="panel usage" aria-labelledby="usage-title">
      <PanelHead icon="clock" titleId="usage-title" title="Cost and timing"
        description={recorded ? "From the original live run." : "Every paid call is reserved against the caps before it is sent."} />
      <div className="metrics">
        <div className="metric">
          <span className="metric-value">{usd(usage.total_microusd)}</span>
          <span className="metric-label">Analysis cost</span>
        </div>
        <div className="metric">
          <span className="metric-value">{processing}</span>
          <span className="metric-label">Processing time</span>
        </div>
      </div>
      <dl className="facts">
        {STAGES.map(({ key, label }) => (
          <div key={key}>
            <dt>{label}</dt>
            <dd>{duration(timings[key])}{key === "tracking_ms" && timings.tracking_reused_requests ? " (reused)" : ""}</dd>
          </div>
        ))}
        <div>
          <dt>Provider calls</dt>
          <dd>{usage.provider_calls}{usage.unknown_cost_calls ? `, ${usage.unknown_cost_calls} of unknown cost` : ""}</dd>
        </div>
        <div><dt>Cost basis</dt><dd>{COST_BASIS[usage.cost_status] ?? usage.cost_status}</dd></div>
      </dl>
      {paid.length > 0 && (
        <details className="ledger-details">
          <summary>Ledger ({paid.length} {recorded ? "replayed calls, no new charges" : "paid attempts"})</summary>
          <div className="table-scroll">
            <table className="ledger">
              <thead><tr><th>Operation</th><th>Model</th><th>Reserved</th><th>Charged</th><th>Status</th></tr></thead>
              <tbody>
                {paid.map((c) => (
                  <tr key={c.provider_call_id}>
                    <td>{c.operation}{c.attempt > 1 ? ` (attempt ${c.attempt})` : ""}</td>
                    <td className="mono" data-label="Model">{c.model_id}</td>
                    <td data-label="Reserved">{usd(c.reserved_microusd)}</td>
                    <td data-label="Charged">{usd(c.actual_microusd)}</td>
                    <td data-label="Status">{c.cost_status.replaceAll("_", " ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      <div className="panel-foot">
        {recorded && (
          <p className="fineprint">
            Cost and timings are from the original live run. This replay made no model calls
            {timings.replay_total_ms !== undefined ? ` and took ${duration(timings.replay_total_ms)}` : ""}.
          </p>
        )}
        <p className="fineprint">Figures are engineering estimates from provider-reported usage and published rates, not an invoice.</p>
      </div>
    </section>
  );
}
