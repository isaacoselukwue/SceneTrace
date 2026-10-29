"""Planted-fault and clean-control cases built from recorded real run records.

Each case is one claim checked in isolation. Faults are deliberate mutations of
claims about real stored events (six categories, five cases each); clean controls
reproduce stored events exactly. These are mutations of a small number of records,
not independent scenes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain import AnswerDraft, Claim
from ..investigator.compiler import compile_draft
from ..store import AnalysisStore

CATEGORIES = ("actor_substitution", "time_substitution", "wrong_predicate_or_citation", "nonexistent_event",
              "certainty_escalation", "stale_revision")


@dataclass
class Case:
    case_id: str
    category: str  # one of CATEGORIES, or "clean"
    analysis_id: str
    claim: Claim
    note: str


def _claim(record: Any, **changes: Any) -> Claim:
    base = dict(claim_id="C1", event_id=record.event_id, event_revision=record.revision, object_id=record.object_id,
                predicate=record.event_type, actor_id=record.actor_id, zone_id=record.zone_id, t_start_ms=record.t_start_ms,
                t_end_ms=record.t_end_ms, certainty="supported" if record.status == "supported" else "possible")
    base.update(changes)
    return Claim.model_validate(base)


def build_cases(store: AnalysisStore, pickup: str, occlusion: str) -> tuple[list[Case], list[Case]]:
    """Cases checked against the current records; the stale-revision cases are built after revisions are appended."""
    p = {k: v.record for k, v in store.current_events(pickup).items()}
    o = {k: v.record for k, v in store.current_events(occlusion).items()}
    lift = next(r for r in p.values() if r.event_type == "pick_up" and r.status == "supported")
    rejected = next(r for r in p.values() if r.event_type == "pick_up" and r.status == "rejected")
    hidden = next(r for r in o.values() if r.event_type == "pick_up")
    p_first = next(r for r in p.values() if r.event_type == "first_seen")
    p_last = next(r for r in p.values() if r.event_type == "last_seen")
    o_first = next(r for r in o.values() if r.event_type == "first_seen")
    o_last = next(r for r in o.values() if r.event_type == "last_seen")
    faults = [
        Case("F01", "actor_substitution", pickup, _claim(lift, actor_id="P1"), "the passer-by blamed for the pickup"),
        Case("F02", "actor_substitution", pickup, _claim(lift, actor_id=None), "actor dropped from a supported actor claim"),
        Case("F03", "actor_substitution", pickup, _claim(lift, actor_id="P3"), "a person track that does not exist"),
        Case("F04", "actor_substitution", occlusion, _claim(hidden, actor_id="P1"), "the occluding person named for a hidden removal"),
        Case("F05", "actor_substitution", pickup, _claim(p_first, actor_id="P2"), "an actor attached to a sighting"),
        Case("F06", "time_substitution", pickup, _claim(lift, t_start_ms=lift.t_start_ms - 1000), "pickup moved one second earlier"),
        Case("F07", "time_substitution", pickup, _claim(lift, t_end_ms=lift.t_end_ms + 900), "pickup end extended"),
        Case("F08", "time_substitution", pickup, _claim(p_first, t_start_ms=1000, t_end_ms=1000), "first sighting moved"),
        Case("F09", "time_substitution", pickup, _claim(p_last, t_start_ms=p_last.t_start_ms - 2000, t_end_ms=p_last.t_end_ms - 2000),
             "last sighting moved earlier"),
        Case("F10", "time_substitution", occlusion, _claim(o_last, t_start_ms=o_last.t_start_ms + 1500, t_end_ms=o_last.t_end_ms + 1500),
             "last sighting moved into the hidden period"),
        Case("F11", "wrong_predicate_or_citation", pickup, _claim(lift, predicate="put_down"), "pickup restated as a put-down"),
        Case("F12", "wrong_predicate_or_citation", pickup, _claim(p_first, predicate="last_seen"), "first sighting cited as the last"),
        Case("F13", "wrong_predicate_or_citation", pickup, _claim(p_last, predicate="pick_up", actor_id=lift.actor_id,
                                                                   t_start_ms=lift.t_start_ms, t_end_ms=lift.t_end_ms),
             "a real pickup claim that cites the last-sighting event"),
        Case("F14", "wrong_predicate_or_citation", occlusion, _claim(hidden, predicate="put_down"), "hidden removal restated as a put-down"),
        Case("F15", "wrong_predicate_or_citation", pickup, _claim(lift, predicate="place_in_zone", zone_id="Z1"), "pickup restated as disposal in a zone"),
        Case("F16", "nonexistent_event", pickup, _claim(lift, event_id="E9"), "citation to an event that does not exist"),
        Case("F17", "nonexistent_event", pickup, _claim(lift, event_id="E99"), "citation to a far-out event number"),
        Case("F18", "nonexistent_event", occlusion, _claim(hidden, event_id="E7"), "citation to an unknown occlusion event"),
        Case("F19", "nonexistent_event", pickup, _claim(p_first, event_id="E5"), "sighting cited by a missing event"),
        Case("F20", "nonexistent_event", occlusion, _claim(o_first, event_id="E4"), "sighting cited by a missing event"),
        Case("F21", "certainty_escalation", occlusion, _claim(hidden, certainty="supported"), "possible hidden removal stated as fact"),
        Case("F22", "certainty_escalation", pickup, _claim(rejected, certainty="supported"), "rejected pass-by stated as fact"),
        Case("F23", "certainty_escalation", pickup, _claim(rejected, certainty="possible"), "rejected pass-by stated as possible"),
        Case("F24", "certainty_escalation", occlusion, _claim(hidden, certainty="supported", actor_id=None), "hidden removal escalated without actor"),
        Case("F25", "certainty_escalation", pickup, _claim(rejected, certainty="supported", actor_id="P1"), "rejected pass-by escalated and blamed"),
    ]
    clean = [
        Case("K01", "clean", pickup, _claim(lift), "supported pickup with actor"),
        Case("K02", "clean", pickup, _claim(p_first), "first sighting"),
        Case("K03", "clean", pickup, _claim(p_last), "last sighting"),
        Case("K04", "clean", occlusion, _claim(o_first), "first sighting"),
        Case("K05", "clean", occlusion, _claim(o_last), "last sighting"),
        Case("K06", "clean", occlusion, _claim(hidden), "hidden removal stated as possible"),
    ]
    return faults, clean


def append_revisions(store: AnalysisStore, pickup: str, occlusion: str) -> tuple[list[Case], list[Case]]:
    """Append an unchanged revision to five events, then build stale-revision faults and fresh-revision controls."""
    stale, fresh = [], []
    targets = [(pickup, "pick_up", "supported"), (pickup, "first_seen", None), (pickup, "last_seen", None),
               (occlusion, "first_seen", None), (occlusion, "last_seen", None)]
    for n, (analysis_id, kind, status) in enumerate(targets, start=26):
        current = store.current_events(analysis_id)
        rev = next(v for v in current.values() if v.record.event_type == kind and (status is None or v.record.status == status))
        record = rev.record
        newer = record.model_copy(update={"revision": record.revision + 1})
        store.append_event_revision(analysis_id, newer, candidate_id=rev.candidate_id, basis={"rule": "EVALUATION_REVISION"})
        stale.append(Case(f"F{n}", "stale_revision", analysis_id, _claim(record), f"{kind} claim citing a superseded revision"))
        fresh.append(Case(f"K{n - 19:02d}", "clean", analysis_id, _claim(newer), f"{kind} claim citing the current revision"))
    return stale, fresh[:4]


def validator_verdict(store: AnalysisStore, case: Case, owner_id: str, source_sha256: str) -> tuple[bool, str | None]:
    """Returns (blocked, reason_code)."""
    compiled = compile_draft(AnswerDraft(claims=[case.claim], unknown_codes=[]), store=store, storage=store.storage,
                             analysis_id=case.analysis_id, owner_id=owner_id, object_id="O1", source_sha256=source_sha256,
                             snapshot=store.snapshot(case.analysis_id)["event_revisions"], allowed=set(), scope=[])
    if compiled.claims:
        return False, None
    return True, compiled.rejected[0]["reason_code"] if compiled.rejected else "BLOCKED"
