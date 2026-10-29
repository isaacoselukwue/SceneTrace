import { useEffect, useRef, useState } from "react";
import { api, ApiError, type AnalysisOut, type InvestigationOut } from "../api/client";
import { explainRejection } from "../lib/explain";
import { usd } from "../lib/format";
import { useJobEvents } from "../lib/useJobEvents";
import { ReviewBadge } from "./Badges";
import { Icon } from "./Icons";
import { OperationalTrace } from "./OperationalTrace";

const SOURCE_TEXT: Record<string, string> = {
  investigator: "Compiled from claims the investigator submitted and the server validated.",
  deterministic_fallback: "Compiled directly from stored event records; the investigator did not submit valid claims.",
  abstained: "No answer could be supported.",
  pending: "Pending.",
};

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
  const label = analysis.entities.find((e) => e.entity_id === "O1")?.label ?? "object";
  const suggestions = [`What happened to the ${label}?`, "Who picked it up?", "Where was it last seen?"];
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

  const [latest, ...earlier] = history;
  return (
    <section className="panel answer" aria-labelledby="answer-title">
      <div className="panel-title">
        <h2 id="answer-title">Ask about O1</h2>
        {history.length > 0 && <span className="muted count">{history.length} answer{history.length === 1 ? "" : "s"}</span>}
      </div>
      {canAsk ? (
        <>
          <p className="muted intro">Answers use only claims the server validates against stored events, each linked to its evidence.</p>
          <form className="composer" onSubmit={(e) => { e.preventDefault(); submit(question); }}>
            <label htmlFor="question" className="sr-only">Question about the selected object</label>
            <input id="question" ref={input} value={question} maxLength={300} onChange={(e) => setQuestion(e.target.value)}
              placeholder="What happened to it?" disabled={Boolean(active)} autoComplete="off" enterKeyHint="send" />
            <button type="submit" disabled={Boolean(active) || !question.trim()}>Investigate</button>
          </form>
          {!active && (
            <div className="suggestions" aria-label="Suggested questions">
              {suggestions.map((s) => <button key={s} type="button" className="chip-button" onClick={() => submit(s)}>{s}</button>)}
            </div>
          )}
        </>
      ) : (
        <p className="note">
          <Icon name="film" size={16} />
          <span>This recorded demonstration replays its recorded questions. Asking new questions needs an owner session and a live or fixture analysis.</span>
        </p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      {active && <div className="trace-box"><OperationalTrace events={events} title="Investigation steps" running /></div>}
      {canAsk ? (
        <>
          {latest && <Answer investigation={latest} onCite={onCite} />}
          {earlier.length > 0 && (
            <details className="earlier">
              <summary>Earlier questions ({earlier.length})</summary>
              {earlier.map((inv) => <Answer key={inv.investigation_id} investigation={inv} onCite={onCite} compact />)}
            </details>
          )}
        </>
      ) : (
        [...history].reverse().map((inv) => <Answer key={inv.investigation_id} investigation={inv} onCite={onCite} />)
      )}
    </section>
  );
}

export function CitationChip({ eventId, onClick }: { eventId: string; onClick: () => void }) {
  return (
    <button type="button" className="citation" onClick={onClick} aria-label={`Open evidence for ${eventId}`}>
      {eventId}
    </button>
  );
}

function Answer({ investigation, onCite, compact = false }: {
  investigation: InvestigationOut;
  onCite: (eventId: string, ms: number) => void;
  compact?: boolean;
}) {
  const blocked = investigation.rejected_claims;
  return (
    <article className={`answer-card${compact ? " compact" : ""}`} aria-live="polite">
      <p className="question"><Icon name="chat" size={16} /> <span>{investigation.question}</span></p>
      {investigation.status === "running" || investigation.status === "queued" ? (
        <p className="muted">Investigating…</p>
      ) : (
        <ul className="sentences">
          {investigation.sentences.map((s, i) => (
            <li key={i} className={`sentence kind-${s.kind}`}>
              {s.citation ? (
                <>
                  {s.text.replace(/ \[E\d+\]\.$/, "")}{" "}
                  <CitationChip eventId={s.citation.event_id} onClick={() => onCite(s.citation!.event_id, s.citation!.t_start_ms)} />
                </>
              ) : s.text}
            </li>
          ))}
        </ul>
      )}
      {!compact && (
        <div className="answer-meta">
          <div className="meta-badges"><ReviewBadge investigation={investigation} /></div>
          <p className="source">{SOURCE_TEXT[investigation.answer_source] ?? investigation.answer_source}</p>
          {blocked.length > 0 && (
            <details className="blocked">
              <summary>{blocked.length} claim{blocked.length === 1 ? "" : "s"} blocked before publication</summary>
              <ul>{blocked.map((r, i) => <li key={i}><span className="mono">{r.claim_id}</span> {explainRejection(r.reason_code)}</li>)}</ul>
            </details>
          )}
          <p className="cost">
            {investigation.cost_status === "replayed"
              ? `This question originally cost ${usd(investigation.current_request_cost_microusd)}; the replay made no model calls.`
              : `This question: ${usd(investigation.current_request_cost_microusd)} (${investigation.cost_status.replaceAll("_", " ")}).`}
            {" "}Original analysis: {usd(investigation.original_processing_cost_microusd)}.
          </p>
        </div>
      )}
    </article>
  );
}
