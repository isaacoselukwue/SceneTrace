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
