import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { api, type AnalysisOut, type ClipOut, type ObservationOut, type VisibilitySpanOut } from "../api/client";
import { candidateActors, describeEvent, sortedEvents } from "../lib/events";
import { clipTime } from "../lib/format";
import { containRect, frameAt, placeLabels, type Rect } from "../lib/geometry";

export interface VideoHandle {
  seek(ms: number): void;
}

const WINDOW_MS = 10_000;
const LANE_PX = 18;
const MAX_LANES = 3;
const LABEL_HEIGHT_PX = 18;
const COLOURS: Record<string, string> = { O: "#14b8a6", P: "#f59e0b" };
const VISIBILITY_TEXT: Record<VisibilitySpanOut["state"], string> = {
  observed: "Observed",
  not_detected: "Not detected",
  unknown: "Not analysed",
};

export const VideoCanvas = forwardRef<VideoHandle, { clip: ClipOut; analysis: AnalysisOut }>(
  function VideoCanvas({ clip, analysis }, ref) {
    const video = useRef<HTMLVideoElement>(null);
    const [rect, setRect] = useState<Rect>({ x: 0, y: 0, width: 0, height: 0 });
    const [frame, setFrame] = useState(0);
    const [overlays, setOverlays] = useState(true);
    const [cache, setCache] = useState<{ generation: string; windows: Record<number, ObservationOut[]> }>({ generation: "", windows: {} });
    const [spans, setSpans] = useState<VisibilitySpanOut[]>([]);
    const width = clip.proxy_width ?? 0;
    const height = clip.proxy_height ?? 0;
    const fps = clip.proxy_fps ?? 10;
    const durationMs = clip.duration_ms ?? 0;
    const finished = ["completed", "partial"].includes(analysis.status);
    const generation = `${analysis.analysis_id}:${analysis.status}`;
    const windows = cache.generation === generation ? cache.windows : {};

    const seek = useCallback((ms: number) => {
      if (video.current) {
        video.current.pause();
        video.current.currentTime = Math.max(0, ms) / 1000 + 0.001;
      }
    }, []);
    useImperativeHandle(ref, () => ({ seek }), [seek]);

    const measure = useCallback(() => {
      const el = video.current;
      if (el) setRect(containRect(el.clientWidth, el.clientHeight, width, height));
    }, [width, height]);

    useEffect(() => {
      measure();
      const observer = new ResizeObserver(measure);
      if (video.current) observer.observe(video.current);
      return () => observer.disconnect();
    }, [measure]);

    useEffect(() => {
      const el = video.current;
      if (!el) return;
      let handle = 0;
      const tick = () => {
        setFrame(frameAt(el.currentTime, fps));
        handle = requestAnimationFrame(tick);
      };
      handle = requestAnimationFrame(tick);
      return () => cancelAnimationFrame(handle);
    }, [fps]);

    useEffect(() => {
      if (!finished) return;
      let cancelled = false;
      api.visibility(analysis.analysis_id).then((v) => !cancelled && setSpans(v.spans)).catch(() => setSpans([]));
      return () => { cancelled = true; };
    }, [analysis.analysis_id, finished]);

    const windowIndex = Math.floor((frame * 1000) / fps / WINDOW_MS);
    useEffect(() => {
      if (!finished) return;
      for (const index of [windowIndex, windowIndex + 1]) {
        if (windows[index] !== undefined) continue;
        setCache((c) => ({ generation, windows: { ...(c.generation === generation ? c.windows : {}), [index]: [] } }));
        api.observations(analysis.analysis_id, index * WINDOW_MS, index * WINDOW_MS + WINDOW_MS - 1)
          .then((page) => setCache((c) => (c.generation === generation
            ? { generation, windows: { ...c.windows, [index]: page.observations } } : c)))
          .catch(() => setCache((c) => {
            if (c.generation !== generation) return c;
            const next = { ...c.windows };
            delete next[index];
            return { generation, windows: next };
          }));
      }
    }, [analysis.analysis_id, finished, generation, windowIndex, windows]);

    const current = useMemo(
      () => (windows[windowIndex] ?? []).filter((o) => o.proxy_frame_index === frame),
      [windows, windowIndex, frame],
    );
    const target = current.find((o) => o.entity_id === "O1");
    const nowMs = (frame * 1000) / fps;
    const proxyPixelsPerScreenPixel = rect.width > 0 ? width / rect.width : 1;
    const labelHeight = LABEL_HEIGHT_PX * proxyPixelsPerScreenPixel;
    const tracked = current.filter((o) => o.detection_state === "observed" && o.box);
    const boxArea = (o: ObservationOut) => { const [x0, y0, x1, y1] = o.box as number[]; return (x1 - x0) * (y1 - y0); };
    const labels = placeLabels(
      [...tracked]
        .sort((a, b) => Number(b.entity_id === "O1") - Number(a.entity_id === "O1") || boxArea(b) - boxArea(a))
        .map((o) => ({ id: o.entity_id, box: o.box as number[], width: (o.entity_id.length * 8 + 10) * proxyPixelsPerScreenPixel })),
      labelHeight, width, height,
    );
    const drawOrder = [...tracked].sort((a, b) => Number(a.entity_id === "O1") - Number(b.entity_id === "O1"));
    const status = !finished
      ? "Tracks appear here when the analysis finishes."
      : !target
        ? "Loading tracks for this moment."
        : target.detection_state === "observed"
          ? `O1 observed at ${clipTime(nowMs)}.`
          : target.detection_state === "unknown"
            ? "O1 state unknown here: this frame was not fully analysed."
            : "O1 not detected in this frame. Not detected is not the same as absent.";

    return (
      <section className="video-panel" aria-label="Video with tracking overlays">
        <div className="stage" style={width && height ? { aspectRatio: `${width} / ${height}` } : undefined}>
          <video
            ref={video}
            src={clip.video_url ?? undefined}
            controls
            playsInline
            preload="metadata"
            onLoadedMetadata={measure}
            aria-label={`${clip.display_name}, tracking proxy at ${fps} frames per second`}
          />
          {overlays && rect.width > 0 && (
            <svg
              className="overlay"
              style={{ left: rect.x, top: rect.y, width: rect.width, height: rect.height }}
              viewBox={`0 0 ${width} ${height}`}
              aria-hidden="true"
            >
              {analysis.zones.map((z) => z.polygon && (
                <g key={z.entity_id}>
                  <polygon points={z.polygon.map((p) => p.join(",")).join(" ")} className="zone-overlay" />
                  <text x={z.polygon[0][0] + 4 * proxyPixelsPerScreenPixel} y={z.polygon[0][1] + 16 * proxyPixelsPerScreenPixel}
                    className="zone-label" style={{ fontSize: 13 * proxyPixelsPerScreenPixel }}>{z.entity_id} {z.label}</text>
                </g>
              ))}
              {drawOrder.map((o) => {
                const [x0, y0, x1, y1] = o.box as number[];
                const colour = COLOURS[o.entity_id[0]] ?? "#64748b";
                return (
                  <g key={o.entity_id}>
                    {o.mask_svg && <path d={o.mask_svg} fill={colour} fillOpacity={0.35} stroke={colour} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />}
                    <rect x={x0} y={y0} width={x1 - x0} height={y1 - y0} fill="none" stroke={colour} strokeWidth={2}
                      strokeDasharray={o.mask_svg ? undefined : "6 4"} vectorEffect="non-scaling-stroke" />
                  </g>
                );
              })}
              {drawOrder.map((o) => {
                const label = labels.get(o.entity_id);
                return label && (
                  <g key={`${o.entity_id}-label`}>
                    <rect x={label.x} y={label.y} width={(o.entity_id.length * 8 + 10) * proxyPixelsPerScreenPixel} height={labelHeight}
                      rx={3 * proxyPixelsPerScreenPixel} fill={COLOURS[o.entity_id[0]] ?? "#64748b"} />
                    <text x={label.x + 5 * proxyPixelsPerScreenPixel} y={label.y + 13 * proxyPixelsPerScreenPixel} className="overlay-label"
                      style={{ fontSize: 12 * proxyPixelsPerScreenPixel }}>{o.entity_id}</text>
                  </g>
                );
              })}
            </svg>
          )}
        </div>
        {durationMs > 0 && (
          <Timeline analysis={analysis} durationMs={durationMs} nowMs={nowMs} spans={spans} onSeek={seek} />
        )}
        <div className="video-meta">
          <label className="switch">
            <input type="checkbox" checked={overlays} onChange={(e) => setOverlays(e.target.checked)} /> Show overlays
          </label>
          <span className="legend">
            <span className="legend-item"><span className="swatch swatch-o" aria-hidden="true" /> O1 mask and box</span>
            <span className="legend-item"><span className="swatch swatch-p" aria-hidden="true" /> P tracks (boxes)</span>
            {analysis.zones.length > 0 && <span className="legend-item"><span className="swatch swatch-z" aria-hidden="true" /> Zones</span>}
          </span>
          <span className="video-status" aria-live="off">{status}</span>
        </div>
      </section>
    );
  },
);

