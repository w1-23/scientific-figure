"""Deterministic SVG regions on the edges of occupied pixel cells.

No fitting is used: removing only collinear vertices preserves thin features,
point contacts and holes exactly, including with SVG's default nonzero fill.
"""
from __future__ import annotations

import math
from numbers import Real

import numpy as np

_MAX_DIMENSION = 512
_MAX_BOUNDARY_EDGES = 262_144
_MAX_OUTPUT_SEGMENTS = 65_536


def pixel_paths(mask, *, tolerance: float = 0.35) -> list[str]:
    """Return closed compound SVG paths for a finite real 2-D binary mask.

    Nonzero entries occupy [x, x+1] x [y, y+1]; input is never modified.
    Empty masks return []; otherwise one path contains all boundary subpaths.
    Commands are absolute M/L/Z (a subset of M/L/C/Z). Foreground lies to the
    right of every directed edge, giving opposite winding for holes. At a
    diagonal contact, right turns take priority, keeping diagonally adjacent
    foreground cells on separate local boundary branches.

    ``tolerance`` must be finite and nonnegative. It is an upper bound on
    allowed geometric error, not a requested smoothing amount: this conservative
    implementation has zero error for every tolerance and only merges collinear
    edges. In particular, increasing it never narrows a one-pixel feature.

    Each dimension is limited to 512; at most 262,144 exposed unit edges and
    65,536 output line segments (including Z closures) are accepted. Invalid or
    excessive input, or inconsistent boundary traversal, raises ValueError;
    output is never silently truncated. Runtime/memory are bounded by mask area
    plus boundary size (with sorting for deterministic traversal).
    """
    if isinstance(tolerance, (bool, np.bool_)) or not isinstance(tolerance, Real):
        raise ValueError("tolerance must be finite and nonnegative")
    try:
        valid_tolerance = math.isfinite(tolerance) and tolerance >= 0
    except (OverflowError, TypeError, ValueError):
        valid_tolerance = False
    if not valid_tolerance:
        raise ValueError("tolerance must be finite and nonnegative")
    try:
        data = np.asarray(mask)
    except (TypeError, ValueError) as exc:
        raise ValueError("mask must be a finite real 2-D array") from exc
    if data.ndim != 2 or data.dtype.kind not in "buif":
        raise ValueError("mask must be a finite real 2-D array")
    if max(data.shape) > _MAX_DIMENSION:
        raise ValueError(f"mask dimensions must be at most {_MAX_DIMENSION} x {_MAX_DIMENSION}")
    if not np.isfinite(data).all():
        raise ValueError("mask must contain only finite values")
    occupied = data != 0
    if not occupied.any():
        return []

    # Cancel shared cell edges before constructing the directed boundary graph.
    padded = np.pad(occupied, 1)
    exposed = (
        occupied & ~padded[:-2, 1:-1],   # top: east
        occupied & ~padded[1:-1, 2:],    # right: south
        occupied & ~padded[2:, 1:-1],    # bottom: west
        occupied & ~padded[1:-1, :-2],   # left: north
    )
    boundary_count = sum(int(np.count_nonzero(side)) for side in exposed)
    if boundary_count > _MAX_BOUNDARY_EDGES:
        raise ValueError(f"boundary edge budget exceeded ({_MAX_BOUNDARY_EDGES})")

    stride = occupied.shape[1] + 1
    outgoing: dict[int, int] = {}
    # Direction bits E/S/W/N also encode clockwise turn order in SVG coordinates.
    for direction, (side, dx, dy) in enumerate(zip(exposed, (0, 1, 1, 0), (0, 0, 1, 1))):
        ys, xs = np.nonzero(side)
        for vertex in ((ys + dy) * stride + xs + dx).tolist():
            outgoing[vertex] = outgoing.get(vertex, 0) | (1 << direction)

    remaining = outgoing.copy()
    steps = (1, stride, -1, -stride)
    parts = []
    segment_count = 0
    visited = 0
    for start in sorted(outgoing):
        while remaining.get(start, 0):
            first_bit = remaining[start] & -remaining[start]
            first_direction = first_bit.bit_length() - 1
            vertex, direction = start, first_direction
            corners = []
            while True:
                bit = 1 << direction
                available = remaining.get(vertex, 0)
                if not available & bit:
                    raise ValueError("inconsistent pixel boundary: edge revisited")
                remaining[vertex] = available & ~bit
                visited += 1
                if visited > boundary_count:
                    raise ValueError("inconsistent pixel boundary: traversal budget exceeded")
                end = vertex + steps[direction]
                exits = outgoing.get(end, 0)
                # Use the original graph, including the consumed starting edge:
                # choosing only unused exits would splice diagonal loops together.
                next_direction = None
                for candidate in ((direction + 1) % 4, direction,
                                  (direction - 1) % 4, (direction + 2) % 4):
                    if exits & (1 << candidate):
                        next_direction = candidate
                        break
                if next_direction is None:
                    raise ValueError("inconsistent pixel boundary: open ring")
                if next_direction != direction:
                    corners.append(end)
                    segment_count += 1
                    if segment_count > _MAX_OUTPUT_SEGMENTS:
                        raise ValueError(f"output segment budget exceeded ({_MAX_OUTPUT_SEGMENTS})")
                if end == start and next_direction == first_direction:
                    break
                vertex, direction = end, next_direction
            if len(corners) < 4:
                raise ValueError("inconsistent pixel boundary: degenerate ring")
            # Stable seam at the topmost, then leftmost corner, independent of
            # array strides and dictionary/set iteration order.
            seam = min(range(len(corners)), key=corners.__getitem__)
            corners = corners[seam:] + corners[:seam]
            coordinates = [f"{v % stride},{v // stride}" for v in corners]
            parts.append("M" + "L".join(coordinates) + "Z")
    if visited != boundary_count:
        raise ValueError("inconsistent pixel boundary: unvisited edges")
    return ["".join(parts)]
