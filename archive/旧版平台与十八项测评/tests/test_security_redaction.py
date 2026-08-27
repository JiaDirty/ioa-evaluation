import unittest

from src.audit.event_bus import EventBus
from src.persistence import MemoryToolCallStore
from src.security import REDACTED_VALUE, redact_sensitive
from src.tools import ToolCall, ToolResult


class SecurityRedactionTest(unittest.TestCase):
    def test_redacts_nested_sensitive_keys(self):
        data = {"token": "abc", "nested": {"api_key": "secret", "safe": "ok"}}
        redacted = redact_sensitive(data)
        self.assertEqual(redacted["token"], REDACTED_VALUE)
        self.assertEqual(redacted["nested"]["api_key"], REDACTED_VALUE)
        self.assertEqual(redacted["nested"]["safe"], "ok")

    def test_event_bus_and_tool_history_redact_payloads(self):
        bus = EventBus()
        event = bus.emit(
            task_id="t1",
            trace_id="tr1",
            stage="tool",
            event_type="tool_call",
            payload={"authorization": "Bearer abc", "query": "ok"},
        )
        self.assertEqual(event.payload["authorization"], REDACTED_VALUE)

        store = MemoryToolCallStore()
        store.append_result(
            ToolCall(
                call_id="c1",
                task_id="t1",
                trace_id="tr1",
                caller_agent_id="a1",
                tool_id="echo",
                arguments={"password": "pw", "text": "hello"},
            ),
            ToolResult(call_id="c1", tool_id="echo", output={"ok": True}),
        )
        record = store.list_by_trace("tr1")[0]
        self.assertEqual(record["arguments"]["password"], REDACTED_VALUE)
        self.assertEqual(record["arguments"]["text"], "hello")


if __name__ == "__main__":
    unittest.main()
