from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, cast

from ..domain import Candidate, Coverage, Entity, FrameMap, Observation, Zone
from .config import DEFAULT_ENGINE_CONFIG, EngineConfig
from .geometry import diagonal, distance, edge_distance, median, median_point, point_in_polygon, to_bp

INTERACTION_TYPES = ("pick_up", "put_down", "place_in_zone")


@dataclass
class Sample:
    frame: int
    t_ms: int
    box: list[int]
    centre: tuple[float, float]
    smooth: tuple[float, float] = (0.0, 0.0)
    stationary: bool = False


@dataclass
class Run:
    start: int
    end: int
    anchor: tuple[float, float]


@dataclass
class Draft:
    kind: str
    t_start: int
    t_end: int
    actors: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    zone: str | None = None


class TargetTrack:
    def __init__(self, observations: list[Observation], config: EngineConfig) -> None:
        self.config = config
        target = sorted((o for o in observations if o.entity_id == "O1"), key=lambda o: o.proxy_frame_index)
        self.frames = target
        self.state = {o.proxy_frame_index: o.detection_state for o in target}
        self.samples = [Sample(o.proxy_frame_index, o.t_ms, list(o.box),
                               (o.centre_milli[0] / 1000, o.centre_milli[1] / 1000) if o.centre_milli else
                               ((o.box[0] + o.box[2]) / 2, (o.box[1] + o.box[3]) / 2))
                        for o in target if o.detection_state == "observed" and o.box]
        diagonals = [diagonal(s.box) for s in self.samples]
        self.scale = max(float(config.min_target_scale_px), median(diagonals)) if diagonals else float(config.min_target_scale_px)
        self.people: dict[str, dict[int, list[int]]] = defaultdict(dict)
        for o in observations:
            if o.entity_id.startswith("P") and o.detection_state == "observed" and o.box:
                self.people[o.entity_id][o.proxy_frame_index] = list(o.box)
        self.t_of = {o.proxy_frame_index: o.t_ms for o in observations}
        self.last_t = max(self.t_of.values()) if self.t_of else 0

    def segments(self) -> list[list[Sample]]:
        out: list[list[Sample]] = []
        current: list[Sample] = []
        for sample in self.samples:
            if current:
                gap = range(current[-1].frame + 1, sample.frame)
                if len(gap) > self.config.max_gap_samples or any(self.state.get(f) == "unknown" for f in gap):
                    out.append(current)
                    current = []
            current.append(sample)
        if current:
            out.append(current)
        for seg in out:
            for i, s in enumerate(seg):
                window = [seg[j].centre for j in range(max(0, i - 1), min(len(seg), i + 2))]
                s.smooth = median_point(window)
        return out

    def near(self, person: str, frame: int, box: list[int]) -> float | None:
        other = self.people[person].get(frame)
        return None if other is None else edge_distance(box, other) / self.scale

    def actors_near(self, t0: int, t1: int, reference: list[int] | None = None) -> list[tuple[str, float, int]]:
        limit = self.config.interaction_distance_bp / 10_000
        found = []
        target_boxes = {s.frame: s.box for s in self.samples}
        for person, boxes in self.people.items():
            dists = []
            for frame, pbox in boxes.items():
                if not t0 <= self.t_of.get(frame, -1) <= t1:
                    continue
                tbox = target_boxes.get(frame, reference)
                if tbox is None:
                    continue
                dists.append(edge_distance(tbox, pbox) / self.scale)
            close = [d for d in dists if d <= limit]
            if len(close) >= self.config.proximity_persistence_samples:
                found.append((person, min(close), len(close)))
        return sorted(found, key=lambda a: (a[1], a[0]))


def _runs(seg: list[Sample], scale: float, config: EngineConfig) -> list[Run]:
    w = config.stationary_window_samples
    limit = scale * config.stationary_displacement_bp / 10_000
    for i in range(0, len(seg) - w + 1):
        window = seg[i:i + w]
        med = median_point([s.smooth for s in window])
        if all(distance(s.smooth, med) <= limit for s in window):
            for s in window:
                s.stationary = True
    runs: list[Run] = []
    i = 0
    while i < len(seg):
        if seg[i].stationary:
            j = i
            while j + 1 < len(seg) and seg[j + 1].stationary and distance(seg[j + 1].centre, seg[j].centre) <= 2 * limit:
                j += 1
            runs.append(Run(i, j, median_point([s.centre for s in seg[i:j + 1]])))
            i = j + 1
        else:
            i += 1
    return runs


