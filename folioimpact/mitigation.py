from __future__ import annotations

from .engine import Evaluator


def selected_ids(ev: Evaluator, mask: int) -> list[str]:
    return [aid for i, aid in enumerate(ev.action_ids) if (mask >> i) & 1]


def segment_guaranteed(fast: dict, bit: int) -> bool:
    if fast["baseline_mask"] & bit:
        return True
    ci = fast["segment_component"].get(bit)
    if ci is None:
        return False
    k = None
    for cr in fast["components"]:
        if cr["component_index"] == ci:
            k = cr["variable_count"]
            break
    if k is None:
        return False
    world_count = 1 << k
    all_worlds = (1 << world_count) - 1
    return fast["segment_worlds"].get(bit, 0) == all_worlds


def evaluate_requirements(ev: Evaluator, state) -> dict:
    case = ev.case
    protection = []
    protection_ok = True
    for req in case.protection_requirements():
        fast = ev.evaluate_scope_fast(state, req["group"], req["sensitive_group"], req["time_slice"])
        sufficient = fast["maximum"] <= req["cap"]
        protection_ok &= sufficient
        protection.append({
            "id": f"cap:{req['group']}:{req['sensitive_group']}:{req['time_slice']}",
            "group": req["group"],
            "sensitive_group": req["sensitive_group"],
            "time_slice": req["time_slice"],
            "cap": req["cap"],
            "minimum_visible": fast["minimum"],
            "maximum_visible": fast["maximum"],
            "sufficient_in_every_world": sufficient,
        })

    reviews = []
    reviews_ok = True
    for mid, req in case.mandatory_reviews.items():
        fast = ev.evaluate_scope_fast(
            state, req["group"], req["sensitive_group"], req["time_slice"], req["review_copy"]
        )
        missing = []
        for sid in req["required_segments"]:
            bit = case.seg_bit[sid]
            if not segment_guaranteed(fast, bit):
                missing.append(sid)
        enough_count = fast["minimum"] >= req["minimum"]
        satisfied = enough_count and not missing
        reviews_ok &= satisfied
        reviews.append({
            "id": mid,
            "review_copy": req["review_copy"],
            "group": req["group"],
            "sensitive_group": req["sensitive_group"],
            "time_slice": req["time_slice"],
            "required_minimum": req["minimum"],
            "required_segments": list(req["required_segments"]),
            "guaranteed_minimum_visible": fast["minimum"],
            "possible_maximum_visible": fast["maximum"],
            "missing_guaranteed_segments": missing,
            "minimum_boundary_preserved": satisfied,
        })

    return {
        "valid_action_combination": state.valid,
        "replacement_conflicts": state.replacement_conflicts,
        "protection": protection,
        "mandatory_reviews": reviews,
        "protection_sufficient": protection_ok,
        "minimum_boundaries_preserved": reviews_ok,
        "feasible": state.valid and protection_ok and reviews_ok,
    }


def plan_is_feasible(ev: Evaluator, state) -> bool:
    """Short-circuit feasibility test used by the 65,536-subset search."""
    if not state.valid:
        return False
    case = ev.case
    for req in case.protection_requirements():
        fast = ev.evaluate_scope_fast(state, req["group"], req["sensitive_group"], req["time_slice"])
        if fast["maximum"] > req["cap"]:
            return False
    for mid, req in case.mandatory_reviews.items():
        fast = ev.evaluate_scope_fast(
            state, req["group"], req["sensitive_group"], req["time_slice"], req["review_copy"]
        )
        if fast["minimum"] < req["minimum"]:
            return False
        for sid in req["required_segments"]:
            if not segment_guaranteed(fast, case.seg_bit[sid]):
                return False
    return True


def _action_summary(case, aid):
    a = case.actions[aid]
    return {"id": aid, "kind": a["kind"], "cost": a["cost"], "target": a["target"]}


