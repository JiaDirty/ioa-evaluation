import tempfile
import unittest
from pathlib import Path

from src.persistence import SQLiteDatabase, SQLiteTaskStore, TaskRecord


class PersistenceTaskStoreTest(unittest.TestCase):
    def test_sqlite_task_create_update_get_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteTaskStore(SQLiteDatabase(Path(tmp) / "tasks.sqlite3"))
            store.create_task(TaskRecord(task_id="t1", trace_id="tr1", status="queued", description="demo"))
            store.update_task_status("t1", "completed", result={"task_id": "t1", "status": "completed"})

            record = store.get_task("t1")
            self.assertEqual(record.status, "completed")
            self.assertEqual(record.result["status"], "completed")
            self.assertEqual([item.task_id for item in store.list_tasks()], ["t1"])


if __name__ == "__main__":
    unittest.main()
