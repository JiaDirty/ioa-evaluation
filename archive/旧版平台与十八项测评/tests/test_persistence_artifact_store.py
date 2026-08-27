import tempfile
import unittest
from pathlib import Path

from src.core.data_models import Artifact
from src.persistence import SQLiteArtifactStore, SQLiteDatabase


class PersistenceArtifactStoreTest(unittest.TestCase):
    def test_artifact_append_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteArtifactStore(SQLiteDatabase(Path(tmp) / "artifacts.sqlite3"))
            artifact = Artifact(
                artifact_id="a1",
                task_id="t1",
                producer_agent_id="agent",
                artifact_type="text_answer",
                content={"answer": "ok"},
            )
            store.append(artifact, trace_id="tr1")

            self.assertEqual(store.list_by_task("t1")[0]["content"]["answer"], "ok")
            self.assertEqual(store.list_by_trace("tr1")[0]["artifact_id"], "a1")


if __name__ == "__main__":
    unittest.main()
