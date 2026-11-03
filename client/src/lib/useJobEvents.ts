import { useEffect, useRef, useState } from "react";
import type { JobEvent } from "../api/client";

export const TERMINAL_EVENTS = ["job.completed", "job.partial", "job.failed", "job.cancelled", "job.needs_selection"];
const TYPES = [
  "job.queued", "stage.started", "stage.progress", "stage.completed", "tool.started", "tool.completed", "evidence.created",
  "claim.rejected", "review.completed", "budget.updated", ...TERMINAL_EVENTS,
];

export function mergeEvents(existing: JobEvent[], incoming: JobEvent): JobEvent[] {
  if (existing.some((e) => e.seq === incoming.seq)) return existing;
  return [...existing, incoming].sort((a, b) => a.seq - b.seq);
}

export function useJobEvents(jobId: string | null, onTerminal?: (event: JobEvent) => void) {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const terminalRef = useRef(onTerminal);
  terminalRef.current = onTerminal;

  useEffect(() => {
    setEvents([]);
    if (!jobId) return;
    const source = new EventSource(`/api/v1/jobs/${jobId}/events`);
    let finished = false;
    const handle = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as JobEvent;
      setEvents((prev) => mergeEvents(prev, event));
      if (TERMINAL_EVENTS.includes(event.type) && !finished) {
        finished = true;
        source.close();
        setConnected(false);
        terminalRef.current?.(event);
      }
    };
    TYPES.forEach((type) => source.addEventListener(type, handle as EventListener));
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    return () => source.close();
  }, [jobId]);

  return { events, connected };
}
