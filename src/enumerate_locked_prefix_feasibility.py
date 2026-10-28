#!/usr/bin/env python3
"""Exact small-state enumeration for E2-L1 frozen-prefix feasibility."""

from __future__ import annotations

import itertools
import json
import time
from collections import defaultdict
from pathlib import Path


def legal(edges, choices, n):
    if len(edges) != len(choices):
        return False
    degree = [0] * n
    allocated = [0] * n
    for (u, v), chosen in zip(edges, choices):
        if chosen not in (u, v):
            return False
        degree[u] += 1
        degree[v] += 1
        allocated[chosen] += 1
        for p in range(n):
            if not (degree[p] // 2 <= allocated[p] <= (degree[p] + 1) // 2):
                return False
    return True


def legal_choices(edges, n):
    for bits in itertools.product((0, 1), repeat=len(edges)):
        choices = tuple(edge[bit] for edge, bit in zip(edges, bits))
        if legal(edges, choices, n):
            yield choices


def run(n_values=(3, 4), max_old_length=3):
    result_rows = {}
    witnesses = {}
    start = time.perf_counter()
    total_old_plans = 0
    total_extension_cases = 0
    lock_counts = defaultdict(lambda: {"total": 0, "feasible": 0, "infeasible": 0,
                                       "minimum_mutable_recourse_histogram": defaultdict(int)})

    for n in n_values:
        pair_types = tuple(itertools.combinations(range(n), 2))
        for old_length in range(max_old_length + 1):
            for old_edges in itertools.product(pair_types, repeat=old_length):
                for old_assignment in legal_choices(old_edges, n):
                    total_old_plans += 1
                    for new_edge in pair_types:
                        total_extension_cases += 1
                        all_edges = old_edges + (new_edge,)
                        for locked_prefix in range(old_length + 1):
                            key = (n, old_length, locked_prefix)
                            summary = lock_counts[key]
                            summary["total"] += 1
                            feasible_costs = []
                            for final_assignment in legal_choices(all_edges, n):
                                if final_assignment[:locked_prefix] != old_assignment[:locked_prefix]:
                                    continue
                                edits = sum(
                                    final_assignment[i] != old_assignment[i]
                                    for i in range(locked_prefix, old_length)
                                )
                                feasible_costs.append(edits)
                            if feasible_costs:
                                summary["feasible"] += 1
                                best = min(feasible_costs)
                                summary["minimum_mutable_recourse_histogram"][str(best)] += 1
                            else:
                                summary["infeasible"] += 1
                                witnesses.setdefault(key, {
                                    "n": n,
                                    "old_edges": old_edges,
                                    "old_assignment": old_assignment,
                                    "new_edge": new_edge,
                                    "locked_prefix_length": locked_prefix,
                                })

    for key, summary in sorted(lock_counts.items()):
        row = {
            "n": key[0],
            "old_length": key[1],
            "locked_prefix_length": key[2],
            "total_cases": summary["total"],
            "feasible_cases": summary["feasible"],
            "infeasible_cases": summary["infeasible"],
            "minimum_mutable_recourse_histogram": dict(sorted(summary["minimum_mutable_recourse_histogram"].items(), key=lambda x: int(x[0]))),
            "first_infeasible_witness": witnesses.get(key),
        }
        result_rows[f"n{key[0]}_old{key[1]}_locked{key[2]}"] = row

    # A failed ell=0 case is a checker/model bug: paired incidence guarantees
    # some prefix-feasible endpoint assignment when all old tasks may move.
    mutable_cases = [row for row in result_rows.values() if row["locked_prefix_length"] == 0]
    if any(row["infeasible_cases"] for row in mutable_cases):
        raise AssertionError("unexpected infeasibility with no frozen old task")

    return {
        "protocol": "E2-L1 frozen-prefix exact enumeration, frozen 2026-10-16",
        "task_model": "simple loopless candidate alphabet; repeated pair arrivals allowed; unit 1/2-1/2 quotas; all prefixes checked",
        "n_values": list(n_values),
        "max_old_length": max_old_length,
        "total_old_legal_plans_enumerated": total_old_plans,
        "total_old_plan_new_edge_cases": total_extension_cases,
        "rows_by_n_old_length_locked_prefix": result_rows,
        "elapsed_seconds": round(time.perf_counter() - start, 6),
    }


if __name__ == "__main__":
    output = run()
    out_path = Path(__file__).with_name("locked_prefix_feasibility_exact.json")
    out_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "total_old_legal_plans_enumerated": output["total_old_legal_plans_enumerated"],
        "total_old_plan_new_edge_cases": output["total_old_plan_new_edge_cases"],
        "summary": [
            {k: row[k] for k in ("n", "old_length", "locked_prefix_length", "total_cases", "feasible_cases", "infeasible_cases")}
            for row in output["rows_by_n_old_length_locked_prefix"].values()
        ],
        "elapsed_seconds": output["elapsed_seconds"],
        "saved": str(out_path),
    }, indent=2))