def _competing(actors: list[tuple[str, float, int]], config: EngineConfig) -> bool:
    return len(actors) >= 2 and actors[1][1] - actors[0][1] < config.association_margin_bp / 10_000


def _drafts(track: TargetTrack, zones: list[Zone], config: EngineConfig) -> list[Draft]:
    drafts: list[Draft] = []
    trigger = track.scale * config.movement_trigger_bp / 10_000
    segments = track.segments()
    if not segments:
        return drafts
    all_runs = [_runs(seg, track.scale, config) for seg in segments]
    first, last = segments[0][0], segments[-1][-1]
    drafts.append(Draft("first_seen", first.t_ms, first.t_ms))
    carried = False
    for index, (seg, runs) in enumerate(zip(segments, all_runs, strict=True)):
        nxt = segments[index + 1] if index + 1 < len(segments) else None
        if index > 0:
            prev, prev_runs = segments[index - 1], all_runs[index - 1]
            ended_still = bool(prev_runs) and prev_runs[-1].end == len(prev) - 1
            if ended_still and not carried and distance(seg[0].centre, prev_runs[-1].anchor) >= trigger:
                drafts.append(_lost_draft(track, prev[-1], seg[0].t_ms))
        for r_index, run in enumerate(runs):
            if run.start > 0 and (carried or (r_index == 0 and index > 0)):
                before = seg[run.start - 1]
                held = run.end - run.start + 1
                lingering = [a for a in track.actors_near(seg[run.start].t_ms, seg[run.end].t_ms) if a[2] >= held - 1]
                if held >= config.release_stationary_samples or not lingering:
                    actors = track.actors_near(before.t_ms - 1000, seg[run.start].t_ms + 500)
                    zone = next((z.zone_id for z in zones if point_in_polygon(run.anchor, z.polygon)), None)
                    codes = ["COMPETING_ACTORS"] if _competing(actors, config) else []
                    drafts.append(Draft("place_in_zone" if zone else "put_down", before.t_ms, seg[run.start].t_ms,
                                        [a[0] for a in actors], codes, zone))
                    carried = False
            limit = runs[r_index + 1].start if r_index + 1 < len(runs) else len(seg)
            trigger_at = next((j for j in range(run.end + 1, limit) if distance(seg[j].smooth, run.anchor) >= trigger), None)
            if trigger_at is None and r_index + 1 < len(runs) and distance(runs[r_index + 1].anchor, run.anchor) >= trigger:
                trigger_at = runs[r_index + 1].start
            carried_during_run = carried
            if trigger_at is not None and not carried:
                t0, t1 = seg[run.end].t_ms, seg[trigger_at].t_ms
                actors = track.actors_near(t0 - 1000, t1 + 500)
                codes = ["TARGET_MOVED"] + (["COMPETING_ACTORS"] if _competing(actors, config) else [])
                drafts.append(Draft("pick_up", t0, t1, [a[0] for a in actors], codes))
                carried = True
            if not carried_during_run:
                at_segment_end = run.end == len(seg) - 1
                returns_here = nxt is not None and distance(nxt[0].centre, run.anchor) < trigger
                changes = trigger_at is not None or (at_segment_end and not returns_here)
                drafts.extend(_pass_bys(track, seg[run.start:run.end + 1], changes, config))
    final_runs = all_runs[-1]
    rests_at_end = bool(final_runs) and final_runs[-1].end == len(segments[-1]) - 1
    rested_before_brief_sighting = (not final_runs and len(segments) > 1 and bool(all_runs[-2])
                                    and all_runs[-2][-1].end == len(segments[-2]) - 1)
    if (rests_at_end or rested_before_brief_sighting) and not carried and last.t_ms < track.last_t:
        tail_end = min(track.last_t, last.t_ms + config.evidence_half_window_ms)
        carrier = _moving_with(track, segments[-1], all_runs[-2][-1].anchor, config) if rested_before_brief_sighting else None
        if carrier is not None:
            drafts.append(Draft("pick_up", segments[-2][-1].t_ms, last.t_ms, [carrier],
                                ["POSSIBLE_OCCLUSION", "TARGET_LOST", "MOVED_WITH_ACTOR"]))
        elif track.actors_near(last.t_ms - 1000, tail_end, last.box):
            drafts.append(_lost_draft(track, last, tail_end))
    drafts.append(Draft("last_seen", last.t_ms, last.t_ms,
                        codes=["PARTIAL_SCOPE"] if any(s == "unknown" for s in track.state.values()) else []))
    return drafts


