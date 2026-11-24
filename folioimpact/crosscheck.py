from __future__ import annotations

from .engine import Evaluator


def _global_choice_masks(variable_count: int):
    world_count = 1 << variable_count
    all_worlds = (1 << world_count) - 1
    result = []
    for j in range(variable_count):
        one = 0
        for world in range(1 << variable_count):
            if (world >> j) & 1:
                one |= 1 << world
        result.append((all_worlds ^ one, one))
    return result


def whole_world_reference(ev: Evaluator, group: str, sg_id: str, time_slice: str, max_variables: int = 18):
    """Directly enumerate every complete global assignment."""
    case = ev.case
    vids = case.variable_ids
    if len(vids) > max_variables:
        raise ValueError(f"whole-world reference supports at most {max_variables} variables")
    state = ev.state(0)
    sg_mask = case.sg_mask(sg_id)
    rows = []
    for world in range(1 << len(vids)):
        choices = {vid: (world >> j) & 1 for j, vid in enumerate(vids)}
        mask = ev._replacement_mask(state, sg_mask, time_slice, None)
        for w in case.witnesses.values():
            if w["group"] != group:
                continue
            value = choices[w["variable"]] if w["variable"] is not None else 0
            mask |= ev._witness_alt_mask(state, w["id"], value, group, sg_mask, time_slice, None)
        rows.append(mask & sg_mask)
    return rows


def segment_wise_reference(ev: Evaluator, group: str, sg_id: str, time_slice: str, max_variables: int = 18):
    """Transposed reference: derive one global visibility truth mask per segment."""
    case = ev.case
    vids = case.variable_ids
    if len(vids) > max_variables:
        raise ValueError(f"segment-wise reference supports at most {max_variables} variables")
    state = ev.state(0)
    sg_mask = case.sg_mask(sg_id)
    v_index = {vid: j for j, vid in enumerate(vids)}
    choices = _global_choice_masks(len(vids))
    all_worlds = (1 << (1 << len(vids))) - 1 if vids else 1
    planes: dict[int, int] = {}

    fixed = ev._replacement_mask(state, sg_mask, time_slice, None)
    for w in case.witnesses.values():
        if w["group"] != group:
            continue
        if w["variable"] is None:
            fixed |= ev._witness_alt_mask(state, w["id"], 0, group, sg_mask, time_slice, None)
            continue
        j = v_index[w["variable"]]
        zero_worlds, one_worlds = choices[j]
        for alt, world_mask in ((0, zero_worlds), (1, one_worlds)):
            mask = ev._witness_alt_mask(state, w["id"], alt, group, sg_mask, time_slice, None)
            for bit in Evaluator._bits(mask & sg_mask):
                planes[bit] = planes.get(bit, 0) | world_mask

    for bit in Evaluator._bits(fixed & sg_mask):
        planes[bit] = all_worlds

    world_count = 1 << len(vids)
    counts = []
    masks = []
    for world in range(world_count):
        mask = 0
        count = 0
        for bit, plane in planes.items():
            if (plane >> world) & 1:
                mask |= bit
                count += 1
        masks.append(mask)
        counts.append(count)
    min_world = min(range(world_count), key=lambda w: (counts[w], w))
    max_world = max(range(world_count), key=lambda w: (counts[w], -w))
    # Deterministic maximum tie should also be lowest world; max() above with -w picks lowest?
    # key (count, -w): for equal count, larger -w means smaller w -> yes.
    return {"counts": counts, "masks": masks, "min_world": min_world, "max_world": max_world,
            "minimum": counts[min_world], "maximum": counts[max_world]}


def crosscheck_case(case, max_variables: int = 12):
    """Compare component bounds, whole worlds and segment-wise truth tables."""
    ev = Evaluator(case)
    if len(case.variable_ids) > max_variables:
        return {"checked": False, "reason": "too many global variables for whole-world reference",
                "variable_count": len(case.variable_ids)}
    checks = []
    ok = True
    state = ev.state(0)
    for group, sg, tid in case.cells():
        fast = ev.evaluate_scope_fast(state, group, sg, tid)
        whole = whole_world_reference(ev, group, sg, tid, max_variables=max_variables)
        seg_ref = segment_wise_reference(ev, group, sg, tid, max_variables=max_variables)
        whole_min_world = min(range(len(whole)), key=lambda w: (whole[w].bit_count(), w))
        whole_max_world = max(range(len(whole)), key=lambda w: (whole[w].bit_count(), -w))
        result = {
            "group": group, "sensitive_group": sg, "time_slice": tid,
            "component_bounds": {"minimum": fast["minimum"], "maximum": fast["maximum"]},
            "whole_world_bounds": {
                "minimum": whole[whole_min_world].bit_count(),
                "maximum": whole[whole_max_world].bit_count(),
                "min_world": whole_min_world,
                "max_world": whole_max_world,
            },
            "segment_wise_bounds": {"minimum": seg_ref["minimum"], "maximum": seg_ref["maximum"]},
        }
        result["matches"] = (
            fast["minimum"] == result["whole_world_bounds"]["minimum"] == seg_ref["minimum"]
            and fast["maximum"] == result["whole_world_bounds"]["maximum"] == seg_ref["maximum"]
            and [m.bit_count() for m in whole] == seg_ref["counts"]
            and whole == seg_ref["masks"]
        )
        ok &= result["matches"]
        checks.append(result)
    return {"checked": True, "ok": ok, "global_world_count": 1 << len(case.variable_ids), "cells": checks}
