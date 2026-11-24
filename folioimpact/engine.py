from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from .errors import InputValidationError
from .intervals import lists_intersect, overlap_record
from .model import CaseModel


@dataclass
class PlanState:
    mask: int
    selected: list[dict]
    availability: dict[str, list[tuple[int, int]]]
    replacements: dict[str, str]
    replacement_conflicts: dict[str, list[str]]
    reach: dict[str, dict[str, tuple[int, tuple[str, ...]]]] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return not self.replacement_conflicts


def bit_sliced_extrema(segment_planes: dict[int, int], world_count: int) -> tuple[int, int, int, int]:
    """Return min count, min world, max count and max world simultaneously.

    ``segment_planes[bit]`` is a ``world_count``-bit integer; bit ``w`` says
    whether that segment is visible in local world ``w``.
    """
    all_worlds = (1 << world_count) - 1
    segment_count = len(segment_planes)
    width = max(1, max(segment_count.bit_length(), 1))
    counters = [0] * (width + 1)
    for plane in segment_planes.values():
        carry = plane & all_worlds
        for j in range(len(counters)):
            if carry == 0:
                break
            new_carry = counters[j] & carry
            counters[j] ^= carry
            carry = new_carry

    # Maximum.
    candidates = all_worlds
    max_value = 0
    for bit_pos in range(len(counters) - 1, -1, -1):
        ones = counters[bit_pos] & candidates
        if ones:
            max_value |= 1 << bit_pos
            candidates = ones
    max_world = (candidates & -candidates).bit_length() - 1 if candidates else 0

    # Minimum.
    candidates = all_worlds
    min_value = 0
    for bit_pos in range(len(counters) - 1, -1, -1):
        ones = counters[bit_pos] & candidates
        zeros = candidates & ~ones
        if zeros:
            candidates = zeros
        else:
            min_value |= 1 << bit_pos
            candidates = ones
    min_world = (candidates & -candidates).bit_length() - 1 if candidates else 0
    return min_value, min_world, max_value, max_world


def component_extrema(planes: dict[int, int], variable_count: int) -> tuple[int, int, int, int]:
    if variable_count == 1:
        world0 = 0
        world1 = 0
        for plane in planes.values():
            world0 += plane & 1
            world1 += (plane >> 1) & 1
        if world0 <= world1:
            min_value, min_world = world0, 0
        else:
            min_value, min_world = world1, 1
        if world1 > world0:
            max_value, max_world = world1, 1
        else:
            max_value, max_world = world0, 0
        return min_value, min_world, max_value, max_world
    world_count = 1 << variable_count
    return bit_sliced_extrema(planes, world_count)


