import unittest

from src.evaluation.agent_model.artifact_dag import ArtifactDAG


class AgentModelArtifactDAGTest(unittest.TestCase):
    def test_multi_parent_lineage_and_primary_artifacts(self):
        events = [
            {"event_type": "artifact", "payload": {
                "artifact_id": "a", "parent_artifact_ids": [], "primary": True,
            }},
            {"event_type": "artifact", "payload": {
                "artifact_id": "b", "parent_artifact_ids": [], "primary": True,
            }},
            {"event_type": "artifact", "payload": {
                "artifact_id": "c", "parent_artifact_ids": ["a", "b"], "primary": True,
            }},
        ]
        dag = ArtifactDAG(events)

        self.assertEqual(set(dag.edges()), {("a", "c"), ("b", "c")})
        self.assertEqual(dag.max_depth(), 1)
        self.assertEqual(dag.primary_artifact_ids(), ["a", "b", "c"])

    def test_cycle_is_rejected(self):
        dag = ArtifactDAG([
            {"event_type": "artifact", "payload": {
                "artifact_id": "a", "parent_artifact_ids": ["b"],
            }},
            {"event_type": "artifact", "payload": {
                "artifact_id": "b", "parent_artifact_ids": ["a"],
            }},
        ])
        with self.assertRaisesRegex(ValueError, "cycle"):
            dag.max_depth()
