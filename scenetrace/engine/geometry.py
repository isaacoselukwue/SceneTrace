from __future__ import annotations

import math
from collections.abc import Sequence


def centre(box: Sequence[int]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def diagonal(box: Sequence[int]) -> float:
    return math.hypot(box[2] - box[0], box[3] - box[1])


def edge_distance(a: Sequence[int], b: Sequence[int]) -> float:
    dx = max(0, max(a[0] - b[2], b[0] - a[2]))
    dy = max(0, max(a[1] - b[3], b[1] - a[3]))
    return math.hypot(dx, dy)


def distance(p: tuple[float, float], q: tuple[float, float]) -> float:
    return math.hypot(p[0] - q[0], p[1] - q[1])


def median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        raise ValueError("median of nothing")
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def median_point(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    return (median([p[0] for p in points]), median([p[1] for p in points]))


def point_in_polygon(point: tuple[float, float], polygon: Sequence[Sequence[int]]) -> bool:
    x, y = point
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def to_bp(value: float) -> int:
    return int(round(value * 10_000))
