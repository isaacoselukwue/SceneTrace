import type { AnalysisOut, EventOut } from "../api/client";
import { EVENT_LABELS } from "./format";

export function describeEvent(event: EventOut, candidateActors: string[] = []): string {
  const record = event.current.record;
  if (record.event_type === "first_seen" || record.event_type === "last_seen") return EVENT_LABELS[record.event_type];
  if (record.status === "rejected") {
    return `${EVENT_LABELS[record.event_type]} candidate${candidateActors.length ? ` near ${candidateActors.join(", ")}` : ""}`;
  }
  const actor = record.actor_id ? ` by ${record.actor_id}` : ", actor not established";
  const zone = record.zone_id ? ` in ${record.zone_id}` : "";
  return `${EVENT_LABELS[record.event_type]}${zone}${actor}`;
}

export function sortedEvents(analysis: AnalysisOut): EventOut[] {
  return [...analysis.events].sort((a, b) => a.current.record.t_start_ms - b.current.record.t_start_ms);
}

export function candidateActors(analysis: AnalysisOut, event: EventOut): string[] {
  return analysis.candidates.find((c) => c.candidate_id === event.candidate_id)?.actor_candidates ?? [];
}
