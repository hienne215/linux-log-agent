"""Validate synthetic acceptance fixtures, not a real Agent/MCP integration."""

import json
import unittest
from pathlib import Path


FIXTURE = Path(__file__).with_name("scenarios.json")


class AcceptanceFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_three_unique_cases(self):
        cases = self.spec["cases"]
        self.assertEqual({case["id"] for case in cases}, {
            "nginx_active", "nginx_failed_port_conflict", "nginx_journal_permission_denied"
        })
        self.assertEqual(len(cases), 3)

    def test_evidence_references_are_resolvable(self):
        for case in self.spec["cases"]:
            with self.subTest(case=case["id"]):
                ids = [item["evidence_id"] for item in case["observations"] if "evidence_id" in item]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertTrue(set(case["expected"]["must_cite"]).issubset(ids))
                self.assertTrue(case["expected"]["must_cite"])

    def test_read_only_and_bounded(self):
        policy = self.spec["policy"]
        self.assertLessEqual(policy["max_tool_calls"], 8)
        self.assertLessEqual(policy["max_llm_turns"], 6)
        self.assertLessEqual(policy["diagnostic_seconds"], 120)
        for case in self.spec["cases"]:
            for observation in case["observations"]:
                self.assertIn(observation["tool"], policy["read_only_tools"])
                self.assertNotIn(observation["tool"], policy["forbidden_tools"])
            self.assertLessEqual(len(case["observations"]), policy["max_tool_calls"])

    def test_permission_denied_is_explicitly_inconclusive(self):
        denied = next(c for c in self.spec["cases"] if c["id"].endswith("permission_denied"))
        self.assertEqual(denied["expected"]["result"], "insufficient_evidence")
        self.assertTrue(any(o.get("error_code") == "PERMISSION_DENIED" for o in denied["observations"]))


if __name__ == "__main__":
    unittest.main()
