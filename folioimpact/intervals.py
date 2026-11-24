"""Integer interval helpers.

Intervals are half-open ``[start, end)``.  Coordinates are ordinary Python
integers, so epoch values spanning 10**9 and larger need no time-unit
enumeration.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

Interval = tuple[int, int]


def normalize_interval(value, path: str) -> Interval:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise ValueError(f"{path} must be [start, end]")
    start, end = value
    if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
        raise ValueError(f"{path} endpoints must be integers")
    if start >= end:
        raise ValueError(f"{path} must satisfy start < end for a half-open interval")
    return (start, end)


def intersection(a: Interval, b: Interval) -> Optional[Interval]:
    start = max(a[0], b[0])
    end = min(a[1], b[1])
    return (start, end) if start < end else None


def intersects(a: Interval, b: Interval) -> bool:
    return max(a[0], b[0]) < min(a[1], b[1])


def lists_intersect(a: Sequence[Interval], b: Sequence[Interval]) -> bool:
    for x in a:
        for y in b:
            if intersects(x, y):
                return True
    return False


def subtract_interval(source: Interval, blackout: Interval) -> list[Interval]:
    """Return source pieces after removing a half-open blackout interval."""
    overlap = intersection(source, blackout)
    if overlap is None:
        return [source]
    pieces: list[Interval] = []
    if source[0] < overlap[0]:
        pieces.append((source[0], overlap[0]))
    if overlap[1] < source[1]:
        pieces.append((overlap[1], source[1]))
    return pieces


def subtract_blackouts(source: Interval, blackouts: Iterable[Interval]) -> list[Interval]:
    pieces = [source]
    for blackout in sorted(blackouts):
        next_pieces: list[Interval] = []
        for piece in pieces:
            next_pieces.extend(subtract_interval(piece, blackout))
        pieces = next_pieces
    return pieces


def overlap_record(a_id: str, a: Sequence[Interval], b_id: str, b: Sequence[Interval]) -> dict:
    """Return the deterministic first overlap for an evidence record."""
    best = None
    for x in sorted(a):
        for y in sorted(b):
            ov = intersection(x, y)
            if ov is not None and (best is None or ov < tuple(best)):
                best = ov
    return {"between": [a_id, b_id], "interval": list(best) if best else None, "intersects": best is not None}
