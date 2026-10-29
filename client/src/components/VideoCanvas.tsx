import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { api, type AnalysisOut, type ClipOut, type ObservationOut } from "../api/client";
import { clipTime } from "../lib/format";
import { containRect, frameAt, type Rect } from "../lib/geometry";

export interface VideoHandle {
  seek(ms: number): void;
}

const WINDOW_MS = 10_000;
const COLOURS: Record<string, string> = { O: "#0f9f8f", P: "#d97706" };

// Plays the tracking proxy and draws only observed boxes/masks at the current frame. Missing
// frames are never interpolated: the overlay hides and the status line says why.
export const VideoCanvas = forwardRef<VideoHandle, { clip: ClipOut; analysis: AnalysisOut; onTime?: (ms: number) => void }>(
  function VideoCanvas({ clip, analysis, onTime }, ref) {
    const video = useRef<HTMLVideoElement>(null);
    const stage = useRef<HTMLDivElement>(null);
    const [rect, setRect] = useState<Rect>({ x: 0, y: 0, width: 0, height: 0 });
    const [frame, setFrame] = useState(0);
    const [overlays, setOverlays] = useState(true);
    const [windows, setWindows] = useState<Record<number, ObservationOut[]>>({});
    const width = clip.proxy_width ?? 0;
    const height = clip.proxy_height ?? 0;
    const fps = clip.proxy_fps ?? 10;

    useImperativeHandle(ref, () => ({
      seek(ms: number) {
        if (video.current) {
          video.current.pause();
          video.current.currentTime = ms / 1000 + 0.001;
        }
      },
    }));

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
        onTime?.(Math.round(el.currentTime * 1000));
        handle = requestAnimationFrame(tick);
      };
      handle = requestAnimationFrame(tick);
      return () => cancelAnimationFrame(handle);
    }, [fps, onTime]);

    const windowIndex = Math.floor((frame * 1000) / fps / WINDOW_MS);
    useEffect(() => {
      for (const index of [windowIndex, windowIndex + 1]) {
        if (windows[index] !== undefined) continue;
        setWindows((w) => ({ ...w, [index]: [] }));
        api.observations(analysis.analysis_id, index * WINDOW_MS, index * WINDOW_MS + WINDOW_MS - 1)
          .then((page) => setWindows((w) => ({ ...w, [index]: page.observations })))
          .catch(() => setWindows((w) => {
            const next = { ...w };
            delete next[index];
            return next;
          }));
      }
    }, [analysis.analysis_id, windowIndex, windows]);

    const current = useMemo(
      () => (windows[windowIndex] ?? []).filter((o) => o.proxy_frame_index === frame),
      [windows, windowIndex, frame],
    );
    const target = current.find((o) => o.entity_id === "O1");
    const status = !target
      ? "Loading tracks for this moment."
      : target.detection_state === "observed"
        ? `O1 observed at ${clipTime((frame * 1000) / fps)}.`
        : target.detection_state === "unknown"
          ? "O1 state unknown here: this frame was not fully analysed."
          : "O1 not detected in this frame. Not detected is not the same as absent.";

    return (
      <section className="video-panel" aria-label="Video with tracking overlays">
        <div className="stage" ref={stage}>
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
              {current
                .filter((o) => o.detection_state === "observed" && o.box)
                .map((o) => {
                  const [x0, y0, x1, y1] = o.box as number[];
                  const colour = COLOURS[o.entity_id[0]] ?? "#64748b";
                  return (
                    <g key={o.entity_id}>
                      {o.mask_svg && <path d={o.mask_svg} fill={colour} fillOpacity={0.35} stroke={colour} strokeWidth={1.5} />}
                      <rect x={x0} y={y0} width={x1 - x0} height={y1 - y0} fill="none" stroke={colour} strokeWidth={2}
                        strokeDasharray={o.mask_svg ? undefined : "6 4"} />
                      <rect x={x0} y={Math.max(0, y0 - 18)} width={o.entity_id.length * 9 + 10} height={18} fill={colour} />
                      <text x={x0 + 5} y={Math.max(13, y0 - 5)} className="overlay-label">{o.entity_id}</text>
                    </g>
                  );
                })}
            </svg>
          )}
        </div>
        <div className="video-meta">
          <label className="toggle">
            <input type="checkbox" checked={overlays} onChange={(e) => setOverlays(e.target.checked)} /> Show overlays
          </label>
          <span className="legend">
            <span className="swatch swatch-o" aria-hidden="true" /> O1 mask and box
            <span className="swatch swatch-p" aria-hidden="true" /> P tracks (boxes, not masks)
          </span>
          <span className="video-status" aria-live="off">{status}</span>
        </div>
      </section>
    );
  },
);
