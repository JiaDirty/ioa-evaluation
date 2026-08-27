import tempfile
import unittest
from pathlib import Path

from src.audit.event_bus import EventBus
from src.persistence import SQLiteDatabase, SQLiteEventStore


class PersistenceEventStoreTest(unittest.TestCase):
    def test_event_append_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteEventStore(SQLiteDatabase(Path(tmp) / "events.sqlite3"))
            bus = EventBus(store)
            bus.emit(task_id="t1", trace_id="tr1", stage="stage", event_type="created")

            self.assertEqual(len(store.list_by_trace("tr1")), 1)
            self.assertEqual(len(store.list_by_task("t1")), 1)
            self.assertEqual(bus.query(trace_id="tr1")[0].event_type, "created")


if __name__ == "__main__":
    unittest.main()
