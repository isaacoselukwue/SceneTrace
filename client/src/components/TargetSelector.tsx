import { useEffect, useRef, useState } from "react";
import { api, ApiError, idempotencyKey, type CapabilitiesOut, type ClipOut, type RunMode } from "../api/client";
import { clipTime, usd } from "../lib/format";
import { boxFromDrag, containRect, toMediaPixel } from "../lib/geometry";

const MODE_HELP: Record<RunMode, string> = {
  live: "Live: SAM 3.1 and Muse Spark run now. Paid calls are reserved against the cost caps first.",
  recorded_real: "Recorded real run: replays a stored live run of this clip without new model calls.",
  fixture: "Development fixture: deterministic code stands in for the models. Not a real analysis.",
};

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
  const modes = capabilities.available_run_modes.filter((m) => m !== "recorded_real" || clip.analyses.length > 0);
  const [mode, setMode] = useState<RunMode>(modes.includes("live") ? "live" : "fixture");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const holder = useRef<HTMLDivElement>(null);
  const frameIndex = Math.round((tMs * (clip.proxy_fps ?? 10)) / 1000);

  useEffect(() => {
    let cancelled = false;
    api.frame(clip.clip_id, tMs).then((f) => !cancelled && setImage(f.image_url)).catch(() => setImage(null));
    return () => { cancelled = true; };
  }, [clip.clip_id, tMs]);

  function point(e: React.PointerEvent) {
    const el = holder.current!;
    const bounds = el.getBoundingClientRect();
    return toMediaPixel(e.clientX - bounds.left, e.clientY - bounds.top, containRect(bounds.width, bounds.height, width, height), width, height);
  }

  async function start() {
    setBusy(true);
    setError(null);
    const target = kind === "box" && box
      ? { kind: "box" as const, frame_index: frameIndex, proxy_width_px: width, proxy_height_px: height, xyxy_exclusive: box, label: phrase || "selected object" }
      : { kind: "concept" as const, phrase: phrase.trim(), frame_index: frameIndex };
    try {
      const job = await api.createAnalysis(clip.clip_id, { target, zones: [], mode, idempotency_key: idempotencyKey("analysis") });
      onStarted(job.analysis_id!);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The analysis could not start.");
      setBusy(false);
    }
  }

  const rect = holder.current ? containRect(holder.current.clientWidth, holder.current.clientHeight, width, height) : null;
  const scale = rect && width ? rect.width / width : 0;
  return (
    <section className="panel selector" aria-labelledby="selector-title">
      <h2 id="selector-title">Select the object to follow</h2>
      <div className="selector-frame" ref={holder}
        onPointerDown={(e) => { if (kind !== "box") return; const p = point(e); if (p) { setDrag(p); setBox(null); } }}
        onPointerMove={(e) => { if (kind === "box" && drag) { const p = point(e); if (p) setBox(boxFromDrag(drag, p, width, height)); } }}
        onPointerUp={() => setDrag(null)}>
        {image && <img src={image} alt={`Frame at ${clipTime(tMs)}`} draggable={false} />}
        {box && rect && (
          <div className="drawn-box" style={{ left: rect.x + box[0] * scale, top: rect.y + box[1] * scale,
            width: (box[2] - box[0]) * scale, height: (box[3] - box[1]) * scale }} aria-hidden="true" />
        )}
      </div>
      <label className="scrub">
        Frame time {clipTime(tMs)}
        <input type="range" min={0} max={clip.duration_ms ?? 0} step={100} value={tMs} onChange={(e) => setTMs(Number(e.target.value))} />
      </label>
      <fieldset className="choice">
        <legend>How to identify it</legend>
        <label><input type="radio" checked={kind === "concept"} onChange={() => setKind("concept")} /> Describe it</label>
        <label><input type="radio" checked={kind === "box"} onChange={() => setKind("box")} /> Draw a box on this frame</label>
      </fieldset>
      <label className="field">
        {kind === "concept" ? "Short noun phrase (one object type)" : "Label for the object"}
        <input value={phrase} maxLength={60} onChange={(e) => setPhrase(e.target.value)} placeholder="plastic bottle" />
      </label>
      {kind === "box" && <p className="fineprint">{box ? `Box ${box.join(", ")} in proxy pixels.` : "Drag on the frame to draw a box around the object."}</p>}
      <fieldset className="choice">
        <legend>Run mode</legend>
        {modes.map((m) => (
          <label key={m}><input type="radio" checked={mode === m} onChange={() => setMode(m)} /> {MODE_HELP[m]}</label>
        ))}
      </fieldset>
      {!capabilities.live_available && <p className="fineprint">Live analysis is unavailable: {capabilities.live_block_reason}.</p>}
      <p className="fineprint">Caps: {usd(capabilities.limits.analysis_cap_microusd)} per analysis, {usd(capabilities.limits.question_cap_microusd)} per question.</p>
      {error && <p className="error" role="alert">{error}</p>}
      <button type="button" onClick={start} disabled={busy || (kind === "box" ? !box : !phrase.trim())}>
        {busy ? "Starting…" : "Analyse this object"}
      </button>
    </section>
  );
}
