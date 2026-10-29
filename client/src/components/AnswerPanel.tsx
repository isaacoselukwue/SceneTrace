import { useEffect, useRef, useState } from "react";
import { api, ApiError, type AnalysisOut, type InvestigationOut } from "../api/client";
import { explainRejection } from "../lib/explain";
import { usd } from "../lib/format";
import { useJobEvents } from "../lib/useJobEvents";
import { ReviewBadge } from "./Badges";
import { OperationalTrace } from "./OperationalTrace";

const SOURCE_TEXT: Record<string, string> = {
  investigator: "Compiled from claims the investigator submitted and the server validated.",
  deterministic_fallback: "Compiled directly from stored event records; the investigator did not submit valid claims.",
  abstained: "No answer could be supported.",
  pending: "Pending.",
};

const SUGGESTIONS = ["What happened to the bottle?", "Who picked it up?", "Where was it last seen?"];

export function AnswerPanel({ analysis, canAsk, onCite }: {
  analysis: AnalysisOut;
  canAsk: boolean;
  onCite: (eventId: string, ms: number) => void;
}) {
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<InvestigationOut[]>([]);
  const [active, setActive] = useState<{ job: string; investigation: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const { events } = useJobEvents(active?.job ?? null, () => {
    api.investigations(analysis.analysis_id).then(setHistory);
    setActive(null);
  });

  useEffect(() => {
    api.investigations(analysis.analysis_id).then(setHistory).catch(() => setHistory([]));
  }, [analysis.analysis_id]);

  async function submit(text: string) {
    if (!text.trim()) return;
    setError(null);
    try {
      const job = await api.ask(analysis.analysis_id, text.trim());
      setActive({ job: job.job_id, investigation: job.investigation_id ?? "" });
      setQuestion("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The question could not be sent.");
    }
  }

  const latest = history[0];
  return (
    <section className="panel answer" aria-labelledby="answer-title">
      <h2 id="answer-title">Ask about O1</h2>
      {canAsk ? (
        <form className="composer" onSubmit={(e) => { e.preventDefault(); submit(question); }}>
          <label htmlFor="question" className="sr-only">Question about the selected object</label>
          <input id="question" ref={input} value={question} maxLength={300} onChange={(e) => setQuestion(e.target.value)}
            placeholder="What happened to it?" disabled={Boolean(active)} autoComplete="off" />
          <button type="submit" disabled={Boolean(active) || !question.trim()}>Investigate</button>
        </form>
      ) : (
        <p className="muted">This recorded demonstration replays its recorded questions. Asking new questions needs an owner session and a live or fixture analysis.</p>
      )}
      {canAsk && !active && (
        <div className="suggestions" aria-label="Suggested questions">
          {SUGGESTIONS.map((s) => <button key={s} type="button" className="chip-button" onClick={() => submit(s)}>{s}</button>)}
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      {active && <OperationalTrace events={events} title="Investigation steps" />}
      {latest && <Answer investigation={latest} onCite={onCite} />}
      {history.length > 1 && (
        <details className="earlier">
          <summary>Earlier questions ({history.length - 1})</summary>
          {history.slice(1).map((inv) => <Answer key={inv.investigation_id} investigation={inv} onCite={onCite} compact />)}
        </details>
      )}
    </section>
  );
}

export function CitationChip({ eventId, onClick }: { eventId: string; onClick: () => void }) {
  return (
    <button type="button" className="citation" onClick={onClick} aria-label={`Open evidence for ${eventId}`}>
      [{eventId}]
    </button>
  );
}

function Answer({ investigation, onCite, compact = false }: {
  investigation: InvestigationOut;
  onCite: (eventId: string, ms: number) => void;
  compact?: boolean;
}) {
  return (
    <article className={`answer-card${compact ? " compact" : ""}`} aria-live="polite">
      <p className="question">“{investigation.question}”</p>
      {investigation.status === "running" || investigation.status === "queued" ? (
        <p className="muted">Investigating…</p>
      ) : (
        <ul className="sentences">
          {investigation.sentences.map((s, i) => (
            <li key={i} className={`sentence kind-${s.kind}`}>
              {s.citation ? (
                <>
                  {s.text.replace(/ \[E\d+\]\.$/, "")}{" "}
                  <CitationChip eventId={s.citation.event_id} onClick={() => onCite(s.citation!.event_id, s.citation!.t_start_ms)} />.
                </>
              ) : s.text}
            </li>
          ))}
        </ul>
      )}
      {!compact && (
        <div className="answer-meta">
          <ReviewBadge investigation={investigation} />
          <span className="badge tone-neutral">{SOURCE_TEXT[investigation.answer_source] ?? investigation.answer_source}</span>
          {investigation.rejected_claims.length > 0 && (
            <details>
              <summary>{investigation.rejected_claims.length} claim(s) blocked before publication</summary>
              <ul>{investigation.rejected_claims.map((r, i) => <li key={i}>{r.claim_id}: {explainRejection(r.reason_code)}</li>)}</ul>
            </details>
          )}
          <span className="cost">
            This question: {usd(investigation.current_request_cost_microusd)} ({investigation.cost_status.replaceAll("_", " ")}).
            Original analysis: {usd(investigation.original_processing_cost_microusd)}.
          </span>
        </div>
      )}
    </article>
  );
}