class Evaluator:
    def __init__(self, case: CaseModel):
        self.case = case
        self.action_ids = list(case.actions)
        self.action_index = {aid: i for i, aid in enumerate(self.action_ids)}
        self.choice_masks = self._build_choice_masks()
        self.static_review_masks = self._build_static_review_masks()
        self.mask_cost = self._build_mask_costs()

    def _build_mask_costs(self):
        costs = [0] * (1 << len(self.action_ids))
        for mask in range(1, len(costs)):
            bit = mask & -mask
            i = bit.bit_length() - 1
            aid = self.action_ids[i]
            costs[mask] = costs[mask ^ bit] + self.case.actions[aid]["cost"]
        return costs

    def _build_static_review_masks(self):
        """Version/page compatibility masks independent of intervals/actions."""
        case = self.case
        result = {}
        for w in case.witnesses.values():
            alt_masks = []
            for dvid in w["alternatives"]:
                svs = set(case.document_versions[dvid]["segment_versions"])
                by_review = {}
                for rid, rc in case.review_copies.items():
                    mask = 0
                    for pid in rc["pages"]:
                        page = case.pages[pid]
                        if page["segment_version"] in svs:
                            mask |= case.seg_bit[case.page_canonical[pid]]
                    by_review[rid] = mask
                alt_masks.append(by_review)
            result[w["id"]] = alt_masks
        return result

    def _build_choice_masks(self):
        result = []
        for comp in self.case.components:
            k = len(comp["variables"])
            world_count = 1 << k
            all_worlds = (1 << world_count) - 1
            masks = []
            for j in range(k):
                one = 0
                for world in range(1 << k):
                    if (world >> j) & 1:
                        one |= 1 << world
                zero = all_worlds ^ one
                masks.append((zero, one))
            result.append(masks)
        return result

    def mask_from_ids(self, ids) -> int:
        mask = 0
        for aid in ids:
            if aid not in self.action_index:
                raise InputValidationError(f"unknown action id {aid!r}", "$")
            mask |= 1 << self.action_index[aid]
        return mask

    # ---------- plan state / graph ----------

    def state(self, mask: int = 0) -> PlanState:
        case = self.case
        selected = [case.actions[aid] for i, aid in enumerate(self.action_ids) if (mask >> i) & 1]
        availability: dict[str, list[tuple[int, int]]] = {}
        for w in case.witnesses.values():
            availability[w["id"]] = [w["interval"]]
        for x in case.handoffs.values():
            availability[x["id"]] = [x["interval"]]
        for r in case.review_copies.values():
            availability[r["id"]] = [r["interval"]]

        # Multiple shorten actions on one target compose by intersection.
        # A empty intersection is sticky so a later action cannot revive it.
        shortened: dict[str, tuple[int, int] | None] = {}
        for action in selected:
            if action["kind"] != "shorten_interval":
                continue
            tid = action["target"]
            current = shortened.get(tid, case._original_interval(tid))
            if current is None:
                shortened[tid] = None
                continue
            from .intervals import intersection
            shortened[tid] = intersection(current, action["interval"])
        for tid, interval in shortened.items():
            availability[tid] = [interval] if interval is not None else []

        # Temporary pauses subtract blackout intervals.
        pauses: dict[str, list] = {}
        for action in selected:
            if action["kind"] == "suspend_review":
                pauses.setdefault(action["target"], []).append(action["pause_interval"])
        for rid, blackouts in pauses.items():
            if availability[rid]:
                from .intervals import subtract_blackouts
                availability[rid] = subtract_blackouts(availability[rid][0], blackouts)

        replacements: dict[str, str] = {}
        conflicts: dict[str, list[str]] = {}
        for action in selected:
            if action["kind"] == "replace_version":
                tid = action["target"]
                if tid in replacements:
                    conflicts.setdefault(tid, [replacements[tid]]).append(action["id"])
                else:
                    replacements[tid] = action["id"]
        state = PlanState(mask, selected, availability, replacements, conflicts)
        # Conflicting replacement plans are rejected before graph construction.
        if state.valid:
            state.reach = self.build_reach(state)
        return state

    def build_reach(self, state: PlanState):
        case = self.case
        avail = state.availability
        reach: dict[str, dict[str, tuple[int, tuple[str, ...]]]] = {}
        witness_ids = sorted(case.witnesses)
        review_ids = sorted(case.review_copies)
        handoff_ids = sorted(case.handoffs)

        for source in witness_ids:
            best_witness: dict[str, tuple[int, tuple[str, ...]]] = {source: (0, (source,))}
            review_paths: dict[str, tuple[int, tuple[str, ...]]] = {}
            heap = [(0, (source,), source)]

            while heap:
                hops, path, node = heapq.heappop(heap)
                if best_witness.get(node) != (hops, path):
                    continue
                wnode = case.witnesses[node]

                # Same-site direct review or review after an intermediate holding.
                for rid in review_ids:
                    rc = case.review_copies[rid]
                    if rc["group"] != wnode["group"] or rc["site"] != wnode["site"]:
                        continue
                    if rid in path or not lists_intersect(avail[node], avail[rid]):
                        continue
                    candidate = (hops, path + (rid,))
                    old = review_paths.get(rid)
                    if old is None or candidate < old:
                        review_paths[rid] = candidate

                # Handoff leg from current holding to a holding/review at to_site.
                for xid in handoff_ids:
                    x = case.handoffs[xid]
                    if x["group"] != wnode["group"] or x["from_site"] != wnode["site"]:
                        continue
                    if xid in path or not lists_intersect(avail[node], avail[xid]):
                        continue

                    for did in witness_ids:
                        if did in path or did == node:
                            continue
                        dest = case.witnesses[did]
                        if dest["group"] != x["group"] or dest["site"] != x["to_site"]:
                            continue
                        if not lists_intersect(avail[xid], avail[did]):
                            continue
                        new_path = path + (xid, did)
                        candidate = (hops + 1, new_path)
                        old = best_witness.get(did)
                        if old is None or candidate < old:
                            best_witness[did] = candidate
                            heapq.heappush(heap, (hops + 1, new_path, did))

                    for rid in review_ids:
                        if rid in path:
                            continue
                        rc = case.review_copies[rid]
                        if rc["group"] != x["group"] or rc["site"] != x["to_site"]:
                            continue
                        if not lists_intersect(avail[xid], avail[rid]):
                            continue
                        new_path = path + (xid, rid)
                        candidate = (hops + 1, new_path)
                        old = review_paths.get(rid)
                        if old is None or candidate < old:
                            review_paths[rid] = candidate

            # Store only path tuples; hop count is derivable but retained for tie checks.
            reach[source] = {rid: value[1] for rid, value in review_paths.items()}
        return reach

    # ---------- masks ----------

    def _slice_interval(self, time_slice: str):
        return self.case.time_slices[time_slice]["interval"]

    def _review_active_in_slice(self, state: PlanState, rid: str, time_slice: str) -> bool:
        interval = self._slice_interval(time_slice)
        return lists_intersect(state.availability[rid], [interval])

    def _witness_alt_mask(
        self, state: PlanState, wid: str, alt_index: int, group: str, sg_mask: int,
        time_slice: str, only_review: str | None,
    ) -> int:
        case = self.case
        w = case.witnesses[wid]
        if w["group"] != group:
            return 0
        result = 0
        slice_interval = self._slice_interval(time_slice)
        static_by_review = self.static_review_masks[wid][alt_index]
        for rid in state.reach.get(wid, {}):
            if only_review is not None and rid != only_review:
                continue
            if rid in state.replacements:
                continue
            if not lists_intersect(state.availability[rid], [slice_interval]):
                continue
            result |= static_by_review.get(rid, 0)
        return result & sg_mask

    def _replacement_mask(self, state: PlanState, sg_mask: int, time_slice: str, only_review: str | None) -> int:
        case = self.case
        interval = self._slice_interval(time_slice)
        result = 0
        for rid, aid in state.replacements.items():
            if only_review is not None and rid != only_review:
                continue
            if not lists_intersect(state.availability[rid], [interval]):
                continue
            for pid in case.actions[aid]["replacement_pages"]:
                result |= case.seg_bit[case.page_canonical[pid]]
        return result & sg_mask

    def evaluate_scope_fast(
        self, state: PlanState, group: str, sg_id: str, time_slice: str,
        only_review: str | None = None,
    ) -> dict:
        case = self.case
        sg_mask = case.sg_mask(sg_id)
        baseline = self._replacement_mask(state, sg_mask, time_slice, only_review)
        segment_worlds: dict[int, int] = {}
        segment_component: dict[int, int] = {}

        for w in case.witnesses.values():
            if w["group"] != group:
                continue
            if only_review is not None and only_review not in state.reach.get(w["id"], {}):
                # A replaced review is handled as deterministic baseline even if
                # no historical custody chain reaches it.
                if only_review not in state.replacements:
                    continue
            if w["variable"] is None:
                baseline |= self._witness_alt_mask(
                    state, w["id"], 0, group, sg_mask, time_slice, only_review
                )
                continue

            ci, j = case.var_component[w["variable"]]
            zero_mask, one_mask = self.choice_masks[ci][j]
            for alt_index, world_mask in ((0, zero_mask), (1, one_mask)):
                mask = self._witness_alt_mask(
                    state, w["id"], alt_index, group, sg_mask, time_slice, only_review
                )
                while mask:
                    bit = mask & -mask
                    mask ^= bit
                    segment_worlds[bit] = segment_worlds.get(bit, 0) | world_mask
                    segment_component[bit] = ci

        baseline &= sg_mask
        comp_planes: list[dict[int, int]] = [dict() for _ in case.components]
        for bit, worlds in segment_worlds.items():
            if baseline & bit:
                continue
            ci = segment_component[bit]
            comp_planes[ci][bit] = worlds

        comp_results = []
        min_total = (baseline & sg_mask).bit_count()
        max_total = min_total
        for ci, comp in enumerate(case.components):
            k = len(comp["variables"])
            mn, min_world, mx, max_world = component_extrema(comp_planes[ci], k)
            comp_results.append({
                "component_index": ci,
                "component_id": comp["id"],
                "variable_count": k,
                "minimum": mn,
                "maximum": mx,
                "min_world": min_world,
                "max_world": max_world,
                "planes": comp_planes[ci],
            })
            min_total += mn
            max_total += mx

        return {
            "group": group,
            "sensitive_group": sg_id,
            "time_slice": time_slice,
            "only_review": only_review,
            "baseline_mask": baseline,
            "segment_worlds": segment_worlds,
            "segment_component": segment_component,
            "components": comp_results,
            "minimum": min_total,
            "maximum": max_total,
        }

    @staticmethod
    def _bits(mask: int):
        while mask:
            bit = mask & -mask
            yield bit
            mask ^= bit

    # ---------- detailed deterministic evidence ----------

    def choices_from_scope_worlds(self, fast: dict, direction: str) -> dict[str, int]:
        choices: dict[str, int] = {}
        for cr in fast["components"]:
            ci = cr["component_index"]
            world = cr["min_world"] if direction == "minimum" else cr["max_world"]
            comp = self.case.components[ci]
            for j, vid in enumerate(comp["variables"]):
                choices[vid] = (world >> j) & 1
        return choices

    def assignment(self, choices: dict[str, int]) -> dict:
        variables = {}
        witness_versions = {}
        for vid, wids in sorted(self.case.variables.items()):
            value = choices.get(vid, 0)
            witnesses = {}
            for wid in wids:
                versions = self.case.witnesses[wid]["alternatives"]
                chosen = versions[value]
                witnesses[wid] = chosen
                witness_versions[wid] = chosen
            variables[vid] = {"choice": value, "document_versions_by_witness": witnesses}
        for w in self.case.witnesses.values():
            if w["variable"] is None:
                witness_versions[w["id"]] = w["alternatives"][0]
        return {"variables": variables, "witness_document_versions": dict(sorted(witness_versions.items()))}

    def detail_scope(
        self, state: PlanState, group: str, sg_id: str, time_slice: str,
        choices: dict[str, int], only_review: str | None = None,
    ) -> dict:
        case = self.case
        sg_mask = case.sg_mask(sg_id)
        interval = self._slice_interval(time_slice)
        evidence: dict[int, dict] = {}

        def consider(bit: int, sort_key, record):
            old = evidence.get(bit)
            if old is None or sort_key < old["_sort_key"]:
                record["_sort_key"] = sort_key
                evidence[bit] = record

        # Certified replacement pages are deterministic after the action is chosen.
        for rid, aid in sorted(state.replacements.items()):
            if only_review is not None and rid != only_review:
                continue
            rc = case.review_copies[rid]
            if rc["group"] != group or not lists_intersect(state.availability[rid], [interval]):
                continue
            action = case.actions[aid]
            for pid in action["replacement_pages"]:
                canon = case.page_canonical[pid]
                bit = case.seg_bit[canon]
                if not (bit & sg_mask):
                    continue
                key = (0, aid, rid, (aid, rid), pid)
                consider(bit, key, {
                    "kind": "recommended_replacement",
                    "segment": canon,
                    "action_id": aid,
                    "review_copy": rid,
                    "page": pid,
                    "exchange_chain": [aid, rid],
                    "chain_kind": "recommendation",
                    "contacts": [],
                })

        # Witness-version-dependent review evidence.
        for wid in sorted(case.witnesses):
            w = case.witnesses[wid]
            if w["group"] != group:
                continue
            value = choices.get(w["variable"], 0) if w["variable"] is not None else 0
            dvid = w["alternatives"][value]
            svs = set(case.document_versions[dvid]["segment_versions"])
            for rid, path in sorted(state.reach.get(wid, {}).items()):
                if only_review is not None and rid != only_review:
                    continue
                if rid in state.replacements:
                    continue
                rc = case.review_copies[rid]
                if not lists_intersect(state.availability[rid], [interval]):
                    continue
                for pid in rc["pages"]:
                    page = case.pages[pid]
                    if page["segment_version"] not in svs:
                        continue
                    canon = case.page_canonical[pid]
                    bit = case.seg_bit[canon]
                    if not (bit & sg_mask):
                        continue
                    contacts = []
                    for a, b in zip(path, path[1:]):
                        contacts.append(overlap_record(a, state.availability[a], b, state.availability[b]))
                    key = (1, len(path), path, pid)
                    consider(bit, key, {
                        "kind": "witnessed_review",
                        "segment": canon,
                        "witness": wid,
                        "assigned_document_version": dvid,
                        "review_copy": rid,
                        "page": pid,
                        "exchange_chain": list(path),
                        "chain_kind": "custody_and_handoff",
                        "contacts": contacts,
                    })

        ordered = []
        visible = []
        for canon in case.canonical_segments:
            bit = case.seg_bit[canon]
            if bit in evidence:
                rec = evidence[bit]
                rec.pop("_sort_key", None)
                ordered.append(rec)
                visible.append(canon)
        assignment = self.assignment(choices)
        return {
            "independent_segment_count": len(visible),
            "visible_independent_segments": visible,
            "version_assignment": assignment,
            "exchange_chains": ordered,
        }

    def boundary_pair(self, state: PlanState, group, sg_id, time_slice, only_review=None) -> dict:
        fast = self.evaluate_scope_fast(state, group, sg_id, time_slice, only_review)
        min_choices = self.choices_from_scope_worlds(fast, "minimum")
        max_choices = self.choices_from_scope_worlds(fast, "maximum")
        minimum = self.detail_scope(state, group, sg_id, time_slice, min_choices, only_review)
        maximum = self.detail_scope(state, group, sg_id, time_slice, max_choices, only_review)
        # The fast bit-sliced count and detailed evidence must agree.
        if minimum["independent_segment_count"] != fast["minimum"]:
            raise AssertionError(("minimum mismatch", minimum["independent_segment_count"], fast["minimum"]))
        if maximum["independent_segment_count"] != fast["maximum"]:
            raise AssertionError(("maximum mismatch", maximum["independent_segment_count"], fast["maximum"]))
        return {
            "minimum": minimum,
            "maximum": maximum,
            "fast_bounds": {"minimum": fast["minimum"], "maximum": fast["maximum"]},
            "component_worlds": [
                {
                    "component_id": cr["component_id"],
                    "minimum_world_index": cr["min_world"],
                    "maximum_world_index": cr["max_world"],
                }
                for cr in fast["components"]
            ],
        }
