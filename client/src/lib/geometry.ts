// Letterbox-aware mapping between the displayed media rectangle and proxy pixels.

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** The rectangle a media of mediaW x mediaH occupies inside a box with object-fit: contain. */
export function containRect(boxW: number, boxH: number, mediaW: number, mediaH: number): Rect {
  if (boxW <= 0 || boxH <= 0 || mediaW <= 0 || mediaH <= 0) return { x: 0, y: 0, width: 0, height: 0 };
  const scale = Math.min(boxW / mediaW, boxH / mediaH);
  const width = mediaW * scale;
  const height = mediaH * scale;
  return { x: (boxW - width) / 2, y: (boxH - height) / 2, width, height };
}

/** Map a point inside the box to integer proxy pixels, or null when it falls in the letterbox bars. */
export function toMediaPixel(
  px: number,
  py: number,
  rect: Rect,
  mediaW: number,
  mediaH: number,
): { x: number; y: number } | null {
  if (px < rect.x || py < rect.y || px > rect.x + rect.width || py > rect.y + rect.height) return null;
  const x = Math.floor(((px - rect.x) * mediaW) / rect.width);
  const y = Math.floor(((py - rect.y) * mediaH) / rect.height);
  return { x: Math.min(mediaW - 1, Math.max(0, x)), y: Math.min(mediaH - 1, Math.max(0, y)) };
}

/** Normalise a dragged box to [x0, y0, x1, y1] with exclusive right/bottom edges. */
export function boxFromDrag(a: { x: number; y: number }, b: { x: number; y: number }, mediaW: number, mediaH: number) {
  const x0 = Math.max(0, Math.min(a.x, b.x));
  const y0 = Math.max(0, Math.min(a.y, b.y));
  const x1 = Math.min(mediaW, Math.max(a.x, b.x) + 1);
  const y1 = Math.min(mediaH, Math.max(a.y, b.y) + 1);
  return [x0, y0, x1, y1] as [number, number, number, number];
}

export function frameAt(tSeconds: number, fps: number): number {
  return Math.max(0, Math.round(tSeconds * fps));
}

export interface LabelRequest {
  id: string;
  box: number[];
  width: number;
}

/** Place labels in priority order beside or inside their boxes without overlapping; a label with no free spot is left out. */
export function placeLabels(requests: LabelRequest[], labelHeight: number, frameW: number, frameH: number): Map<string, { x: number; y: number }> {
  const placed = new Map<string, { x: number; y: number }>();
  const taken: Rect[] = [];
  const overlaps = (a: Rect, b: Rect) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
  for (const { id, box, width } of requests) {
    const [x0, y0, x1, y1] = box;
    const spots = [[x0, y0 - labelHeight], [x0, y0], [x0, y1 - labelHeight], [x1 - width, y0 - labelHeight]];
    for (const [x, y] of spots) {
      const spot = { x: Math.min(Math.max(0, x), frameW - width), y: Math.min(Math.max(0, y), frameH - labelHeight), width, height: labelHeight };
      if (taken.some((other) => overlaps(spot, other))) continue;
      taken.push(spot);
      placed.set(id, { x: spot.x, y: spot.y });
      break;
    }
  }
  return placed;
}
