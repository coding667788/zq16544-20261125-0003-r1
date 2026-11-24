from __future__ import annotations

from .engine import Evaluator
from .mitigation import countermeasure_report


def build_bound_report(case, include_countermeasures: bool = True) -> dict:
    ev = Evaluator(case)
    state = ev.state(0)

    bounds = []
    for group, sg, tid in case.cells():
        pair = ev.boundary_pair(state, group, sg, tid)
        bounds.append({
            "group": group,
            "sensitive_group": sg,
            "time_slice": tid,
            "minimum_independent_segments": pair["minimum"]["independent_segment_count"],
            "maximum_independent_segments": pair["maximum"]["independent_segment_count"],
            "minimum_boundary": pair["minimum"],
            "maximum_boundary": pair["maximum"],
            "component_worlds": pair["component_worlds"],
        })

    components = []
    for comp in case.components:
        components.append({
            "id": comp["id"],
            "variables": list(comp["variables"]),
            "binary_variables": len(comp["variables"]),
            "world_count": comp["world_count"],
            "witnesses": {
                vid: list(case.variables[vid]) for vid in comp["variables"]
            },
        })

    folds = []
    for canon in case.canonical_segments:
        members = case.canonical_members[canon]
        folds.append({
            "canonical_independent_segment": canon,
            "members": list(members),
            "sensitive_group": case.segment_sensitive_group[canon],
            "collapsed_by_proven_fold": len(members) > 1,
        })

    report = {
        "ok": True,
        "command": "bound",
        "schema": case.raw.get("schema"),
        "case_id": case.case_id,
        "semantics": {
            "interval": "half-open [start,end)",
            "world": "one deterministic alternative for every binary witness variable",
            "independent_count": "distinct canonical independent segments after proven folds",
            "upper_policy_rule": "use maximum over legal worlds; uncertainty is not wholesale disclosure",
            "lower_review_rule": "use minimum over legal worlds; uncertainty is not guaranteed retention",
            "chain_tie_break": ["minimum handoff count", "lexicographic node id tuple", "lexicographic page id"],
        },
        "ambiguity_components": components,
        "independent_segment_canonicalization": folds,
        "bounds": bounds,
    }
    if include_countermeasures:
        report["countermeasure_evaluation"] = countermeasure_report(case)
    return report
