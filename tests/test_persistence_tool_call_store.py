import tempfile
import unittest
from pathlib import Path

from src.persistence import SQLiteDatabase, SQLiteToolCallStore
from src.tools import ToolCall, ToolResult


class PersistenceToolCallStoreTest(unittest.TestCase):
    def test_tool_call_append_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SQLiteToolCallStore(SQLiteDatabase(Path(tmp) / "tools.sqlite3"))
            call = ToolCall(call_id="c1", task_id="t1", trace_id="tr1", caller_agent_id="a1", tool_id="echo")
            result = ToolResult(call_id="c1", tool_id="echo", output={"ok": True})
            store.append_result(call, result)

            self.assertEqual(store.list_by_task("t1")[0]["tool_id"], "echo")
            self.assertEqual(store.list_by_trace("tr1")[0]["result"]["status"], "completed")
            self.assertEqual(store.list_recent()[0]["call_id"], "c1")


if __name__ == "__main__":
    unittest.main()
