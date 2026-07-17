import unittest

from src.audit.event_bus import EventBus


class TaskEventsTest(unittest.TestCase):
    def test_event_bus_filters_by_trace_and_task(self):
        bus = EventBus()
        bus.emit(task_id="t1", trace_id="tr1", stage="task_intake", event_type="received")
        bus.emit(task_id="t2", trace_id="tr2", stage="task_intake", event_type="received")
        self.assertEqual(len(bus.query(trace_id="tr1")), 1)
        self.assertEqual(len(bus.query(task_id="t2")), 1)
        self.assertEqual(bus.query(trace_id="tr1")[0].event_type, "received")


if __name__ == "__main__":
    unittest.main()
