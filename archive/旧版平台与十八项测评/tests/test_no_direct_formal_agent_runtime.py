from pathlib import Path
import unittest


class NoDirectFormalAgentRuntimeTest(unittest.TestCase):
    def test_risk_tests_do_not_use_direct_agent_task_helper_for_formal_probes(self):
        offenders = []
        for path in Path("risk_tests").rglob("*.py"):
            if path.name == "base_test.py":
                continue
            text = path.read_text(encoding="utf-8")
            if "run_agent_task(" in text or "self.run_agent_task(" in text:
                offenders.append(str(path))

        self.assertEqual(offenders, [])

    def test_risk_tests_do_not_use_private_runtime_or_registry_injection_paths(self):
        forbidden = [
            "env._agents",
            "inject_sybil_attack(",
            "inject_fake_agent(",
            "inject_similar_name(",
            "manipulate_reputation(",
            "inject_capability_inflation(",
        ]
        offenders = []
        for path in Path("risk_tests").rglob("*.py"):
            if path.name == "base_test.py":
                continue
            text = path.read_text(encoding="utf-8")
            for snippet in forbidden:
                if snippet in text:
                    offenders.append(f"{path}:{snippet}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
