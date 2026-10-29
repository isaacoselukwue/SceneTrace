"""Event engine: synthetic trajectories for control logic, plus real SAM output for the synthetic hero clip.

Synthetic trajectories verify control logic only. They are not model-accuracy evidence.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scenetrace.config import Settings
from scenetrace.domain import AssessmentResult, Candidate, Observation
from scenetrace.engine.candidates import generate_candidates
from scenetrace.engine.observations import build_observations
from scenetrace.engine.promotion import promote

from .support import hero_frame_map, hero_tracking

N = 160
TARGET = [500, 360, 518, 404]  # about 47 px diagonal


def _obs(entity: str, boxes: dict[int, list[int]], *, unknown: set[int] = frozenset(), n: int = N) -> list[Observation]:
    out = []
    for f in range(n):
        box = boxes.get(f)
        state = "observed" if box else ("unknown" if f in unknown else "not_detected")
        centre = (int((box[0] + box[2]) * 500), int((box[1] + box[3]) * 500)) if box else None
        out.append(Observation(analysis_id="ana_t", request_key=f"rk-{entity}", entity_id=entity, track_key=f"c:{entity}",
                               proxy_frame_index=f, t_ms=f * 100, width_px=960, height_px=540, box=box, centre_milli=centre,
                               mask_svg=None, mask_area_px=None, provider_confidence_bp=None, detection_state=state))
    return out


def _shift(box: list[int], dx: float, dy: float) -> list[int]:
    return [int(box[0] + dx), int(box[1] + dy), int(box[2] + dx), int(box[3] + dy)]


def _person_at(x: float) -> list[int]:
    return [int(x - 30), 250, int(x + 30), 500]


def _candidates(observations: list[Observation], zones=()) -> list[Candidate]:
    return generate_candidates(observations=observations, entities=[], coverage=[], zones=list(zones), frame_map=hero_frame_map(N))


def _interactions(cands: list[Candidate]) -> list[Candidate]:
    return [c for c in cands if c.candidate_type not in ("first_seen", "last_seen")]


def _assessment(c: Candidate, verdict="supports", transition="yes", carried="yes", support="yes", actor=None, zone=None):
    return AssessmentResult(candidate_id=c.candidate_id, object_id="O1", visible_before="yes", transition_visible=transition,
                            visible_after="yes", support_changed=support, carried_motion_visible=carried,
                            actor_candidates=c.actor_candidates, actor_assignment=actor, destination_zone=zone,
                            evidence_ids=["V1"], verdict=verdict, reason_codes=["CLEAR_LIFT"] if verdict == "supports" else [])


def _promote(c: Candidate, obs: list[Observation], assessment) -> object:
    record, _basis = promote(candidate=c, assessment=assessment, observations=obs, coverage=[], event_id="E1", revision=1,
                             evidence_ids=["V1"])
    return record


def test_stationary_target_has_only_sightings() -> None:
    obs = _obs("O1", {f: TARGET for f in range(N)})
    cands = _candidates(obs)
    assert [c.candidate_type for c in cands] == ["first_seen", "last_seen"]
    assert _promote(cands[0], obs, None).status == "supported"


def test_pass_by_is_stored_and_rejected_even_if_a_model_says_supports() -> None:
    person = {f: _person_at(200 + (f - 30) * 12) for f in range(30, 80)}
    obs = _obs("O1", {f: TARGET for f in range(N)}) + _obs("P1", person)
    passes = _interactions(_candidates(obs))
    assert len(passes) == 1 and passes[0].candidate_type == "pick_up"
    assert {"PROXIMITY_ONLY", "TARGET_STATIONARY"} <= set(passes[0].reason_codes) and passes[0].actor_candidates == ["P1"]
    record = _promote(passes[0], obs, _assessment(passes[0], actor="P1"))
    assert record.status == "rejected" and record.actor_id is None
    assert {"TARGET_UNCHANGED", "MODEL_SUPPORT_OVERRULED"} <= set(record.reason_codes)


def _lift_scene(competing: bool = False) -> list[Observation]:
    target = {f: TARGET for f in range(60)}
    target.update({f: _shift(TARGET, (f - 60) * 8 + 20, -40) for f in range(60, 110)})
    p1 = {f: _person_at(560) for f in range(40, 60)} | {f: _person_at(560 + (f - 60) * 8 + 20) for f in range(60, 110)}
    obs = _obs("O1", target) + _obs("P1", p1)
    if competing:
        obs += _obs("P2", {f: _person_at(460) for f in range(40, 70)})
    return obs


def test_visible_lift_supported_with_unique_actor() -> None:
    obs = _lift_scene()
    picks = [c for c in _interactions(_candidates(obs)) if "TARGET_MOVED" in c.reason_codes]
    assert len(picks) == 1 and picks[0].actor_candidates == ["P1"]
    record = _promote(picks[0], obs, _assessment(picks[0], actor="P1"))
    assert (record.status, record.actor_id) == ("supported", "P1")
    unclear = _promote(picks[0], obs, _assessment(picks[0], verdict="insufficient", transition="unclear", actor="P1"))
    assert (unclear.status, unclear.actor_id) == ("plausible", None)
    assert _promote(picks[0], obs, None).status == "plausible"


def test_competing_actors_leave_actor_unassigned() -> None:
    obs = _lift_scene(competing=True)
    picks = [c for c in _interactions(_candidates(obs)) if "TARGET_MOVED" in c.reason_codes]
    assert picks and "COMPETING_ACTORS" in picks[0].reason_codes
    record = _promote(picks[0], obs, _assessment(picks[0], actor="P1"))
    assert record.status == "supported" and record.actor_id is None and "ACTOR_NOT_ESTABLISHED" in record.reason_codes


def test_visible_put_down_and_return_trip_give_two_transitions_each() -> None:
    a, b = TARGET, _shift(TARGET, 200, 0)
    target = {f: a for f in range(30)}
    target.update({f: _shift(a, (f - 30) * 10, -30) for f in range(30, 50)})
    target.update({f: b for f in range(50, 90)})
    target.update({f: _shift(b, -(f - 90) * 10, -30) for f in range(90, 110)})
    target.update({f: a for f in range(110, N)})
    person = {f: _person_at(560) for f in range(15, 30)} | {f: _person_at(560 + (f - 30) * 10) for f in range(30, 52)}
    person |= {f: _person_at(760) for f in range(80, 90)} | {f: _person_at(760 - (f - 90) * 10) for f in range(90, 112)}
    obs = _obs("O1", target) + _obs("P1", person)
    kinds = [c.candidate_type for c in _interactions(_candidates(obs))]
    assert kinds == ["pick_up", "put_down", "pick_up", "put_down"]


def test_disappearance_during_unknown_coverage_is_never_supported() -> None:
    target = {f: TARGET for f in range(60)} | {f: _shift(TARGET, 250, 0) for f in range(90, N)}
    person = {f: _person_at(509) for f in range(50, 95)}
    obs = _obs("O1", target, unknown=set(range(60, 90))) + _obs("P1", person)
    lost = [c for c in _interactions(_candidates(obs)) if "POSSIBLE_OCCLUSION" in c.reason_codes]
    assert len(lost) == 1 and "INCOMPLETE_COVERAGE" in lost[0].reason_codes
    record = _promote(lost[0], obs, _assessment(lost[0], actor="P1"))
    assert record.status == "plausible" and record.actor_id is None


def test_occluded_disappearance_without_return_is_plausible_and_unattributed() -> None:
    target = {f: TARGET for f in range(55)}
    blocker = {f: _person_at(509) for f in range(40, 110)}
    obs = _obs("O1", target) + _obs("P1", blocker)
    cands = _interactions(_candidates(obs))
    assert [c.candidate_type for c in cands] == ["pick_up"]  # no contradictory pass-by alongside it
    assert {"POSSIBLE_OCCLUSION", "TARGET_LOST"} <= set(cands[0].reason_codes)
    record = _promote(cands[0], obs, _assessment(cands[0], actor="P1"))
    assert (record.status, record.actor_id) == ("plausible", None) and "MODEL_SUPPORT_OVERRULED" in record.reason_codes


def test_identity_switch_marks_identity_warning() -> None:
    from scenetrace.domain import Coverage, ProviderBox, TrackingResult
    boxes = [ProviderBox(proxy_frame_index=f, provider_object_id="0", box=_person_at(100 + f), frame_width=960,
                         frame_height=540, confidence_bp=9000) for f in list(range(0, 20)) + list(range(40, 60))]
    cov = Coverage(request_key="rk", concept="person", role="person", requested_start_frame=0, requested_end_frame=N - 1,
                   terminal_status="completed")
    result = TrackingResult(request_key="rk", provider_response_id=None, model_id="m", concept="person", role="person",
                            prompt_kind="concept", frame_map_digest="x", records=boxes, coverage=cov, outcome="completed",
                            provider_call_ids=[], raw_digest=None, parser_version="p")
    _obs_out, entities, _ = build_observations(analysis_id="a", clip_id="c", frame_map=hero_frame_map(N), results=[result])
    assert entities[0].identity_warning is True


def test_sparse_output_does_not_invent_motion() -> None:
    obs = _obs("O1", {f: TARGET for f in range(0, N, 4)})
    assert _interactions(_candidates(obs)) == []


def test_stream_truncation_gives_partial_scope_not_absence() -> None:
    obs = _obs("O1", {f: TARGET for f in range(100)}, unknown=set(range(100, N)))
    cands = _candidates(obs)
    last = [c for c in cands if c.candidate_type == "last_seen"][0]
    assert last.t_start_ms == 9900 and "PARTIAL_SCOPE" in last.reason_codes
    assert _interactions(cands) == []  # nobody was near, so an unknown tail is not a pickup


# Real SAM 3.1 output for samples/synthetic/synthetic_bench_pickup.mp4 ---------------------------------
def _hero(db, settings: Settings, **kw):
    target, person, transport, ctx = hero_tracking(db, settings, **kw)
    obs, ents, cov = build_observations(analysis_id=ctx.analysis_id, clip_id="clip_" + "d" * 32,
                                        frame_map=hero_frame_map(), results=[target, person])
    return obs, ents, cov, transport


def test_real_hero_tracks_and_candidates(db, settings: Settings) -> None:
    obs, ents, cov, transport = _hero(db, settings)
    assert [(e.entity_id, e.first_t_ms, e.last_t_ms) for e in ents] == [("O1", 0, 17800), ("P1", 3000, 8900), ("P2", 10500, 18300)]
    assert sum(1 for c in transport.calls if "upload" in c) == 1  # proxy uploaded once per analysis
    cands = generate_candidates(observations=obs, entities=ents, coverage=cov, zones=[], frame_map=hero_frame_map())
    kinds = [(c.candidate_type, c.actor_candidates) for c in cands]
    assert kinds == [("first_seen", []), ("pick_up", ["P1"]), ("pick_up", ["P2"]), ("last_seen", [])]
    pass_by, lift = cands[1], cands[2]
    assert 5000 <= pass_by.t_start_ms <= 7000 and "PROXIMITY_ONLY" in pass_by.reason_codes
    assert 13000 <= lift.t_start_ms <= lift.t_end_ms <= 14600 and "TARGET_MOVED" in lift.reason_codes
    assert _promote(pass_by, obs, _assessment(pass_by, actor="P1")).status == "rejected"
    supported = _promote(lift, obs, _assessment(lift, actor="P2"))
    assert (supported.status, supported.actor_id) == ("supported", "P2")
    wrong_actor = _promote(lift, obs, _assessment(lift, actor="P1"))
    assert wrong_actor.actor_id is None  # the model cannot assign someone geometry does not support
    unclear = _promote(lift, obs, _assessment(lift, verdict="insufficient", transition="unclear"))
    assert unclear.status == "plausible"


def test_real_truncated_stream_is_unknown_not_absent(db, settings: Settings) -> None:
    obs, ents, cov, _ = _hero(db, settings, drop_terminal=True)
    assert all(c.terminal_status == "incomplete" for c in cov)
    states = {o.detection_state for o in obs if o.entity_id == "O1" and o.box is None}
    assert states == {"unknown"}
    row = db.one("SELECT cost_status FROM provider_calls WHERE operation = 'sam_video' LIMIT 1")
    assert row["cost_status"] == "unknown"  # no terminal usage: the reservation is retained


def test_real_selection_on_image(db, settings: Settings) -> None:
    from scenetrace.providers.meta_sam import MetaSamTracker
    from .support import FakeSamTransport, make_ctx
    tracker = MetaSamTracker(transport=FakeSamTransport(), settings=settings, db=db)
    found = tracker.select_instances(make_ctx(db, settings), image_png=b"\x89PNG", width=960, height=540, phrase="bottle",
                                     proxy_frame_index=10)
    assert len(found) == 1 and found[0].box == [512, 361, 528, 404] and found[0].confidence_bp == 9024


def test_real_evidence_extraction_from_sample_clip(db, settings: Settings, tmp_path: Path) -> None:
    from scenetrace.engine.evidence import collect_evidence
    from scenetrace.media.normalise import normalise_clip
    from scenetrace.media.probe import probe_media
    from scenetrace.storage import Storage
    sample = Path(__file__).resolve().parents[2] / "samples" / "synthetic" / "synthetic_bench_pickup.mp4"
    probe = probe_media(sample, settings=settings)
    norm = normalise_clip(sample, tmp_path / "proxy", probe=probe, settings=settings)
    obs, ents, cov, _ = _hero(db, settings)
    lift = [c for c in generate_candidates(observations=obs, entities=ents, coverage=cov, zones=[],
                                           frame_map=norm.frame_map) if "TARGET_MOVED" in c.reason_codes][0]
    storage = Storage(settings.data_dir, db)
    analysis_dir = storage.analysis_dir("owner_local", "clip_" + "d" * 32, "ana_" + "c" * 32)
    items = collect_evidence(analysis_id="ana_" + "c" * 32, owner_id="owner_local", clip_id="clip_" + "d" * 32,
                             candidate=lift, frame_map=norm.frame_map, source_path=sample, source_sha256="a" * 64,
                             observations=obs, storage=storage, analysis_dir=analysis_dir, first_evidence_number=1)
    raw = [i for i in items if not i.evidence.derived]
    annotated = [i for i in items if i.evidence.derived]
    assert len(raw) == len(annotated) == 6
    assert [i.evidence.role for i in raw] == ["before", "before", "during", "during", "after", "after"]
    assert all((i.width, i.height) == (960, 540) for i in raw)
    assert all(r.evidence.asset_sha256 != a.evidence.asset_sha256 for r, a in zip(raw, annotated))
    for item in raw:
        path, media = storage.asset_path(item.asset_id, "owner_local", verify_digest=True)
        assert media == "image/png" and path.name == item.evidence.asset_path.split("/")[-1]


@pytest.mark.parametrize("zone_hit", [True, False])
def test_place_in_zone_requires_destination_confirmation(zone_hit: bool) -> None:
    from scenetrace.domain import Zone
    a, b = TARGET, _shift(TARGET, 250, 0)
    target = {f: a for f in range(30)} | {f: _shift(a, (f - 30) * 12, -30) for f in range(30, 50)} | {f: b for f in range(50, N)}
    person = {f: _person_at(560) for f in range(15, 30)} | {f: _person_at(560 + (f - 30) * 12) for f in range(30, 52)}
    obs = _obs("O1", target) + _obs("P1", person)
    zone = Zone(zone_id="Z1", label="tray", polygon=[(700, 300), (820, 300), (820, 450), (700, 450)])
    placed = [c for c in _interactions(_candidates(obs, [zone])) if c.candidate_type == "place_in_zone"]
    assert placed and placed[0].zone_id == "Z1"
    record = _promote(placed[0], obs, _assessment(placed[0], actor="P1", zone="Z1" if zone_hit else None))
    assert record.status == ("supported" if zone_hit else "plausible") and record.zone_id == "Z1"
