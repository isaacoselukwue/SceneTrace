from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

WIDTH, HEIGHT = 960, 540
SCALE = 2
FPS = 25

SKIN = (224, 172, 132)
HAIR = (58, 42, 32)
OUTLINE = (40, 40, 48)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def smooth(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


@dataclass
class Person:
    name: str
    shirt: tuple[int, int, int]
    trousers: tuple[int, int, int]
    height_px: float = 250.0
    keyframes: list[tuple[float, float]] = field(default_factory=list)
    reach: list[tuple[float, float, float]] = field(default_factory=list)

    def x_at(self, t: float) -> float | None:
        if not self.keyframes or t < self.keyframes[0][0] or t > self.keyframes[-1][0]:
            return None
        for (t0, x0), (t1, x1) in pairwise(self.keyframes):
            if t0 <= t <= t1:
                return lerp(x0, x1, (t - t0) / (t1 - t0)) if t1 > t0 else x1
        return self.keyframes[-1][1]

    def velocity_at(self, t: float) -> float:
        a, b = self.x_at(t - 0.02), self.x_at(t + 0.02)
        if a is None or b is None:
            return 0.0
        return (b - a) / 0.04

    def reach_amount(self, t: float) -> float:
        amount = 0.0
        for t0, t1, peak in self.reach:
            if t0 <= t <= t1:
                mid = (t0 + t1) / 2
                amount = max(amount, peak * (smooth((t - t0) / (mid - t0)) if t < mid else 1.0))
        return amount


@dataclass
class Scenario:
    clip_id: str
    duration_s: float
    description: str
    people: list[Person]
    bottles: list[dict]
    labels: list[dict]
    questions: list[dict]


def draw_background(d: ImageDraw.ImageDraw, s: int) -> None:
    for y in range(0, 300 * s, 4):
        c = int(lerp(196, 232, y / (300 * s)))
        d.rectangle([0, y, WIDTH * s, y + 4], fill=(c - 40, c - 10, 245))
    d.rectangle([0, 230 * s, WIDTH * s, 330 * s], fill=(96, 140, 88))
    for tx in (90, 300, 700, 880):
        d.rectangle([(tx - 9) * s, 150 * s, (tx + 9) * s, 300 * s], fill=(102, 72, 50))
        d.ellipse([(tx - 70) * s, 60 * s, (tx + 70) * s, 210 * s], fill=(70, 128, 72))
    d.rectangle([0, 300 * s, WIDTH * s, 440 * s], fill=(122, 170, 96))
    d.rectangle([0, 440 * s, WIDTH * s, HEIGHT * s], fill=(196, 186, 168))
    for px in range(0, WIDTH, 80):
        d.line([px * s, 440 * s, (px - 30) * s, HEIGHT * s], fill=(180, 170, 152), width=2 * s)
    bx0, bx1 = 360, 640
    for i in range(3):
        y = 352 + i * 14
        d.rounded_rectangle([bx0 * s, y * s, bx1 * s, (y + 9) * s], radius=3 * s, fill=(150, 98, 58), outline=OUTLINE, width=s)
    d.rounded_rectangle([(bx0 - 6) * s, 404 * s, (bx1 + 6) * s, 414 * s], radius=3 * s, fill=(164, 108, 64), outline=OUTLINE, width=s)
    for lx in (bx0 + 12, bx1 - 20):
        d.rectangle([lx * s, 414 * s, (lx + 8) * s, 446 * s], fill=(60, 60, 66))
    d.rounded_rectangle([780 * s, 372 * s, 830 * s, 446 * s], radius=6 * s, fill=(46, 104, 70), outline=OUTLINE, width=s)
    d.rectangle([774 * s, 366 * s, 836 * s, 376 * s], fill=(36, 84, 56))


def draw_bottle(d: ImageDraw.ImageDraw, s: int, cx: float, bottom: float, h: float = 44.0) -> None:
    w = h * 0.36
    body_top = bottom - h * 0.72
    x0, x1 = cx - w / 2, cx + w / 2
    d.rounded_rectangle([x0 * s, body_top * s, x1 * s, bottom * s], radius=int(w * 0.3 * s), fill=(170, 214, 238), outline=(70, 110, 140), width=s)
    d.polygon([(x0 * s, (body_top + 2) * s), ((cx - w * 0.22) * s, (bottom - h * 0.86) * s),
               ((cx + w * 0.22) * s, (bottom - h * 0.86) * s), (x1 * s, (body_top + 2) * s)],
              fill=(170, 214, 238), outline=(70, 110, 140))
    d.rectangle([(cx - w * 0.2) * s, (bottom - h) * s, (cx + w * 0.2) * s, (bottom - h * 0.86) * s], fill=(245, 245, 250), outline=OUTLINE, width=s)
    d.rectangle([x0 * s, (bottom - h * 0.48) * s, x1 * s, (bottom - h * 0.30) * s], fill=(40, 96, 190))
    d.line([(x0 + w * 0.22) * s, (body_top + 4) * s, (x0 + w * 0.22) * s, (bottom - 4) * s], fill=(235, 248, 255), width=max(1, int(w * 0.12 * s)))


def limb(d: ImageDraw.ImageDraw, s: int, a: tuple[float, float], b: tuple[float, float], width: float, colour) -> None:
    d.line([a[0] * s, a[1] * s, b[0] * s, b[1] * s], fill=colour, width=int(width * s))
    r = width / 2
    for p in (a, b):
        d.ellipse([(p[0] - r) * s, (p[1] - r) * s, (p[0] + r) * s, (p[1] + r) * s], fill=colour)


def draw_person(d: ImageDraw.ImageDraw, s: int, p: Person, t: float, feet_y: float = 500.0) -> tuple[float, float] | None:
    x = p.x_at(t)
    if x is None:
        return None
    H = p.height_px
    v = p.velocity_at(t)
    facing = 1 if v >= 0 else -1
    reach = p.reach_amount(t)
    if reach > 0:
        facing = -1
    walking = abs(v) > 5
    phase = (x / (H * 0.42)) * math.pi if walking else 0.0
    swing = math.sin(phase) * (0.42 if walking else 0.0)
    hip = (x, feet_y - H * 0.48)
    shoulder = (x, feet_y - H * 0.80)
    head_c = (x + facing * H * 0.01, feet_y - H * 0.91)
    leg = H * 0.24
    for sign, shade in ((1, 0.82), (-1, 1.0)):
        a = sign * swing
        knee = (hip[0] + math.sin(a) * leg, hip[1] + math.cos(a) * leg)
        a2 = a - abs(math.sin(phase)) * 0.25 * sign if walking else 0.0
        foot = (knee[0] + math.sin(a2) * leg, knee[1] + math.cos(a2) * leg)
        col = tuple(int(c * shade) for c in p.trousers)
        limb(d, s, hip, knee, H * 0.075, col)
        limb(d, s, knee, foot, H * 0.065, col)
        d.ellipse([(foot[0] - H * 0.045) * s, (foot[1] - H * 0.02) * s, (foot[0] + facing * H * 0.06 + H * 0.02) * s, (foot[1] + H * 0.02) * s], fill=(30, 30, 34))
    d.rounded_rectangle([(x - H * 0.085) * s, shoulder[1] * s, (x + H * 0.085) * s, (hip[1] + H * 0.03) * s], radius=int(H * 0.04 * s), fill=p.shirt, outline=OUTLINE, width=s)
    arm = H * 0.17
    hand = None
    for sign, shade in ((-1, 0.82), (1, 1.0)):
        if sign == 1 and reach > 0:
            ang = math.radians(lerp(10, 70, reach))
            elbow = (shoulder[0] - math.sin(ang) * arm, shoulder[1] + math.cos(ang) * arm)
            ang2 = math.radians(lerp(10, 40, reach))
            hand_p = (elbow[0] - math.sin(ang2) * arm, elbow[1] + math.cos(ang2) * arm)
        else:
            a = -sign * swing * 0.8
            elbow = (shoulder[0] + math.sin(a) * arm, shoulder[1] + math.cos(a) * arm)
            hand_p = (elbow[0] + math.sin(a * 1.2) * arm, elbow[1] + math.cos(a * 1.2) * arm)
        col = tuple(int(c * shade) for c in p.shirt)
        limb(d, s, shoulder, elbow, H * 0.055, col)
        limb(d, s, elbow, hand_p, H * 0.05, SKIN)
        if sign == 1:
            hand = hand_p
    r = H * 0.065
    d.ellipse([(head_c[0] - r) * s, (head_c[1] - r) * s, (head_c[0] + r) * s, (head_c[1] + r) * s], fill=SKIN, outline=OUTLINE, width=s)
    d.chord([(head_c[0] - r) * s, (head_c[1] - r * 1.05) * s, (head_c[0] + r) * s, (head_c[1] + r * 0.6) * s], 180, 360, fill=HAIR)
    return hand


def scenarios() -> dict[str, Scenario]:
    bottle_x = 520.0
    passer = Person("passer", shirt=(52, 104, 196), trousers=(48, 52, 70),
                    keyframes=[(3.0, -60.0), (9.0, 1020.0)])
    picker = Person("picker", shirt=(226, 128, 46), trousers=(70, 64, 58),
                    keyframes=[(10.5, -60.0), (12.6, 566.0), (14.6, 566.0), (18.4, 1020.0)],
                    reach=[(12.7, 14.5, 1.0)])
    pickup = Scenario(
        clip_id="synthetic_bench_pickup",
        duration_s=20.0,
        description="Drawn animation. A bottle stands on a bench. One figure walks past in front of it; a second figure lifts it and carries it away.",
        people=[passer, picker],
        bottles=[{"x": bottle_x, "held_by": "picker", "grab_t": 13.6, "hidden_after": None}],
        labels=[
            {"event_type": "pick_up", "t_start_ms": 13000, "t_end_ms": 14600, "actor": "picker", "observable": True, "zone": None},
        ],
        questions=[
            {"intent": "actor", "question": "Who picked up the bottle?", "observable": True},
            {"intent": "history", "question": "What happened to the bottle?", "observable": True},
            {"intent": "interaction_window", "question": "Did anyone touch the bottle between 00:04 and 00:08?", "observable": True},
        ],
    )
    blocker = Person("blocker", shirt=(120, 60, 150), trousers=(40, 44, 60),
                     keyframes=[(3.0, -60.0), (5.4, bottle_x + 4), (10.0, bottle_x + 4), (13.0, 1020.0)])
    occlusion = Scenario(
        clip_id="synthetic_bench_occlusion",
        duration_s=16.0,
        description="Drawn animation. A figure stops directly in front of the bottle, hiding it. When the figure leaves, the bottle is no longer on the bench. The removal is never visible.",
        people=[blocker],
        bottles=[{"x": bottle_x, "held_by": None, "grab_t": None, "hidden_after": 7.5}],
        labels=[
            {"event_type": "pick_up", "t_start_ms": 5400, "t_end_ms": 10000, "actor": None, "observable": False, "zone": None},
        ],
        questions=[
            {"intent": "actor", "question": "Who took the bottle?", "observable": False},
            {"intent": "last_seen", "question": "Where was the bottle last seen?", "observable": True},
        ],
    )
    return {pickup.clip_id: pickup, occlusion.clip_id: occlusion}


def render(scenario: Scenario, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit("ffmpeg is required")
    frames = int(round(scenario.duration_s * FPS))
    with tempfile.TemporaryDirectory(prefix="scenetrace-render-") as tmp:
        tmp_path = Path(tmp)
        for i in range(frames):
            t = i / FPS
            img = Image.new("RGB", (WIDTH * SCALE, HEIGHT * SCALE))
            d = ImageDraw.Draw(img)
            draw_background(d, SCALE)
            held: list[tuple[dict, str]] = []
            for b in scenario.bottles:
                if b["hidden_after"] is not None and t >= b["hidden_after"]:
                    continue
                if b["held_by"] and b["grab_t"] is not None and t >= b["grab_t"]:
                    held.append((b, b["held_by"]))
                    continue
                draw_bottle(d, SCALE, b["x"], 404.0)
            hands: dict[str, tuple[float, float] | None] = {}
            for p in scenario.people:
                hands[p.name] = draw_person(d, SCALE, p, t)
            for _bottle, holder in held:
                hand = hands.get(holder)
                if hand is not None:
                    draw_bottle(d, SCALE, hand[0], hand[1] + 14.0)
            frame = img.resize((WIDTH, HEIGHT), Image.LANCZOS).filter(ImageFilter.SMOOTH_MORE)
            frame.save(tmp_path / f"f{i:05d}.png")
        target = out_dir / f"{scenario.clip_id}.mp4"
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-framerate", str(FPS),
               "-i", str(tmp_path / "f%05d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-preset", "medium", "-crf", "20", "-movflags", "+faststart", str(target)]
        subprocess.run(cmd, check=True, timeout=600)
    label = {
        "clip_id": scenario.clip_id,
        "media_kind": "synthetic_render",
        "not_real_footage": True,
        "description": scenario.description,
        "fps": FPS,
        "width": WIDTH,
        "height": HEIGHT,
        "duration_ms": int(scenario.duration_s * 1000),
        "renderer": "tools/render_synthetic_clip.py",
        "events": scenario.labels,
        "actors": [{"actor": p.name, "present_ms": [int(p.keyframes[0][0] * 1000), int(p.keyframes[-1][0] * 1000)]}
                   for p in scenario.people],
        "questions": scenario.questions,
        "note": "Ground truth describes what the renderer drew. Synthetic results are smoke tests, not vision benchmark evidence.",
    }
    (out_dir / f"{scenario.clip_id}.label.json").write_text(json.dumps(label, indent=2) + "\n", encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Render deterministic, clearly synthetic cartoon test clips with ground-truth labels. They are drawn animations, never real footage, and are not counted in real-video evaluation.")
    parser.add_argument("--out", type=Path, default=Path("samples/synthetic"))
    parser.add_argument("--clip", choices=sorted(scenarios()), action="append")
    args = parser.parse_args()
    chosen = args.clip or sorted(scenarios())
    for clip_id in chosen:
        path = render(scenarios()[clip_id], args.out)
        print(path)


if __name__ == "__main__":
    main()