def _moving_with(track: TargetTrack, sightings: list[Sample], resting_anchor: tuple[float, float], config: EngineConfig) -> str | None:
    still_limit = track.scale * config.stationary_displacement_bp / 10_000
    if len(sightings) < 2 or distance(sightings[-1].centre, resting_anchor) <= still_limit:
        return None
    object_dx = sightings[-1].centre[0] - sightings[0].centre[0]
    object_dy = sightings[-1].centre[1] - sightings[0].centre[1]
    margin = config.association_margin_bp / 10_000
    touching, nearby = [], []
    for person, boxes in track.people.items():
        person_boxes = [boxes.get(s.frame) for s in sightings]
        gaps = [None if box is None else edge_distance(s.box, box) / track.scale for s, box in zip(sightings, person_boxes, strict=True)]
        first, last = person_boxes[0], person_boxes[-1]
        if first is not None and last is not None and all(gap == 0 for gap in gaps):
            person_dx = (last[0] + last[2] - first[0] - first[2]) / 2
            person_dy = (last[1] + last[3] - first[1] - first[3]) / 2
            if object_dx * person_dx + object_dy * person_dy > 0:
                touching.append(person)
                continue
        if any(gap is not None and gap <= margin for gap in gaps):
            nearby.append(person)
    return touching[0] if len(touching) == 1 and not nearby else None


def _lost_draft(track: TargetTrack, last_seen: Sample, t_end: int) -> Draft:
    gap = [f for f, s in track.state.items() if track.t_of.get(f, -1) > last_seen.t_ms and track.t_of.get(f, -1) <= t_end]
    codes = ["POSSIBLE_OCCLUSION", "TARGET_LOST"]
    if any(track.state.get(f) == "unknown" for f in gap):
        codes.append("INCOMPLETE_COVERAGE")
    actors = track.actors_near(last_seen.t_ms - 1000, t_end, last_seen.box)
    return Draft("pick_up", last_seen.t_ms, t_end, [a[0] for a in actors], codes)


def _pass_bys(track: TargetTrack, run: list[Sample], run_ends_in_change: bool, config: EngineConfig) -> list[Draft]:
    limit = config.interaction_distance_bp / 10_000
    drafts = []
    run_end_t = run[-1].t_ms
    for person in sorted(track.people, key=lambda p: int(p[1:])):
        streak: list[Sample] = []
        for sample in [*run, None]:
            d = None if sample is None else track.near(person, sample.frame, sample.box)
            if d is not None and d <= limit:
                streak.append(sample)  # type: ignore[arg-type]
                continue
            if len(streak) >= config.proximity_persistence_samples:
                if not (run_ends_in_change and streak[-1].t_ms >= run_end_t - 1000):
                    drafts.append(Draft("pick_up", streak[0].t_ms, streak[-1].t_ms, [person],
                                        ["PROXIMITY_ONLY", "TARGET_STATIONARY"]))
            streak = []
    return drafts


def _merge(drafts: list[Draft], config: EngineConfig) -> list[Draft]:
    merged: list[Draft] = []
    for draft in sorted(drafts, key=lambda d: (d.t_start, d.kind)):
        same = next((m for m in reversed(merged) if m.kind == draft.kind and m.zone == draft.zone
                     and ("PROXIMITY_ONLY" in m.codes) == ("PROXIMITY_ONLY" in draft.codes)
                     and draft.t_start - m.t_end <= config.merge_window_ms and draft.kind in INTERACTION_TYPES), None)
        if same is None:
            merged.append(Draft(draft.kind, draft.t_start, draft.t_end, list(draft.actors), list(draft.codes), draft.zone))
            continue
        same.t_end = max(same.t_end, draft.t_end)
        same.actors = sorted(set(same.actors) | set(draft.actors), key=lambda p: int(p[1:]))
        same.codes = sorted(set(same.codes) | set(draft.codes))
    return merged


