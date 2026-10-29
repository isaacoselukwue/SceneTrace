import unittest
from pydantic import ValidationError
from reference_core.models import AnswerDraft, Claim, EventRecord, Evidence
from reference_core.claims import ClaimValidationError, compile_answer, validate_claim

SOURCE = "a" * 64


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.event = EventRecord(event_id="E1", revision=1, object_id="O1", event_type="pick_up",
                                 actor_id="P1", zone_id=None, t_start_ms=1000, t_end_ms=1500,
                                 status="supported", evidence_ids=["V1", "V2"], reason_codes=[])
        self.claim = Claim(claim_id="C1", event_id="E1", event_revision=1, object_id="O1",
                           predicate="pick_up", actor_id="P1", zone_id=None, t_start_ms=1000,
                           t_end_ms=1500, certainty="supported")
        self.evidence = {
            key: Evidence(evidence_id=key, source_sha256=SOURCE, asset_path=f"evidence/{key}.png",
                          asset_sha256="b"*64, t_ms=t, role=role, derived=False)
            for key, t, role in [("V1", 900, "before"), ("V2", 1700, "after")]
        }

    def validate(self, claim=None, event=None, evidence=None):
        return validate_claim(claim or self.claim, {"E1": event or self.event},
                              self.evidence if evidence is None else evidence,
                              object_id="O1", source_sha256=SOURCE)

    def test_valid_claim(self):
        self.assertEqual(self.validate().event_id, "E1")

    def test_missing_event(self):
        with self.assertRaises(ClaimValidationError):
            validate_claim(self.claim, {}, self.evidence, object_id="O1", source_sha256=SOURCE)

    def test_rejected_event(self):
        with self.assertRaises(ClaimValidationError):
            self.validate(event=self.event.model_copy(update={"status": "rejected"}))

    def test_missing_evidence(self):
        with self.assertRaises(ClaimValidationError):
            self.validate(evidence={})

    def test_cross_clip_evidence(self):
        self.evidence["V1"] = self.evidence["V1"].model_copy(update={"source_sha256": "c"*64})
        with self.assertRaises(ClaimValidationError):
            self.validate()

    def test_possible_claim_allowed(self):
        self.validate(claim=self.claim.model_copy(update={"certainty": "possible"}),
                      event=self.event.model_copy(update={"status": "plausible"}))

    def test_free_text_rejected(self):
        with self.assertRaises(ValidationError):
            Claim.model_validate({**self.claim.model_dump(), "text": "P2 stole it"})

    def test_invalid_id_rejected(self):
        with self.assertRaises(ValidationError):
            Claim.model_validate({**self.claim.model_dump(), "actor_id": "real-person-name"})

    def test_reversed_event_rejected(self):
        with self.assertRaises(ValidationError):
            EventRecord.model_validate({**self.event.model_dump(), "t_end_ms": 0})

    def test_zone_required(self):
        with self.assertRaises(ValidationError):
            EventRecord.model_validate({**self.event.model_dump(), "event_type": "place_in_zone"})

    def test_string_timestamp_rejected(self):
        with self.assertRaises(ValidationError):
            Claim.model_validate({**self.claim.model_dump(), "t_start_ms": "1000"})

    def test_compile(self):
        lines = compile_answer(AnswerDraft(claims=[self.claim], unknown_codes=[]), {"E1": self.event},
                               self.evidence, object_id="O1", source_sha256=SOURCE, allowed_unknown_codes=set())
        self.assertEqual(lines, ["P1 picked up O1 at 00:01.000 to 00:01.500 [E1]."])

    def test_duplicate_claim_id(self):
        with self.assertRaises(ClaimValidationError):
            compile_answer(AnswerDraft(claims=[self.claim, self.claim], unknown_codes=[]),
                           {"E1": self.event}, self.evidence, object_id="O1", source_sha256=SOURCE,
                           allowed_unknown_codes=set())

    def test_invented_abstention_reason(self):
        with self.assertRaises(ClaimValidationError):
            compile_answer(AnswerDraft(claims=[], unknown_codes=["BUDGET_EXHAUSTED"]), {}, {},
                           object_id="O1", source_sha256=SOURCE, allowed_unknown_codes=set())

    def test_authorised_abstention(self):
        lines = compile_answer(AnswerDraft(claims=[], unknown_codes=["CURRENT_LOCATION_UNKNOWN"]), {}, {},
                               object_id="O1", source_sha256=SOURCE,
                               allowed_unknown_codes={"CURRENT_LOCATION_UNKNOWN"})
        self.assertEqual(lines, ["The object's current location is unknown."])


def mismatch_test(field, value):
    def test(self):
        with self.assertRaises(ClaimValidationError):
            self.validate(claim=self.claim.model_copy(update={field: value}))
    return test


for name, field, value in [
    ("wrong_actor", "actor_id", "P2"), ("wrong_time", "t_start_ms", 1200),
    ("wrong_end_time", "t_end_ms", 1400), ("wrong_predicate", "predicate", "put_down"),
    ("wrong_object", "object_id", "O2"), ("stale_revision", "event_revision", 2),
    ("wrong_zone", "zone_id", "Z1")
]:
    setattr(ClaimTests, "test_" + name, mismatch_test(field, value))


def status_test(status):
    def test(self):
        with self.assertRaises(ClaimValidationError):
            self.validate(event=self.event.model_copy(update={"status": status}))
    return test


for status in ["plausible", "contested"]:
    setattr(ClaimTests, "test_overclaim_" + status, status_test(status))