def optimize(case) -> dict:
    ev = Evaluator(case)
    n = len(ev.action_ids)
    if n > 16:
        from .errors import SearchLimitError
        raise SearchLimitError("mitigation supports at most 16 actions")

    total_masks = 1 << n
    best_key = None
    best_mask = None
    evaluated_subsets = 0
    valid_subsets = 0
    feasible_count = 0

    for mask in range(total_masks):
        cost = ev.mask_cost[mask]
        bit_count = mask.bit_count()
        if best_key is not None:
            best_cost, best_count, _ = best_key
            if cost > best_cost or (cost == best_cost and bit_count > best_count):
                continue
        evaluated_subsets += 1
        state = ev.state(mask)
        if not state.valid:
            continue
        valid_subsets += 1
        if not plan_is_feasible(ev, state):
            continue
        feasible_count += 1
        ids = tuple(selected_ids(ev, mask))
        key = (cost, bit_count, ids)
        if best_key is None or key < best_key:
            best_key = key
            best_mask = mask

    search_stats = {
        "available_actions": n,
        "enumerated_subsets": total_masks,
        "evaluated_after_cost_and_count_pruning": evaluated_subsets,
        "valid_evaluated_subsets": valid_subsets,
        "feasible_evaluated_subsets": feasible_count,
        "exhaustive_when_no_plan": best_mask is None,
        "tie_break": ["total_cost", "action_count", "lexicographic_action_ids"],
    }

    if best_mask is None:
        empty_state = ev.state(0)
        collision = _collision_certificate(ev, empty_state)
        return {
            "ok": True,
            "feasible": False,
            "case_id": case.case_id,
            "search": search_stats,
            "collision_worlds": collision,
            "review_hint": "Rerun `crosscheck` and inspect version_assignment/exchange_chains in each collision world.",
        }

    state = ev.state(best_mask)
    verdict = evaluate_requirements(ev, state)
    ids = selected_ids(ev, best_mask)
    detailed_protection = []
    for req in case.protection_requirements():
        pair = ev.boundary_pair(state, req["group"], req["sensitive_group"], req["time_slice"])
        detailed_protection.append({
            "target": {k: req[k] for k in ("group", "sensitive_group", "time_slice", "cap")},
            "evaluation": next(p for p in verdict["protection"]
                               if p["group"] == req["group"] and p["sensitive_group"] == req["sensitive_group"]
                               and p["time_slice"] == req["time_slice"]),
            "boundaries": pair,
        })
    detailed_reviews = []
    for mid, req in case.mandatory_reviews.items():
        pair = ev.boundary_pair(state, req["group"], req["sensitive_group"], req["time_slice"], req["review_copy"])
        detailed_reviews.append({
            "id": mid,
            "requirement": {
                "review_copy": req["review_copy"], "group": req["group"],
                "sensitive_group": req["sensitive_group"], "time_slice": req["time_slice"],
                "minimum": req["minimum"], "required_segments": list(req["required_segments"]),
            },
            "evaluation": next(r for r in verdict["mandatory_reviews"] if r["id"] == mid),
            "boundaries": pair,
        })

    return {
        "ok": True,
        "feasible": True,
        "case_id": case.case_id,
        "minimum_cost": best_key[0],
        "action_count": best_key[1],
        "selected_action_ids": list(best_key[2]),
        "actions": [_action_summary(case, aid) for aid in best_key[2]],
        "search": search_stats,
        "policy_verdict": {
            "protection_sufficient_in_every_world": verdict["protection_sufficient"],
            "all_minimum_boundaries_preserved": verdict["minimum_boundaries_preserved"],
        },
        "protection_evaluation": detailed_protection,
        "mandatory_review_evaluation": detailed_reviews,
    }


def _collision_certificate(ev: Evaluator, state) -> dict:
    case = ev.case
    cap_world = None
    review_world = None

    for req in case.protection_requirements():
        fast = ev.evaluate_scope_fast(state, req["group"], req["sensitive_group"], req["time_slice"])
        if fast["maximum"] > req["cap"]:
            pair = ev.boundary_pair(state, req["group"], req["sensitive_group"], req["time_slice"])
            cap_world = {
                "kind": "protection_cap_collision_world",
                "requirement": req,
                "observed_maximum": fast["maximum"],
                "boundary": pair["maximum"],
                "component_worlds": pair["component_worlds"],
            }
            break

    for mid, req in case.mandatory_reviews.items():
        fast = ev.evaluate_scope_fast(
            state, req["group"], req["sensitive_group"], req["time_slice"], req["review_copy"]
        )
        missing = [sid for sid in req["required_segments"]
                   if not segment_guaranteed(fast, case.seg_bit[sid])]
        if fast["minimum"] < req["minimum"] or missing:
            pair = ev.boundary_pair(
                state, req["group"], req["sensitive_group"], req["time_slice"], req["review_copy"]
            )
            review_world = {
                "kind": "mandatory_review_minimum_collision_world",
                "mandatory_review_id": mid,
                "requirement": {
                    "review_copy": req["review_copy"], "minimum": req["minimum"],
                    "required_segments": list(req["required_segments"]),
                },
                "observed_minimum": fast["minimum"],
                "missing_guaranteed_segments": missing,
                "boundary": pair["minimum"],
                "component_worlds": pair["component_worlds"],
            }
            break

    return {
        "certificate_plan": "empty_action_set",
        "selected_action_ids": [],
        "too_much_disclosure_world": cap_world,
        "too_little_information_world": review_world,
        "interpretation": (
            "The exhaustive 2**16 action search found no feasible subset. "
            "These deterministic worlds expose the colliding upper and lower information boundaries."
        ),
    }


def countermeasure_report(case) -> dict:
    ev = Evaluator(case)
    ids = list(case.countermeasure_ids)
    try:
        mask = ev.mask_from_ids(ids)
    except Exception:
        raise
    state = ev.state(mask)
    if not state.valid:
        return {
            "provided": bool(ids),
            "selected_action_ids": ids,
            "valid_combination": False,
            "replacement_conflicts": state.replacement_conflicts,
            "sufficient_in_all_legal_worlds": False,
            "reason": "multiple replace_version actions target the same review copy",
            "targets": [],
        }
    verdict = evaluate_requirements(ev, state)
    targets = []
    for req in case.protection_requirements():
        pair = ev.boundary_pair(state, req["group"], req["sensitive_group"], req["time_slice"])
        item = next(p for p in verdict["protection"]
                    if p["group"] == req["group"] and p["sensitive_group"] == req["sensitive_group"]
                    and p["time_slice"] == req["time_slice"])
        item["maximum_boundary"] = pair["maximum"]
        targets.append(item)
    overall = state.valid and all(t["sufficient_in_every_world"] for t in targets)
    return {
        "provided": bool(ids),
        "selected_action_ids": ids,
        "valid_combination": state.valid,
        "replacement_conflicts": state.replacement_conflicts,
        "sufficient_in_all_legal_worlds": overall,
        "cannot_treat_uncertainty_as_disclosed": "maximum-over-worlds used for caps",
        "targets": targets,
    }
