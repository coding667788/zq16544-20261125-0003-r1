from __future__ import annotations

import json
from pathlib import Path

from . import SCHEMA
from .errors import InputValidationError
from .intervals import normalize_interval

MAX_COMPONENT_VARS = 14
MAX_ACTIONS = 16


class DSU:
    def __init__(self, items):
        self.parent = {x: x for x in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _require_list(obj, key, path):
    value = obj.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise InputValidationError(f"{path}.{key} must be a list", f"{path}.{key}")
    return value


def _require_id(obj, path):
    value = obj.get("id")
    if not isinstance(value, str) or not value:
        raise InputValidationError("every object needs a non-empty string id", path)
    return value


class CaseModel:
    """Validated CASE input and deterministic preprocessing indexes."""

    def __init__(self, raw: dict, source_name: str = "<dict>"):
        if not isinstance(raw, dict):
            raise InputValidationError("top-level JSON value must be an object", "$")
        schema = raw.get("schema")
        if schema != SCHEMA:
            raise InputValidationError(f"schema must be {SCHEMA}", "$.schema", {"actual": schema})
        self.raw = raw
        self.source_name = source_name
        self.case_id = raw.get("case_id", "case")
        if not isinstance(self.case_id, str):
            raise InputValidationError("case_id must be a string", "$.case_id")

        self._validate_top_level(raw)
        self._load_core(raw)
        self._load_pages_and_folds(raw)
        self._load_custody(raw)
        self._load_queries_and_requirements(raw)
        self._load_actions(raw)
        self._validate_global_ids()
        self._build_variables_and_components()

    def _validate_global_ids(self):
        collections = [
            self.sensitive_groups, self.independent_segments, self.segment_versions,
            self.document_versions, self.pages, self.groups, self.witnesses,
            self.handoffs, self.review_copies, self.time_slices,
            self.mandatory_reviews, self.actions,
        ]
        seen = set()
        for collection in collections:
            for oid in collection:
                if oid in seen:
                    raise InputValidationError(f"id {oid!r} is used by more than one object", "$")
                seen.add(oid)

    # ---------- loading ----------

    @classmethod
    def load_file(cls, path: str | Path) -> "CaseModel":
        p = Path(path)
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            raise InputValidationError(f"cannot read input file: {exc}", str(p)) from exc
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InputValidationError(f"invalid UTF-8 JSON: {exc}", str(p)) from exc
        return cls(raw, str(p))

    @staticmethod
    def loads(text: str) -> "CaseModel":
        return CaseModel(json.loads(text), "<string>")

    def _validate_top_level(self, raw):
        known = {
            "schema", "case_id", "groups", "sensitive_groups", "independent_segments",
            "segment_versions", "document_versions", "pages", "time_slices",
            "witnesses", "handoffs", "review_copies", "mandatory_reviews",
            "protection_targets", "mitigation_actions", "countermeasures", "notes",
        }
        unknown = set(raw) - known
        if unknown:
            raise InputValidationError("unknown top-level fields", "$", {"fields": sorted(unknown)})

    def _load_core(self, raw):
        # Sensitive groups.
        self.sensitive_groups = {}
        for i, obj in enumerate(_require_list(raw, "sensitive_groups", "$")):
            path = f"$.sensitive_groups[{i}]"
            sid = _require_id(obj, path)
            if sid in self.sensitive_groups:
                raise InputValidationError(f"duplicate sensitive group id {sid}", path)
            cap = obj.get("protection_cap")
            if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int) or cap < 0):
                raise InputValidationError("protection_cap must be a nonnegative integer or null", f"{path}.protection_cap")
            self.sensitive_groups[sid] = {"id": sid, "protection_cap": cap}

        # Independent segments.
        self.independent_segments = {}
        for i, obj in enumerate(_require_list(raw, "independent_segments", "$")):
            path = f"$.independent_segments[{i}]"
            sid = _require_id(obj, path)
            if sid in self.independent_segments:
                raise InputValidationError(f"duplicate independent segment id {sid}", path)
            sg = obj.get("sensitive_group")
            if sg not in self.sensitive_groups:
                raise InputValidationError(f"sensitive_group {sg!r} is not defined", f"{path}.sensitive_group")
            self.independent_segments[sid] = {"id": sid, "sensitive_group": sg}

        # Versioned segments.
        self.segment_versions = {}
        for i, obj in enumerate(_require_list(raw, "segment_versions", "$")):
            path = f"$.segment_versions[{i}]"
            vid = _require_id(obj, path)
            if vid in self.segment_versions:
                raise InputValidationError(f"duplicate segment version id {vid}", path)
            ind = obj.get("independent_segment")
            if ind not in self.independent_segments:
                raise InputValidationError(f"independent_segment {ind!r} is not defined", f"{path}.independent_segment")
            self.segment_versions[vid] = {"id": vid, "independent_segment": ind}

        # Document versions.
        self.document_versions = {}
        for i, obj in enumerate(_require_list(raw, "document_versions", "$")):
            path = f"$.document_versions[{i}]"
            did = _require_id(obj, path)
            if did in self.document_versions:
                raise InputValidationError(f"duplicate document version id {did}", path)
            svs = _require_list(obj, "segment_versions", path)
            if not svs or not all(isinstance(x, str) for x in svs):
                raise InputValidationError("segment_versions must be a non-empty list of ids", f"{path}.segment_versions")
            missing = [x for x in svs if x not in self.segment_versions]
            if missing:
                raise InputValidationError("unknown segment version ids", f"{path}.segment_versions", {"missing": missing})
            if len(set(svs)) != len(svs):
                raise InputValidationError("document version contains a duplicate segment version", f"{path}.segment_versions")
            self.document_versions[did] = {"id": did, "segment_versions": tuple(svs)}

        # Groups and sites.
        self.groups = {}
        for i, obj in enumerate(_require_list(raw, "groups", "$")):
            path = f"$.groups[{i}]"
            gid = _require_id(obj, path)
            if gid in self.groups:
                raise InputValidationError(f"duplicate group id {gid}", path)
            sites = _require_list(obj, "sites", path)
            if not sites or not all(isinstance(x, str) and x for x in sites):
                raise InputValidationError("sites must be a non-empty list of non-empty strings", f"{path}.sites")
            if len(set(sites)) != len(sites):
                raise InputValidationError("duplicate site id in group", path)
            self.groups[gid] = {"id": gid, "sites": tuple(sites)}

    def _load_pages_and_folds(self, raw):
        self.pages = {}
        for i, obj in enumerate(_require_list(raw, "pages", "$")):
            path = f"$.pages[{i}]"
            pid = _require_id(obj, path)
            if pid in self.pages:
                raise InputValidationError(f"duplicate page id {pid}", path)
            svid = obj.get("segment_version")
            if svid not in self.segment_versions:
                raise InputValidationError(f"segment_version {svid!r} is not defined", f"{path}.segment_version")
            fold = obj.get("folded_summary_of")
            proven = obj.get("equivalence_proven", False)
            if fold is not None and not isinstance(fold, str):
                raise InputValidationError("folded_summary_of must be a page id", f"{path}.folded_summary_of")
            if not isinstance(proven, bool):
                raise InputValidationError("equivalence_proven must be boolean", f"{path}.equivalence_proven")
            self.pages[pid] = {
                "id": pid,
                "segment_version": svid,
                "folded_summary_of": fold,
                "equivalence_proven": proven,
            }
        for pid, page in self.pages.items():
            target = page["folded_summary_of"]
            if target is not None and target not in self.pages:
                raise InputValidationError(f"fold target {target!r} is not a page", f"$.pages.{pid}.folded_summary_of")

        # Proven folds merge the underlying independent segments.
        self.fold_dsu = DSU(sorted(self.independent_segments))
        self.fold_edges = []
        for pid, page in self.pages.items():
            target = page["folded_summary_of"]
            if target is not None and page["equivalence_proven"]:
                a = self.segment_versions[page["segment_version"]]["independent_segment"]
                b = self.segment_versions[self.pages[target]["segment_version"]]["independent_segment"]
                if a != b:
                    self.fold_dsu.union(a, b)
                    self.fold_edges.append({"page": pid, "target_page": target, "segments": [a, b]})

        members = {}
        for sid in self.independent_segments:
            root = self.fold_dsu.find(sid)
            members.setdefault(root, []).append(sid)
        self.canonical_members = {}
        self.canonical_of = {}
        for root, ids in members.items():
            canon = min(ids)
            self.canonical_members[canon] = tuple(sorted(ids))
            sg_ids = {self.independent_segments[x]["sensitive_group"] for x in ids}
            if len(sg_ids) != 1:
                raise InputValidationError(
                    "proven fold cannot cross sensitive segment groups",
                    "$.pages",
                    {"canonical": canon, "sensitive_groups": sorted(sg_ids)},
                )
            for sid in ids:
                self.canonical_of[sid] = canon

        self.canonical_segments = sorted(self.canonical_members)
        self.seg_bit = {sid: 1 << i for i, sid in enumerate(self.canonical_segments)}
        self.bit_segment = {1 << i: sid for i, sid in enumerate(self.canonical_segments)}
        self.segment_sensitive_group = {
            canon: self.independent_segments[members[0]]["sensitive_group"]
            for canon, members in ((c, self.canonical_members[c]) for c in self.canonical_segments)
        }
        self.page_canonical = {
            pid: self.canonical_of[self.segment_versions[p["segment_version"]]["independent_segment"]]
            for pid, p in self.pages.items()
        }

    def _load_custody(self, raw):
        self.witnesses = {}
        for i, obj in enumerate(_require_list(raw, "witnesses", "$")):
            path = f"$.witnesses[{i}]"
            wid = _require_id(obj, path)
            if wid in self.witnesses:
                raise InputValidationError(f"duplicate witness id {wid}", path)
            gid = obj.get("group")
            site = obj.get("site")
            self._check_group_site(gid, site, path)
            interval = normalize_interval(obj.get("interval"), f"{path}.interval")
            alts = obj.get("alternatives")
            if not isinstance(alts, list) or len(alts) not in (1, 2):
                raise InputValidationError("alternatives must contain one or two document version ids", f"{path}.alternatives")
            if len(set(alts)) != len(alts):
                raise InputValidationError("alternatives must be distinct", f"{path}.alternatives")
            missing = [x for x in alts if x not in self.document_versions]
            if missing:
                raise InputValidationError("unknown document version in alternatives", f"{path}.alternatives", {"missing": missing})
            variable = obj.get("variable")
            if len(alts) == 1 and variable is not None:
                raise InputValidationError("a one-version witness is fixed and must not declare variable", f"{path}.variable")
            if variable is not None and (not isinstance(variable, str) or not variable):
                raise InputValidationError("variable must be a non-empty string", f"{path}.variable")
            self.witnesses[wid] = {
                "id": wid, "group": gid, "site": site, "interval": interval,
                "alternatives": tuple(alts), "variable": variable if len(alts) == 2 else None,
            }

        self.handoffs = {}
        for i, obj in enumerate(_require_list(raw, "handoffs", "$")):
            path = f"$.handoffs[{i}]"
            xid = _require_id(obj, path)
            if xid in self.handoffs:
                raise InputValidationError(f"duplicate handoff id {xid}", path)
            gid = obj.get("group")
            from_site = obj.get("from_site")
            to_site = obj.get("to_site")
            self._check_group_site(gid, from_site, f"{path}.from_site")
            self._check_group_site(gid, to_site, f"{path}.to_site")
            if from_site == to_site:
                raise InputValidationError("handoff from_site and to_site must differ", path)
            interval = normalize_interval(obj.get("interval"), f"{path}.interval")
            self.handoffs[xid] = {
                "id": xid, "group": gid, "from_site": from_site, "to_site": to_site, "interval": interval,
            }

        self.review_copies = {}
        for i, obj in enumerate(_require_list(raw, "review_copies", "$")):
            path = f"$.review_copies[{i}]"
            rid = _require_id(obj, path)
            if rid in self.review_copies:
                raise InputValidationError(f"duplicate review copy id {rid}", path)
            gid = obj.get("group")
            site = obj.get("site")
            self._check_group_site(gid, site, path)
            interval = normalize_interval(obj.get("interval"), f"{path}.interval")
            page_ids = _require_list(obj, "pages", path)
            if not page_ids or not all(isinstance(x, str) for x in page_ids):
                raise InputValidationError("pages must be a non-empty list of page ids", f"{path}.pages")
            missing = [x for x in page_ids if x not in self.pages]
            if missing:
                raise InputValidationError("unknown page ids", f"{path}.pages", {"missing": missing})
            if len(set(page_ids)) != len(page_ids):
                raise InputValidationError("duplicate page in review copy", f"{path}.pages")
            self.review_copies[rid] = {
                "id": rid, "group": gid, "site": site, "interval": interval, "pages": tuple(page_ids),
            }

        declared_groups = set(self.groups)
        derived = {w["group"] for w in self.witnesses.values()}
        derived |= {x["group"] for x in self.handoffs.values()}
        derived |= {r["group"] for r in self.review_copies.values()}
        for gid in derived:
            if gid not in self.groups:
                # Ungrouped synthetic input is allowed; sites are unconstrained.
                self.groups[gid] = {"id": gid, "sites": ()}
        self.group_ids = sorted(declared_groups | derived)

        # Global ID uniqueness makes evidence chains unambiguous.
        seen = {}
        for collection in (
            self.sensitive_groups, self.independent_segments, self.segment_versions,
            self.document_versions, self.pages, self.groups, self.witnesses,
            self.handoffs, self.review_copies,
        ):
            for oid in collection:
                if oid in seen:
                    raise InputValidationError(f"id {oid!r} is used by more than one object", "$")
                seen[oid] = True

    def _check_group_site(self, gid, site, path):
        if not isinstance(gid, str) or not gid:
            raise InputValidationError("group must be a non-empty string", path)
        if not isinstance(site, str) or not site:
            raise InputValidationError("site must be a non-empty string", path)
        group = self.groups.get(gid)
        if group is not None and group["sites"] and site not in group["sites"]:
            raise InputValidationError(f"site {site!r} is not declared in group {gid}", path)

    def _load_queries_and_requirements(self, raw):
        slices = {}
        for i, obj in enumerate(_require_list(raw, "time_slices", "$")):
            path = f"$.time_slices[{i}]"
            tid = _require_id(obj, path)
            if tid in slices:
                raise InputValidationError(f"duplicate time slice id {tid}", path)
            slices[tid] = {"id": tid, "interval": normalize_interval(obj.get("interval"), f"{path}.interval")}
        if not slices:
            raise InputValidationError("at least one time slice is required", "$.time_slices")
        self.time_slices = {tid: slices[tid] for tid in sorted(slices)}

        self.mandatory_reviews = {}
        for i, obj in enumerate(_require_list(raw, "mandatory_reviews", "$")):
            path = f"$.mandatory_reviews[{i}]"
            mid = _require_id(obj, path)
            if mid in self.mandatory_reviews:
                raise InputValidationError(f"duplicate mandatory review id {mid}", path)
            tid = obj.get("time_slice")
            if tid not in self.time_slices:
                raise InputValidationError(f"unknown time_slice {tid!r}", f"{path}.time_slice")
            sg = obj.get("sensitive_group")
            if sg not in self.sensitive_groups:
                raise InputValidationError(f"unknown sensitive_group {sg!r}", f"{path}.sensitive_group")
            rid = obj.get("review_copy")
            group = obj.get("group")
            if rid is not None:
                if rid not in self.review_copies:
                    raise InputValidationError(f"unknown review_copy {rid!r}", f"{path}.review_copy")
                rc_group = self.review_copies[rid]["group"]
                if group is not None and group != rc_group:
                    raise InputValidationError("group does not match review_copy", f"{path}.group")
                group = rc_group
            elif group not in self.groups:
                raise InputValidationError("mandatory review needs a valid group or review_copy", path)
            required = obj.get("required_segments", [])
            if not isinstance(required, list) or not all(isinstance(x, str) for x in required):
                raise InputValidationError("required_segments must be a list of segment ids", f"{path}.required_segments")
            required_canon = []
            for sid in required:
                if sid in self.canonical_of:
                    required_canon.append(self.canonical_of[sid])
                elif sid in self.seg_bit:
                    required_canon.append(sid)
                else:
                    raise InputValidationError(f"unknown required segment {sid!r}", f"{path}.required_segments")
            minimum = obj.get("minimum_independent_segments")
            if minimum is None:
                minimum = len(required_canon)
            if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
                raise InputValidationError("minimum_independent_segments must be nonnegative integer", f"{path}.minimum_independent_segments")
            if minimum == 0 and not required_canon:
                raise InputValidationError("mandatory review must have a positive minimum or required_segments", path)
            self.mandatory_reviews[mid] = {
                "id": mid, "review_copy": rid, "group": group, "time_slice": tid,
                "sensitive_group": sg, "minimum": minimum,
                "required_segments": tuple(sorted(set(required_canon))),
            }

        self.protection_targets = {}
        for i, obj in enumerate(_require_list(raw, "protection_targets", "$")):
            path = f"$.protection_targets[{i}]"
            tid = obj.get("time_slice")
            sg = obj.get("sensitive_group")
            gid = obj.get("group")
            if gid not in self.groups:
                raise InputValidationError(f"unknown group {gid!r}", f"{path}.group")
            if sg not in self.sensitive_groups:
                raise InputValidationError(f"unknown sensitive_group {sg!r}", f"{path}.sensitive_group")
            if tid not in self.time_slices:
                raise InputValidationError(f"unknown time_slice {tid!r}", f"{path}.time_slice")
            cap = obj.get("cap")
            if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
                raise InputValidationError("cap must be a nonnegative integer", f"{path}.cap")
            key = (gid, sg, tid)
            if key in self.protection_targets:
                raise InputValidationError("duplicate protection target", path)
            self.protection_targets[key] = cap

        cm = raw.get("countermeasures") or {}
        if not isinstance(cm, dict):
            raise InputValidationError("countermeasures must be an object", "$.countermeasures")
        ids = cm.get("selected_action_ids", [])
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            raise InputValidationError("countermeasures.selected_action_ids must be a list", "$.countermeasures")
        self.countermeasure_ids = tuple(ids)

    def _load_actions(self, raw):
        actions = {}
        for i, obj in enumerate(_require_list(raw, "mitigation_actions", "$")):
            path = f"$.mitigation_actions[{i}]"
            aid = _require_id(obj, path)
            if aid in actions:
                raise InputValidationError(f"duplicate action id {aid}", path)
            kind = obj.get("kind")
            if kind not in {"suspend_review", "shorten_interval", "replace_version"}:
                raise InputValidationError("unsupported action kind", f"{path}.kind")
            cost = obj.get("cost", 1)
            if isinstance(cost, bool) or not isinstance(cost, int) or cost < 0:
                raise InputValidationError("cost must be a nonnegative integer", f"{path}.cost")
            target = obj.get("target")
            action = {"id": aid, "kind": kind, "cost": cost, "target": target, "raw": obj}
            if kind == "suspend_review":
                if target not in self.review_copies:
                    raise InputValidationError("suspend_review target must be a review_copy id", f"{path}.target")
                pause = obj.get("pause_interval")
                if pause is None:
                    action["pause_interval"] = self.review_copies[target]["interval"]
                else:
                    interval = normalize_interval(pause, f"{path}.pause_interval")
                    original = self.review_copies[target]["interval"]
                    if interval[0] < original[0] or interval[1] > original[1]:
                        raise InputValidationError("pause_interval must be contained in review interval", f"{path}.pause_interval")
                    action["pause_interval"] = interval
            elif kind == "shorten_interval":
                if target not in self.witnesses and target not in self.handoffs and target not in self.review_copies:
                    raise InputValidationError("shorten target must be witness, handoff or review_copy id", f"{path}.target")
                interval = normalize_interval(obj.get("interval"), f"{path}.interval")
                original = self._original_interval(target)
                if interval[0] < original[0] or interval[1] > original[1]:
                    raise InputValidationError("shortened interval must be contained in original interval", f"{path}.interval")
                action["interval"] = interval
            else:
                if target not in self.review_copies:
                    raise InputValidationError("replace_version target must be a review_copy id", f"{path}.target")
                pids = _require_list(obj, "replacement_pages", path)
                if not all(isinstance(x, str) for x in pids):
                    raise InputValidationError("replacement_pages must be page ids", f"{path}.replacement_pages")
                missing = [x for x in pids if x not in self.pages]
                if missing:
                    raise InputValidationError("unknown replacement page ids", f"{path}.replacement_pages", {"missing": missing})
                action["replacement_pages"] = tuple(pids)
            actions[aid] = action
        if len(actions) > MAX_ACTIONS:
            raise InputValidationError(f"at most {MAX_ACTIONS} mitigation actions are supported", "$.mitigation_actions")
        self.actions = {aid: actions[aid] for aid in sorted(actions)}
        for aid in self.countermeasure_ids:
            if aid not in self.actions:
                raise InputValidationError(f"countermeasure action {aid!r} is not defined", "$.countermeasures.selected_action_ids")

    def _original_interval(self, node_id):
        if node_id in self.witnesses:
            return self.witnesses[node_id]["interval"]
        if node_id in self.handoffs:
            return self.handoffs[node_id]["interval"]
        return self.review_copies[node_id]["interval"]

    def _build_variables_and_components(self):
        # One binary variable per implicit/explicit two-alternative witness.
        self.variables = {}
        for w in self.witnesses.values():
            if len(w["alternatives"]) == 2:
                vid = w["variable"] or w["id"]
                self.variables.setdefault(vid, []).append(w["id"])
        self.variable_ids = sorted(self.variables)
        for vid, wids in self.variables.items():
            if len({len(self.witnesses[w]["alternatives"]) for w in wids}) != 1:
                raise InputValidationError(f"shared variable {vid!r} must use two alternatives everywhere", "$.witnesses")

        # Potential masks connect variables that can ever reveal same segment.
        self.var_potential = {vid: 0 for vid in self.variable_ids}
        for vid, wids in self.variables.items():
            mask = 0
            for wid in wids:
                w = self.witnesses[wid]
                for dvid in w["alternatives"]:
                    svs = set(self.document_versions[dvid]["segment_versions"])
                    for rc in self.review_copies.values():
                        for pid in rc["pages"]:
                            page = self.pages[pid]
                            if page["segment_version"] in svs:
                                mask |= self.seg_bit[self.page_canonical[pid]]
            self.var_potential[vid] = mask

        dsu = DSU(self.variable_ids)
        for i, a in enumerate(self.variable_ids):
            for b in self.variable_ids[i + 1:]:
                if self.var_potential[a] & self.var_potential[b]:
                    dsu.union(a, b)
        comp_members = {}
        for vid in self.variable_ids:
            comp_members.setdefault(dsu.find(vid), []).append(vid)
        comps = []
        for vids in comp_members.values():
            vids = sorted(vids)
            if len(vids) > MAX_COMPONENT_VARS:
                raise InputValidationError(
                    f"ambiguity component has {len(vids)} variables; limit is {MAX_COMPONENT_VARS}",
                    "$.witnesses", {"variables": vids},
                )
            comps.append(vids)
        comps.sort(key=lambda vids: vids[0])
        self.components = [
            {"id": f"ac{i + 1:02d}", "variables": tuple(vids), "world_count": 1 << len(vids)}
            for i, vids in enumerate(comps)
        ]
        self.var_component = {}
        for ci, comp in enumerate(self.components):
            for j, vid in enumerate(comp["variables"]):
                self.var_component[vid] = (ci, j)

    # ---------- policy helpers ----------

    def sg_mask(self, sg_id: str) -> int:
        mask = 0
        for sid in self.sensitive_group_segments(sg_id):
            mask |= self.seg_bit[sid]
        return mask

    def sensitive_group_segments(self, sg_id: str) -> list[str]:
        return sorted(sid for sid, group in self.segment_sensitive_group.items() if group == sg_id)

    def protection_requirements(self):
        # Sensitive-group caps provide defaults; explicit targets override or
        # add cells.  Explicit targets do not silence defaults on other cells.
        caps = {}
        for sg_id, sg in self.sensitive_groups.items():
            if sg["protection_cap"] is None:
                continue
            for gid in self.group_ids:
                for tid in self.time_slices:
                    caps[(gid, sg_id, tid)] = sg["protection_cap"]
        caps.update(self.protection_targets)
        return [
            {"group": g, "sensitive_group": sg, "time_slice": ts, "cap": cap}
            for (g, sg, ts), cap in sorted(caps.items())
        ]

    def cells(self):
        for gid in self.group_ids:
            for sg in sorted(self.sensitive_groups):
                for tid in self.time_slices:
                    yield gid, sg, tid

    def resolve_segment(self, sid):
        if sid in self.canonical_of:
            return self.canonical_of[sid]
        if sid in self.seg_bit:
            return sid
        raise InputValidationError(f"unknown segment {sid!r}", "$")
