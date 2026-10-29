import { useEffect, useRef, useState } from "react";
import { api, ApiError, idempotencyKey, type CapabilitiesOut, type ClipOut, type RunMode } from "../api/client";
import { clipTime, usd } from "../lib/format";
import { boxFromDrag, containRect, toMediaPixel } from "../lib/geometry";

const MODE_TITLE: Record<RunMode, string> = {
  live: "Live analysis",
  recorded_real: "Recorded real run",
  fixture: "Development fixture",
};
const MODE_HELP: Record<RunMode, string> = {
  live: "SAM 3.1 and Muse Spark run now. Paid calls are reserved against the cost caps first.",
  recorded_real: "Replays a stored live run of this clip without new model calls.",
  fixture: "Deterministic code stands in for the models. Not a real analysis.",
};
const MODE_ORDER: RunMode[] = ["live", "recorded_real", "fixture"];
const MAX_ZONE_POINTS = 16;

type Tool = "none" | "box" | "zone";

export function TargetSelector({ clip, capabilities, onStarted }: {
  clip: ClipOut;
  capabilities: CapabilitiesOut;
  onStarted: (analysisId: string) => void;
}) {
  const width = clip.proxy_width ?? 0;
  const height = clip.proxy_height ?? 0;
  const [tMs, setTMs] = useState(0);
  const [image, setImage] = useState<string | null>(null);
  const [kind, setKind] = useState<"concept" | "box">("concept");
  const [phrase, setPhrase] = useState("");
  const [box, setBox] = useState<[number, number, number, number] | null>(null);
  const [drag, setDrag] = useState<{ x: number; y: number } | null>(null);
  const [zoneOn, setZoneOn] = useState(false);
  const [zoneLabel, setZoneLabel] = useState("tray");
  const [zone, setZone] = useState<[number, number][]>([]);
  const modes = MODE_ORDER.filter((m) => capabilities.available_run_modes.includes(m) && (m !== "recorded_real" || clip.analyses.length > 0));
  const [mode, setMode] = useState<RunMode>(modes.includes("live") ? "live" : "fixture");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [, setLayout] = useState(0);
  const holder = useRef<HTMLDivElement>(null);
  const frameIndex = Math.round((tMs * (clip.proxy_fps ?? 10)) / 1000);
  const tool: Tool = zoneOn ? "zone" : kind === "box" ? "box" : "none";

  useEffect(() => {
    let cancelled = false;
    api.frame(clip.clip_id, tMs).then((f) => !cancelled && setImage(f.image_url)).catch(() => setImage(null));
    return () => { cancelled = true; };
  }, [clip.clip_id, tMs]);

  useEffect(() => {
    const observer = new ResizeObserver(() => setLayout((n) => n + 1));
    if (holder.current) observer.observe(holder.current);
    return () => observer.disconnect();
  }, []);

  function point(e: React.PointerEvent) {
    const bounds = holder.current!.getBoundingClientRect();
    return toMediaPixel(e.clientX - bounds.left, e.clientY - bounds.top, containRect(bounds.width, bounds.height, width, height), width, height);
  }

  function onPointerDown(e: React.PointerEvent) {
    const p = point(e);
    if (!p) return;
    if (tool === "box") {
      e.currentTarget.setPointerCapture?.(e.pointerId);
      setDrag(p);
      setBox(null);
    } else if (tool === "zone" && zone.length < MAX_ZONE_POINTS) {
      setZone((z) => [...z, [p.x, p.y]]);
    }
  }

  async function start() {
    setBusy(true);
    setError(null);
    const target = kind === "box" && box
      ? { kind: "box" as const, frame_index: frameIndex, proxy_width_px: width, proxy_height_px: height, xyxy_exclusive: box, label: phrase || "selected object" }
      : { kind: "concept" as const, phrase: phrase.trim(), frame_index: frameIndex };
    const zones = zoneOn && zone.length >= 3 ? [{ label: zoneLabel.trim() || "zone", polygon: zone }] : [];
    try {
      const job = await api.createAnalysis(clip.clip_id, { target, zones, mode, idempotency_key: idempotencyKey("analysis") });
      onStarted(job.analysis_id!);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The analysis could not start.");
      setBusy(false);
    }
  }

  const rect = holder.current ? containRect(holder.current.clientWidth, holder.current.clientHeight, width, height) : null;
  const zoneIncomplete = zoneOn && zone.length > 0 && zone.length < 3;
  const hint = tool === "zone"
    ? "Tap or click the corners of the zone on the frame."
    : tool === "box"
      ? box ? `Box ${box.join(", ")} in proxy pixels. Drag again to redraw.` : "Drag on the frame to draw a box around the object."
      : "SAM 3.1 looks for the phrase on this frame. If several things match, you choose one.";
  return (
    <section className="panel selector" aria-labelledby="selector-title">
      <div className="selector-grid">
        <div className="selector-media">
          <div className={`selector-frame tool-${tool}`} ref={holder} onPointerDown={onPointerDown}
            style={width && height ? { aspectRatio: `${width} / ${height}` } : undefined}
            onPointerMove={(e) => { if (tool === "box" && drag) { const p = point(e); if (p) setBox(boxFromDrag(drag, p, width, height)); } }}
            onPointerUp={() => setDrag(null)} onPointerCancel={() => setDrag(null)}>
            {image && <img src={image} alt={`Frame at ${clipTime(tMs)}`} draggable={false} />}
            {rect && rect.width > 0 && (box || zone.length > 0) && (
              <svg className="selector-overlay" style={{ left: rect.x, top: rect.y, width: rect.width, height: rect.height }}
                viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
                {zone.length > 0 && (
                  <>
                    <polygon points={zone.map((p) => p.join(",")).join(" ")} className="zone-shape" />
                    {zone.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={Math.max(4, width / 160)} className="zone-point" />)}
                  </>
                )}
                {box && <rect x={box[0]} y={box[1]} width={box[2] - box[0]} height={box[3] - box[1]} className="box-shape" />}
              </svg>
            )}
            {tool !== "none" && <span className="frame-mode" aria-hidden="true">{tool === "box" ? "Drawing a box" : "Drawing a zone"}</span>}
          </div>
          <p className="tool-hint" aria-live="polite">{hint}</p>
          <label className="scrub">
            <span className="scrub-label">Frame <span className="mono">{clipTime(tMs)}</span></span>
            <input type="range" min={0} max={clip.duration_ms ?? 0} step={100} value={tMs} onChange={(e) => setTMs(Number(e.target.value))}
              aria-label="Frame time" />
          </label>
        </div>

        <div className="selector-controls">
          <h2 id="selector-title">Select the object to follow</h2>
          <p className="muted">Choose a frame where the object is clearly visible, then describe it or draw a box.</p>
          <fieldset className="field-group">
            <legend>How to identify it</legend>
            <div className="segmented">
              <label className={kind === "concept" ? "active" : ""}>
                <input type="radio" name="identify" checked={kind === "concept"} onChange={() => { setKind("concept"); setZoneOn(false); }} />
                Describe it
              </label>
              <label className={kind === "box" ? "active" : ""}>
                <input type="radio" name="identify" checked={kind === "box"} onChange={() => { setKind("box"); setZoneOn(false); }} />
                Draw a box
              </label>
            </div>
          </fieldset>
          <label className="field">
            <span className="field-label">{kind === "concept" ? "Short noun phrase (one object type)" : "Label for the object"}</span>
            <input value={phrase} maxLength={60} onChange={(e) => setPhrase(e.target.value)} placeholder="plastic bottle" />
          </label>
          <fieldset className="field-group">
            <legend>Destination zone (optional)</legend>
            <label className="check">
              <input type="checkbox" checked={zoneOn} onChange={(e) => setZoneOn(e.target.checked)} />
              <span>Draw a zone, such as a tray or a bin, by clicking its corners on the frame</span>
            </label>
            {zoneOn && (
              <div className="zone-tools">
                <label className="field">
                  <span className="field-label">Zone label</span>
                  <input value={zoneLabel} maxLength={40} onChange={(e) => setZoneLabel(e.target.value)} />
                </label>
                <div className="row">
                  <button type="button" className="secondary" disabled={!zone.length} onClick={() => setZone((z) => z.slice(0, -1))}>Undo point</button>
                  <button type="button" className="secondary" disabled={!zone.length} onClick={() => setZone([])}>Clear zone</button>
                </div>
                <p className="fineprint">
                  {zone.length} of {MAX_ZONE_POINTS} points{zoneIncomplete ? ": add at least 3." : "."} A zone is a neutral region: an object counts
                  as placed in it only when the release and the destination are both visible.
                </p>
              </div>
            )}
          </fieldset>
          <fieldset className="field-group">
            <legend>Run mode</legend>
            <div className="mode-options">
              {modes.map((m) => (
                <label key={m} className={`mode-option mode-option-${m}${mode === m ? " selected" : ""}`}>
                  <input type="radio" name="run-mode" checked={mode === m} onChange={() => setMode(m)} />
                  <span className="mode-title">{MODE_TITLE[m]}</span>
                  <span className="mode-help">{MODE_HELP[m]}</span>
                </label>
              ))}
            </div>
            {!capabilities.live_available && <p className="fineprint">Live analysis is unavailable: {capabilities.live_block_reason}.</p>}
          </fieldset>
          {error && <p className="error" role="alert">{error}</p>}
          <div className="selector-submit">
            <button type="button" className="block" onClick={start} disabled={busy || zoneIncomplete || (kind === "box" ? !box : !phrase.trim())}>
              {busy ? "Starting…" : "Analyse this object"}
            </button>
            <p className="fineprint caps">
              Caps: {usd(capabilities.limits.analysis_cap_microusd)} per analysis, {usd(capabilities.limits.question_cap_microusd)} per question.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
