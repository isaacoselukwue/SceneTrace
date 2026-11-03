from __future__ import annotations

from scenetrace.evaluation.matching import Interval, actor_scores, event_scores, match, ratio


def _i(key: str, start: int, end: int, kind: str = "pick_up", actor: str | None = None) -> Interval:
    return Interval(key, kind, start, end, actor)


def test_one_prediction_cannot_explain_two_labels_and_duplicates_are_false_positives() -> None:
    labels = [_i("L1", 1000, 1600), _i("L2", 1300, 1900)]
    preds = [_i("P1", 1200, 1700)]
    scores = event_scores(preds, labels)
    assert (scores["tp"], scores["fp"], scores["fn"]) == (1, 0, 1)
    dup = event_scores([_i("P1", 1000, 1600), _i("P2", 1050, 1650)], [_i("L1", 1000, 1600)])
    assert (dup["tp"], dup["fp"], dup["fn"]) == (1, 1, 0)


def test_eligibility_uses_iou_or_both_errors_and_type() -> None:
    assert match([_i("P", 13500, 13600)], [_i("L", 13000, 14600)])
    assert not match([_i("P", 13500, 13600)], [_i("L", 11000, 14600)])
    assert not match([_i("P", 1000, 1600, "put_down")], [_i("L", 1000, 1600)])


def test_maximum_cardinality_reassigns_to_cover_more_labels() -> None:
    labels = [_i("L1", 1000, 2000), _i("L2", 2600, 3200)]
    preds = [_i("P1", 1900, 2800), _i("P2", 1000, 2000)]
    assert len(match(preds, labels)) == 2


def test_zero_denominators_are_null_not_perfect() -> None:
    assert ratio(0, 0) is None
    scores = event_scores([], [])
    assert scores["precision"] is None and scores["recall"] is None and scores["f1"] is None


def test_actor_accuracy_counts_abstentions_as_not_correct() -> None:
    pairs = [(_i("P1", 0, 1, actor="P2"), _i("L1", 0, 1, actor="picker")),
             (_i("P2", 5, 6, actor=None), _i("L2", 5, 6, actor="picker")),
             (_i("P3", 9, 9, actor=None), _i("L3", 9, 9, actor=None))]
    scores = actor_scores(pairs, {"P2": "picker"})
    assert (scores["observable_matched"], scores["correct"], scores["assigned"], scores["accuracy"]) == (2, 1, 1, 0.5)
