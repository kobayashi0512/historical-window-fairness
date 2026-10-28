#!/usr/bin/env python3
"""Exact small-instance MILP oracle for pointwise prefix-quota recourse.

Given a previously legal draft and one newly arrived two-candidate unit task,
find the minimum number of old assignments that must change while every prefix
of the updated draft remains within floor/ceiling half quotas. Optional frozen
old tasks model claimed/started cases; infeasibility is returned explicitly.

This is a reference oracle for small cases, not a production allocator.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Iterable, Sequence

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix


Edge = tuple[int, int]


@dataclass(frozen=True)
class OracleResult:
    status: str
    message: str
    minimum_old_recourse: int | None
    assignment: tuple[int, ...] | None
    changed_old_indices: tuple[int, ...] | None
    frozen_old_indices: tuple[int, ...]
    elapsed_seconds: float
    variable_count: int
    constraint_count: int


def _validate_inputs(
    n: int,
    old_edges: Sequence[Edge],
    old_assignment: Sequence[int],
    new_edge: Edge,
    frozen_old_indices: Iterable[int],
) -> tuple[tuple[Edge, ...], tuple[int, ...], tuple[int, ...]]:
    if n < 2:
        raise ValueError("n must be at least 2")
    edges = tuple(old_edges) + (new_edge,)
    if len(old_assignment) != len(old_edges):
        raise ValueError("old_assignment must have one entry per old edge")
    for edge in edges:
        if len(edge) != 2 or not (0 <= edge[0] < edge[1] < n):
            raise ValueError(f"edge must be normalized with 0 <= u < v < n: {edge}")
    for i, (edge, chosen) in enumerate(zip(old_edges, old_assignment)):
        if chosen not in edge:
            raise ValueError(f"old assignment {i} is not eligible for edge {edge}")
    frozen = tuple(sorted(set(frozen_old_indices)))
    if any(i < 0 or i >= len(old_edges) for i in frozen):
        raise ValueError("frozen index outside old task range")
    old_bits = tuple(0 if chosen == edge[0] else 1 for edge, chosen in zip(old_edges, old_assignment))
    return edges, old_bits, frozen


def is_prefix_quota_valid(edges: Sequence[Edge], assignment: Sequence[int], n: int) -> bool:
    """Independent direct floor/ceiling check, recomputing each prefix."""
    if len(edges) != len(assignment):
        return False
    entitlement_twice = [0] * n
    allocated = [0] * n
    for edge, chosen in zip(edges, assignment):
        u, v = edge
        if chosen not in edge:
            return False
        entitlement_twice[u] += 1
        entitlement_twice[v] += 1
        allocated[chosen] += 1
        for p in range(n):
            degree = entitlement_twice[p]
            if not (degree // 2 <= allocated[p] <= (degree + 1) // 2):
                return False
    return True


def solve_min_recourse(
    n: int,
    old_edges: Sequence[Edge],
    old_assignment: Sequence[int],
    new_edge: Edge,
    *,
    frozen_old_indices: Iterable[int] = (),
    time_limit_seconds: float | None = None,
) -> OracleResult:
    """Solve the exact 0-1 MILP and return an explicit status.

    A binary variable x_j selects the upper endpoint for task j. For each
    prefix k and participant p, the allocation count is an integer affine
    function of x_0..x_{k-1}; it is bounded by floor/ceiling(degree/2).
    The objective counts changed old assignments; the new task is uncharged.
    """
    edges, old_bits, frozen = _validate_inputs(
        n, old_edges, old_assignment, new_edge, frozen_old_indices
    )
    t = len(edges)
    old_t = len(old_edges)

    objective = np.zeros(t, dtype=float)
    lower_bounds = np.zeros(t, dtype=float)
    upper_bounds = np.ones(t, dtype=float)
    for j, old_bit in enumerate(old_bits):
        # Omitting an objective constant preserves the minimizer.
        objective[j] = 1.0 if old_bit == 0 else -1.0
        if j in frozen:
            lower_bounds[j] = float(old_bit)
            upper_bounds[j] = float(old_bit)

    row_indices: list[int] = []
    col_indices: list[int] = []
    coefficients: list[float] = []
    lower_constraint: list[float] = []
    upper_constraint: list[float] = []
    row = 0

    # For assignment bit x_j (0=lower endpoint, 1=upper endpoint), the task
    # contributes 1-x_j to its lower endpoint and x_j to its upper endpoint.
    for prefix_length in range(1, t + 1):
        degree = [0] * n
        lower_endpoint_count = [0] * n
        for u, v in edges[:prefix_length]:
            degree[u] += 1
            degree[v] += 1
            lower_endpoint_count[u] += 1
        for participant in range(n):
            low_alloc = degree[participant] // 2
            high_alloc = (degree[participant] + 1) // 2
            constant = lower_endpoint_count[participant]
            lower_constraint.append(float(low_alloc - constant))
            upper_constraint.append(float(high_alloc - constant))
            for j, (u, v) in enumerate(edges[:prefix_length]):
                if participant == u:
                    row_indices.append(row)
                    col_indices.append(j)
                    coefficients.append(-1.0)
                elif participant == v:
                    row_indices.append(row)
                    col_indices.append(j)
                    coefficients.append(1.0)
            row += 1

    matrix = coo_matrix(
        (coefficients, (row_indices, col_indices)), shape=(row, t), dtype=float
    ).tocsr()
    constraints = LinearConstraint(
        matrix, np.asarray(lower_constraint), np.asarray(upper_constraint)
    )
    options: dict[str, float | bool] = {"presolve": True}
    if time_limit_seconds is not None:
        if time_limit_seconds <= 0:
            raise ValueError("time_limit_seconds must be positive")
        options["time_limit"] = float(time_limit_seconds)

    start = perf_counter()
    result = milp(
        c=objective,
        integrality=np.ones(t, dtype=np.int8),
        bounds=Bounds(lower_bounds, upper_bounds),
        constraints=constraints,
        options=options,
    )
    elapsed = perf_counter() - start

    status_by_code = {
        0: "optimal",
        1: "limit",
        2: "infeasible",
        3: "unbounded",
        4: "solver_error",
    }
    status = status_by_code.get(int(result.status), "unknown")
    if status != "optimal" or result.x is None:
        return OracleResult(
            status=status,
            message=str(result.message),
            minimum_old_recourse=None,
            assignment=None,
            changed_old_indices=None,
            frozen_old_indices=frozen,
            elapsed_seconds=elapsed,
            variable_count=t,
            constraint_count=row,
        )

    bits = tuple(1 if value > 0.5 else 0 for value in result.x)
    assignment = tuple(edge[bit] for edge, bit in zip(edges, bits))
    changed = tuple(j for j in range(old_t) if bits[j] != old_bits[j])
    if any(j in frozen for j in changed):
        raise AssertionError("solver changed a frozen old task")
    if not is_prefix_quota_valid(edges, assignment, n):
        raise AssertionError("MILP returned an assignment that fails direct prefix audit")
    return OracleResult(
        status=status,
        message=str(result.message),
        minimum_old_recourse=len(changed),
        assignment=assignment,
        changed_old_indices=changed,
        frozen_old_indices=frozen,
        elapsed_seconds=elapsed,
        variable_count=t,
        constraint_count=row,
    )
