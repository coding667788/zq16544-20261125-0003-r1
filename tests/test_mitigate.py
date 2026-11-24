import unittest
from pathlib import Path

from folioimpact.crosscheck import crosscheck_case
from folioimpact.errors import InputValidationError
from folioimpact.mitigation import optimize
from folioimpact.model import CaseModel

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


def tiny_tie_case() -> CaseModel:
    raw = {
        "schema": "folioimpact.case/v1",
        "case_id": "tie-id",
        "groups": [{"id": "g", "sites": ["s"]}],
        "sensitive_groups": [{"id": "sg"}],
        "independent_segments": [
            {"id": "a", "sensitive_group": "sg"},
            {"id": "b", "sensitive_group": "sg"},
        ],
        "segment_versions": [
            {"id": "sa", "independent_segment": "a"},
            {"id": "sb", "independent_segment": "b"},
        ],
        "document_versions": [{"id": "dv_ab", "segment_versions": ["sa", "sb"]}],
        "pages": [{"id": "pa", "segment_version": "sa"}, {"id": "pb", "segment_version": "sb"}],
        "time_slices": [{"id": "t", "interval": [100, 200]}],
        "witnesses": [{
            "id": "w", "group": "g", "site": "s", "interval": [0, 1000],
            "alternatives": ["dv_ab"],
        }],
        "review_copies": [{
            "id": "r", "group": "g", "site": "s", "interval": [50, 300], "pages": ["pa", "pb"],
        }],
        "protection_targets": [{"group": "g", "sensitive_group": "sg", "time_slice": "t", "cap": 1}],
        "mitigation_actions": [
            {"id": "A_replace", "kind": "replace_version", "cost": 1, "target": "r", "replacement_pages": ["pa"]},
            {"id": "B_suspend", "kind": "suspend_review", "cost": 1, "target": "r"},
        ],
    }
    return CaseModel(raw)


def count_tie_case() -> CaseModel:
    raw = tiny_tie_case().raw
    raw["case_id"] = "tie-count"
    raw["protection_targets"][0]["cap"] = 0
    raw["review_copies"] = [
        {"id": "r1", "group": "g", "site": "s", "interval": [50, 300], "pages": ["pa"]},
        {"id": "r2", "group": "g", "site": "s", "interval": [50, 300], "pages": ["pb"]},
    ]
    raw["mitigation_actions"] = [
        {"id": "A_suspend_r1", "kind": "suspend_review", "cost": 1, "target": "r1"},
        {"id": "B_suspend_r2", "kind": "suspend_review", "cost": 1, "target": "r2"},
        {"id": "Z_shorten_witness", "kind": "shorten_interval", "cost": 2,
         "target": "w", "interval": [0, 40]},
    ]
    return CaseModel(raw)


def fourteen_variable_case(n: int = 14) -> CaseModel:
    witnesses = []
    for i in range(n):
        witnesses.append({
            "id": f"w{i:02d}", "group": "g", "site": "s", "interval": [0, 1000],
            "alternatives": ["dv_common", "dv_hidden"],
        })
    raw = {
        "schema": "folioimpact.case/v1",
        "case_id": f"component-{n}",
        "groups": [{"id": "g", "sites": ["s"]}],
        "sensitive_groups": [{"id": "sg"}],
        "independent_segments": [
            {"id": "common", "sensitive_group": "sg"},
            {"id": "hidden", "sensitive_group": "sg"},
        ],
        "segment_versions": [
            {"id": "sv_common", "independent_segment": "common"},
            {"id": "sv_hidden", "independent_segment": "hidden"},
        ],
        "document_versions": [
            {"id": "dv_common", "segment_versions": ["sv_common"]},
            {"id": "dv_hidden", "segment_versions": ["sv_hidden"]},
        ],
        "pages": [
            {"id": "p_common", "segment_version": "sv_common"},
            {"id": "p_hidden", "segment_version": "sv_hidden"},
        ],
        "time_slices": [{"id": "t", "interval": [100, 200]}],
        "witnesses": witnesses,
        "review_copies": [{
            "id": "r", "group": "g", "site": "s", "interval": [50, 300],
            "pages": ["p_common", "p_hidden"],
        }],
        "protection_targets": [{"group": "g", "sensitive_group": "sg", "time_slice": "t", "cap": n}],
        "mitigation_actions": [],
    }
    return CaseModel(raw)


class MitigationTests(unittest.TestCase):
    def test_minimum_cost_and_lexicographic_id_tie(self):
        result = optimize(tiny_tie_case())
        self.assertTrue(result["feasible"])
        self.assertEqual(result["minimum_cost"], 1)
        self.assertEqual(result["action_count"], 1)
        self.assertEqual(result["selected_action_ids"], ["A_replace"])

    def test_equal_cost_prefers_fewer_actions_before_id(self):
        result = optimize(count_tie_case())
        self.assertTrue(result["feasible"])
        self.assertEqual(result["minimum_cost"], 2)
        self.assertEqual(result["action_count"], 1)
        self.assertEqual(result["selected_action_ids"], ["Z_shorten_witness"])

    def test_comprehensive_fixture_plan(self):
        case = CaseModel.load_file(FIXTURES / "case_replay.json")
        result = optimize(case)
        self.assertTrue(result["feasible"])
        self.assertEqual(result["minimum_cost"], 3)
        self.assertEqual(result["action_count"], 2)
        self.assertEqual(result["selected_action_ids"], [
            "A01_safe_north_alternative", "A04_shorten_north_holding"
        ])
        self.assertEqual(result["search"]["enumerated_subsets"], 65536)
        self.assertTrue(result["policy_verdict"]["protection_sufficient_in_every_world"])
        self.assertTrue(result["policy_verdict"]["all_minimum_boundaries_preserved"])

    def test_no_plan_returns_reviewable_collision_worlds(self):
        case = CaseModel.load_file(FIXTURES / "case_collision.json")
        result = optimize(case)
        self.assertFalse(result["feasible"])
        self.assertEqual(result["search"]["enumerated_subsets"], 65536)
        self.assertEqual(result["search"]["feasible_evaluated_subsets"], 0)
        collision = result["collision_worlds"]
        too_much = collision["too_much_disclosure_world"]
        too_little = collision["too_little_information_world"]
        self.assertEqual(too_much["observed_maximum"], 2)
        self.assertEqual(too_much["requirement"]["cap"], 0)
        self.assertEqual(too_little["observed_minimum"], 0)
        self.assertEqual(too_little["missing_guaranteed_segments"], ["alpha"])
        self.assertTrue(too_much["boundary"]["version_assignment"]["witness_document_versions"])
        self.assertTrue(too_little["boundary"]["version_assignment"]["witness_document_versions"])


class ComponentLimitTests(unittest.TestCase):
    def test_fourteen_binary_variable_component_is_exhaustive(self):
        case = fourteen_variable_case(14)
        self.assertEqual(len(case.components), 1)
        self.assertEqual(len(case.components[0]["variables"]), 14)
        self.assertEqual(case.components[0]["world_count"], 16384)
        report = crosscheck_case(case, max_variables=14)
        self.assertTrue(report["checked"])
        self.assertTrue(report["ok"], report)

    def test_fifteen_variable_component_is_rejected(self):
        with self.assertRaises(InputValidationError):
            fourteen_variable_case(15)


if __name__ == "__main__":
    unittest.main()