function Timeline({ analysis, durationMs, nowMs, spans, onSeek }: {
  analysis: AnalysisOut;
  durationMs: number;
  nowMs: number;
  spans: VisibilitySpanOut[];
  onSeek: (ms: number) => void;
}) {
  const track = useRef<HTMLDivElement>(null);
  const share = (ms: number) => Math.min(100, Math.max(0, (ms / durationMs) * 100));
  const percent = (ms: number) => `${share(ms)}%`;
  const events = sortedEvents(analysis);
  const states = new Set(spans.map((s) => s.state));
  const laneEnds: number[] = [];
  const placed = events.map((event) => {
    const record = event.current.record;
    const start = share(record.t_start_ms);
    const end = share(record.t_end_ms);
    let lane = laneEnds.findIndex((laneEnd) => laneEnd + 3 <= start);
    if (lane === -1) lane = laneEnds.length < MAX_LANES ? laneEnds.length : MAX_LANES - 1;
    laneEnds[lane] = Math.max(laneEnds[lane] ?? 0, end);
    return { event, record, start, end, lane };
  });
  return (
    <div className="timeline">
      <div className="timeline-events" style={{ height: Math.max(1, laneEnds.length) * LANE_PX }}>
        {placed.map(({ event, record, start, end, lane }) => {
          const label = describeEvent(event, candidateActors(analysis, event));
          return (
            <button key={record.event_id} type="button" className={`marker status-${record.status}`}
              style={{ left: `calc(${start}% - 7px)`, width: `calc(${end - start}% + 14px)`, top: lane * LANE_PX }}
              title={`${record.event_id}: ${label}`}
              aria-label={`Seek to ${record.event_id}, ${label}, at ${clipTime(record.t_start_ms)}`}
              onClick={() => onSeek(record.t_start_ms)} />
          );
        })}
      </div>
      <div className="timeline-track" ref={track} aria-hidden="true" onClick={(e) => {
        const bounds = track.current?.getBoundingClientRect();
        if (bounds && bounds.width > 0) onSeek(((e.clientX - bounds.left) / bounds.width) * durationMs);
      }}>
        {spans.map((s) => (
          <span key={s.start_ms} className={`vis vis-${s.state}`}
            style={{ left: percent(s.start_ms), width: `${((s.end_ms - s.start_ms) / durationMs) * 100}%` }} />
        ))}
        <span className="playhead" style={{ left: percent(nowMs) }} />
      </div>
      <div className="timeline-scale" aria-hidden="true">
        <span>{clipTime(0)}</span>
        <span>{clipTime(durationMs)}</span>
      </div>
      {spans.length > 0 && (
        <p className="timeline-legend">
          <span className="legend-title">O1 visibility</span>
          {(["observed", "not_detected", "unknown"] as const).filter((s) => states.has(s)).map((s) => (
            <span key={s} className="legend-item"><span className={`legend-swatch vis-${s}`} aria-hidden="true" /> {VISIBILITY_TEXT[s]}</span>
          ))}
          <span className="legend-item"><span className="legend-dot" aria-hidden="true" /> Events</span>
        </p>
      )}
    </div>
  );
}
