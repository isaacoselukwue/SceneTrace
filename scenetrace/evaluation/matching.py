from __future__ import annotations

from dataclasses import dataclass

ACTIONS = ("pick_up", "put_down", "place_in_zone")
MIN_IOU = 0.30
MAX_ERROR_MS = 1_000
MAX_ACTION_MS = 5_000


@dataclass(frozen=True)
class Interval:
    key: str
    event_type: str
    t_start_ms: int
    t_end_ms: int
    actor: str | None = None


def iou(a: Interval, b: Interval) -> float:
    inter = max(0, min(a.t_end_ms, b.t_end_ms) - max(a.t_start_ms, b.t_start_ms))
    union = max(a.t_end_ms, b.t_end_ms) - min(a.t_start_ms, b.t_start_ms)
    return inter / union if union > 0 else (1.0 if a.t_start_ms == b.t_start_ms else 0.0)


def eligible(pred: Interval, label: Interval) -> bool:
    if pred.event_type != label.event_type:
        return False
    if pred.t_end_ms - pred.t_start_ms > MAX_ACTION_MS and label.t_end_ms - label.t_start_ms <= MAX_ACTION_MS:
        return False
    close = abs(pred.t_start_ms - label.t_start_ms) <= MAX_ERROR_MS and abs(pred.t_end_ms - label.t_end_ms) <= MAX_ERROR_MS
    return iou(pred, label) >= MIN_IOU or close


def match(predictions: list[Interval], labels: list[Interval]) -> list[tuple[Interval, Interval]]:
    def preference(p: Interval, label: Interval) -> tuple:
        error = abs(p.t_start_ms - label.t_start_ms) + abs(p.t_end_ms - label.t_end_ms)
        return (-iou(p, label), error, label.key)

    edges = {p.key: sorted((label for label in labels if eligible(p, label)), key=lambda label: preference(p, label))
             for p in predictions}
    owner: dict[str, Interval] = {}

    def augment(p: Interval, seen: set[str]) -> bool:
        for label in edges[p.key]:
            if label.key in seen:
                continue
            seen.add(label.key)
            if label.key not in owner or augment(owner[label.key], seen):
                owner[label.key] = p
                return True
        return False

    for p in sorted(predictions, key=lambda x: (x.t_start_ms, x.key)):
        augment(p, set())
    by_key = {label.key: label for label in labels}
    return sorted(((p, by_key[k]) for k, p in owner.items()), key=lambda pair: pair[1].t_start_ms)


def ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 4)


def event_scores(predictions: list[Interval], labels: list[Interval]) -> dict:
    pairs = match(predictions, labels)
    tp = len(pairs)
    fp = len(predictions) - tp
    fn = len(labels) - tp
    return {"tp": tp, "fp": fp, "fn": fn, "precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
            "f1": ratio(2 * tp, 2 * tp + fp + fn), "pairs": [(p.key, label.key) for p, label in pairs]}


def actor_scores(pairs: list[tuple[Interval, Interval]], association: dict[str, str]) -> dict:
    observable = [(p, label) for p, label in pairs if label.actor is not None]
    assigned = [(p, label) for p, label in observable if p.actor is not None]
    correct = sum(1 for p, label in assigned if association.get(p.actor or "") == label.actor)
    wrong = len(assigned) - correct
    return {"observable_matched": len(observable), "assigned": len(assigned), "correct": correct, "wrong_confident": wrong,
            "accuracy": ratio(correct, len(observable)), "coverage": ratio(len(assigned), len(observable))}
