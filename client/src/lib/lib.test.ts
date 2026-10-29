import { describe, expect, it } from "vitest";
import type { JobEvent } from "../api/client";
import { explainRejection, whyNotSupported } from "./explain";
import { clipTime, duration, elapsed, interval, usd } from "./format";
import { boxFromDrag, containRect, frameAt, toMediaPixel } from "./geometry";
import { mergeEvents } from "./useJobEvents";

describe("letterbox geometry", () => {
  it("fits a wide video in a tall box with bars above and below", () => {
    const rect = containRect(400, 400, 960, 540);
    expect(rect.width).toBe(400);
    expect(rect.height).toBeCloseTo(225);
    expect(rect.y).toBeCloseTo(87.5);
  });

  it("maps clicks through the media rectangle and ignores the bars", () => {
    const rect = containRect(400, 400, 960, 540);
    expect(toMediaPixel(200, 50, rect, 960, 540)).toBeNull();
    expect(toMediaPixel(200, 200, rect, 960, 540)).toEqual({ x: 480, y: 270 });
  });

  it("maps portrait media in a landscape box", () => {
    const rect = containRect(800, 450, 540, 960);
    const point = toMediaPixel(rect.x + rect.width - 0.001, rect.y + rect.height - 0.001, rect, 540, 960);
    expect(point).toEqual({ x: 539, y: 959 });
  });

  it("normalises drags to exclusive boxes clamped to the frame", () => {
    expect(boxFromDrag({ x: 30, y: 40 }, { x: 10, y: 5 }, 960, 540)).toEqual([10, 5, 31, 41]);
    expect(boxFromDrag({ x: 959, y: 539 }, { x: 950, y: 530 }, 960, 540)).toEqual([950, 530, 960, 540]);
    expect(frameAt(13.55, 10)).toBe(136);
  });
});

describe("formatting", () => {
  it("formats clip times and intervals without dashes", () => {
    expect(clipTime(13500)).toBe("00:13.500");
    expect(interval(13500, 13600)).toBe("00:13.500 to 00:13.600");
    expect(interval(0, 0)).toBe("00:00.000");
    expect(usd(101422)).toBe("$0.10");
    expect(usd(2500)).toBe("$0.0025");
    expect(usd(null)).toBe("unknown");
  });

  it("switches long durations to minutes and keeps the trace clock compact", () => {
    expect(duration(888)).toBe("888 ms");
    expect(duration(23_105)).toBe("23.1 s");
    expect(duration(59_949)).toBe("59.9 s");
    expect(duration(59_950)).toBe("1 min");
    expect(duration(404_512)).toBe("6 min 45 s");
    expect(duration(undefined)).toBe("not recorded");
    expect(elapsed(4_260)).toBe("0:04.3");
    expect(elapsed(404_512)).toBe("6:44.5");
  });
});

describe("explanations come only from fixed codes", () => {
  it("explains a rejected pass-by", () => {
    expect(whyNotSupported(["PROXIMITY_ONLY", "TARGET_UNCHANGED", "UNKNOWN_CODE"])).toHaveLength(2);
    expect(explainRejection("STALE_EVENT_REVISION")).toMatch(/outdated revision/);
    expect(explainRejection("UNSUPPORTED_ABSTENTION_REASON:BUDGET_EXHAUSTED")).toMatch(/removed/);
  });
});

describe("event stream", () => {
  const event = (seq: number): JobEvent => ({
    job_id: "job", seq, type: "stage.progress", stage: null, summary: "", elapsed_ms: 0, mode: null, created_at: "", data: {},
  });
  it("de-duplicates by sequence and keeps order after a reconnect", () => {
    let events: JobEvent[] = [];
    for (const seq of [1, 2, 3, 2, 3, 4]) events = mergeEvents(events, event(seq));
    expect(events.map((e) => e.seq)).toEqual([1, 2, 3, 4]);
  });
});
