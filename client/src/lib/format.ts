export function clipTime(ms: number): string {
  const total = Math.max(0, Math.round(ms));
  const minutes = Math.floor(total / 60_000);
  const seconds = Math.floor((total % 60_000) / 1000);
  const millis = total % 1000;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

export function interval(startMs: number, endMs: number): string {
  return startMs === endMs ? clipTime(startMs) : `${clipTime(startMs)} to ${clipTime(endMs)}`;
}

export function shortInterval(startMs: number, endMs: number): string {
  return startMs === endMs ? clipTime(startMs) : `${clipTime(startMs)}–${clipTime(endMs)}`;
}

export function usd(microusd: number | null | undefined): string {
  if (microusd === null || microusd === undefined) return "unknown";
  const dollars = microusd / 1_000_000;
  return dollars < 0.01 && dollars > 0 ? `$${dollars.toFixed(4)}` : `$${dollars.toFixed(2)}`;
}

export function shortHash(digest: string | null | undefined, length = 10): string {
  return digest ? digest.slice(0, length) : "none";
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "not recorded";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 59_950) return `${(ms / 1000).toFixed(1)} s`;
  const wholeSeconds = Math.round(ms / 1000);
  const minutes = Math.floor(wholeSeconds / 60);
  const seconds = wholeSeconds % 60;
  return seconds ? `${minutes} min ${seconds} s` : `${minutes} min`;
}

export function elapsed(ms: number): string {
  const tenths = Math.round(ms / 100);
  const minutes = Math.floor(tenths / 600);
  const seconds = ((tenths % 600) / 10).toFixed(1).padStart(4, "0");
  return `${minutes}:${seconds}`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export const EVENT_LABELS: Record<string, string> = {
  first_seen: "First observed",
  last_seen: "Last observed",
  pick_up: "Pick-up",
  put_down: "Put-down",
  place_in_zone: "Placed in zone",
};

export const STATUS_LABELS: Record<string, string> = {
  supported: "Supported",
  plausible: "Possible",
  contested: "Contested",
  rejected: "Not supported",
  pending: "Pending",
};
