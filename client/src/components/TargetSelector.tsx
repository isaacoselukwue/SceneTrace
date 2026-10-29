import { useEffect, useRef, useState } from "react";
import { api, ApiError, idempotencyKey, type CapabilitiesOut, type ClipOut, type RunMode } from "../api/client";
import { clipTime, usd } from "../lib/format";
import { boxFromDrag, containRect, toMediaPixel } from "../lib/geometry";

const MODE_HELP: Record<RunMode, string> = {
  live: "Live: SAM 3.1 and Muse Spark run now. Paid calls are reserved against the cost caps first.",
  recorded_real: "Recorded real run: replays a stored live run of this clip without new model calls.",
  fixture: "Development fixture: deterministic code stands in for the models. Not a real analysis.",
};
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
  const [phrase, setPhrase] = useState("bottle");
  const [box, setBox] = useState<[number, number, number, number] | null>(null);
  const [drag, setDrag] = useState<{ x: number; y: number } | null>(null);
  const [zoneOn, setZoneOn] = useState(false);
  const [zoneLabel, setZoneLabel] = useState("tray");
  const [zone, setZone] = useState<[number, number][]>([]);
  const modes = capabilities.available_run_modes.filter((m) => m !== "recorded_real" || clip.analyses.length > 0);
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
  return (
    <section className="panel selector" aria-labelledby="selector-title">
      <h2 id="selector-title">Select the object to follow</h2>
      <div className={`selector-frame tool-${tool}`} ref={holder} onPointerDown={onPointerDown}
        onPointerMove={(e) => { if (tool === "box" && drag) { const p = point(e); if (p) setBox(boxFromDrag(drag, p, width, height)); } }}
        onPointerUp={() => setDrag(null)}>
        {image && <img src={image} alt={`Frame at ${clipTime(tMs)}`} draggable={false} />}
        {rect && rect.width > 0 && (box || zone.length > 0) && (
          <svg className="selector-overlay" style={{ left: rect.x, top: rect.y, width: rect.width, height: rect.height }}
            viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
            {zone.length > 0 && (
              <>
                <polygon points={zone.map((p) => p.join(",")).join(" ")} className="zone-shape" />
                {zone.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={Math.max(3, width / 200)} className="zone-point" />)}
              </>
            )}
            {box && <rect x={box[0]} y={box[1]} width={box[2] - box[0]} height={box[3] - box[1]} className="box-shape" />}
          </svg>
        )}
      </div>
      <label className="scrub">
        Frame time {clipTime(tMs)}
        <input type="range" min={0} max={clip.duration_ms ?? 0} step={100} value={tMs} onChange={(e) => setTMs(Number(e.target.value))} />
      </label>
      <fieldset className="choice">
        <legend>How to identify it</legend>
        <label><input type="radio" checked={kind === "concept"} onChange={() => { setKind("concept"); setZoneOn(false); }} /> Describe it</label>
        <label><input type="radio" checked={kind === "box"} onChange={() => { setKind("box"); setZoneOn(false); }} /> Draw a box on this frame</label>
      </fieldset>
      <label className="field">
        {kind === "concept" ? "Short noun phrase (one object type)" : "Label for the object"}
        <input value={phrase} maxLength={60} onChange={(e) => setPhrase(e.target.value)} placeholder="plastic bottle" />
      </label>
      {kind === "box" && !zoneOn && <p className="fineprint">{box ? `Box ${box.join(", ")} in proxy pixels.` : "Drag on the frame to draw a box around the object."}</p>}
      <fieldset className="choice">
        <legend>Destination zone (optional)</legend>
        <label>
          <input type="checkbox" checked={zoneOn} onChange={(e) => setZoneOn(e.target.checked)} />
          Draw a zone, such as a tray or a bin, by clicking its corners on the frame
        </label>
        {zoneOn && (
          <>
            <label className="field">Zone label
              <input value={zoneLabel} maxLength={40} onChange={(e) => setZoneLabel(e.target.value)} />
            </label>
            <div className="row">
              <button type="button" className="secondary" disabled={!zone.length} onClick={() => setZone((z) => z.slice(0, -1))}>Undo point</button>
              <button type="button" className="secondary" disabled={!zone.length} onClick={() => setZone([])}>Clear zone</button>
            </div>
            <p className="fineprint">
              {zone.length} of {MAX_ZONE_POINTS} points{zoneIncomplete ? ": add at least 3." : "."} A zone is a neutral region: an object counts as placed
              in it only when the release and the destination are both visible.
            </p>
          </>
        )}
      </fieldset>
      <fieldset className="choice">
        <legend>Run mode</legend>
        {modes.map((m) => (
          <label key={m}><input type="radio" checked={mode === m} onChange={() => setMode(m)} /> {MODE_HELP[m]}</label>
        ))}
      </fieldset>
      {!capabilities.live_available && <p className="fineprint">Live analysis is unavailable: {capabilities.live_block_reason}.</p>}
      <p className="fineprint">Caps: {usd(capabilities.limits.analysis_cap_microusd)} per analysis, {usd(capabilities.limits.question_cap_microusd)} per question.</p>
      {error && <p className="error" role="alert">{error}</p>}
      <button type="button" onClick={start} disabled={busy || zoneIncomplete || (kind === "box" ? !box : !phrase.trim())}>
        {busy ? "Starting…" : "Analyse this object"}
      </button>
    </section>
  );
}