def generate_candidates(*, observations: list[Observation], entities: list[Entity], coverage: list[Coverage],
                        zones: list[Zone], frame_map: FrameMap, config: EngineConfig = DEFAULT_ENGINE_CONFIG,
                        report: dict | None = None) -> list[Candidate]:
    del entities, coverage
    track = TargetTrack(observations, config)
    drafts = _merge(_drafts(track, zones, config), config)
    interactions = [d for d in drafts if d.kind in INTERACTION_TYPES]
    if len(interactions) > config.max_candidates:
        dropped = interactions[config.max_candidates:]
        drafts = [d for d in drafts if d not in dropped]
        if report is not None:
            report["capped"] = len(dropped)
    last_t = frame_map.entries[-1].t_ms if frame_map.entries else track.last_t
    candidates: list[Candidate] = []
    for number, draft in enumerate(sorted(drafts, key=lambda d: (d.t_start, d.kind == "last_seen", d.t_end)), start=1):
        half = config.evidence_half_window_ms if draft.kind in INTERACTION_TYPES else 0
        w0, w1 = max(0, draft.t_start - half), min(last_t, draft.t_end + half)
        codes = list(draft.codes)
        if draft.kind in INTERACTION_TYPES and (w0 > draft.t_start - half or w1 < draft.t_end + half):
            codes.append("BOUNDARY_WINDOW")
        if draft.kind in INTERACTION_TYPES and not draft.actors:
            codes.append("ACTOR_NOT_ESTABLISHED")
        candidate = Candidate(candidate_id=f"K{number}", candidate_type=cast(Any, draft.kind), t_start_ms=draft.t_start,
                              t_end_ms=draft.t_end, window_start_ms=w0, window_end_ms=w1,
                              actor_candidates=sorted(set(draft.actors), key=lambda p: int(p[1:])), zone_id=draft.zone,
                              reason_codes=sorted(set(codes)))
        candidates.append(candidate.model_copy(update={"cues": assessment_cue_table(candidate, observations, config)}))
    return candidates


def assessment_cue_table(candidate: Candidate, observations: list[Observation],
                         config: EngineConfig = DEFAULT_ENGINE_CONFIG) -> dict:
    track = TargetTrack(observations, config)
    window = [o for o in track.frames if candidate.window_start_ms <= o.t_ms <= candidate.window_end_ms]
    before = [s for s in track.samples if candidate.window_start_ms <= s.t_ms < candidate.t_start_ms]
    after = [s for s in track.samples if candidate.t_end_ms < s.t_ms <= candidate.window_end_ms]
    cues: dict = {
        "target_scale_px": int(round(track.scale)),
        "window_samples": len(window),
        "target_observed_samples": sum(1 for o in window if o.detection_state == "observed"),
        "target_not_detected_samples": sum(1 for o in window if o.detection_state == "not_detected"),
        "target_unknown_samples": sum(1 for o in window if o.detection_state == "unknown"),
        "observed_before_samples": len(before),
        "observed_after_samples": len(after),
    }
    if before and after:
        start = median_point([s.centre for s in before])
        end = median_point([s.centre for s in after])
        cues["displacement_before_to_after_bp"] = to_bp(distance(start, end) / track.scale)
    else:
        cues["displacement_before_to_after_bp"] = None
    inside = [s for s in track.samples if candidate.window_start_ms <= s.t_ms <= candidate.window_end_ms]
    if inside:
        origin = inside[0].centre
        cues["max_displacement_in_window_bp"] = to_bp(max(distance(origin, s.centre) for s in inside) / track.scale)
    for person in sorted(track.people, key=lambda p: int(p[1:])):
        dists = [track.near(person, s.frame, s.box) for s in inside]
        dists = [d for d in dists if d is not None]
        if dists:
            cues[f"min_edge_distance_bp:{person}"] = to_bp(min(dists))
            cues[f"samples_within_interaction:{person}"] = sum(1 for d in dists if d <= config.interaction_distance_bp / 10_000)
    return cues
