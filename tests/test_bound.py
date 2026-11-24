import copy
import json
import unittest
from pathlib import Path

from folioimpact.bound import build_bound_report
from folioimpact.crosscheck import crosscheck_case
from folioimpact.engine import Evaluator
from folioimpact.model import CaseModel

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


def load_fixture(name: str) -> CaseModel:
    return CaseModel.load_file(FIXTURES / name)


def bound_map(report):
    return {(b["group"], b["sensitive_group"], b["time_slice"]): b for b in report["bounds"]}


class BoundFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = load_fixture("case_replay.json")
        cls.report = build_bound_report(cls.case)
        cls.bounds = bound_map(cls.report)

    def test_proven_fold_has_one_canonical_segment(self):
        folds = {f["canonical_independent_segment"]: f for f in self.report["independent_segment_canonicalization"]}
        self.assertIn("alpha", folds)
        self.assertTrue(folds["alpha"]["collapsed_by_proven_fold"])
        self.assertIn("alpha_fold_source", folds["alpha"]["members"])

    def test_open_copy_min_max_and_assignments(self):
        b = self.bounds[("g_archive", "sg_ledger", "ts_open_copy")]
        self.assertEqual(b["minimum_independent_segments"], 1)
        self.assertEqual(b["maximum_independent_segments"], 2)
        self.assertEqual(
            b["minimum_boundary"]["version_assignment"]["witness_document_versions"]["w_north_holding"],
            "dv_closed_summary_v2",
        )
        self.assertEqual(
            b["maximum_boundary"]["version_assignment"]["witness_document_versions"]["w_north_holding"],
            "dv_open_ledger_v1",
        )
        min_segments = {c["segment"] for c in b["minimum_boundary"]["exchange_chains"]}
        max_segments = {c["segment"] for c in b["maximum_boundary"]["exchange_chains"]}
        self.assertEqual(min_segments, {"alpha"})
        self.assertEqual(max_segments, {"alpha", "beta"})

    def test_handoff_chain_is_emitted_for_south_review(self):
        b = self.bounds[("g_archive", "sg_ledger", "ts_south_review")]
        chain = b["maximum_boundary"]["exchange_chains"][0]["exchange_chain"]
        self.assertEqual(chain, ["w_north_holding", "ho_north_to_south", "rc_south_desk"])
        contacts = b["maximum_boundary"]["exchange_chains"][0]["contacts"]
        self.assertTrue(all(c["intersects"] for c in contacts))

    def test_map_group_has_zero_to_one_bound(self):
        b = self.bounds[("g_archive", "sg_map", "ts_open_copy")]
        self.assertEqual((b["minimum_independent_segments"], b["maximum_independent_segments"]), (0, 1))

    def test_late_slice_is_empty(self):
        b = self.bounds[("g_archive", "sg_ledger", "ts_after_seal")]
        self.assertEqual((b["minimum_independent_segments"], b["maximum_independent_segments"]), (0, 0))
        self.assertEqual(b["minimum_boundary"]["exchange_chains"], [])

    def test_countermeasures_are_not_uncertainty_as_disclosure(self):
        cm = self.report["countermeasure_evaluation"]
        self.assertTrue(cm["sufficient_in_all_legal_worlds"])
        self.assertEqual(cm["selected_action_ids"], ["A01_safe_north_alternative", "A04_shorten_north_holding"])

    def test_crosscheck_fixture(self):
        result = crosscheck_case(self.case)
        self.assertTrue(result["checked"])
        self.assertTrue(result["ok"], result)


class HandoffLegalityTests(unittest.TestCase):
    def test_nonintersecting_handoff_interval_cannot_carry_visibility(self):
        raw = json.loads((FIXTURES / "case_replay.json").read_text(encoding="utf-8"))
        raw.pop("mitigation_actions", None)
        raw.pop("countermeasures", None)
        raw["handoffs"][0]["interval"] = [1700001100, 1700001200]
        case = CaseModel(raw)
        ev = Evaluator(case)
        pair = ev.boundary_pair(ev.state(0), "g_archive", "sg_ledger", "ts_south_review")
        self.assertEqual(pair["maximum"]["independent_segment_count"], 0)
        self.assertEqual(pair["maximum"]["exchange_chains"], [])

    def test_intersecting_handoff_is_found(self):
        case = load_fixture("case_replay.json")
        ev = Evaluator(case)
        reach = ev.state(0).reach["w_north_holding"]
        self.assertIn("rc_south_desk", reach)
        self.assertEqual(reach["rc_south_desk"], ("w_north_holding", "ho_north_to_south", "rc_south_desk"))


if __name__ == "__main__":
    unittest.main()
